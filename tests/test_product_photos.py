import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from product_photos import ProductPhotos


class ProductPhotosTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.cache = ProductPhotos(Path(folder.name) / "photos")

    def test_parallel_readers_share_one_cropped_thumbnail_and_a_restart_reads_it_offline(self):
        source = Image.new("RGBA", (800, 800))
        source.paste("black", (240, 320, 560, 480))
        png = BytesIO()
        source.save(png, format="PNG")
        with patch("product_photos._download", return_value=png.getvalue()) as download:
            with ThreadPoolExecutor(4) as pool:
                results = list(pool.map(self.cache.get, ["robot"] * 4))
            self.assertEqual(len(set(results)), 1)
            self.assertEqual(ProductPhotos(self.cache.directory).get("robot"), results[0])
            download.assert_called_once_with("robot")
        with Image.open(BytesIO(results[0])) as photo:
            self.assertEqual(photo.format, "WEBP")
            self.assertEqual(photo.width, 256)
            self.assertLess(photo.height, 145)
            self.assertGreater(min(photo.getpixel((0, 0))), 240)
            self.assertLess(max(photo.getpixel((128, 70))), 15)

    def test_empty_and_truncated_cached_files_are_replaced(self):
        jpeg = BytesIO()
        Image.new("RGB", (48, 48), "silver").save(jpeg, format="JPEG")
        with patch("product_photos._download", return_value=jpeg.getvalue()) as download:
            correct = self.cache.get("robot")
            path = self.cache.directory / "robot.webp"
            for damaged in (b"", correct[:-1], correct[:8] + b"oops" + correct[12:]):
                with self.subTest(length=len(damaged)):
                    path.write_bytes(damaged)
                    self.assertEqual(self.cache.get("robot"), correct)
                    self.assertEqual(path.read_bytes(), correct)
            self.assertEqual(download.call_count, 4)

    def test_failed_source_is_rate_limited_and_can_recover(self):
        with patch("product_photos.monotonic", return_value=100) as clock, \
                patch("product_photos._download", return_value=b"bad image") as download:
            with self.assertRaises(OSError):
                self.cache.get("dock")
            with self.assertRaises(OSError):
                self.cache.get("dock")
            download.assert_called_once_with("dock")
            with self.assertRaises(KeyError):
                self.cache.get("../account")
            self.assertFalse(self.cache.directory.exists())
            clock.return_value += 61
            jpeg = BytesIO()
            Image.new("RGB", (48, 48), "silver").save(jpeg, format="JPEG")
            download.return_value = jpeg.getvalue()
            self.assertEqual(self.cache.get("dock")[:4], b"RIFF")
            self.assertEqual(download.call_count, 2)
