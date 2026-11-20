"""Model-specific settings that can be written without starting an action."""
from config import MODELS, Refused, integer
from details import CLEANING_MODES, SETTINGS, TOGGLE
from vendor.types import DreameVacuumAutoSwitchProperty as A, DreameVacuumCarpetCleaning as Carpet
from vendor.types import DreameVacuumCleaningMode as Mode, DreameVacuumCleaningRoute as Route
from vendor.types import DreameVacuumDrainageStatus as Drainage
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as MAPPING
from vendor.types import DreameVacuumSelfCleanFrequency as Frequency
from vendor.types import DreameVacuumTaskStatus as Task, DreameVacuumWaterTank as Tank

OPTIONS = {
    "cleaning_mode": (P.CLEANING_MODE, "Режим уборки", CLEANING_MODES),
    **{prop.name.lower(): (prop, label, labels) for prop, label, labels in SETTINGS if prop is not A.CLEANGENIUS},
}
KEYS = frozenset(OPTIONS)
MANUAL = {P.CLEANING_MODE, P.SUCTION_LEVEL, P.WATER_VOLUME, A.CLEANING_ROUTE, P.MOP_WASH_LEVEL}
MODE_WIRE = {Mode.SWEEPING: 2, Mode.MOPPING: 1, Mode.SWEEPING_AND_MOPPING: 0, Mode.MOPPING_AFTER_SWEEPING: 3}
UNAVAILABLE = "Робот не подтвердил доступность настройки"
BUSY = "Робот или станция заняты либо сообщили об ошибке"
STATE = (P.STATE, P.TASK_STATUS, P.ERROR)
DOCK = (P.CHARGING_STATUS, P.SELF_WASH_BASE_STATUS)


def _bit(value):
    return int(value) if type(value) is bool or integer(value, 0, 1) else None


def _packed(cap):
    return cap.self_wash_base or cap.wetness or cap.custom_mopping_route


def _mode(cap, raw):
    if not integer(raw, 0, 2**31 - 1 if _packed(cap) else 3):
        return None
    if _packed(cap) and not cap.mop_pad_lifting:
        return None
    wire = raw & 3
    return (2, 1, 0, 3)[wire] if cap.mop_pad_lifting else wire


def _mounted(cap, get):
    tank = get(P.WATER_TANK)
    installed = type(tank) is int and tank in (Tank.INSTALLED, Tank.MOP_INSTALLED, 2, 3)
    if installed and cap.waterbox_extend:
        installed = _bit(get(P.WATER_TANK_STATUS)) == 1
    if cap.mop_pad_unmounting:
        pad = _bit(get(P.MOP_PAD_INSTALLED))
        if pad is not None:
            return pad == 0 or installed
    return installed or cap.embedded_tank


def _wetness_values(cap):
    return (2, 8, 14) if cap.mop_clean_frequency else (5, 16, 27)


def _write_property(cap, prop):
    if prop is A.CLEANING_ROUTE:
        return P.AUTO_SWITCH_SETTINGS
    if prop is P.WATER_VOLUME:
        if cap.wetness:
            return P.ONBOARD_WETNESS_LEVEL if cap.onboard_wetness_level else P.WETNESS_LEVEL
        if cap.self_wash_base:
            return P.CLEANING_MODE
    return prop


def _dependencies(device, prop):
    cap = device.capability
    props = []
    if (prop in MANUAL and cap.cleangenius
            or prop is P.SUCTION_LEVEL and cap.max_suction_power
            or prop is P.MOP_WASH_LEVEL and cap.ultra_clean_mode
            or prop is P.CLEANING_MODE and cap.cleaning_route and not cap.cleaning_route_v2
            or prop is P.WATER_VOLUME and cap.wetness and cap.self_clean_frequency):
        props.append(P.AUTO_SWITCH_SETTINGS)
    if prop in (P.CLEANING_MODE, P.SUCTION_LEVEL, P.WATER_VOLUME, A.CLEANING_ROUTE):
        props.append(P.CLEANING_MODE)
        if cap.customized_cleaning:
            props.append(P.CUSTOMIZED_CLEANING)
    if prop in (P.CLEANING_MODE, P.WATER_VOLUME):
        props.append(P.WATER_TANK)
        if cap.waterbox_extend:
            props.append(P.WATER_TANK_STATUS)
        if cap.mop_pad_unmounting:
            props.extend((P.MOP_PAD_INSTALLED, P.AUTO_MOUNT_MOP))
            if device.get_property(P.MOP_IN_STATION) is not None:
                props.append(P.MOP_IN_STATION)
    if prop is P.MOP_WASH_LEVEL:
        props.append(P.SELF_CLEAN)
        if cap.smart_mop_washing:
            props.append(P.SMART_MOP_WASHING)
    if prop is P.CARPET_BOOST and cap.carpet_recognition:
        props.extend((P.CARPET_RECOGNITION, P.CARPET_CLEANING))
    return props


