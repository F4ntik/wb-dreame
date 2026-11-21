"""Prepare the vendor's report-scoped follow-up without changing robot settings."""
import json
from hashlib import sha256

import numpy as np

from config import Refused, integer
from history_map import coverage_pixels
from jobs import room_selection
from room_status import room_outcomes
from vendor.types import CleanupMethod, DreameVacuumStatus as DS, MapPixelType


def _grid(frame, *, combined=False):
    dims = getattr(frame, "combined_dimensions", None) if combined else None
    pixels = frame.combined_pixel_type if dims is not None else frame.pixel_type
    dims = dims if dims is not None else frame.dimensions
    if (dims is None or not integer(dims.grid_size, 1, 32_767) or not isinstance(pixels, np.ndarray)
            or not all(type(coord) in (int, float) and -1_000_000 <= coord <= 1_000_000 for coord in (dims.left, dims.top))
            or pixels.shape != (dims.width, dims.height) or not 0 < pixels.size <= 262_144):
        raise Refused("В архиве или текущей карте нет читаемой сетки пола")
    maximum = 31 if frame.version == 3 else 63
    labels = np.where((pixels > 100) & (pixels <= 100 + maximum), pixels - 100, pixels)
    return dims, np.where(labels == MapPixelType.NEW_SEGMENT, MapPixelType.FLOOR, labels), maximum


def _offset(source, target):
    if source.grid_size != target.grid_size:
        raise Refused("Масштаб карты изменился после последней уборки")
    dx, rx = divmod(source.left - target.left, target.grid_size)
    dy, ry = divmod(source.top - target.top, target.grid_size)
    if rx or ry:
        raise Refused("Привязка карты изменилась после последней уборки")
    return int(dx), int(dy)


def _cells(grid, x, y):
    if (x.min() < 0 or y.min() < 0 or x.max() >= grid.shape[0] or y.max() >= grid.shape[1]):
        raise Refused("Загрязнённые участки из отчёта не совпадают с границами карты")
    return grid[x, y]


def _check_geometry(archive, saved, blocked, dirty, rooms):
    archived_dim, archived_grid, archived_max = _grid(archive, combined=True)
    saved_dim, saved_grid, saved_max = _grid(saved)
    dx, dy = _offset(archived_dim, saved_dim)
    targets = set(blocked)
    mask = None
    if dirty:
        raw_dim, _, _ = _grid(archive)
        marked = coverage_pixels(archive, raw_dim)
        if marked is None:
            raise Refused("На архивной карте нет читаемой маски загрязнённых участков")
        mask = marked == 2
        if not mask.any():
            raise Refused("На архивной карте нет читаемой маски загрязнённых участков")
        dirty_dx, dirty_dy = _offset(raw_dim, archived_dim)
        x, y = np.nonzero(mask)
        x, y = x + dirty_dx, y + dirty_dy
        reported = _cells(archived_grid, x, y)
        current = _cells(saved_grid, x + dx, y + dy)
        matches_room = (1 <= reported) & (reported <= archived_max)
        if not np.array_equal(reported, current) or not np.all(matches_room | (reported == MapPixelType.FLOOR)):
            raise Refused("Загрязнённые участки из отчёта изменились на текущей карте")
        targets.update(map(int, np.unique(reported[matches_room])))
        if not matches_room.all():
            targets.add(MapPixelType.FLOOR)

    message = "Комнаты из последнего отчёта изменились. Проверьте карту в Dreamehome"
    for room in targets:
        if room != MapPixelType.FLOOR:
            before, after = (archive.segments or {}).get(room), (saved.segments or {}).get(room)
            if room > min(archived_max, saved_max) or str(room) not in rooms or before is None or after is None:
                raise Refused(message)
            if before.unique_id is not None and after.unique_id is not None and before.unique_id != after.unique_id:
                raise Refused(message)
        x, y = np.nonzero(archived_grid == room)
        a, b = np.nonzero(saved_grid == room)
        if not x.size or not np.array_equal(x + dx, a) or not np.array_equal(y + dy, b):
            raise Refused("Границы участков из последнего отчёта изменились. Проверьте карту в Dreamehome")
    return mask


