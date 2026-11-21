"""Room identities and the room names in the Wiren Board configuration editor."""
import json
import logging
import os
import stat
from contextlib import suppress
from hashlib import sha256
from pathlib import Path

from config import ROOM_KEY, STATE, Refused, atomic, valid_room_name, valid_room_names, validate

ROOM_CATALOG = Path("/run/wb-mqtt-dreame/rooms.json")


def read_room_file(path):
    # The editor runs as root; the driver owns these files.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Expected a regular file")
        content = stream.read(1_048_577)
        if len(content) > 1_048_576:
            raise ValueError("Room file exceeds size limit")
    return json.loads(content)


def room_aliases(config, directory=STATE):
    if "room_names" in config:
        return config["room_names"]
    try:
        names = read_room_file(directory / "room-names.json")
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, RecursionError) as exc:
        raise Refused("Не удалось прочитать прежние названия комнат на WB.") from exc
    if not valid_room_names(names):
        raise Refused("Файл прежних названий комнат повреждён.")
    return names


class RoomNames:
    def __init__(self, device_id="", aliases=None, catalog=None):
        self.device_id = device_id
        self.aliases = aliases or {}
        self.catalog = catalog
        self.published = None

    def _keys(self, plan):
        return {room: sha256(f"{self.device_id}:{key}".encode()).hexdigest()
                for room, key in (plan or {}).get("_room_keys", {}).items()}

    def for_map(self, plan):
        return {room: {"key": key, "alias": self.aliases.get(key)} for room, key in self._keys(plan).items()}

    def publish(self, plan):
        if self.catalog is None:
            return
        rows = [{"key": key, "original": f"{plan['rooms'][room]} (№ {room})"}
                for room, key in self._keys(plan).items()]
        if rows == self.published:
            return
        try:
            atomic(self.catalog, rows)
        except OSError:
            self.published = None
            with suppress(OSError):
                self.catalog.unlink(missing_ok=True)
            logging.warning("Не удалось обновить список комнат в конфигураторе WB")
        else:
            self.published = rows


def read_room_catalog(path=ROOM_CATALOG):
    try:
        rooms = read_room_file(path)
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError) as exc:
        raise Refused("Не удалось прочитать список комнат. Откройте настройки заново после обновления карты.") from exc
    if not isinstance(rooms, list) or any(
        not isinstance(room, dict) or room.keys() != {"key", "original"}
        or not isinstance(room["key"], str) or not ROOM_KEY.fullmatch(room["key"])
        or not isinstance(room["original"], str) for room in rooms
    ) or len({room["key"] for room in rooms}) != len(rooms):
        raise Refused("Не удалось прочитать список комнат. Откройте настройки заново после обновления карты.")
    return rooms


def confed_view(config, rooms, aliases):
    revision = sha256(json.dumps([config, aliases], sort_keys=True).encode()).hexdigest()
    return {"poll_seconds": config["poll_seconds"], "allow_commands": config["allow_commands"],
            "edit_revision": revision,
            "rooms": [{**room, "name": aliases.get(room["key"], "")} for room in rooms]}


def confed_save(form, config, rooms, aliases):
    current = confed_view(config, rooms, aliases)
    if (not isinstance(form, dict) or form.keys() != current.keys()
            or form["edit_revision"] != current["edit_revision"]):
        raise Refused("Настройки уже изменились. Откройте конфигуратор заново.")
    if not isinstance(form["rooms"], list) or len(form["rooms"]) != len(rooms):
        raise Refused("Список комнат изменился. Откройте конфигуратор заново.")

    names = aliases.copy()
    for row, room in zip(form["rooms"], rooms, strict=True):
        if not isinstance(row, dict) or row.keys() != {"key", "original", "name"} or any(
            row[key] != value for key, value in room.items()
        ):
            raise Refused("Комната на карте изменилась. Откройте конфигуратор заново.")
        name = row["name"]
        if name == "":
            names.pop(room["key"], None)
        elif isinstance(name, str) and name.isprintable() and valid_room_name(name.strip()):
            names[room["key"]] = name.strip()
        else:
            raise Refused("Введите название длиной от 1 до 48 символов без переносов строк.")
    saved = {**config, "poll_seconds": form["poll_seconds"], "allow_commands": form["allow_commands"]}
    if names != aliases:
        saved["room_names"] = names
    return validate(saved)
