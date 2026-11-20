"""Bounded, confirmed writes of robot preferences."""
import json
import re
from contextvars import ContextVar

import cleaning_preferences
from config import MODELS, Refused, integer
from jobs import blocking_error
from vendor.types import DreameVacuumAutoSwitchProperty as AS, DreameVacuumCleanGenius as G
from vendor.types import DreameVacuumChargingStatus as C, DreameVacuumCleaningRoute as R
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as PROPERTY_MAPPING
from vendor.types import DreameVacuumSelfWashBaseStatus as B, DreameVacuumState as S, DreameVacuumTaskStatus as T

PREFERENCES = {
    "volume": (P.VOLUME, "Громкость", 100),
    "dnd_reduce_volume": (P.DND_REDUCE_VOLUME, "Снижать громкость в тихом режиме", 1),
    "dnd_disable_auto_empty": (P.DND_DISABLE_AUTO_EMPTY, "Отключать автоопорожнение в тихом режиме", 1),
    "cleangenius": (P.AUTO_SWITCH_SETTINGS, "CleanGenius", G.DEEP_CLEANING.value),
}
CLEANGENIUS_LABELS = {G.OFF.value: "Выкл.", G.ROUTINE_CLEANING.value: "Обычная уборка",
                      G.DEEP_CLEANING.value: "Глубокая уборка"}
WRITE = ContextVar("allowed_preference_write", default=None)
IDLE = {S.IDLE.value, S.CHARGING.value, S.CHARGING_COMPLETED.value}
DOCKED = {C.CHARGING.value, C.CHARGING_COMPLETED.value}
DND_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


def auto_switch_wire(prop, value):
    return json.dumps({"k": prop.value, "v": value}, separators=(",", ":"))


AUTO_SWITCH_WRITES = {
    auto_switch_wire(prop, value)
    for prop, values in ((AS.CLEANGENIUS, CLEANGENIUS_LABELS),
                         (AS.CLEANING_ROUTE, (R.STANDARD, R.INTENSIVE, R.DEEP, R.QUICK)))
    for value in values
}


def property_write_permitted(method, params):
    expected = WRITE.get()
    if method != "set_properties" or expected is None or type(params) is not list or len(params) != 1:
        return False
    prop = params[0]
    if (type(prop) is not dict or set(prop) != {"did", "siid", "piid", "value"}
            or type(prop["did"]) is not str or any(type(prop[k]) is not int for k in ("siid", "piid"))
            or type(prop["value"]) not in (int, str)
            or type(prop["value"]) is not type(expected[3])
            or (prop["did"], prop["siid"], prop["piid"], prop["value"]) != expected):
        return False
    if type(prop["value"]) is str:
        mapping = PROPERTY_MAPPING[P.AUTO_SWITCH_SETTINGS]
        if (prop["siid"] != mapping["siid"] or prop["piid"] != mapping["piid"]
                or prop["value"] not in AUTO_SWITCH_WRITES):
            return False
    WRITE.set(None)
    return True


