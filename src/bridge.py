"""Dreame telemetry and the local, permission-gated web interface."""
import logging
import os
import signal
import threading
from hashlib import sha256
from pathlib import Path
from time import time

from config import CONFIG, DATA, MODELS, STATE, Refused, read, validate
from entrance import Entrance
from history_map import history_position, render_history
from history_media import obstacle_photo, obstacles
from jobs import ACTIVE, PAUSED, Jobs, _normalize
from mqtt_namespace import CHANNELS, OWNER_FILE, ROOM_CHANNELS, STATE_LABELS, SUFFIXES, RetainedSession, encode, lock_namespace, read_owner, room_meta
from overlays import live_room_outcomes, read_overlays
from preferences import apply_preference
from panel import interactive_map, map_snapshot, render_map_frame
from robot import TRACKING_STATES, Robot, check_writes_with
from room_names import ROOM_CATALOG, RoomNames, room_aliases
from schedules import read_schedules, save_charging_schedule, save_cleaning_schedule
from shortcuts import read_shortcut, read_shortcuts
from underlay import Underlay
from webui import WebConflict, start_ui

POSITION_STATES = {"PAUSED", "IDLE", "RETURNING", "RETURNING_TO_WASH", "RETURNING_AUTO_EMPTY",
                   "RETURNING_INSTALL_MOP", "RETURNING_REMOVE_MOP", "RETURNING_TO_DRAIN"}
ROOM_STATUS_TASKS = ACTIVE | PAUSED


