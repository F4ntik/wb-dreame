import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from time import monotonic

from config import COMMAND_MODELS, MODELS, Refused, atomic, read
from details import FRESH_BASE_PROPERTIES, history_records, read_details
from history_media import camera_status
from overlays import read_overlays
from jobs import CLEANING, blocking_error, check_observation_age, job_zones, room_selection
from panel import map_snapshot
from preferences import cleangenius_value, offered, property_write_permitted
from reclean import report_request
from schedules import delete_write_permitted
from shortcuts import permitted as shortcut_permitted, start_shortcut
from vendor.device import DreameVacuumDevice
from vendor.protocol import DreameVacuumDreameHomeCloudProtocol
from vendor.types import DreameVacuumAction as A, DreameVacuumAutoSwitchProperty as AS, DreameVacuumStatus as DS
from vendor.types import DreameVacuumChargingStatus as Charging, DreameVacuumSelfWashBaseStatus as BaseStatus
from vendor.types import DreameVacuumProperty as P, DreameVacuumState as S, DreameVacuumTaskStatus as T

ALLOWED_ACTION = ContextVar("allowed_action", default=())
RECLEAN_ACTION = ContextVar("reclean_action", default=None)
WRITE_AUTHORIZATION = ContextVar("write_authorization", default=None)
READ_ACTIONS = {(6, 1), (6, 4), (14, 1)}  # Map data, Wi-Fi map, map-file key exchange.
JOB_MODELS = COMMAND_MODELS | {"dreame.vacuum.r9419a"}
TRACKING_STATES = CLEANING | {"SECOND_CLEANING", "EXTRA_CLEANING", "INITIAL_DEEP_CLEANING", "HEADING_TO_EXTRA_CLEANING"}
JOB_ACTIONS = {"start": A.START, "resume": A.START, "pause": A.PAUSE, "stop": A.STOP, "dock": A.CHARGE}
JOB_MODES = {"SWEEPING": "sweep", "MOPPING": "mop", "SWEEPING_AND_MOPPING": "both",
             "MOPPING_AFTER_SWEEPING": "mop_after_sweep"}


def dock_position(state, charging):
    if state.startswith("RETURNING") or charging == Charging.RETURN_TO_CHARGE:
        return "returning"
    if state in TRACKING_STATES or state in {"BUILDING", "REMOTE_CONTROL"}:
        return "away"
    if charging in (Charging.CHARGING, Charging.CHARGING_COMPLETED) or state in {
        "CHARGING", "CHARGING_COMPLETED", "WASHING", "WASHING_PAUSED",
    }:
        return "docked"
    return None


def guard(method, parameters):
    if method == "get_properties" or shortcut_permitted(method, parameters, write=False):
        return
    if shortcut_permitted(method, parameters, write=True):
        authorized = WRITE_AUTHORIZATION.get()
        if authorized is None or not authorized():
            raise Refused("Разрешение управления отозвано")
        return
    reclean = RECLEAN_ACTION.get()
    if reclean is not None:
        authorized_write = method == "action" and parameters == reclean
    elif method == "action" and isinstance(parameters, dict):
        pair = (parameters.get("siid"), parameters.get("aiid"))
        if pair in READ_ACTIONS:
            return
        authorized_write = pair in ALLOWED_ACTION.get() or delete_write_permitted(method, parameters)
    else:
        authorized_write = property_write_permitted(method, parameters)
    if authorized_write:
        verify = WRITE_AUTHORIZATION.get()
        if verify is not None and not verify():
            raise Refused("Разрешение управления отозвано")
        return
    raise Refused("Драйвер запретил фоновую запись или неподдерживаемую команду")


@contextmanager
def check_writes_with(check):
    token = WRITE_AUTHORIZATION.set(check)
    try:
        yield
    finally:
        WRITE_AUTHORIZATION.reset(token)


@contextmanager
def permit(*pairs):
    flag = ALLOWED_ACTION.set(pairs)
    try:
        yield
    finally:
        ALLOWED_ACTION.reset(flag)


