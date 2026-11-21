import json
import os
import re
import tempfile
from pathlib import Path

DATA = Path("/mnt/data/etc/wb-mqtt-dreame")
CONFIG = DATA / "config.json"
ACCOUNT = DATA / "account.json"
STATE = DATA / "state"
COMMAND_MODELS = frozenset("dreame.vacuum.r2492" + suffix for suffix in "abj")
MODELS = dict.fromkeys(COMMAND_MODELS, "Dreame L40 Ultra") | {
    "dreame.vacuum.r9419a": "Dreame L40s Pro Ultra",
}
ID = re.compile(r"[a-z][a-z0-9_]{0,47}\Z")
ROOM_KEY = re.compile(r"[a-f0-9]{64}\Z")
DEFAULT = {"allow_commands": False, "poll_seconds": 30, "profiles": []}


class Refused(ValueError):
    """A safe, user-readable reason. Never include credentials or cloud replies."""


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic(path, value):
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + "-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2, allow_nan=False)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(tmp).unlink(missing_ok=True)


def integer(v, low, high):
    return type(v) is int and low <= v <= high


def valid_room_name(name):
    return isinstance(name, str) and 1 <= len(name) <= 48 and name.isprintable() and name == name.strip()


def valid_room_names(names):
    return isinstance(names, dict) and all(isinstance(key, str) and ROOM_KEY.fullmatch(key) and valid_room_name(name)
                                           for key, name in names.items())


def validate(c):
    if not isinstance(c, dict) or not DEFAULT.keys() <= c.keys() <= DEFAULT.keys() | {"room_names", "panel_navigation"}:
        raise Refused("Проверьте поля конфигурации")
    if c.get("panel_navigation", "auto") not in ("auto", "top", "left"):
        raise Refused("Выберите расположение разделов панели в конфигураторе WB")
    if not valid_room_names(c.get("room_names", {})):
        raise Refused("Проверьте сохранённые названия комнат")
    if type(c["allow_commands"]) is not bool or not integer(c["poll_seconds"], 15, 300):
        raise Refused("Интервал опроса должен быть целым числом от 15 до 300 с")
    profiles = c["profiles"]
    if not isinstance(profiles, list) or len(profiles) > 32:
        raise Refused("Допустимо до 32 профилей")
    seen = set()
    for p in profiles:
        base = {"id", "name", "map_id", "repeats", "kind"}
        if not isinstance(p, dict) or not base <= p.keys() or not isinstance(p["id"], str) or not ID.fullmatch(p["id"]) or p["id"] in seen or p["id"] == "all":
            raise Refused("Проверьте идентификаторы профилей")
        seen.add(p["id"])
        if not isinstance(p["name"], str) or not 1 <= len(p["name"].strip()) <= 64 or not integer(p["map_id"], 1, 2**31-1) or not integer(p["repeats"], 1, 3):
            raise Refused("Проверьте имя, карту и число проходов")
        kind = p["kind"]
        if kind not in ("rooms", "zone") or p.keys() - base - {"rooms", "zone", "suction", "water"}:
            raise Refused("Неизвестные параметры профиля")
        target = p.get(kind)
        if not isinstance(target, list) or p.get("rooms" if kind == "zone" else "zone") not in (None, []):
            raise Refused("Укажите одну цель профиля")
        if kind == "rooms" and not (1 <= len(target) <= 32 and all(integer(x, 1, 255) for x in target) and len(set(target)) == len(target)):
            raise Refused("Нужны разные числовые ID комнат")
        if kind == "zone" and not (len(target) == 4 and all(integer(x, -999999, 999999) for x in target) and target[0] < target[2] and target[1] < target[3]):
            raise Refused("Зона задается как [x1,y1,x2,y2] по возрастанию координат")
        for key, low, high in (("suction", 0, 3), ("water", 1, 3)):
            if key in p and not integer(p[key], low, high):
                raise Refused("Проверьте ступени всасывания и увлажнения")
    return c
