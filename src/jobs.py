"""Validate and journal explicitly requested robot actions."""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic

from config import Refused, integer

REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
REVISION = re.compile(r"[a-f0-9]{64}\Z")
ACTION_FIELDS = {"request_id", "action"}
START_FIELDS = ACTION_FIELDS | {"map_id", "revision", "kind"}
ACTIVE = {"AUTO_CLEANING", "SEGMENT_CLEANING", "ZONE_CLEANING", "SPOT_CLEANING"}
PAUSED = {task + "_PAUSED" for task in ACTIVE} | {
    "MOPPING_PAUSED", "AUTO_MOPPING_PAUSED", "SEGMENT_MOPPING_PAUSED", "ZONE_MOPPING_PAUSED",
}
CLEANING = {"SWEEPING", "MOPPING", "SWEEPING_AND_MOPPING", "SPOT_CLEANING"}
AT_REST = {"IDLE", "CHARGING", "CHARGING_COMPLETED"}


def blocking_error(raw_error, faults, self_wash_base):
    if type(raw_error) is not int or raw_error < 0:
        return None
    # Dreame's self-wash dock reports REMOVE_MOP (68) during normal pad handling.
    if raw_error != 0 and not (self_wash_base and raw_error == 68):
        return raw_error
    if faults is None:
        return 0 if raw_error == 0 else None
    if type(faults) is int:
        codes = [faults]
    elif type(faults) is str and len(faults) <= 256:
        raw = faults.strip()
        if raw and not re.fullmatch(r"[0-9]{1,5}(?:\s*,\s*[0-9]{1,5})*", raw):
            return None
        codes = [int(item) for item in raw.split(",")] if raw else []
    else:
        return None
    return next((n for n in codes if n != 0 and not (self_wash_base and n == 68)), 0)


def _normalize(request):
    if type(request) is not dict or not ACTION_FIELDS <= request.keys():
        raise Refused("Укажите действие и идентификатор запроса")
    request_id, action = request["request_id"], request["action"]
    if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
        raise Refused("Некорректный идентификатор запроса")
    if action in ("pause", "resume", "stop", "dock") and request.keys() == ACTION_FIELDS:
        return request.copy()
    if action != "start" or not START_FIELDS <= request.keys():
        raise Refused("Проверьте действие и его параметры")
    revision, kind = request["revision"], request["kind"]
    if not integer(request["map_id"], 1, 2**31 - 1) or not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise Refused("Укажите карту и её ревизию")
    if kind == "shortcut":
        if (request.keys() != START_FIELDS | {"shortcut_id", "shortcut_revision"}
                or not integer(request["shortcut_id"], 26, 128) or not isinstance(request["shortcut_revision"], str)
                or not REVISION.fullmatch(request["shortcut_revision"])):
            raise Refused("Выберите сценарий из актуального списка Dreamehome")
        return request.copy()
    if kind == "all":
        if request.keys() != START_FIELDS:
            raise Refused("Для всей карты задайте параметры уборки в Dreamehome")
        return request.copy()
    options = {"repeats", "suction", "water"}
    if kind == "rooms":
        options.add("expected_cleangenius")
    if kind not in ("rooms", "zone") or kind not in request or request.keys() - START_FIELDS - {kind} - options:
        raise Refused("Укажите одну цель уборки")
    target = request[kind]
    if kind == "rooms" and not (type(target) is list and 1 <= len(target) <= 32
                                and all(integer(x, 1, 255) for x in target) and len(set(target)) == len(target)):
        raise Refused("Укажите от 1 до 32 разных числовых ID комнат")
    if kind == "zone" and not (type(target) is list and len(target) == 4
                               and all(integer(x, -999999, 999999) for x in target)
                               and target[0] < target[2] and target[1] < target[3]):
        raise Refused("Укажите прямоугольник [x1, y1, x2, y2] с возрастающими координатами")
    for key, low, high in (("suction", 0, 3), ("water", 1, 3), ("repeats", 1, 3)):
        if key in request and not integer(request[key], low, high):
            raise Refused("Проверьте мощность, влажность и число проходов")
    if "expected_cleangenius" in request and not integer(request["expected_cleangenius"], 0, 2):
        raise Refused("Неизвестный режим CleanGenius")
    return {"repeats": 1, **request, kind: target.copy()}


