"""A user-drawn entrance area on the bound robot's saved map."""
import json
from hashlib import sha256

import numpy as np

from config import Refused, atomic, integer, read
from jobs import MAX_ZONES, check_zone_geometry
from underlay import close_footprints


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _zone_geometry(plan, zones):
    anchor = plan.get("geometry_key")
    bits = plan.get("_footprint_bits")
    if not anchor or not bits:
        return None
    left, bottom, right, top = plan["bounds"]
    grid = plan["floor"]["grid_size"]
    width, height = (right - left) // grid, (top - bottom) // grid
    occupied = np.unpackbits(np.frombuffer(bits, dtype=np.uint8), count=width * height).reshape(height, width)[::-1]
    walkable = np.zeros((height, width), dtype=bool)
    for y, start, end in plan["floor"]["runs"]:
        walkable[(y - bottom) // grid, (start - left) // grid:(end - left) // grid] = True
    local = []
    for zone in zones:
        check_zone_geometry(zone, plan, grid)
        x1, y1, x2, y2 = zone
        # A contour edit in the adjoining grid cells can change the physical entrance.
        col0, row0 = max(0, (x1 - left) // grid - 1), max(0, (y1 - bottom) // grid - 1)
        col1 = min(width, (x2 - left + grid - 1) // grid + 1)
        row1 = min(height, (y2 - bottom + grid - 1) // grid + 1)
        region = np.s_[row0:row1, col0:col1]
        local.extend(sha256(np.packbits(mask[region]).tobytes()).hexdigest() for mask in (occupied, walkable))
    return {"anchor": anchor, "footprint": bits.hex(), "walkable": np.packbits(walkable).tobytes().hex(),
            "local": _fingerprint(local)}


def _compatible(item, plan):
    if "geometry" not in item:
        return item["revision"] == plan["revision"]
    reference = item["geometry"]
    if not isinstance(reference, dict) or reference.get("anchor") != plan.get("geometry_key"):
        return False
    try:
        current = _zone_geometry(plan, item["zones"])
        return (current is not None and reference["local"] == current["local"]
                and all(close_footprints(bytes.fromhex(reference[key]), bytes.fromhex(current[key]))
                        for key in ("footprint", "walkable")))
    except (KeyError, TypeError, ValueError):
        return False


def _settings(value):
    limits = {"suction": (0, 3), "water": (1, 3), "repeats": (1, 3)}
    if (not isinstance(value, dict) or value.keys() - limits.keys()
            or any(not integer(v, *limits[key]) for key, v in value.items())):
        raise Refused("Проверьте мощность, подачу воды и число проходов в сценарии.")
    return {"repeats": 1, **value}


class Entrance:
    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            if self.path.stat().st_size > 140_000:
                raise ValueError
            item = read(self.path)
            if not isinstance(item, dict) or not {"binding", "map_id", "revision", "zones"} <= item.keys():
                raise ValueError
            return item
        except FileNotFoundError:
            return None
        except (OSError, ValueError, RecursionError):
            raise Refused("Не удалось прочитать сохранённый сценарий входа.") from None

    def view(self, device_id, plan):
        item = self._read()
        if item is None:
            return {"version": None, "status": "empty"}
        same = (device_id and plan and item["binding"] == _fingerprint(device_id)
                and item["map_id"] == plan["id"] and _compatible(item, plan))
        result = {"version": _fingerprint(item), "status": "ready" if same else "stale"}
        if same:
            result.update(zones=item["zones"], settings=_settings(item.get("settings", {})))
        return result

    def save(self, device_id, plan, displayed, data):
        if not device_id or not plan or not displayed:
            raise Refused("Дождитесь связи с пылесосом и его карты.")
        clear = set(data) == {"action", "version", "map_id", "revision"} and data["action"] == "clear"
        if (not clear and set(data) - {"settings"} != {"version", "map_id", "revision", "zones"}
                or not integer(data["map_id"], 1, 2**31-1) or data["map_id"] != displayed["id"]
                or data["revision"] != displayed["revision"]):
            raise Refused("Карта изменилась. Выберите участок у входа заново.")
        if data["version"] != self.view(device_id, plan)["version"]:
            raise Refused("Сценарий уже изменили. Обновите панель.")
        if clear:
            self.path.unlink(missing_ok=True)
            return {"version": None, "status": "empty"}
        zones = data["zones"]
        if (not isinstance(zones, list) or not 1 <= len(zones) <= MAX_ZONES
                or any(not isinstance(z, list) or len(z) != 4
                       or not all(integer(v, -999999, 999999) for v in z) for z in zones)):
            raise Refused("Нарисуйте от одной до десяти зон у входа.")
        if len(set(map(tuple, zones))) != len(zones):
            raise Refused("В сценарии есть повторяющаяся зона.")
        for z in zones:
            check_zone_geometry(z, plan, plan["floor"]["grid_size"])
        saved = {"binding": _fingerprint(device_id), "map_id": plan["id"],
                 "revision": plan["revision"], "zones": zones}
        geometry = _zone_geometry(plan, zones)
        if geometry is not None:
            saved["geometry"] = geometry
        elif "geometry_key" in plan:
            raise Refused("Привязка к карте не подтверждена. Обновите карту и выберите участок у входа заново.")
        # A preview22 client only edits the shapes. Preserve a newer client's tuning.
        existing = self._read() if data["version"] is not None else None
        saved["settings"] = _settings(data.get("settings", (existing or {}).get("settings", {})))
        atomic(self.path, saved)
        return self.view(device_id, plan)