def _setting(device, prop, labels, get):
    cap, status = device.capability, device.status
    raw = get(_write_property(cap, prop))
    choices = dict(labels)
    reason = None

    mode = _mode(cap, get(P.CLEANING_MODE))
    if prop is P.CLEANING_MODE:
        value = mode
        if not cap.mopping_after_sweeping:
            choices.pop(Mode.MOPPING_AFTER_SWEEPING)
        if _packed(cap) and not cap.self_wash_base:
            reason = "Формат записи режима уборки не подтверждён"
        installed = _mounted(cap, get)
        station = (cap.mop_pad_unmounting and _bit(get(P.AUTO_MOUNT_MOP)) == 1
                   and type(get(P.MOP_IN_STATION)) is int and get(P.MOP_IN_STATION) in (1, 4))
        if not installed and not station:
            choices = {Mode.SWEEPING: labels[Mode.SWEEPING]}
        elif installed and not (cap.mop_pad_lifting or cap.sweep_with_mop or station):
            choices.pop(Mode.SWEEPING)
        if cap.cleaning_route and not cap.cleaning_route_v2:
            route = get(A.CLEANING_ROUTE)
            if route in (Route.INTENSIVE, Route.DEEP):
                choices.pop(Mode.SWEEPING, None)
                choices.pop(Mode.SWEEPING_AND_MOPPING, None)
            elif route not in (Route.NOT_SET, Route.STANDARD, Route.QUICK):
                reason = "Робот не подтвердил маршрут уборки"
    elif prop is P.WATER_VOLUME:
        value = raw
        if cap.wetness:
            maximum = 15 if cap.mop_clean_frequency else 32
            if integer(raw, 1, maximum):
                value = 3 if raw >= _wetness_values(cap)[2] else 1 if raw < 6 else 2
            else:
                value = None
            if cap.custom_mopping_route or not cap.wetness_level:
                reason = "На этой модели подача воды связана с другими настройками уборки"
        elif cap.self_wash_base:
            value = raw >> 16 if integer(raw, 0, 2**31 - 1) else None
            if cap.custom_mopping_route:
                reason = "На этой модели подача воды связана с маршрутом влажной уборки"
        grouped = get(P.CLEANING_MODE)
        if cap.self_wash_base and integer(grouped, 0, 2**31 - 1):
            # Match the vendor's split_group_value, which masks the packed water bits.
            interval = (grouped >> 8) & ~0x300
            frequency = get(A.SELF_CLEAN_FREQUENCY)
            # The vendor setter would also shorten the wash interval in these cases.
            if (cap.wetness and cap.wetness_level and _wetness_values(cap)[2] > 26 and interval > 20
                    and (not cap.self_clean_frequency or frequency not in (Frequency.BY_AREA, Frequency.INTELLIGENT))
                    or cap.mopping_settings and not cap.wetness_level and interval > 15
                    and not (cap.self_clean_frequency and frequency == Frequency.BY_TIME)):
                choices.pop(3)
        if mode in (None, Mode.SWEEPING) or not _mounted(cap, get):
            reason = "Подача воды доступна в режиме влажной уборки с установленными салфетками"
    elif prop is A.CLEANING_ROUTE:
        value = get(A.CLEANING_ROUTE)
        if not (cap.cleaning_route and cap.auto_switch_settings and status.has_saved_map and not status.has_temporary_map):
            reason = "Маршрут доступен для сохранённой карты"
        if mode is None:
            reason = "Робот не подтвердил режим уборки"
        if not cap.cleaning_route_v2 and mode in (Mode.SWEEPING, Mode.SWEEPING_AND_MOPPING):
            choices.pop(Route.INTENSIVE)
            choices.pop(Route.DEEP)
    elif prop is P.SUCTION_LEVEL:
        value = raw
        if mode in (None, Mode.MOPPING):
            reason = "Всасывание доступно в режиме с сухой уборкой"
        if cap.max_suction_power and get(A.MAX_SUCTION_POWER) != 0:
            reason = "Сначала отключите максимальное всасывание в Dreamehome"
    elif prop is P.MOP_WASH_LEVEL:
        value = raw
        if cap.mop_clean_frequency and not cap.smart_mop_washing:
            choices.pop(0)
        if not cap.self_wash_base or _bit(get(P.SELF_CLEAN)) != 1:
            reason = "Требуется станция с включённой промывкой салфеток"
        if (cap.smart_mop_washing and _bit(get(P.SMART_MOP_WASHING)) != 0
                or cap.ultra_clean_mode and get(A.ULTRA_CLEAN_MODE) != 0):
            reason = "Сначала отключите автоматическую или усиленную промывку в Dreamehome"
    else:
        if prop is P.RESUME_CLEANING and cap.auto_charging:
            value = {0: 0, 2: 1}.get(raw) if type(raw) is int else None
        else:
            value = _bit(raw)
        if prop is P.CARPET_BOOST and cap.carpet_recognition:
            carpet = get(P.CARPET_CLEANING)
            if (_bit(get(P.CARPET_RECOGNITION)) != 1 or not integer(carpet, 0, 7)
                    or carpet == Carpet.AVOIDANCE):
                reason = "Усиление доступно при распознавании ковров и разрешённой уборке на них"
        if prop is P.AUTO_WATER_REFILLING and not cap.water_check:
            reason = "Модель не подтвердила поддержку автодолива воды"

    if prop in (P.CLEANING_MODE, P.SUCTION_LEVEL, P.WATER_VOLUME, A.CLEANING_ROUTE) and cap.customized_cleaning:
        if (prop in (P.SUCTION_LEVEL, P.WATER_VOLUME) or cap.custom_cleaning_mode) and status.has_saved_map:
            if _bit(get(P.CUSTOMIZED_CLEANING)) != 0:
                reason = "Настройка недоступна при индивидуальных параметрах комнат"
    if prop in MANUAL and cap.cleangenius and get(A.CLEANGENIUS) != 0:
        reason = "Сначала выключите CleanGenius"
    if type(value) is not int or value not in labels:
        return None, choices, reason
    if value not in choices:
        reason = reason or "Текущее значение недоступно для выбранного режима"
    return value, choices, reason