def check_observation_age(snap):
    observed_at = snap.get("command_time")
    if type(observed_at) not in (float, int) or not 0 <= monotonic() - observed_at <= 15:
        raise Refused("Нет свежего ответа о состоянии робота")


def _check_state(action, snap):
    check_observation_age(snap)
    state, task = snap.get("state"), snap.get("task")
    if action == "start":
        ready = state in AT_REST or (state == "DRYING" and snap.get("docked_drying") is True)
        if task == "COMPLETED" and ready and snap.get("error") == 0:
            return
        if state == "WASHING":
            raise Refused("Станция моет швабры. Запуск станет доступен после промывки")
        if type(snap.get("error")) is int and snap["error"] > 0:
            raise Refused(f"Робот сообщает об ошибке {snap['error']}. Подробности есть в Dreamehome")
        if task in ACTIVE or task in PAUSED:
            raise Refused("У робота есть незавершённая уборка. Завершите её перед новым заданием")
        raise Refused("Робот занят, в ошибке или ещё не подтвердил готовность")
    running = task in ACTIVE and state in CLEANING
    paused = task in PAUSED and state == "PAUSED"
    valid = {"pause": running, "resume": paused and snap.get("error") == 0,
             "stop": running or paused or state == "RETURNING",
             "dock": running or paused or (task == "COMPLETED" and state == "IDLE")}
    if not valid[action]:
        raise Refused("Действие недоступно в текущем состоянии робота")


def _check_start(request, snap, map_info):
    if (not map_info or snap.get("command_map") is not True or request["map_id"] != snap.get("map_id")
            or request["map_id"] != map_info.get("id") or request["revision"] != map_info.get("revision")
            or snap.get("rooms") != map_info.get("rooms")):
        raise Refused("Карта изменилась или её актуальность не подтверждена. Обновите панель")
    settings = snap.get("command_settings", {})
    mode, min_charge = settings.get("mode"), settings.get("min_battery")
    if mode not in ("sweep", "mop", "both", "mop_after_sweep") or not integer(min_charge, 0, 99):
        raise Refused("Робот не подтвердил режим уборки")
    if not integer(snap.get("battery"), min_charge + 1, 100):
        raise Refused("Заряд слишком низкий или не подтверждён")
    kind = request["kind"]
    if kind == "shortcut":
        scene = snap.get("command_shortcut") or {}
        if (request["shortcut_id"] != scene.get("id") or request["shortcut_revision"] != scene.get("revision")
                or scene.get("map_id") != map_info["id"]
                or any(str(room) not in map_info["rooms"] for room in scene["rooms"])):
            raise Refused("Карта или сценарий Dreamehome изменились. Выберите сценарий заново")
        for zone in scene["zones"]:
            check_zone_geometry(zone, map_info, snap.get("command_grid"))
        return
    if kind == "all":
        return
    if "expected_cleangenius" in request and request["expected_cleangenius"] != settings.get("cleangenius_level"):
        raise Refused("CleanGenius изменился. Обновите панель и подтвердите режим заново")
    genius = settings.get("cleangenius")
    if genius is None:
        raise Refused("Робот не подтвердил состояние CleanGenius")
    if genius:
        if kind != "rooms":
            raise Refused("CleanGenius поддерживает уборку комнат и всей квартиры. Для зоны выберите ручной режим")
        if settings.get("mop_installed") is not True:
            raise Refused("Для CleanGenius робот должен подтвердить, что швабры установлены")
        if request["repeats"] != 1 or "water" in request or "suction" in request:
            raise Refused("В CleanGenius мощность, воду и проходы выбирает робот")
    if settings.get("customized") is not False:
        raise Refused("В Dreamehome включены или не подтверждены индивидуальные настройки комнат")
    if mode == "mop_after_sweep" and not genius:
        raise Refused("Для комнат и зон выберите один этап уборки в Dreamehome")
    if ("suction" in request and mode == "mop") or (not genius and mode != "mop" and settings.get("max_suction") is not False):
        raise Refused("Текущий режим не позволяет задать мощность всасывания")
    native_water = settings.get("verified_wetness") is True
    per_target_water = settings.get("preserve_water") is True or (
        native_water and (kind == "rooms" or request.get("water") == settings.get("water")))
    if "water" in request and (mode == "sweep" or not per_target_water):
        raise Refused("Текущий режим не позволяет задать влажность по шкале от 1 до 3")
    if not integer(settings.get("suction"), 0, 3) or not integer(settings.get("water"), 1, 3):
        raise Refused("Робот не подтвердил мощность или влажность")
    if settings.get("preserve_water") is not True and not native_water and mode != "sweep":
        raise Refused("Для мойки в выбранных комнатах и зонах не подтверждена совместимость шкалы влажности")
    if kind == "rooms" and not all(str(room) in map_info["rooms"] for room in request["rooms"]):
        raise Refused("На текущей карте нет выбранных комнат")
    if kind == "zone":
        bounds, grid = map_info.get("bounds"), snap.get("command_grid")
        if not bounds or len(bounds) != 4 or not integer(grid, 1, 999999):
            raise Refused("Робот не подтвердил границы карты")
        left, bottom, right, top = bounds
        x1, y1, x2, y2 = request["zone"]
        if not (left <= x1 < x2 <= right and bottom <= y1 < y2 <= top and min(x2 - x1, y2 - y1) > 2 * grid):
            raise Refused("Зона слишком мала или выходит за границы карты")
        floor = map_info.get("floor") or {}
        if not any(y < y2 and y + grid > y1 and left_x < x2 and right_x > x1
                   for y, left_x, right_x in floor.get("runs", [])):
            raise Refused("В выбранной зоне нет доступного пола по карте пылесоса")