def discovery(username, password, country):
    client = DreameVacuumDreameHomeCloudProtocol(username, password, country=country)
    try:
        if not client.login():
            raise Refused("Dreamehome не подтвердил вход. Проверьте регион, учетные данные и связь")
        result = client.get_devices()
        page = result.get("page") if isinstance(result, dict) else None
        records = page.get("records") if isinstance(page, dict) else None
        if not isinstance(records, list):
            raise Refused("Облако не вернуло список устройств")
        if not records:
            raise Refused("В аккаунте Dreamehome нет устройств")
        listed = [d for d in records if isinstance(d, dict) and isinstance(d.get("model"), str)]
        devices = [d for d in listed if d["model"] in MODELS and d.get("did")]
        if not devices:
            models = {d["model"] for d in listed
                      if re.fullmatch(r"\w{1,24}(?:\.\w{1,24}){2}", d["model"], re.ASCII)}
            if models & MODELS.keys():
                raise Refused("В ответе Dreamehome нет идентификатора поддерживаемого устройства")
            names = ", ".join(sorted(models)) or "не указаны"
            raise Refused("В аккаунте нет поддерживаемых моделей. Модели в аккаунте — " + names)
        return devices, client.auth_key
    finally:
        client.disconnect()


class Robot:
    def __init__(self, account, directory):
        self.account = account
        self.session_file = Path(directory) / "session.json"
        self.device = None
        self._archives = {}
        self._preference_poll = {}
        self._preference_reads = {}

    def open(self):
        a = self.account
        if a.get("model") not in MODELS or not a.get("did"):
            raise Refused("Запустите wb-dreame setup для привязки Dreame")
        token = a.get("auth_key")
        if self.session_file.exists():
            session = read(self.session_file)
            if session.get("binding") == a["binding"]:
                token = session.get("auth_key") or token
        self.device = DreameVacuumDevice(MODELS[a["model"]], None, None, username=a["username"],
            password=a["password"], country=a["country"], account_type="dreame",
            prefer_cloud=True, device_id=a["did"], auth_key=token)
        self.device.update()
        self.identity()
        self.device.schedule_update()
        self.save_token()

    def identity(self):
        d = self.device
        if not d or not d.info or d.info.model != self.account["model"] or str(d._protocol.cloud.device_id) != str(self.account["did"]):
            raise Refused("Облако не подтвердило выбранную модель и ID")

    def save_token(self):
        token = self.device._protocol.cloud.auth_key
        if token:
            payload = {"binding": self.account["binding"], "auth_key": token}
            if not self.session_file.exists() or read(self.session_file) != payload:
                atomic(self.session_file, payload)

    def saved_map(self):
        device = self.device
        if device:
            status = device.status
            current, selected = status.current_map, status.selected_map
            if current and selected and status.has_saved_map and current.saved_map_id == selected.map_id:
                return selected
        return None

    def archived_map(self, timestamp):
        """Resolve on this account at request time; the browser never supplies a file or key."""
        self.identity()
        entries = history_records(self.device.status._cleaning_history)
        if timestamp not in entries:
            raise KeyError(timestamp)
        _, reference = entries[timestamp]
        if reference is None:
            raise Refused("У этой уборки нет однозначно привязанной карты.")
        cache_key = (timestamp, reference)
        if cache_key not in self._archives:
            manager = self.device._map_manager
            archive = manager.get_history_map(*reference) if manager else None
            if archive is None or archive.history_map is not True:
                raise OSError("Архивная карта недоступна")
            archive.last_updated = timestamp
            if len(self._archives) >= 2:
                del self._archives[next(iter(self._archives))]
            self._archives[cache_key] = archive
        return self._archives[cache_key]

    def reclean_report(self, timestamp):
        """Read the latest report and its original map, bypassing the archive display cache."""
        self.identity()
        device = self.device
        if self.account["model"] not in JOB_MODELS:
            raise Refused("Для этой модели доступно только чтение отчёта")
        if not device.refresh_cleaning_records(force=True, strict=True):
            raise Refused("Не удалось обновить последний отчёт Dreamehome. Повторите проверку")
        records = tuple(device.status._cleaning_history or ())
        if not records:
            raise Refused("В Dreamehome пока нет отчётов об уборке")
        latest = records[0]
        if int(latest.date.timestamp()) != timestamp:
            raise Refused("В Dreamehome появился другой последний отчёт. Обновите панель")
        row = history_records(records).get(timestamp)
        if row is None or row[1] is None or sum(int(record.date.timestamp()) == timestamp for record in records) != 1:
            raise Refused("У последнего отчёта нет однозначно привязанной карты")
        manager = device._map_manager
        archive = manager.get_history_map(*row[1]) if manager else None
        if archive is None or archive.history_map is not True or archive.empty_map:
            raise Refused("Архивная карта последней уборки недоступна. Повторите позже")
        return latest, row[1], archive

    def prepare_reclean(self, report, observation, map_info):
        current = self.device.status.current_map
        if (current is None or self.device.status.has_temporary_map or
                current.saved_map_id != observation["map_id"]):
            raise Refused("Робот не подтвердил сохранённую карту для доуборки")
        return report_request(*report, current, observation, map_info["rooms"], self.device.capability,
                              (self.account["model"], self.account["did"]))

    def note_live_map_demand(self) -> None:
        """Signal demand from a panel with confirmed active cleaning."""
        device = self.device
        if device is not None and device._map_manager is not None:
            device._map_manager.note_live_map_demand()

    def observe(self, *, for_command=False):
        self.identity()
        d = self.device
        cap = getattr(d, "capability", None)
        self_wash_base = getattr(cap, "self_wash_base", False)
        station_props = (P.CHARGING_STATUS, P.SELF_WASH_BASE_STATUS, P.FAULTS) if self_wash_base else ()
        props = [P.STATE, P.TASK_STATUS, P.BATTERY_LEVEL, P.ERROR]
        reported = getattr(d, "data", {})
        fresh = [p for p in FRESH_BASE_PROPERTIES if p in d.property_mapping and reported.get(p.value) is not None]
        if not for_command:
            props.extend(p for p in station_props if p in d.property_mapping)
            if getattr(cap, "cleangenius", False) and getattr(cap, "auto_switch_settings", False):
                props.append(P.AUTO_SWITCH_SETTINGS)
            props.extend(p for p in fresh if p not in props)
        mode_props = []
        if for_command:
            mode_props = [P.CLEANING_MODE, P.WATER_TANK]
            if cap.waterbox_extend:
                mode_props.append(P.WATER_TANK_STATUS)
            if cap.mop_pad_unmounting:
                mode_props.extend((P.MOP_PAD_INSTALLED, P.AUTO_MOUNT_MOP))
            props.extend(mode_props + [P.SUCTION_LEVEL, P.WATER_VOLUME, P.CUSTOMIZED_CLEANING,
                                       P.AUTO_SWITCH_SETTINGS, P.MAP_LIST])
            props.extend(station_props)
            if cap.wetness:
                props.append(P.ONBOARD_WETNESS_LEVEL if cap.onboard_wetness_level else P.WETNESS_LEVEL)
        query = [{"did": str(p.value), **d.property_mapping[p]} for p in props]
        observed_at = monotonic()
        # Keep the readings for command availability together in the first poll batch.
        limit = len(query) if for_command else (10 if P.AUTO_SWITCH_SETTINGS in props else 15)
        result = d._protocol.get_properties(query[:limit], retry_count=0, timeout=6)
        if result and len(query) > limit:
            result = [*result, *(d._protocol.get_properties(query[limit:], retry_count=0, timeout=6) or [])]
        by_key = {(r.get("siid"), r.get("piid")): r["value"] for r in result or []
                  if isinstance(r, dict) and r.get("code") == 0 and type(r.get("value")) in (int, str)}
        def val(p, value_type=int):
            k = d.property_mapping.get(p)
            value = by_key.get((k["siid"], k["piid"])) if k and p in props else None
            return value if type(value) is value_type else None
        state, task = val(P.STATE), val(P.TASK_STATUS)
        if state is None or task is None:
            raise Refused("Нет свежего ответа о состоянии робота")
        selected = self.saved_map()
        map_id = None
        rooms = {}
        if selected:
            map_id = selected.map_id
            rooms = {str(k): seg.name or str(k) for k, seg in (selected.segments or {}).copy().items()
                     if seg.visibility is not False and not seg.unmapped}
        self.save_token()
        charging = val(P.CHARGING_STATUS)
        faults = val(P.FAULTS, str)
        error = blocking_error(val(P.ERROR), faults if faults is not None else val(P.FAULTS), self_wash_base)
        docked_drying = bool(self_wash_base and val(P.SELF_WASH_BASE_STATUS) == BaseStatus.DRYING
                             and charging in (Charging.CHARGING, Charging.CHARGING_COMPLETED))
        snapshot = {"state": S(state).name if state in S._value2member_map_ else str(state),
                    "task": T(task).name if task in T._value2member_map_ else str(task),
                    "battery": val(P.BATTERY_LEVEL), "error": error, "docked_drying": docked_drying, "map_id": map_id,
                    "rooms": rooms, "saved_map": selected, "fw": str(d.info.firmware_version or "")}
        if not for_command:
            snapshot["dock_status"] = dock_position(snapshot["state"], charging)
            snapshot["camera"] = camera_status(d)
            switches = val(P.AUTO_SWITCH_SETTINGS, str)
            current = {p: val(p) for p in (*fresh, *station_props, P.STATE, P.TASK_STATUS, P.ERROR) if p in props}
            if P.FAULTS in current and faults is not None:
                current[P.FAULTS] = faults
            if P.AUTO_SWITCH_SETTINGS in props:
                current[P.AUTO_SWITCH_SETTINGS] = switches
                current[AS.CLEANGENIUS] = cleangenius_value(switches)
            snapshot.update(self.preference_state(poll=current))
            snapshot["overlays"] = read_overlays(selected, None)
            snapshot["details"] = read_details(d, current=current)
        if for_command:
            map_list = val(P.MAP_LIST, str)
            map_key = self._command_map_key(selected, map_list)
            snapshot.update(command_time=observed_at, command_map=map_key is not None,
                            command_grid=selected.dimensions.grid_size if map_key else None,
                            command_map_key=map_key, command_map_list=map_list,
                            command_settings=self._command_settings(val, mode_props))
        return snapshot

    def preference_state(self, *, poll=None, readings=None):
        device = self.device
        if poll is not None:
            self._preference_poll = poll
            for prop in poll:
                self._preference_reads.pop(prop, None)
        if readings:
            for prop, value in readings.items():
                self._preference_reads[prop] = (value, device.get_property(prop))
        current = self._preference_poll.copy()
        # Prefer a direct read until the normal poll or the vendor cache reports another observation.
        for prop, (value, cached) in self._preference_reads.copy().items():
            latest = device.get_property(prop)
            if type(latest) is type(cached) and latest == cached:
                current[prop] = value
            else:
                del self._preference_reads[prop]
        return {"preferences": offered(device, current=current),
                "cleangenius": cleangenius_value(current.get(P.AUTO_SWITCH_SETTINGS))}

    def _command_map_key(self, saved, raw_map_list):
        manager = self.device._map_manager
        if not manager or not saved:
            return None
        try:
            listing = json.loads(raw_map_list)
            object_name = listing.get("object_name") or listing.get("obj_name")
            checksum = listing.get("md5")
        except (TypeError, ValueError, AttributeError):
            return None
        if not object_name or not checksum:
            return None
        parts = map_snapshot(saved)
        if parts and manager.command_map_matches(saved, (object_name, checksum)):
            dim = saved.dimensions
            return (parts[0], dim.left, dim.top, dim.grid_size, dim.width, dim.height)

    def _command_settings(self, val, mode_props):
        d, switches = self.device, {}
        cap = d.capability
        try:
            items = json.loads(val(P.AUTO_SWITCH_SETTINGS, str))
            if isinstance(items, dict):
                items = [items]
            switches = {entry["k"]: entry["v"] for entry in items}
        except (TypeError, ValueError, KeyError):
            pass

        def flag(value):
            return bool(value) if type(value) is int and value >= 0 else None

        mode = None
        if all(val(p) is not None and val(p) == d.get_property(p) for p in mode_props):
            mode = JOB_MODES.get(d.status.cleaning_mode.name)
        grouped = cap.self_wash_base or cap.wetness or cap.custom_mopping_route
        water = val(P.WATER_VOLUME)
        if grouped:
            raw = val(P.CLEANING_MODE)
            water = d.split_group_value(raw, cap.mop_pad_lifting)[2] if raw is not None and raw >= 0 else None
        verified_wetness = False
        if cap.wetness:
            prop = P.ONBOARD_WETNESS_LEVEL if cap.onboard_wetness_level else P.WETNESS_LEVEL
            wetness = val(prop)
            if wetness and wetness > 0 and wetness == d.get_property(prop):
                # clean_segment and clean_zone use this upstream three-level value in their payload.
                water = d.status.water_volume.value
                verified_wetness = water in (1, 2, 3)
        customized = val(P.CUSTOMIZED_CLEANING)
        genius = cleangenius_value(val(P.AUTO_SWITCH_SETTINGS, str)) if cap.cleangenius else 0
        return {"mode": mode, "min_battery": cap.min_battery, "suction": val(P.SUCTION_LEVEL), "water": water,
                "preserve_water": not (cap.wetness or cap.custom_mopping_route),
                "verified_wetness": verified_wetness,
                "cleangenius": flag(genius), "cleangenius_level": genius,
                "mop_installed": bool(mode and d.status.mop_pad_installed),
                "max_suction": flag(switches.get(AS.MAX_SUCTION_POWER.value, None if cap.max_suction_power else 0)),
                "customized": flag(customized if customized is not None or cap.customized_cleaning else 0)}

    def validate_job(self, request, snap):
        if self.account.get("model") not in JOB_MODELS:
            raise Refused("Для этой модели доступно только чтение")
        self.identity()
        check_observation_age(snap)
        if request["action"] in ("start", "reclean"):
            key = self._command_map_key(self.saved_map(), snap.get("command_map_list"))
            if key is None or key != snap.get("command_map_key"):
                raise Refused("Карта изменилась перед отправкой. Обновите панель")

    def send_job(self, request, snap, *, allow_commands=False):
        if allow_commands is not True:
            raise Refused("Управление отключено в настройках драйвера")
        self.validate_job(request, snap)
        d = self.device
        if request.get("kind") == "shortcut":
            return start_shortcut(self, request["shortcut_id"])
        if request["action"] == "reclean":
            status, payload, _, _ = snap["reclean"]
            params = [{"piid": d.property_mapping[P.STATUS]["piid"], "value": status.value},
                      {"piid": d.property_mapping[P.CLEANING_PROPERTIES]["piid"], "value": payload}]
            mapping = d.action_mapping[A.START_CUSTOM]
            did = str(d._protocol.cloud.device_id)
            context = RECLEAN_ACTION.set({"did": did, **mapping, "in": params})
            try:
                result = d._protocol.action(mapping["siid"], mapping["aiid"], params, retry_count=0)
            finally:
                RECLEAN_ACTION.reset(context)
            return result[0] if isinstance(result, list) and len(result) == 1 else result
        action = JOB_ACTIONS[request["action"]]
        params = None
        kind = request.get("kind")
        if action is A.START and kind in ("rooms", "zone"):
            action = A.START_CUSTOM
            settings = snap["command_settings"]
            options = [request["repeats"], request.get("suction", settings["suction"]), request.get("water", settings["water"])]
            # Use the vendor payload directly: clean_zone also writes global settings; cached controls can divert to dock washing.
            if kind == "rooms":
                status = DS.SEGMENT_CLEANING
                payload = {"selects": room_selection(request["rooms"], options, d.capability.customized_cleaning)}
            else:
                status = DS.ZONE_CLEANING
                payload = {"areas": [[*zone, *options] for zone in job_zones(request)]}
            params = [{"piid": d.property_mapping[P.STATUS]["piid"], "value": status.value},
                      {"piid": d.property_mapping[P.CLEANING_PROPERTIES]["piid"],
                       "value": json.dumps(payload, separators=(",", ":"))}]
        mapping = d.action_mapping[action]
        pair = mapping["siid"], mapping["aiid"]
        with permit(pair):
            result = d._protocol.action(*pair, params, retry_count=0)
        if isinstance(result, list) and len(result) == 1:
            return result[0]
        return result

    def prepare(self, action, profile, snap):
        d = self.device
        if action == "start":
            if snap["task"] != "COMPLETED" or snap["state"] not in ("CHARGING", "IDLE", "CHARGING_COMPLETED") or snap["error"] != 0:
                raise Refused("Робот занят, в ошибке или еще не подтвердил готовность")
            if profile:
                if snap["map_id"] != profile["map_id"]:
                    raise Refused("Активная карта не совпадает с картой профиля")
                if d.status.cleangenius_cleaning:
                    raise Refused("Для профиля выключите CleanGenius в Dreamehome")
                if profile["kind"] == "rooms":
                    if not all(str(k) in snap["rooms"] for k in profile["rooms"]):
                        raise Refused("Комнаты профиля отсутствуют на текущей карте")
                else:
                    dim = d.status.current_map.dimensions
                    x1, y1, x2, y2 = profile["zone"]
                    if not (dim.left <= x1 < x2 <= dim.left + dim.width * dim.grid_size
                            and dim.top <= y1 < y2 <= dim.top + dim.height * dim.grid_size
                            and min(x2-x1, y2-y1) > 2*dim.grid_size):
                        raise Refused("Прямоугольник вне карты или слишком мал")
                if int(d.status.suction_level) < 0 or int(d.status.water_volume) < 1:
                    raise Refused("Робот не подтвердил параметры уборки")
        if action == "resume" and snap["task"] not in ("AUTO_CLEANING_PAUSED", "SEGMENT_CLEANING_PAUSED", "ZONE_CLEANING_PAUSED"):
            raise Refused("Нет приостановленной уборки")
        if action == "pause" and snap["task"] not in ("AUTO_CLEANING", "SEGMENT_CLEANING", "ZONE_CLEANING", "SPOT_CLEANING"):
            raise Refused("Сейчас нет активной уборки")

    def send(self, action, profile):
        if self.account.get("model") not in COMMAND_MODELS:
            raise Refused("Для этой модели доступно только чтение")
        self.identity()
        d = self.device
        if action == "start" and profile:
            args = (profile["repeats"], profile.get("suction"), profile.get("water"))
            with permit((4, 1)):
                if profile["kind"] == "rooms":
                    return d.clean_segment(profile["rooms"], *args)
                return d.clean_zone(profile["zone"], *args)
        method, pairs = {"start": (d.start, [(2, 1), (4, 1)]), "resume": (d.start, [(2, 1), (4, 1)]),
                         "pause": (d.pause, [(2, 2)]), "stop": (d.stop, [(4, 2)]),
                         "dock": (d.return_to_base, [(3, 1)])}[action]
        with permit(*pairs):
            return method()

    def close(self):
        self._archives.clear()
        if self.device:
            self.device.disconnect()
            self.device = None