def report_request(history, reference, archive, current, observation, rooms, capability, binding):
    archived_id = archive.saved_map_id
    if ((archived_id is not None and archived_id != observation["map_id"]) or
            (archived_id is None and archive.map_id != current.map_id)):
        raise Refused("Последняя уборка относится к другой карте")

    blocked_flags = archive.blocked_segments or {}
    reported = history.blocked_segments or {}
    if reported and reported.keys() != blocked_flags.keys():
        raise Refused("Архивная карта не подтвердила пропуск комнат из отчёта")
    if not all(integer(room, 1, 63) for room in blocked_flags):
        raise Refused("В отчёте указаны неизвестные номера пропущенных комнат")
    outcomes = room_outcomes(archive, archived=True)
    blocked = [room for room in blocked_flags if outcomes.get(room) == "blocked"]
    skipped = sorted(room for room, status in outcomes.items() if status == "skipped")
    skipped_flags = {room for room in archive.skipped_segments or () if integer(room, 1, 63)}
    ambiguous = sorted((blocked_flags.keys() | skipped_flags) - outcomes.keys())

    method = history.cleanup_method
    overlay = archive.cleaning_map_data
    areas = archive.cleaned_segments if archive.cleaned_segments is not None else []
    settings = observation["command_settings"]
    dirty_marked = overlay is not None and overlay.has_dirty_area is True
    dirty = (method == CleanupMethod.CLEANGENIUS and not capability.cleangenius_auto and dirty_marked and
             (history.mopping_mode == 2 or settings.get("mop_installed") is True) and
             (history.second_mopping == 2 or bool(areas)))
    if not blocked and not skipped and not dirty:
        if ambiguous:
            raise Refused("В последнем отчёте противоречат друг другу отметки комнат. Проверьте отчёт в Dreamehome")
        raise Refused("Dreamehome не подтвердил пропущенные комнаты или загрязнённые участки в последнем отчёте")

    timestamp = history.multiple_cleaning_time
    if timestamp is None or type(timestamp) is int and timestamp == 0:
        timestamp = ""
    if not (integer(timestamp, 1, 2**63 - 1) or isinstance(timestamp, str) and len(timestamp) <= 64
            and timestamp.isascii() and timestamp.isprintable() and not any(c.isspace() for c in timestamp)):
        raise Refused("Dreamehome вернул нечитаемый идентификатор доуборки")
    if timestamp and archive.multiple_cleaning_time and str(archive.multiple_cleaning_time) != str(timestamp):
        raise Refused("Идентификаторы уборки в отчёте и на архивной карте различаются")

    native = (capability.auto_recleaning and len(blocked_flags) == len(blocked) and
              method in (CleanupMethod.CLEANGENIUS, CleanupMethod.DEFAULT_MODE, CleanupMethod.CUSTOMIZED_CLEANING))
    dos = archive.dos if archive.dos is not None else 1
    mop_again = native and (dirty or blocked and not areas) and type(areas) is list and integer(dos, 0, 255)
    mask = None
    if dirty and mop_again:
        try:
            mask = _check_geometry(archive, observation["saved_map"], blocked, True, rooms)
        except Refused:
            if not skipped:
                raise
            mop_again = False

    ordinary = bool(skipped and not (dirty and mop_again))
    selected = blocked + skipped if ordinary else blocked
    if mask is None:
        _check_geometry(archive, observation["saved_map"], selected, False, rooms)

    if ordinary:
        if len(selected) > 32:
            raise Refused("В отчёте больше 32 пропущенных комнат. Выберите их на карте по частям")
        status = DS.SEGMENT_CLEANING
        options = [1, settings.get("suction"), settings.get("water")]
        payload = {"selects": room_selection(selected, options, capability.customized_cleaning)}
    elif native and method != CleanupMethod.CLEANGENIUS and not areas and blocked:
        if settings.get("cleangenius") is not False or settings.get("customized") is not False:
            raise Refused("Для доуборки пропущенных комнат проверьте режим уборки в Dreamehome")
        if (settings.get("mode") == "mop_after_sweep" or not integer(settings.get("suction"), 0, 3)
                or not integer(settings.get("water"), 1, 3)
                or settings.get("mode") != "mop" and settings.get("max_suction") is not False
                or settings.get("mode") != "sweep" and not (settings.get("verified_wetness") is True or
                                                           settings.get("preserve_water") is True)):
            raise Refused("Робот не подтвердил параметры доуборки пропущенных комнат")
        status = DS.SEGMENT_CLEANING
        options = [1, settings["suction"], settings["water"]]
        payload = {"selects": room_selection(blocked, options, capability.customized_cleaning), "timestamp": timestamp}
    elif mop_again:
        status = DS.CLEANING
        payload = {"MopAgain": dos, "timestamp": timestamp, "CleanArea": areas, "BigArea": blocked}
    else:
        raise Refused("Dreamehome не подтвердил параметры доуборки по этому отчёту")

    try:
        serialized = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise Refused("Данные доуборки в отчёте не читаются") from None
    if len(serialized) > 8192:
        raise Refused("Данные доуборки в отчёте слишком велики")
    targets = {"rooms": selected, "dirty_areas": mask is not None}
    if ordinary:
        targets["ordinary_rooms"] = True
    if dirty_marked and mask is None:
        targets["separate_dirty"] = True
    if skipped and not ordinary:
        targets["separate_rooms"] = skipped
    if ambiguous:
        targets["ambiguous_rooms"] = ambiguous
    evidence = sha256(json.dumps([binding, reference, archive.map_id, archived_id, archive.frame_id, archive.timestamp_ms,
                                  archive.multiple_cleaning_time, method, reported, blocked_flags, outcomes, targets,
                                  history.mopping_mode, history.second_mopping, settings, status, payload],
                                 separators=(",", ":")).encode())
    if mask is not None:
        dims = archive.dimensions
        evidence.update(str((dims.left, dims.top, dims.grid_size, mask.shape)).encode())
        evidence.update(np.packbits(mask).tobytes())
    return status, serialized, evidence.digest(), targets
