"""A user-drawn entrance area on the bound robot's saved map."""
import json
from hashlib import sha256

from config import Refused, atomic, integer, read
from jobs import MAX_ZONES, check_zone_geometry


def _fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Entrance:
    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            item = read(self.path)
            if not isinstance(item, dict) or not {"binding", "map_id", "revision", "zones"} <= item.keys():
                raise ValueError
            return item
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise Refused("Не удалось прочитать сохранённый сценарий входа.") from None

    def view(self, device_id, plan):
        item = self._read()
        if item is None:
            return {"version": None, "status": "empty"}
        same = (device_id and plan and item["binding"] == _fingerprint(device_id)
                and item["map_id"] == plan["id"] and item["revision"] == plan["revision"])
        result = {"version": _fingerprint(item), "status": "ready" if same else "stale"}
        if same:
            result["zones"] = item["zones"]
        return result

    def save(self, device_id, plan, displayed, data):
        if not device_id or not plan or not displayed:
            raise Refused("Дождитесь связи с пылесосом и его карты.")
        if (set(data) != {"version", "map_id", "revision", "zones"}
                or not integer(data["map_id"], 1, 2**31-1) or data["map_id"] != displayed["id"]
                or data["revision"] != displayed["revision"]):
            raise Refused("Карта изменилась. Выберите участок у входа заново.")
        if data["version"] != self.view(device_id, plan)["version"]:
            raise Refused("Сценарий уже изменили. Обновите панель.")
        zones = data["zones"]
        if (not isinstance(zones, list) or not 1 <= len(zones) <= MAX_ZONES
                or any(not isinstance(z, list) or len(z) != 4
                       or not all(integer(v, -999999, 999999) for v in z) for z in zones)):
            raise Refused("Нарисуйте от одной до десяти зон у входа.")
        if len(set(map(tuple, zones))) != len(zones):
            raise Refused("В сценарии есть повторяющаяся зона.")
        for z in zones:
            check_zone_geometry(z, plan, plan["floor"]["grid_size"])
        atomic(self.path, {"binding": _fingerprint(device_id), "map_id": plan["id"],
                           "revision": plan["revision"], "zones": zones})
        return self.view(device_id, plan)
