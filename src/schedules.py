"""Fresh reads and confirmed, narrowly scoped writes of Dreame schedules."""
import json
import re
from contextvars import ContextVar
from hashlib import sha256
from time import sleep

from config import Refused, integer
from preferences import DND_TIME, WRITE, _check_binding, _read
from vendor.types import DreameVacuumAction as A, DreameVacuumActionMapping as ACTIONS
from vendor.types import DreameVacuumProperty as P, DreameVacuumPropertyMapping as MAPPING

PROPERTIES = {"charging": P.OFF_PEAK_CHARGING, "cleaning": P.SCHEDULE}
EDIT_MODEL = "dreame.vacuum.r9419a"
DELETE_TASK = ACTIONS[A.DELETE_SCHEDULED_TASK]
DELETE_WRITE = ContextVar("allowed_schedule_delete", default=None)
DAY_MASK = re.compile(r"[01]{1,7}\Z")
MAX_TASK_ID = 2**31 - 1
MAX_WIRE = 8192
UNAVAILABLE = "Робот не подтвердил параметры. Проверьте расписание в Dreamehome."
STALE = "Расписание изменилось. Обновите данные и проверьте новое значение."
UNCONFIRMED = "Робот не подтвердил изменение. Обновите данные перед новой попыткой."


def _time(value):
    return isinstance(value, str) and DND_TIME.fullmatch(value) is not None


def _peak(raw):
    if len(raw) > MAX_WIRE:
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
    rows = {}
    for record in raw.split(";") if raw else []:
        row = record.split("-")
        if (len(row) < 9 or not re.fullmatch(r"[1-9][0-9]{0,9}", row[0])
                or int(row[0]) > MAX_TASK_ID or int(row[0]) in rows or row[1] not in ("0", "1", "2", "3")
                or not _time(row[2]) or row[4] not in ("0", "1")):
            raise Refused(UNAVAILABLE)
        rows[int(row[0])] = row
    return rows


def cleaning_wire_valid(raw):
    if not raw or len(raw) > MAX_WIRE:
        return False
    try:
        _tasks(raw)
    except Refused:
        return False
    return True


def _revision(did, wire):
    return sha256(json.dumps([did, wire], ensure_ascii=True).encode()).hexdigest()