class Jobs:
    """The driver serializes calls for the single robot under its lock."""

    def __init__(self, state_dir):
        self._path = Path(state_dir) / "jobs.sqlite3"
        with closing(sqlite3.connect(self._path)) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL, at TEXT NOT NULL
            )""")
            db.execute("UPDATE requests SET status = 'unknown' WHERE status = 'sending'")

    def preview(self, request, fresh_observation, map_info):
        request = _normalize(request)
        _check_state(request["action"], fresh_observation)
        if request["action"] == "start":
            _check_start(request, fresh_observation, map_info)
        return {"request": request}

    def submit(self, request, robot, fresh_observation, map_info, allow_commands=False):
        request = _normalize(request)
        request_id = request["request_id"]
        body = self._body(request)
        with closing(sqlite3.connect(self._path)) as db:
            prior = self._lookup(db, request_id, body)
            if prior:
                return prior
            if allow_commands is not True:
                raise Refused("Управление отключено в настройках драйвера")
            self.preview(request, fresh_observation, map_info)
            robot.validate_job(request, fresh_observation)
            at = datetime.now(timezone.utc).isoformat()
            with db:
                db.execute("INSERT INTO requests VALUES (?, ?, 'sending', ?)", (request_id, body, at))

            status = "unknown"
            try:
                reply = robot.send_job(request, fresh_observation, allow_commands=True)
            except Exception:
                pass  # The cloud may have received the request before the reply was lost.
            else:
                if isinstance(reply, dict) and type(reply.get("code")) is int and reply["code"] == 0:
                    status = "acknowledged"
            with db:
                db.execute("UPDATE requests SET status = ? WHERE id = ?", (status, request_id))
        return self._record((body, status, at))

    def lookup(self, request):
        request = _normalize(request)
        with closing(sqlite3.connect(self._path)) as db:
            return self._lookup(db, request["request_id"], self._body(request))

    def _lookup(self, db, request_id, body):
        row = db.execute("SELECT body, status, at FROM requests WHERE id = ?", (request_id,)).fetchone()
        if row:
            if row[0] != body:
                raise Refused("Этот идентификатор уже использован для другого запроса")
            return self._record(row)

    @staticmethod
    def _body(request):
        return json.dumps(request, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _record(row):
        body, status, at = row
        return {"request": json.loads(body), "status": "unknown" if status == "sending" else status, "at": at}

    @property
    def last(self):
        with closing(sqlite3.connect(self._path)) as db:
            row = db.execute("SELECT body, status, at FROM requests ORDER BY rowid DESC LIMIT 1").fetchone()
        return self._record(row) if row else None

    @property
    def history(self):
        with closing(sqlite3.connect(self._path)) as db:
            rows = db.execute("SELECT body, status, at FROM requests ORDER BY rowid DESC LIMIT 50").fetchall()
        return [self._record(row) for row in rows]
