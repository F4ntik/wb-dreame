"""Private, on-demand thumbnails of the manufacturer's fixed product catalogue."""
from http.client import HTTPSConnection
from io import BytesIO
from threading import Lock, Semaphore
from time import monotonic


_EQUIPMENT = "/media/catalog/product/cache/74c1057f7991b4edb2bc7bdaa94de933/s/"
_PARTS = "/cdn/shop/files/"
_MAX_BYTES = 512 * 1024
SOURCES = {
    "robot": ("api.ru.dreametech.com", _EQUIPMENT + "wk3xYixrkQPQ5pr/download/04.jpg?w=256"),
    "dock": ("api.ru.dreametech.com", _EQUIPMENT + "EPRqR47CysdkPBc/download/05.jpg?w=256"),
    "main_brush_left": ("fr.dreametech.com", _PARTS + "RollerBrush-_02_1_384x.jpg?v=1736847097"),
    "side_brush_left": ("fr.dreametech.com", _PARTS + "output_160x.jpg?v=1716261506"),
    "filter_left": ("fr.dreametech.com", _PARTS + "output_7ecbea13-05d4-4e0e-8147-3d161e37029a_384x.jpg?v=1736847458"),
    "mop_pad_left": ("fr.dreametech.com", _PARTS + "1_d09c295f-0c12-4a23-9a99-50c7e22121d5_160x.jpg?v=1698127445"),
    "dust_bag_status": ("fr.dreametech.com", _PARTS + "output_2_edff9d8a-7f55-4426-af76-7935a11acdda_384x.jpg?v=1729757344"),
    "detergent_left": ("fr.dreametech.com", _PARTS + "14_160x.png?v=1703064036"),
    "scale_inhibitor_left": ("fr.dreametech.com", _PARTS + "Scale_inhibitor_384x.jpg?v=1762325107"),
}


def _download(key):
    host, path = SOURCES[key]
    connection = HTTPSConnection(host, timeout=5)
    try:
        connection.request("GET", path, headers={"Accept": "image/webp,image/jpeg,image/png"})
        with connection.getresponse() as response:
            if response.status != 200:
                raise OSError("Photo unavailable")
            data = response.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise ValueError("Photo too large")
            return data
    finally:
        connection.close()


def _thumbnail(data, size):
    from PIL import Image

    with Image.open(BytesIO(data), formats=("JPEG", "PNG", "WEBP")) as source:
        if source.width * source.height > 4_000_000:
            raise ValueError("Photo too large")
        if source.has_transparency_data:
            rgba = source.convert("RGBA")
            photo = Image.new("RGB", source.size, "white")
            photo.paste(rgba, mask=rgba.getchannel("A"))
        else:
            photo = source.convert("RGB")
    # The official packshots use a white background with widely differing margins.
    bounds = photo.convert("L").point(lambda level: 255 if level < 240 else 0).getbbox()
    if bounds:
        left, top, right, bottom = bounds
        pad = max(2, round(max(right - left, bottom - top) * 0.025))
        photo = photo.crop((max(0, left - pad), max(0, top - pad),
                            min(photo.width, right + pad), min(photo.height, bottom + pad)))
    photo.thumbnail((size, size), Image.Resampling.LANCZOS)
    output = BytesIO()
    photo.save(output, format="WEBP", quality=82)
    return output.getvalue()


class ProductPhotos:
    def __init__(self, directory):
        self.directory = directory
        self.locks = {key: Lock() for key in SOURCES}
        self.workers = Semaphore(3)
        self.retry_after = {}

    def get(self, key):
        with self.locks[key]:
            path = self.directory / (key + ".webp")
            try:
                with path.open("rb") as cached:
                    data = cached.read(_MAX_BYTES + 1)
                if (20 <= len(data) <= _MAX_BYTES and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
                        and data[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
                        and int.from_bytes(data[4:8], "little") == len(data) - 8):
                    return data
            except FileNotFoundError:
                pass
            if monotonic() < self.retry_after.get(key, 0):
                raise OSError("Photo temporarily unavailable")
            try:
                with self.workers:
                    data = _thumbnail(_download(key), 256 if key in ("robot", "dock") else 128)
                self.directory.mkdir(mode=0o700, exist_ok=True)
                pending = path.with_suffix(".tmp")
                pending.write_bytes(data)
                pending.replace(path)
            except Exception as exc:
                self.retry_after[key] = monotonic() + 60
                raise OSError("Photo unavailable") from exc
            return data