def _idle(cap, get):
    from jobs import blocking_error
    from preferences import IDLE, _station_idle

    if not integer(get(P.TASK_STATUS), Task.COMPLETED, Task.COMPLETED):
        return False
    if cap.self_wash_base:
        if cap.drainage:
            drain = get(P.DRAINAGE_STATUS)
            if type(drain) is not int or drain not in (Drainage.IDLE, Drainage.DRAINING_SUCCESS):
                return False
        return _station_idle({p: get(p) for p in (*STATE, *DOCK, P.FAULTS)}, cap.self_wash_base)
    state = get(P.STATE)
    return type(state) is int and state in IDLE and blocking_error(get(P.ERROR), None, False) == 0


def offered(device, *, current=None):
    from preferences import auto_switch_value

    if (device is None or device.info is None or device.info.model not in MODELS
            or not getattr(device, "device_connected", False) or not hasattr(device.capability, "self_wash_base")):
        return []

    def get(prop):
        if isinstance(prop, A) and current is not None and P.AUTO_SWITCH_SETTINGS in current:
            return auto_switch_value(current[P.AUTO_SWITCH_SETTINGS], prop)
        return current[prop] if current is not None and prop in current else device.get_property(prop)

    idle = _idle(device.capability, get)
    result = []
    for key, (prop, label, labels) in OPTIONS.items():
        wire_prop = _write_property(device.capability, prop)
        if device.property_mapping.get(wire_prop) != MAPPING[wire_prop]:
            continue
        value, choices, reason = _setting(device, prop, labels, get)
        if value is None:
            continue
        if value not in choices:
            choices = {value: labels[value], **choices}
        item = {"key": key, "label": label, "value": value, "min": min(choices), "max": max(choices),
                "choices": [{"value": int(v), "label": text} for v, text in choices.items()]}
        if reason or not idle:
            item["disabled_reason"] = reason or BUSY
        result.append(item)
    return result


