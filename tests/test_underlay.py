import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from underlay import Underlay


class CalibratedMapTest(unittest.TestCase):
    def test_room_renumbering_preserves_drawing_new_footprint_hides_it(self):
        with TemporaryDirectory() as folder:
            asset = Underlay(Path(folder))
            image = b"\xff\xd8fixture\xff\xd9"
            spec = {"bounds": [0, 0, 100, 120], "footprint": "floor", "matrix": [1, 0, 0, 1, 0, 0],
                    "width": 15, "height": 20, "image_sha256": hashlib.sha256(image).hexdigest()}
            asset.meta.write_text(json.dumps(spec), encoding="utf8")
            asset.image.write_bytes(image)
            self.assertTrue(asset.for_map(dict(spec, id=7, rooms={"1": "Room"})))
            self.assertTrue(asset.for_map(dict(spec, id=8, rooms={"2": "Renamed"})))
            self.assertIsNone(asset.for_map(dict(spec, footprint="new")))
            self.assertIsNone(asset.for_map(dict(spec, bounds=[10, 0, 110, 120])))
            (Path(folder) / "state").mkdir()
            plan = dict(spec, revision="test-version")
            shift = [1, 0, 0, 1, 4, 5]
            adjustment = {"expected": spec["matrix"], "matrix": shift, "revision": "test-version"}
            self.assertEqual(asset.adjust(plan, adjustment)["status"], "saved")
            self.assertEqual(asset.for_map(plan)["matrix"], shift)
            with self.assertRaises(ValueError):
                asset.adjust(plan, adjustment)
