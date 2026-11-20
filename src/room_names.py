"""Persistent names for rooms in the current map of a bound robot."""
from hashlib import sha256

from config import Refused, atomic, read


def valid_name(name):
    return isinstance(name, str) and 1 <= len(name) <= 48 and name.isprintable() and name == name.strip()


class RoomNames:
    def __init__(self, directory, device_id=""):
        self.path = directory / "room-names.json"
        self.device_id = device_id

    def _read(self):
        try:
            names = read(self.path)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, RecursionError) as exc:
            raise Refused("Не удалось прочитать сохранённые названия комнат на WB.") from exc
        if not isinstance(names, dict) or not all(valid_name(name) for name in names.values()):
            raise Refused("Файл сохранённых названий комнат повреждён.")
        return names

    def _keys(self, plan):
        return {room: sha256(f"{self.device_id}:{key}".encode()).hexdigest()
                for room, key in (plan or {}).get("_room_keys", {}).items()}

    def for_map(self, plan):
        keys = self._keys(plan)
        if not keys:
            return {}
        try:
            names = self._read()
        except Refused:
            names = {}
        return {room: {"key": key, "alias": names.get(key)} for room, key in keys.items()}

    def save(self, plan, request):
        if set(request) != {"key", "expected", "name"} or not isinstance(request["key"], str):
            raise Refused("Выберите комнату на сохранённой карте.")
        key = request["key"]
        if key not in self._keys(plan).values():
            raise Refused("Комната на карте изменилась. Выберите её заново.")
        name = request["name"]
        if name is not None:
            if not isinstance(name, str) or not name.isprintable() or not valid_name(name.strip()):
                raise Refused("Введите название длиной от 1 до 48 символов без переносов строк.")
            name = name.strip()
        names = self._read()
        if names.get(key) != request["expected"]:
            raise Refused("Название уже изменилось. Проверьте его перед повтором.")
        if name is None:
            names.pop(key, None)
        else:
            names[key] = name
        try:
            atomic(self.path, names)
        except OSError as exc:
            raise Refused("Не удалось сохранить название на WB. Проверьте имя перед повтором.") from exc
        return {"status": "saved"}
