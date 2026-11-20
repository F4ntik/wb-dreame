"""Fresh reads and confirmed, narrowly scoped writes of Dreame schedules."""
import json
import re
from hashlib import sha256

from config import Refused, integer
from preferences import DND_TIME, WRITE, _check_binding, _read
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as MAPPING

PROPERTIES = {"charging": P.OFF_PEAK_CHARGING, "cleaning": P.SCHEDULE}
UNAVAILABLE = "Робот не подтвердил параметры. Проверьте расписание в Dreamehome."
STALE = "Расписание изменилось. Обновите данные и проверьте новое значение."
UNCONFIRMED = "Робот не подтвердил изменение. Обновите данные перед новой попыткой."


def _time(value):
    return isinstance(value, str) and DND_TIME.fullmatch(value) is not None


def _peak(raw):
    if len(raw) > 8192:
        raise Refused(UNAVAILABLE)
    try:
        pairs = json.loads(raw, object_pairs_hook=list)
    except (ValueError, RecursionError):
        raise Refused(UNAVAILABLE) from None
    if type(pairs) is not list or len(pairs) != 3 or not all(type(pair) is tuple for pair in pairs):
        raise Refused(UNAVAILABLE)
    data = dict(pairs)
    if (data.keys() != {"enable", "startTime", "endTime"}
            or not (type(data["enable"]) is bool or integer(data["enable"], 0, 1))
            or not (_time(data["startTime"]) and _time(data["endTime"])) or data["startTime"] == data["endTime"]):
        raise Refused(UNAVAILABLE)
    return data


def charging_wire_valid(raw):
    try:
        _peak(raw)
    except Refused:
        return False
    return True


def _tasks(raw):
    rows = [row.split("-") for row in raw.split(";")] if raw else []
    ids = set()
    for row in rows:
        if (len(row) < 9 or not re.fullmatch(r"[1-9][0-9]{0,9}", row[0])
                or int(row[0]) > 2**31 - 1 or row[0] in ids or row[1] not in ("0", "1", "2", "3")
                or not _time(row[2]) or row[4] not in ("0", "1")):
            raise Refused(UNAVAILABLE)
        ids.add(row[0])
    return rows


def _revision(did, wire):
    return sha256(json.dumps([did, wire], ensure_ascii=True).encode()).hexdigest()


def _charging(did, raw):
    data = _peak(raw)
    return {"revision": _revision(did, raw), "enabled": bool(data["enable"]),
            "start": data["startTime"], "end": data["endTime"]}


def _cleaning(raw):
    return {"tasks": [
        {"id": int(row[0]), "enabled": row[1] in ("1", "2"), "invalid": row[1] == "3",
         "time": row[2], "once": row[4] == "0",
         "days": row[3].count("1") if re.fullmatch(r"[01]{1,7}", row[3]) else None}
        for row in sorted(_tasks(raw), key=lambda row: (row[2], int(row[0])))
    ]}


def _reader(robot):
    robot.identity()
    device, account = robot.device, robot.account
    protocol, model, did = device._protocol, account["model"], str(account.get("did") or "")

    def read(prop):
        _check_binding(robot, device, protocol, model, did)
        if not device.device_connected or device.property_mapping.get(prop) != MAPPING[prop]:
            raise Refused(UNAVAILABLE)
        raw = _read(protocol, [prop])[prop]
        _check_binding(robot, device, protocol, model, did)
        if not device.device_connected or type(raw) is not str or len(raw) > 8192:
            raise Refused(UNAVAILABLE)
        return raw

    return protocol, did, read


def read_schedules(robot):
    _, did, read = _reader(robot)
    data = {}
    for kind, prop in PROPERTIES.items():
        try:
            raw = read(prop)
            data[kind] = _charging(did, raw) if kind == "charging" else _cleaning(raw)
        except Refused:
            data[kind] = {"reason": UNAVAILABLE}
    return data


def save_charging_schedule(robot, payload):
    revision = payload.get("revision")
    if (payload.keys() != {"start", "end", "revision", "enabled"} or type(payload["enabled"]) is not bool
            or type(revision) is not str or not re.fullmatch(r"[0-9a-f]{64}", revision)):
        raise Refused("Проверьте поля расписания.")
    enabled, start, end = payload["enabled"], payload["start"], payload["end"]
    if not (_time(start) and _time(end)) or start == end:
        raise Refused("Укажите разное время начала и конца в формате ЧЧ:ММ.")
    prop = P.OFF_PEAK_CHARGING
    protocol, did, read = _reader(robot)
    before = read(prop)
    if revision != _revision(did, before):
        raise Refused(STALE)
    current = _peak(before)
    expected = {"enable": type(current["enable"])(enabled), "startTime": start, "endTime": end}
    if current == expected:
        return _charging(did, before)

    wire = json.dumps(expected, separators=(",", ":"))
    mapping = MAPPING[prop]
    token = WRITE.set((did, mapping["siid"], mapping["piid"], wire))
    try:
        protocol.set_property(mapping["siid"], mapping["piid"], wire, retry_count=0)
    except Exception:
        pass  # Confirm by reading after a lost reply. Sending again could overwrite a newer schedule.
    finally:
        WRITE.reset(token)

    try:
        after = read(prop)
        if _peak(after) == expected:
            return _charging(did, after)
    except Refused:
        pass
    raise Refused(UNCONFIRMED)