class Bridge:
    def __init__(self):
        self.mqtt = None
        self.refresh = threading.Event()
        self.halt = threading.Event()
        self.cloud = None
        self.map_key = None
        self.map_frame = None
        self.room_channels = {}
        self.title = "Dreame"
        self.io_lock = threading.Lock()
        self.allow_commands = False
        self.jobs = None
        self.underlay = Underlay(DATA)
        self.room_names = RoomNames()
        self.entrance = Entrance(STATE / "entrance.json")
        self.latest = {"online": False, "state": "Ожидание данных", "map": None, "battery": None, "details": {}}

    def pub(self, suffix, value):
        if self.mqtt and self.mqtt.ready:
            return self.mqtt.publish(suffix, value)

    def connect_mqtt(self):
        if self.mqtt:
            self.mqtt.close()
        self.mqtt = RetainedSession()
        self.mqtt.claim(STATE, self.title)
        self.room_channels = {name: None for name in ROOM_CHANNELS
                              if any(f"/controls/{name}{suffix}" in self.mqtt.snapshot for suffix in SUFFIXES)}
        self.mqtt.start_watch(self.refresh.set)
        self.map_frame = None
        self.offline("Ожидание данных")

    def offline(self, status):
        self.room_names.publish(None)
        self.latest = {**self.latest, "online": False, "state": status, "map": None, "_live_plan": None,
                       "tracking_active": False, "overlays": [], "camera": {}, "cleangenius": None}
        for name in (*CHANNELS, *self.room_channels):
            if name not in ("state_connection", "map_frame"):
                self.pub("/controls/" + name + "/meta/error", "r")
        self.room_channels = dict.fromkeys(self.room_channels)
        self.publish_map(None)
        self.pub("/meta/error", "r")
        return self.pub("/controls/state_connection", status)

    def snapshot(self, data):
        tracking = data.get("task") in ACTIVE and data.get("state") in TRACKING_STATES
        station_pause = data.get("task") in ACTIVE and data.get("state") in {"WASHING", "WASHING_PAUSED"}
        room_status_since = None
        if data.get("task") in ROOM_STATUS_TASKS:
            room_status_since = self.latest.get("_room_status_since") if self.latest["online"] else None
            if room_status_since is None:
                room_status_since = int(time() * 1000)
        self.latest = {"online": True, "state": data["state"], "battery": data["battery"], "dock_status": data.get("dock_status"),
                       "map": interactive_map(data.get("saved_map"), self.latest.get("map")),
                       "tracking_active": tracking, "_live_plan": data.get("saved_map"), "_station_pause": station_pause,
                       "_room_status_since": room_status_since,
                       "details": data.get("details", {}), "overlays": data.get("overlays", []), "cleangenius": data.get("cleangenius"),
                       "preferences": data.get("preferences", []), "camera": data.get("camera", {})}
        self.room_names.publish(self.latest["map"])
        for key, value in (("state_robot", data["state"]), ("fw", data["fw"]),
                           ("level_battery", data["battery"]), ("map_id", data["map_id"]),
                           ("rooms", encode(data["rooms"]))):
            topic = "/controls/" + key
            if value is None or value == "":
                self.pub(topic + "/meta/error", "r")
            else:
                self.pub(topic + "/meta/error", "")
                self.pub(topic, str(value))
        self.publish_room_channels(self.latest["map"])
        self.publish_map(data.get("saved_map"))
        self.pub("/meta/error", "")
        self.pub("/controls/state_connection", "На связи")

    def publish_room_channels(self, plan):
        aliases = self.room_names.for_map(plan)
        names = {f"room_{room}": aliases.get(room, {}).get("alias") or title
                 for room, title in (plan or {}).get("rooms", {}).items() if f"room_{room}" in ROOM_CHANNELS}
        for channel in self.room_channels.keys() - names.keys():
            for suffix in SUFFIXES:
                self.pub(f"/controls/{channel}{suffix}", "")
        for channel, value in names.items():
            if self.room_channels.get(channel) != value:
                topic = f"/controls/{channel}"
                if self.room_channels.get(channel) is None:
                    self.pub(topic + "/meta", room_meta(ROOM_CHANNELS[channel]))
                self.pub(topic + "/meta/error", "")
                self.pub(topic, value)
        self.room_channels = names

    def publish_map(self, saved):
        snapshot = map_snapshot(saved)
        key = snapshot[0] if snapshot is not None else None
        if self.map_frame is not None and key == self.map_key:
            return
        frame = render_map_frame(snapshot)
        if frame != self.map_frame:
            self.pub("/controls/map_frame/meta/error", "")
            self.pub("/controls/map_frame", frame)
            self.map_frame = frame
        self.map_key = key

    def web_state(self):
        latest = self.latest.copy()
        saved = latest.pop("_live_plan", None)
        station_pause = latest.pop("_station_pause", False)
        room_status_since = latest.pop("_room_status_since", None)
        room_status = {} if latest["online"] and room_status_since is not None else None
        plan, drawing = self.display_map(latest["map"])
        overlays = latest.get("overlays", [])
        cloud = self.cloud
        device = cloud.device if cloud else None
        frame_time = room_id = None
        tracking = latest.get("tracking_active", False)
        position_allowed = tracking or station_pause or latest["state"] in POSITION_STATES
        if latest["online"] and device and (position_allowed or room_status is not None):
            if tracking:
                cloud.note_live_map_demand()
            frame = device.status.current_map
            if saved is not None and plan and frame:
                stamp = (frame.frame_id, frame.timestamp_ms)
                features = read_overlays(saved, frame, cleaning=tracking or station_pause, station_pause=station_pause)
                room = frame.robot_segment
                outcomes = (live_room_outcomes(saved, frame, plan["_room_keys"], room_status_since)
                            if room_status is not None else None)
                if stamp == (frame.frame_id, frame.timestamp_ms):
                    if position_allowed:
                        overlays = features
                    room_status = outcomes
                    if outcomes or any(item["kind"] in ("robot", "path", "coverage") for item in overlays):
                        frame_time = stamp[1]
                    if str(room) in plan["rooms"] and any(item["kind"] == "robot" for item in overlays):
                        room_id = int(room)
        last = self.jobs.last if self.jobs else None
        if last:
            message = "Команда принята роботом" if last["status"] == "acknowledged" else "Результат команды не подтверждён"
            last = {**last, "message": message}
        names = self.room_names.for_map(plan)
        report = next(iter(latest.get("details", {}).get("history", [])), None)
        reclean_attempt = self.jobs.reclean_attempt(report["timestamp"]) if self.jobs and report else None
        if plan:
            plan = {key: value for key, value in plan.items() if key not in ("_room_keys", "_footprint_bits")}
        return {**latest, "state_title": STATE_LABELS.get(latest["state"], latest["state"]), "model": self.title, "allow_commands": self.commands_enabled(),
                "map": plan, "overlays": overlays, "live_frame_at_ms": frame_time, "live_room_id": room_id,
                "live_room_status": room_status,
                "last_job": last, "underlay": drawing, "room_names": names,
                "reclean_attempt": reclean_attempt["status"] if reclean_attempt else None}

    def web_history(self, timestamp, obstacle_id=None):
        if not self.io_lock.acquire(timeout=1.5):
            raise WebConflict("Драйвер занят чтением данных. Повторите через несколько секунд.")
        try:
            if not self.cloud or not self.latest["online"] or self.halt.is_set():
                raise WebConflict("Нет связи с Dreamehome. Повторите после восстановления связи.")
            archive = self.cloud.archived_map(timestamp)
            device = self.cloud.device
            if obstacle_id is not None:
                photo = obstacle_photo(archive, device, obstacle_id)
                if not photo:
                    raise LookupError("Снимок недоступен. Проверьте разрешение на просмотр снимков в Dreamehome.")
                return photo
            frame = render_history(archive)
            if not frame:
                raise LookupError("В записи нет читаемой архивной карты.")
            markers = []
            for entry in obstacles(archive, device):
                marker = dict(entry)
                marker["position"] = history_position(archive, marker.pop("x"), marker.pop("y"))
                markers.append(marker)
            return {**frame, "timestamp": timestamp, "obstacles": markers}
        finally:
            self.io_lock.release()

    def display_map(self, plan):
        drawing = self.underlay.for_map(plan)
        if not drawing:
            return plan, None
        revision = sha256(encode([plan["revision"], drawing["matrix"]]).encode()).hexdigest()
        return {**plan, "revision": revision}, drawing

    def web_entrance(self, request=None):
        with self.io_lock:
            plan = self.latest.get("map") if self.latest["online"] else None
            device_id = self.room_names.device_id
            displayed, _ = self.display_map(plan)
            result = (self.entrance.view(device_id, plan) if request is None
                      else self.entrance.save(device_id, plan, displayed, request))
            if result["status"] == "ready":
                result.update(map_id=displayed["id"], revision=displayed["revision"])
            return result

    def web_adjust(self, request):
        with self.io_lock:
            plan = self.latest.get("map")
            displayed, drawing = self.display_map(plan)
            if not drawing or request.get("revision") != displayed["revision"]:
                raise WebConflict("Карта или подложка изменилась. Обновите страницу.")
            return self.underlay.adjust(plan, {**request, "revision": plan["revision"]})

    def commands_enabled(self):
        if not self.allow_commands or self.halt.is_set():
            return False
        try:
            return validate(read(CONFIG))["allow_commands"]
        except (OSError, ValueError, TypeError):
            return False

    def web_job(self, request, preview):
        if not preview and self.jobs:
            existing = self.jobs.lookup(request)
            if existing:
                return {**existing, "request_id": request.get("request_id"),
                        "message": "Задание уже зарегистрировано. Повторная команда не отправлена."}
        if not preview and not self.commands_enabled():
            raise WebConflict("Запуск выключен в настройках драйвера. Доступна проверка задания.")
        if not self.io_lock.acquire(blocking=False):
            raise WebConflict("Драйвер сейчас опрашивает пылесос. Повторите через несколько секунд.")
        try:
            if not self.cloud or not self.latest["online"] or self.halt.is_set():
                raise WebConflict("Нет свежих данных робота")
            if request.get("kind") == "shortcut":
                normalized = _normalize(request)
                scene = read_shortcut(self.cloud, normalized["shortcut_id"])
            else:
                scene = None
            if request.get("action") == "reclean":
                self.jobs.check_reclean_source(request)
                try:
                    report = self.cloud.reclean_report(request["timestamp"])
                finally:
                    self.refresh.set()
            observation = self.cloud.observe(for_command=True)
            if scene is not None:
                observation["command_shortcut"] = scene
            current = interactive_map(observation.get("saved_map"), self.latest["map"])
            current, _ = self.display_map(current)
            if request.get("action") == "reclean":
                if not current:
                    raise Refused("Робот не подтвердил текущую карту для доуборки")
                observation["reclean"] = self.cloud.prepare_reclean(report, observation, current)
            if preview:
                result = self.jobs.preview(request, observation, current)
                return {**result, "request_id": request.get("request_id"), "status": "preview",
                        "message": "Карта, цель и параметры проверены. Уборка не запущена."}
            with check_writes_with(self.commands_enabled):
                result = self.jobs.submit(request, self.cloud, observation, current, allow_commands=self.commands_enabled())
            self.refresh.set()
            return {**result, "request_id": request.get("request_id"),
                    "message": "Ответ на команду получен." if result["status"] == "acknowledged"
                    else "Результат уточняется. Автоматического повтора команды нет."}
        finally:
            self.io_lock.release()

    def web_preference(self, request):
        if not self.commands_enabled():
            raise WebConflict("Изменение настроек выключено в конфигурации драйвера")
        if not self.io_lock.acquire(blocking=False):
            raise WebConflict("Драйвер опрашивает пылесос. Повторите через несколько секунд.")
        try:
            if not self.cloud or not self.latest["online"] or self.halt.is_set():
                raise WebConflict("Нет свежих данных робота")
            with check_writes_with(self.commands_enabled):
                result = apply_preference(self.cloud, request, allowed=self.commands_enabled())
            self.refresh.set()
            return {**result, "status": "confirmed", "message": "Значение подтверждено роботом"}
        finally:
            self.io_lock.release()

    def web_shortcuts(self, sid=None):
        if not self.io_lock.acquire(timeout=1.5):
            raise WebConflict("Драйвер опрашивает пылесос. Обновите список через несколько секунд.")
        try:
            if not self.cloud or not self.latest["online"] or self.halt.is_set():
                raise WebConflict("Нет связи с Dreamehome.")
            return read_shortcut(self.cloud, sid) if sid is not None else {"items": read_shortcuts(self.cloud)}
        finally:
            self.io_lock.release()

    def web_schedules(self, request=None):
        if request is not None and not self.commands_enabled():
            raise WebConflict("Изменение настроек выключено в конфигурации драйвера")
        if not self.io_lock.acquire(blocking=False):
            raise WebConflict("Драйвер опрашивает пылесос. Повторите через несколько секунд.")
        try:
            if not self.cloud or not self.latest["online"] or self.halt.is_set():
                raise WebConflict("Нет связи с Dreamehome. Повторите после восстановления связи.")
            if request is None:
                return {**read_schedules(self.cloud), "allow_commands": self.commands_enabled()}
            with check_writes_with(self.commands_enabled):
                if request.get("kind") == "cleaning":
                    cleaning = save_cleaning_schedule(self.cloud, request)
                    message = ("Робот подтвердил создание выключенного задания" if request["operation"] == "clone"
                               else "Расписание уборок подтверждено роботом")
                    return {"cleaning": cleaning, "message": message}
                charging = save_charging_schedule(self.cloud, request)
            return {"charging": charging, "message": "Интервал зарядки подтверждён роботом"}
        finally:
            self.io_lock.release()

    def stop(self, *_):
        self.halt.set()
        self.refresh.set()

    def run(self):
        with lock_namespace(STATE):
            self.run_locked()

    def run_locked(self):
        config = validate(read(CONFIG))
        account = read(Path(os.environ["CREDENTIALS_DIRECTORY"]) / "account.json")
        self.title = MODELS.get(account.get("model"), "Dreame")
        self.allow_commands = config["allow_commands"]
        self.jobs = Jobs(STATE)
        try:
            aliases = room_aliases(config)
        except Refused as exc:
            logging.warning("%s", exc)
            aliases = {}
        self.room_names = RoomNames(account.get("did", ""), aliases, ROOM_CATALOG)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        failures = 0
        server = start_ui(self)
        try:
            while not self.halt.is_set():
                self.refresh.clear()
                if not self.mqtt or not self.mqtt.ready:
                    try:
                        self.connect_mqtt()
                    except (OSError, Refused) as exc:
                        status = str(exc) if isinstance(exc, Refused) else "Локальный MQTT недоступен"
                        if self.latest["state"] != status:
                            logging.warning("%s", status)
                        self.mqtt.close()
                        self.mqtt = None
                        self.offline(status)
                        self.refresh.wait(5)
                        continue
                delay = config["poll_seconds"]
                if not account:
                    self.offline("Требуется привязка через wb-dreame setup")
                else:
                    try:
                        with self.io_lock:
                            if not self.cloud:
                                self.cloud = Robot(account, STATE)
                                self.cloud.open()
                            self.cloud.device.refresh_cleaning_records()
                            self.snapshot(self.cloud.observe())
                        failures = 0
                    except Exception as exc:
                        logging.warning("Ошибка чтения Dreamehome (%s)", type(exc).__name__)
                        with self.io_lock:
                            self.offline("Нет ответа Dreamehome")
                            if self.cloud:
                                self.cloud.close()
                                self.cloud = None
                        failures = min(4, failures + 1)
                        delay = max(delay, 30 * 2**failures)
                self.refresh.wait(delay)
        finally:
            server.shutdown()
            server.server_close()
            if self.mqtt:
                try:
                    # Older releases can claim only the original seven controls after a downgrade.
                    self.publish_room_channels(None)
                    stopped = self.offline("Служба остановлена")
                    if stopped is not None:
                        stopped.wait_for_publish(timeout=2)
                except RuntimeError:
                    pass
                finally:
                    self.mqtt.close()
            with self.io_lock:
                if self.cloud:
                    self.cloud.close()


def clear_retained():
    if not STATE.is_dir():
        return
    with lock_namespace(STATE):
        owner = read_owner(STATE / OWNER_FILE)
        if owner is None:
            return
        session = RetainedSession()
        try:
            session.clear(owner)
        finally:
            session.close()