def _wire(cap, prop, raw, value):
    from preferences import auto_switch_wire

    if prop is A.CLEANING_ROUTE:
        return auto_switch_wire(prop, value)
    if prop is P.CLEANING_MODE:
        return (raw & ~3) | MODE_WIRE[value] if cap.mop_pad_lifting else value
    if prop is P.WATER_VOLUME:
        if cap.wetness:
            return _wetness_values(cap)[value - 1]
        if cap.self_wash_base:
            return (raw & 0xFFFF) | value << 16
    if prop is P.RESUME_CLEANING and cap.auto_charging:
        return value * 2
    return value


def apply(robot, payload):
    from preferences import WRITE, _check_binding, _read, auto_switch_value

    def observed(values, prop):
        if isinstance(prop, A):
            return auto_switch_value(values.get(P.AUTO_SWITCH_SETTINGS), prop)
        return values.get(prop)

    key, value, expected = payload["key"], payload["value"], payload["expected"]
    prop, _, labels = OPTIONS[key]
    if type(value) is not int or type(expected) is not int or value not in labels or expected not in labels:
        raise Refused("Настройка вне допустимого диапазона")
    robot.identity()
    device, model = robot.device, robot.account["model"]
    protocol, cap = device._protocol, device.capability
    did = str(robot.account.get("did") or "")
    _check_binding(robot, device, protocol, model, did)
    if key not in {item["key"] for item in offered(device)}:
        raise Refused(UNAVAILABLE)
    target = _write_property(cap, prop)
    state_props = list(STATE)
    if cap.self_wash_base:
        state_props.extend(DOCK)
        if cap.drainage:
            state_props.append(P.DRAINAGE_STATUS)
        if device.get_property(P.FAULTS) is not None:
            state_props.append(P.FAULTS)
    props = list(dict.fromkeys([target, *_dependencies(device, prop), *state_props]))
    if any(device.property_mapping.get(p) != MAPPING[p] for p in props):
        raise Refused(UNAVAILABLE)
    # Dreame limits requests containing the bulk auto-switch property to ten items.
    limit = 10 if P.AUTO_SWITCH_SETTINGS in props else 15

    def read():
        values = {}
        for start in range(0, len(props), limit):
            values.update(_read(protocol, props[start:start + limit]))
        return values

    before = read()
    _check_binding(robot, device, protocol, model, did)
    if not device.device_connected or not _idle(cap, before.get):
        raise Refused(BUSY)
    current, choices, reason = _setting(device, prop, labels, lambda p: observed(before, p))
    if reason or value not in choices:
        raise Refused(reason or "Этот вариант сейчас недоступен")
    if current != expected:
        raise Refused("Значение уже изменилось. Обновите панель")
    result = {"key": key, "value": value, "changed": value != expected}
    if value == expected:
        return result

    if target is P.CLEANING_MODE and _packed(cap):
        # The station shares this register with the wash interval; Dreame exposes no compare-and-swap.
        if _read(protocol, [target])[target] != before[target]:
            raise Refused("Параметры уборки изменились в Dreamehome. Обновите панель")
    wire = _wire(cap, prop, before[target], value)
    mapping = MAPPING[target]
    token = WRITE.set((did, mapping["siid"], mapping["piid"], wire))
    try:
        protocol.set_property(mapping["siid"], mapping["piid"], wire, retry_count=0)
    except Exception:
        pass  # A lost reply still requires confirmation, without repeating the write.
    finally:
        WRITE.reset(token)
    message = "Не удалось подтвердить изменение настройки. Обновите панель"
    try:
        _check_binding(robot, device, protocol, model, did)
        after = read()
        _check_binding(robot, device, protocol, model, did)
    except Refused:
        raise Refused(message) from None
    current, _, reason = _setting(device, prop, labels, lambda p: observed(after, p))
    raw_after = after[target]
    if labels is TOGGLE and type(raw_after) is bool:
        raw_after = int(raw_after)
    confirmed = (auto_switch_value(raw_after, prop) == value if prop is A.CLEANING_ROUTE
                 else type(raw_after) is int and raw_after == wire)
    if (not confirmed or current != value or reason or not device.device_connected
            or any(type(after[p]) is not type(before[p]) or after[p] != before[p] for p in props if p is not target)):
        raise Refused(message)
    return result
