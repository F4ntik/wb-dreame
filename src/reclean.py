"""Prepare the vendor's report-scoped follow-up without changing robot settings."""
import json
from hashlib import sha256

import numpy as np

from config import Refused, integer
from vendor.types import CleanupMethod, DreameVacuumStatus as DS, MapPixelType


def report_request(history, reference, archive, current, observation, rooms, capability, binding):
    archived_id = archive.saved_map_id
    if ((archived_id is not None and archived_id != observation["map_id"]) or
            (archived_id is None and archive.map_id != current.map_id)):
        raise Refused("Последняя уборка относится к другой карте")

    blocked = archive.blocked_segments or {}
    reported = history.blocked_segments or {}
    if reported and reported.keys() != blocked.keys():
        raise Refused("Архивная карта не подтвердила пропуск комнат из отчёта")
    if not all(integer(room, 1, 255) and str(room) in rooms for room in blocked):
        raise Refused("Комнаты из последнего отчёта изменились. Проверьте карту в Dreamehome")
    for room in blocked:
        before = (archive.segments or {}).get(room)
        after = (observation["saved_map"].segments or {}).get(room)
        if before is None or after is None or before.unique_id != after.unique_id:
            raise Refused("Комнаты из последнего отчёта изменились. Проверьте карту в Dreamehome")

    method = history.cleanup_method
    overlay = archive.cleaning_map_data
    areas = archive.cleaned_segments if archive.cleaned_segments is not None else []
    settings = observation["command_settings"]
    dirty = (method == CleanupMethod.CLEANGENIUS and not capability.cleangenius_auto and
             (history.mopping_mode == 2 or settings.get("mop_installed") is True) and
             (history.second_mopping == 2 or bool(areas)) and
             overlay is not None and overlay.has_dirty_area is True)
    if not blocked and not dirty:
        raise Refused("Dreamehome не подтвердил пропущенные комнаты или загрязнённые участки в последнем отчёте")
    if method not in (CleanupMethod.CLEANGENIUS, CleanupMethod.DEFAULT_MODE, CleanupMethod.CUSTOMIZED_CLEANING):
        raise Refused("Режим последней уборки не позволяет проверить доуборку")

    timestamp = history.multiple_cleaning_time
    if not (integer(timestamp, 1, 2**63 - 1) or isinstance(timestamp, str) and 0 < len(timestamp) <= 64
            and timestamp.isascii() and timestamp.isprintable() and not any(c.isspace() for c in timestamp)):
        raise Refused("Dreamehome не вернул идентификатор доуборки для последнего отчёта")
    if archive.multiple_cleaning_time is not None and str(archive.multiple_cleaning_time) != str(timestamp):
        raise Refused("Идентификаторы уборки в отчёте и на архивной карте различаются")

    if method != CleanupMethod.CLEANGENIUS and not areas and blocked:
        if settings.get("cleangenius") is not False or settings.get("customized") is not False:
            raise Refused("Для доуборки пропущенных комнат проверьте режим уборки в Dreamehome")
        if (settings.get("mode") == "mop_after_sweep" or not integer(settings.get("suction"), 0, 3)
                or not integer(settings.get("water"), 1, 3)
                or settings.get("mode") != "mop" and settings.get("max_suction") is not False
                or settings.get("mode") != "sweep" and not (settings.get("verified_wetness") is True or
                                                           settings.get("preserve_water") is True)):
            raise Refused("Робот не подтвердил параметры доуборки пропущенных комнат")
        status = DS.SEGMENT_CLEANING
        payload = {"selects": [[room, 1, settings["suction"], settings["water"],
                                1 if capability.customized_cleaning else i]
                               for i, room in enumerate(blocked, 1)], "timestamp": timestamp}
    else:
        dos = archive.dos if archive.dos is not None else 1
        if type(areas) is not list or not integer(dos, 0, 255) or areas and not dirty:
            raise Refused("Dreamehome не подтвердил параметры доуборки загрязнённых участков")
        status = DS.CLEANING
        payload = {"MopAgain": dos, "timestamp": timestamp, "CleanArea": areas, "BigArea": list(blocked)}

    try:
        serialized = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise Refused("Данные доуборки в отчёте не читаются") from None
    if len(serialized) > 8192:
        raise Refused("Данные доуборки в отчёте слишком велики")
    evidence = sha256(json.dumps([binding, reference, archive.map_id, archived_id, archive.frame_id, archive.timestamp_ms,
                                  method, reported, blocked, history.mopping_mode, history.second_mopping,
                                  settings, status, payload], separators=(",", ":")).encode())
    if dirty:
        dims, pixels = overlay.dimensions, overlay.pixel_type
        if (dims is None or not isinstance(pixels, np.ndarray) or pixels.shape != (dims.width, dims.height)
                or not 0 < pixels.size <= 262_144):
            raise Refused("Отчёт не содержит читаемой маски загрязнённых участков")
        mask = pixels == MapPixelType.DIRTY_AREA
        if not mask.any():
            raise Refused("На архивной карте нет подтверждённых загрязнённых участков")
        evidence.update(str((dims.left, dims.top, dims.grid_size, pixels.shape)).encode())
        evidence.update(np.packbits(mask).tobytes())
    return status, serialized, evidence.digest(), {"rooms": list(blocked), "dirty_areas": dirty}