def auto_switch_value(raw, prop):
    if type(raw) is not str:
        return None

    def unique_fields(fields):
        entry = dict(fields)
        if len(entry) != len(fields):
            raise ValueError
        return entry

    try:
        entries = json.loads(raw, object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        return None
    if type(entries) is dict:
        entries = [entries]
    if type(entries) is not list or any(
        type(entry) is not dict or type(entry.get("k")) is not str or "v" not in entry for entry in entries
    ):
        return None
    values = [entry["v"] for entry in entries if entry["k"] == prop.value]
    if len(values) == 1 and type(values[0]) is int:
        return values[0]
    return None


def cleangenius_value(raw):
    value = auto_switch_value(raw, AS.CLEANGENIUS)
    return value if value in CLEANGENIUS_LABELS else None


def _cleangenius_choices(device):
    cap = device.capability
    if not (getattr(cap, "cleangenius", False) and getattr(cap, "auto_switch_settings", False)
            and getattr(cap, "self_wash_base", False)):
        return {}
    options = getattr(device.status, "cleangenius_list", None)
    if type(options) is not dict:
        return {}
    supported = {int(value) for value in options.values() if type(value) in (int, G)}
    return {value: label for value, label in CLEANGENIUS_LABELS.items() if value in supported}


def offered(device, *, current=None):
    if device is None or device.info is None or device.info.model not in MODELS or not getattr(device, "device_connected", False):
        return []
    result = []
    for key, (prop, label, maximum) in PREFERENCES.items():
        if device.property_mapping.get(prop) != PROPERTY_MAPPING[prop]:
            continue
        if prop is P.AUTO_SWITCH_SETTINGS:
            choices = _cleangenius_choices(device)
            raw = current[prop] if current is not None and prop in current else device.get_property(prop)
            value = cleangenius_value(raw)
            if value in choices:
                result.append({"key": key, "label": label, "value": value, "min": min(choices), "max": max(choices),
                               "choices": [{"value": code, "label": name} for code, name in choices.items()]})
            continue
        value = device.get_property(prop)
        if not integer(value, 0, maximum) or (
            prop is not P.VOLUME and not (device.capability.dnd_functions and device.status.dnd is True)
        ):
            continue
        item = {"key": key, "label": label, "value": value, "min": 0, "max": maximum}
        if prop is P.VOLUME:
            item["unit"] = "%"
        else:
            item["choices"] = [{"value": 0, "label": "Выкл."}, {"value": 1, "label": "Вкл."}]
        result.append(item)
    return cleaning_preferences.offered(device, current=current) + result


def _read(protocol, props):
    lookup = {(PROPERTY_MAPPING[p]["siid"], PROPERTY_MAPPING[p]["piid"]): p for p in props}
    query = [{"did": str(prop.value), **PROPERTY_MAPPING[prop]} for prop in props]
    try:
        rows = protocol.get_properties(query, retry_count=0, timeout=6)
    except Exception:
        raise Refused("Робот не подтвердил текущие настройки") from None
    values = {}
    for row in rows if isinstance(rows, list) else []:
        if type(row) is not dict or type(row.get("siid")) is not int or type(row.get("piid")) is not int:
            continue
        prop = lookup.get((row["siid"], row["piid"]))
        if prop is not None:
            if prop in values or type(row.get("code")) is not int or row["code"] != 0 or "value" not in row:
                raise Refused("Робот не подтвердил текущие настройки")
            values[prop] = row["value"]
    if values.keys() != set(props):
        raise Refused("Робот не подтвердил текущие настройки")
    return values


def _dnd_enabled(prop, value):
    if prop is P.DND:
        return value is True or integer(value, 1, 1)
    try:
        tasks = json.loads(value) if type(value) is str else None
    except ValueError:
        return False
    # shortcut: accept a single schedule until live evidence confirms how multiple schedules are applied.
    if type(tasks) is not list or len(tasks) != 1 or type(tasks[0]) is not dict:
        return False
    task = tasks[0]
    return ((task.get("en") is True or integer(task.get("en"), 1, 1)) and integer(task.get("wk"), 1, 127)
            and integer(task.get("id"), 0, 2**31 - 1) and integer(task.get("ss"), -1, 2**31 - 1)
            and all(type(task.get(k)) is str and DND_TIME.fullmatch(task[k]) for k in ("st", "et"))
            and task["st"] != task["et"])


def _check_binding(robot, device, protocol, model, did):
    robot.identity()
    if (model not in MODELS or not did or device is not robot.device or protocol is not device._protocol
            or robot.account.get("model") != model or str(robot.account.get("did")) != did
            or not protocol.dreame_cloud or protocol.device_cloud is not protocol.cloud
            or str(protocol.cloud.device_id) != did):
        raise Refused("Робот или облачное подключение изменились")


def _station_idle(observation, self_wash_base):
    state, charging, base = (observation[p] for p in (P.STATE, P.CHARGING_STATUS, P.SELF_WASH_BASE_STATUS))
    if (any(type(value) is not int for value in (state, charging, base))
            or blocking_error(observation[P.ERROR], observation[P.FAULTS], self_wash_base) != 0):
        return False
    if base == B.DRYING:
        return charging in DOCKED and (state in IDLE or state == S.DRYING)
    return (base == B.IDLE and state in IDLE
            and (charging in DOCKED or (state == S.IDLE and charging == C.NOT_CHARGING)))


def apply_preference(robot, payload, *, allowed=False):
    if allowed is not True:
        raise Refused("Изменение настроек отключено в драйвере")
    if (type(payload) is not dict or set(payload) != {"key", "value", "expected"}
            or type(payload["key"]) is not str):
        raise Refused("Укажите доступную настройку и её текущее значение")
    if payload["key"] in cleaning_preferences.KEYS:
        return cleaning_preferences.apply(robot, payload)
    if payload["key"] not in PREFERENCES:
        raise Refused("Укажите доступную настройку и её текущее значение")
    key, value, expected = payload["key"], payload["value"], payload["expected"]
    prop, _, maximum = PREFERENCES[key]
    if not integer(value, 0, maximum) or not integer(expected, 0, maximum):
        raise Refused("Настройка вне допустимого диапазона")
    robot.identity()
    device = robot.device
    protocol = device._protocol
    model, did = robot.account["model"], str(robot.account.get("did") or "")
    _check_binding(robot, device, protocol, model, did)
    genius = prop is P.AUTO_SWITCH_SETTINGS
    state_props = [P.STATE, P.TASK_STATUS, P.ERROR]
    dnd = None
    if genius:
        choices = _cleangenius_choices(device)
        if not (choices and device.device_connected and device.property_mapping.get(prop) == PROPERTY_MAPPING[prop]):
            raise Refused("Эта настройка сейчас недоступна")
        if value not in choices or expected not in choices:
            raise Refused("Режим CleanGenius недоступен на этом роботе")
    elif key not in {item["key"] for item in offered(device)}:
        raise Refused("Эта настройка сейчас недоступна")
    elif prop is not P.VOLUME:
        dnd = P.DND_TASK if device.capability.dnd_task else P.DND
        state_props.append(dnd)
    check_station = genius or device.capability.self_wash_base
    if check_station:
        state_props.extend((P.CHARGING_STATUS, P.SELF_WASH_BASE_STATUS, P.FAULTS))
    before = _read(protocol, [prop, *state_props])
    _check_binding(robot, device, protocol, model, did)
    idle = (_station_idle(before, device.capability.self_wash_base) if check_station else
            type(before[P.STATE]) is int and before[P.STATE] in IDLE and integer(before[P.ERROR], 0, 0))
    if (not idle or not integer(before[P.TASK_STATUS], T.COMPLETED.value, T.COMPLETED.value)
            or not device.device_connected
            or (dnd is not None and not (device.status.dnd is True and _dnd_enabled(dnd, before[dnd])))):
        if genius:
            raise Refused("Нельзя изменить CleanGenius, пока робот или база заняты либо есть ошибка")
        raise Refused("Робот занят, в ошибке или тихий режим отключён")
    previous = cleangenius_value(before[prop]) if genius else before[prop]
    if not integer(previous, 0, maximum) or previous != expected:
        raise Refused("Значение уже изменилось. Обновите панель")
    result = {"key": key, "value": value, "changed": value != expected}
    if value == expected:
        return result

    mapping = PROPERTY_MAPPING[prop]
    wire_value = auto_switch_wire(AS.CLEANGENIUS, value) if genius else value
    token = WRITE.set((did, mapping["siid"], mapping["piid"], wire_value))
    try:
        protocol.set_property(mapping["siid"], mapping["piid"], wire_value, retry_count=0)
    except Exception:
        pass  # A lost reply can still be followed by a successful read-back.
    finally:
        WRITE.reset(token)
    try:
        _check_binding(robot, device, protocol, model, did)
        after = _read(protocol, [prop, *state_props])
        _check_binding(robot, device, protocol, model, did)
    except Refused:
        raise Refused("Не удалось подтвердить изменение настройки. Обновите панель") from None
    confirmed = cleangenius_value(after[prop]) if genius else after[prop]
    stable_props = (p for p in state_props if not check_station or p not in (P.ERROR, P.FAULTS))
    if (not integer(confirmed, 0, maximum) or confirmed != value
            or any(type(after[p]) is not type(before[p]) or after[p] != before[p] for p in stable_props)
            or (check_station and not _station_idle(after, device.capability.self_wash_base))):
        raise Refused("Не удалось подтвердить изменение настройки. Обновите панель")
    return result
