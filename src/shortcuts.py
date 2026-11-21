"""Read Dreamehome cleaning shortcuts without importing or rewriting their settings."""
import base64
import binascii
import json
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256

from config import Refused, integer
from preferences import _read
from vendor.types import DreameVacuumAction as A, DreameVacuumProperty as P
from vendor.types import DreameVacuumStatus as S

UNAVAILABLE = "Dreame не подтвердил список или параметры сценария. Обновите его из Dreamehome."
STALE = "Сценарий изменился. Откройте его заново перед запуском."
PERMISSION = ContextVar("dreame_shortcut_rpc", default=None)


def permitted(method, params, *, write):
    token = PERMISSION.get()
    if method != "action" or token is None or token[0] != write or not isinstance(params, dict):
        return False
    # JSON comparison distinguishes true from 1 in nested protocol arguments.
    if json.dumps(params, sort_keys=True) != token[1]:
        return False
    PERMISSION.set(None)
    return True


@contextmanager
def _permit(write, wire):
    token = PERMISSION.set((write, json.dumps(wire, sort_keys=True)))
    try:
        yield
    finally:
        PERMISSION.reset(token)


def _action(robot, action, args, *, write=False):
    robot.identity()
    device = robot.device
    mapping = device.action_mapping[action]
    wire = {"did": str(robot.account["did"]), **mapping, "in": args}
    with _permit(write, wire):
        result = device._protocol.action(mapping["siid"], mapping["aiid"], args, retry_count=0)
    if isinstance(result, list) and len(result) == 1:
        result = result[0]
    return result


def _query(robot, task_id=None):
    command = {"cmd": "GET_COMMANDS", "params": {}} if task_id is None else {
        "cmd": "GET_COMMAND_BY_ID", "params": {"id": task_id}}
    pid = robot.device.property_mapping[P.CLEANING_PROPERTIES]["piid"]
    result = _action(robot, A.SHORTCUTS, [{"piid": pid, "value": json.dumps(command, separators=(",", ":"))}])
    if not isinstance(result, dict) or type(result.get("code")) is not int or result["code"] != 0:
        raise Refused(UNAVAILABLE)
    try:
        raw = result["out"][0]["value"]
        if not isinstance(raw, str) or len(raw) > 65536:
            raise ValueError
        values = json.loads(raw)
        if not isinstance(values, list) or len(values) > 128:
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        raise Refused(UNAVAILABLE) from None
    return values


def read_shortcuts(robot):
    robot.identity()
    wire = _read(robot.device._protocol, [P.SHORTCUTS])[P.SHORTCUTS]
    try:
        if not isinstance(wire, str) or len(wire) > 32768:
            raise ValueError
        rows = json.loads(wire or "[]")
        if not isinstance(rows, list) or len(rows) > 104:
            raise ValueError
        if not rows:
            return []
        maps = {}
        for row in _query(robot):
            sid = row["id"]
            if not integer(sid, 25, 128) or sid in maps:
                raise ValueError
            maps[sid] = row["mapId"]
        items = {}
        for row in rows:
            sid = row["id"]
            if not integer(sid, 25, 128) or sid in items:
                raise ValueError
            name = base64.b64decode(row["name"], validate=True).decode().strip()
            if not 1 <= len(name) <= 160 or not name.isprintable():
                raise ValueError
            map_id = maps.get(sid)
            items[sid] = {"id": sid, "name": name, "map_id": map_id if integer(map_id, 1, 2**31-1) else None}
        return list(items.values())
    except (KeyError, TypeError, ValueError, binascii.Error, RecursionError):
        raise Refused(UNAVAILABLE) from None


def read_shortcut(robot, sid):
    item = next((row for row in read_shortcuts(robot) if row["id"] == sid), None)
    if not item:
        raise Refused("Сценарий больше не найден в Dreamehome.")
    if not item["map_id"] or sid == 25:
        raise Refused("Dreame не передал привязку к карте или состав этого сценария.")
    steps = _query(robot, sid)
    if not steps:
        raise Refused(UNAVAILABLE)
    rooms, zones, whole = [], [], False
    for group in steps:
        if not isinstance(group, list) or not 1 <= len(group) <= 128:
            raise Refused(UNAVAILABLE)
        for task in group:
            if (not isinstance(task, list) or not 5 <= len(task) <= 48
                    or any(type(value) is not int for value in task)):
                raise Refused(UNAVAILABLE)
            target = task[0]
            if target == -1:
                whole = True
            elif target == -2 and len(task) >= 10 and task[5] < task[7] and task[6] < task[8]:
                zones.append(task[5:9])
            elif integer(target, 1, 255):
                if target not in rooms:
                    rooms.append(target)
            else:
                raise Refused(UNAVAILABLE)
    identity = json.dumps([str(robot.account["did"]), item, steps], sort_keys=True, separators=(",", ":"))
    return {**item, "revision": sha256(identity.encode()).hexdigest(), "rooms": rooms, "zones": zones,
            "whole": whole, "steps": len(steps)}


def start_shortcut(robot, sid):
    d = robot.device
    params = [{"piid": d.property_mapping[P.STATUS]["piid"], "value": S.SHORTCUT.value},
              {"piid": d.property_mapping[P.CLEANING_PROPERTIES]["piid"], "value": str(sid)}]
    return _action(robot, A.START_CUSTOM, params, write=True)