def _valid_revision(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _charging(did, raw):
    data = _peak(raw)
    return {"revision": _revision(did, raw), "enabled": bool(data["enable"]),
            "start": data["startTime"], "end": data["endTime"]}


def _editable(row):
    return (row[1] in ("0", "1") and DAY_MASK.fullmatch(row[3]) is not None
            and (row[4] == "0" or "1" in row[3]))


def _can_delete(robot):
    return (robot.device.action_mapping.get(A.DELETE_SCHEDULED_TASK) == DELETE_TASK
            and robot.device.property_mapping.get(P.SCHEDULE_ID) == MAPPING[P.SCHEDULE_ID])


def _cleaning(robot, did, raw):
    can_edit = robot.account["model"] == EDIT_MODEL
    rows = _tasks(raw)
    slot_available = max(rows, default=0) < MAX_TASK_ID
    tasks = []
    for task_id, row in sorted(rows.items(), key=lambda item: (item[1][2], item[0])):
        weekdays = row[3].zfill(7) if DAY_MASK.fullmatch(row[3]) else None
        tasks.append({"id": task_id, "enabled": row[1] in ("1", "2"), "invalid": row[1] == "3",
                      "time": row[2], "once": row[4] == "0", "weekdays": weekdays,
                      "days": weekdays.count("1") if weekdays is not None else None,
                      "editable": can_edit and _editable(row),
                      "copyable": can_edit and slot_available and row[4] == "1" and _editable(row)})
    return {"tasks": tasks, "revision": _revision(did, raw), "can_edit": can_edit,
            "can_delete": can_edit and _can_delete(robot)}


def _reader(robot):
    robot.identity()
    device, account = robot.device, robot.account
    protocol, model, did = device._protocol, account["model"], str(account.get("did") or "")

    def read(prop, *, readback=False):
        _check_binding(robot, device, protocol, model, did)
        # The transport can mark the connection down when a write reply is lost.
        if (not readback and not device.device_connected) or device.property_mapping.get(prop) != MAPPING[prop]:
            raise Refused(UNAVAILABLE)
        raw = _read(protocol, [prop])[prop]
        _check_binding(robot, device, protocol, model, did)
        if not device.device_connected or type(raw) is not str or len(raw) > MAX_WIRE:
            raise Refused(UNAVAILABLE)
        return raw

    return protocol, did, read


def read_schedules(robot):
    _, did, read = _reader(robot)
    data = {}
    for kind, prop in PROPERTIES.items():
        try:
            raw = read(prop)
            data[kind] = _charging(did, raw) if kind == "charging" else _cleaning(robot, did, raw)
        except Refused:
            data[kind] = {"reason": UNAVAILABLE}
    return data


def save_charging_schedule(robot, payload):
    revision = payload.get("revision")
    if (payload.keys() != {"start", "end", "revision", "enabled"} or type(payload["enabled"]) is not bool
            or not _valid_revision(revision)):
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
    _set_property(protocol, did, prop, wire)

    try:
        after = read(prop, readback=True)
        if _peak(after) == expected:
            return _charging(did, after)
    except Refused:
        pass
    raise Refused(UNCONFIRMED)


def _set_property(protocol, did, prop, wire):
    mapping = MAPPING[prop]
    token = WRITE.set((did, mapping["siid"], mapping["piid"], wire))
    try:
        protocol.set_property(mapping["siid"], mapping["piid"], wire, retry_count=0)
    except Exception:
        pass  # A lost reply can follow a successful write. Check the result by reading.
    finally:
        WRITE.reset(token)


def delete_write_permitted(method, params):
    expected = DELETE_WRITE.get()
    if method != "action" or expected is None or type(params) is not dict:
        return False
    did, task_id = expected
    if (params != {"did": did, **DELETE_TASK, "in": [{"piid": MAPPING[P.SCHEDULE_ID]["piid"], "value": task_id}]}
            or any(type(params[key]) is not int for key in ("siid", "aiid"))
            or type(params["in"][0]["piid"]) is not int):
        return False
    DELETE_WRITE.set(None)
    return True


def _delete_task(protocol, did, task_id):
    # Some firmware acknowledges the action even when ignoring a numeric ID.
    task_id = str(task_id)
    token = DELETE_WRITE.set((did, task_id))
    try:
        protocol.action(DELETE_TASK["siid"], DELETE_TASK["aiid"],
                        [{"piid": MAPPING[P.SCHEDULE_ID]["piid"], "value": task_id}], retry_count=0)
    except Exception:
        pass  # Confirm deletion independently of the action reply.
    finally:
        DELETE_WRITE.reset(token)


def _copy_task(rows, template, time, weekdays):
    # The schedule setter allocates the next ID after the highest occupied slot.
    task_id = max(rows) + 1
    if task_id > MAX_TASK_ID or template[4] != "1" or not _editable(template):
        raise Refused("Создайте задание в Dreamehome.")
    for row in rows.values():
        if row[2] != time:
            continue
        if (row[4] == "0" or not DAY_MASK.fullmatch(row[3]) or "1" not in row[3]
                or any(a == b == "1" for a, b in zip(weekdays, row[3].zfill(7)))):
            raise Refused("На это время уже есть задание. Выберите другое время или дни.")
    rows[task_id] = [str(task_id), "0", time, weekdays, *template[4:]]


def save_cleaning_schedule(robot, payload):
    fields = {"kind", "operation", "id", "revision"}
    task_id, revision, operation = payload.get("id"), payload.get("revision"), payload.get("operation")
    if operation == "clone":
        fields.update(("time", "weekdays"))
    elif operation == "update":
        fields.update(("enabled", "time"))
        if "weekdays" in payload:
            fields.add("weekdays")
    if (operation not in ("clone", "update", "delete") or payload.keys() != fields
            or not integer(task_id, 1, MAX_TASK_ID) or not _valid_revision(revision)
            or (operation != "delete" and not _time(payload["time"]))
            or (operation == "update" and type(payload["enabled"]) is not bool)):
        raise Refused("Проверьте поля расписания уборки.")
    weekdays = payload.get("weekdays")
    if "weekdays" in payload and (type(weekdays) is not str or len(weekdays) != 7
                                  or not DAY_MASK.fullmatch(weekdays) or "1" not in weekdays):
        raise Refused("Выберите хотя бы один день недели.")
    if robot.account["model"] != EDIT_MODEL or (operation == "delete" and not _can_delete(robot)):
        raise Refused("Для этой модели действие доступно в Dreamehome.")

    prop = P.SCHEDULE
    protocol, did, read = _reader(robot)
    before = read(prop)
    if revision != _revision(did, before):
        raise Refused(STALE)
    expected = _tasks(before)
    row = expected.get(task_id)
    if row is None:
        raise Refused(STALE)
    if operation == "delete":
        del expected[task_id]
        _delete_task(protocol, did, task_id)
    else:
        if operation == "clone":
            _copy_task(expected, row, payload["time"], weekdays)
        else:
            if not _editable(row) or (weekdays is not None and row[4] == "0"):
                raise Refused("Параметры этого задания можно изменить в Dreamehome.")
            row[1] = "1" if payload["enabled"] else "0"
            row[2] = payload["time"]
            if weekdays is not None and weekdays != row[3].zfill(7):
                row[3] = weekdays
        wire = ";".join("-".join(row) for row in expected.values())
        if wire == before:
            return _cleaning(robot, did, before)
        if len(wire) > MAX_WIRE:
            raise Refused(UNAVAILABLE)
        _set_property(protocol, did, prop, wire)

    try:
        after = read(prop, readback=True)
        if after == before:
            sleep(0.5)  # The robot may report the previous list just after acknowledging the write.
            after = read(prop, readback=True)
        if _tasks(after) == expected:
            return _cleaning(robot, did, after)
    except Refused:
        pass
    raise Refused(UNCONFIRMED)
