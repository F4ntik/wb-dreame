"""A private, calibrated apartment drawing in the robot's coordinate system."""
import hashlib
import json
import math
from threading import Lock

from config import Refused, atomic


def valid_matrix(value):
    return (isinstance(value, list) and len(value) == 6
            and all(type(x) in (float, int) and math.isfinite(x) and abs(x) < 1e9 for x in value)
            and abs(value[0]*value[3] - value[1]*value[2]) > 1e-8)


def close_footprints(original, current):
    if not original or len(original) != len(current):
        return False
    a, b = int.from_bytes(original, "big"), int.from_bytes(current, "big")
    return bool(a | b) and (a ^ b).bit_count() * 100 <= (a | b).bit_count()


class Underlay:
    def __init__(self, directory):
        self.meta = directory / "underlay.json"
        self.image = directory / "underlay.jpg"
        self.alignment = directory / "state" / "underlay-alignment.json"
        self.reference_path = directory / "state" / "underlay-footprint.json"
        self.reference = (None, b"")
        try:
            if self.reference_path.stat().st_size <= 66_000:
                saved = json.loads(self.reference_path.read_bytes())
                bits = bytes.fromhex(saved["bits"])
                key = (hashlib.sha256(bits).hexdigest(), saved["map_id"], saved["step"])
                self.reference = key, bits
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            pass
        self.lock = Lock()
        self.stamp = None
        self.data = self.spec = None

    def _matches(self, plan):
        if self.spec["bounds"] != plan["bounds"]:
            return False
        original = self.spec["footprint"]
        exact = original == plan["footprint"]
        bits = plan.get("_footprint_bits")
        if not bits:
            return exact
        key = (original, plan["id"], plan["floor"]["grid_size"])
        reference_key, reference_bits = self.reference
        if exact:
            if key != reference_key:
                self.reference = key, bits
                try:
                    atomic(self.reference_path, {"map_id": key[1], "step": key[2], "bits": bits.hex()})
                except OSError:
                    pass  # The exact calibrated outline is usable even with read-only storage.
            return True
        # Compare with the calibrated outline, never with the previous refresh.
        return key == reference_key and close_footprints(reference_bits, bits)

    def for_map(self, plan):
        if not plan:
            return None
        try:
            stat = self.meta.stat(), self.image.stat()
            stamp = tuple((s.st_mtime_ns, s.st_size) for s in stat)
            if stamp != self.stamp:
                if stat[0].st_size > 4096 or not 0 < stat[1].st_size <= 8_000_000:
                    return None
                spec = json.loads(self.meta.read_bytes())
                raw = self.image.read_bytes()
                matrix = spec["matrix"]
                if not (valid_matrix(matrix)
                        and all(type(spec[k]) is int and 0 < spec[k] <= 8192 for k in ("width", "height"))
                        and raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
                        and hashlib.sha256(raw).hexdigest() == spec["image_sha256"]):
                    return None
                self.spec, self.data, self.stamp = spec, raw, stamp
            if not self._matches(plan):
                return None
            result = {k: self.spec[k] for k in ("matrix", "width", "height")} | {"url": "/dreame/api/underlay",
                      "note": self.spec.get("note", "Границы для уборки берутся с карты Dreame.")}
            if self.alignment.exists() and self.alignment.stat().st_size <= 4096:
                adjustment = json.loads(self.alignment.read_bytes())
                if (adjustment.get("image_sha256") == self.spec["image_sha256"]
                        and adjustment.get("original") == self.spec["matrix"] and valid_matrix(adjustment.get("matrix"))):
                    result.update(matrix=adjustment["matrix"], note="Подложка подогнана вручную. Границы уборки задаёт карта Dreame.")
            return result
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return None

    def adjust(self, plan, request):
        with self.lock:
            current = self.for_map(plan)
            if (not current or set(request) != {"matrix", "expected", "revision"} or request["revision"] != plan["revision"]
                    or request["expected"] != current["matrix"]):
                raise Refused("Карта или подложка уже изменилась. Обновите страницу.")
            matrix = request["matrix"]
            if not valid_matrix(matrix):
                raise Refused("Не удалось проверить положение подложки")
            original = self.spec["matrix"]
            det = lambda v: v[0]*v[3] - v[1]*v[2]
            if not 0.05 <= det(matrix) / det(original) <= 20:
                raise Refused("Масштаб или ориентация слишком сильно отличаются от исходного плана")
            atomic(self.alignment, {"original": original, "image_sha256": self.spec["image_sha256"], "matrix": matrix})
            return {"status": "saved", "message": "Положение подложки сохранено на контроллере"}
