"""Read existing obstacle photos from one selected Dreamehome archive."""
import hashlib
import json
import logging
import re
from contextlib import contextmanager
from secrets import token_bytes
from threading import get_ident

from vendor.types import (
    DreameVacuumAIProperty as AI,
    DreameVacuumProperty as P,
    DreameVacuumStrAIProperty as STR_AI,
    DreameVacuumStreamStatus as STREAM,
    MapData, Obstacle, ObstacleIgnoreStatus, ObstaclePictureStatus, ObstacleType,
)

PRIVACY_KEY = "prop.s_ai_config"
OPAQUE_ID = re.compile(r"[0-9a-f]{32}\Z")
ID_KEY = token_bytes(32)
MAX_OBSTACLES = 128
MAX_PHOTO = 8_000_000
LABELS = {
    "SCALE": "Весы", "THREAD": "Нить", "WIRE": "Провод", "TOY": "Игрушка",
    "SHOES": "Обувь", "SOCK": "Носок", "FECE": "След питомца", "TRASH_CAN": "Корзина",
    "FABRIC": "Ткань", "POWER_STRIP": "Удлинитель", "LIQUID_STAIN": "Жидкость", "PET": "Питомец",
    "CLEANING_TOOLS": "Инвентарь", "DETECTED_STAIN": "Пятно", "BLOCKED_ROOM": "Проход перекрыт",
    "EASY_TO_STUCK_FURNITURE": "Низкая мебель", "UNCLEANABLE_STAIN": "Пятно", "LEG": "Ножка мебели",
    "LARGE_PARTICLES": "Крупный мусор", "DRIED_STAIN": "Засохшее пятно", "FOOD_BOWL": "Миска",
    "PET_BED": "Лежанка", "HAIR": "Волосы", "PAPER": "Бумага",
}


@contextmanager
def _quiet_vendor():
    # Vendor file helpers can log signed URLs and raw cloud replies.
    thread = get_ident()

    def other_thread(_):
        return get_ident() != thread

    loggers = [logging.getLogger("vendor." + name) for name in ("map", "protocol", "device")]
    for logger in loggers:
        logger.addFilter(other_thread)
    try:
        yield
    finally:
        for logger in loggers:
            logger.removeFilter(other_thread)


def _flag(value):
    if type(value) is bool:
        return value
    if type(value) is int and value in (0, 1):
        return bool(value)
    return None


def _property_flags(raw):
    if type(raw) is int and raw >= 0:
        return bool(raw & AI.AI_OBSTACLE_PICTURE), bool(raw & AI.AI_OBSTACLE_IMAGE_UPLOAD)
    if isinstance(raw, str) and len(raw) <= 4096:
        try:
            settings = json.loads(raw)
        except ValueError:
            return None, None
        if type(settings) is dict:
            return None, _flag(settings.get(STR_AI.AI_OBSTACLE_IMAGE_UPLOAD.value))
    return None, None


def _fresh_flags(device, protocol):
    mapping = device.property_mapping.get(P.AI_OBSTACLE_DETECTION)
    if not mapping:
        return None, None
    rows = protocol.get_properties([{"did": str(P.AI_OBSTACLE_DETECTION.value), **mapping}], retry_count=0, timeout=6)
    if type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict:
        return None, None
    row = rows[0]
    if type(row.get("code")) is not int or row["code"] != 0 or any(row.get(k) != v for k, v in mapping.items()):
        return None, None
    return _property_flags(row.get("value"))


def _privacy(cloud):
    reply = cloud.get_batch_device_datas([PRIVACY_KEY])
    raw = reply.get(PRIVACY_KEY) if type(reply) is dict else None
    if type(raw) is not str or len(raw) > 16384:
        return None
    payload = json.loads(raw)
    if type(payload) is not dict:
        return None
    flags = [_flag(payload[key]) for key in ("privacyAuthed", "aiPrivacyAuthed") if key in payload]
    if not flags or None in flags:
        return None
    return all(flags)


def _cloud(device):
    protocol = getattr(device, "_protocol", None)
    cloud = getattr(protocol, "cloud", None)
    if (getattr(protocol, "dreame_cloud", None) is True and getattr(cloud, "logged_in", None) is True
            and cloud is getattr(protocol, "device_cloud", None)):
        return protocol, cloud
    return None, None


def camera_status(device, *, refresh=False):
    """Return scalar diagnostics. A routine status read is cache-only."""
    status = getattr(device, "status", None)
    stream = getattr(status, "stream_status", None)
    result = {
        "camera_supported": _flag(getattr(getattr(device, "capability", None), "camera_streaming", None)),
        "stream_status": stream.name.lower() if isinstance(stream, STREAM) else None,
        "photo_capture": _flag(getattr(status, "ai_obstacle_picture", None)),
        "photo_upload": _flag(getattr(status, "ai_obstacle_image_upload", None)),
        "privacy_confirmed": None,
        "live_available": False,
    }
    if refresh:
        result.update(photo_capture=None, photo_upload=None)
        protocol, cloud = _cloud(device)
        if cloud is not None:
            with _quiet_vendor():
                try:
                    result["photo_capture"], result["photo_upload"] = _fresh_flags(device, protocol)
                    if result["photo_capture"] is True:
                        result["privacy_confirmed"] = _privacy(cloud)
                except Exception:
                    # Leave unavailable diagnostics unset on transport or decoding failure.
                    pass
    return result


def _history_timestamp(map_data):
    updated = map_data.last_updated
    return float(updated) if type(updated) in (int, float) and 0 < updated < 2**53 else None


def _public_id(map_data, source, key, obstacle):
    identity = repr((map_data.map_id, map_data.frame_id, map_data.timestamp_ms, _history_timestamp(map_data),
                     source, key, obstacle.x, obstacle.y, obstacle.type.value, obstacle.object_id,
                     obstacle.file_name, obstacle.key, obstacle.picture_status))
    return hashlib.blake2s(identity.encode(), key=ID_KEY, digest_size=16).hexdigest()


def _entries(map_data):
    if (not isinstance(map_data, MapData) or map_data.history_map is not True
            or _history_timestamp(map_data) is None):
        return
    emitted = 0
    for source, values in (("camera", map_data.obstacles), ("laser", map_data.laser_obstacles)):
        if type(values) is not dict:
            continue
        for key, obstacle in values.copy().items():
            if (not isinstance(obstacle, Obstacle) or type(key) is not str or len(key) > 32
                    or not isinstance(obstacle.type, ObstacleType)
                    or obstacle.ignore_status in (ObstacleIgnoreStatus.HIDDEN, ObstacleIgnoreStatus.ERROR)
                    or not all(type(v) in (int, float) and -10_000_000 <= v <= 10_000_000 for v in (obstacle.x, obstacle.y))):
                continue
            yield _public_id(map_data, source, key, obstacle), source, key, obstacle
            emitted += 1
            if emitted >= MAX_OBSTACLES:
                return


def _archive_field(value):
    return (type(value) is str and 2 <= len(value) <= 1024 and "://" not in value
            and all(32 <= ord(char) != 127 for char in value))


def _photo_manager(device, obstacle, source):
    if (source != "camera" or obstacle.picture_status is not ObstaclePictureStatus.UPLOADED
            or not _archive_field(obstacle.file_name)):
        return None
    protocol, _ = _cloud(device)
    manager = getattr(device, "_map_manager", None)
    if protocol is None or manager is None or getattr(manager, "_protocol", None) is not protocol:
        return None
    capability = getattr(device, "capability", None)
    if getattr(capability, "local_storage", None) is True:
        if not getattr(capability, "resources_aes_iv", None):
            return None
    elif getattr(capability, "local_storage", None) is False:
        if not _archive_field(obstacle.key):
            return None
    else:
        return None
    return manager


def obstacles(map_data, device):
    """Return archive markers in native coordinates, with no URLs or photo reads."""
    display = camera_status(device)["photo_capture"] is True
    return [{"id": public_id, "kind": item.type.name.lower(), "label": LABELS.get(item.type.name, "Препятствие"),
             "x": item.x, "y": item.y, "photo_available": bool(display and _photo_manager(device, item, source))}
            for public_id, source, _, item in _entries(map_data)]


def _valid_dimensions(width, height):
    return 0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16_777_216


def _image_type(data):
    if type(data) is not bytes or not 34 <= len(data) <= MAX_PHOTO:
        return None
    if (data.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR") and data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
            and _valid_dimensions(int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big"))):
        return "image/png"
    if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
        return None
    pos = 2
    while pos + 3 < len(data) and data[pos] == 0xFF:
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        if marker in (0, 0xDA, 0xD9):
            break
        size = int.from_bytes(data[pos + 2:pos + 4], "big")
        if size < 2 or pos + size + 2 > len(data):
            break
        if marker in (0xC0, 0xC1, 0xC2) and size >= 8:
            width, height = int.from_bytes(data[pos + 7:pos + 9], "big"), int.from_bytes(data[pos + 5:pos + 7], "big")
            return "image/jpeg" if _valid_dimensions(width, height) else None
        pos += size + 2
    return None


def obstacle_photo(map_data, device, obstacle_id):
    """Fetch one already recorded image after an explicit authenticated request."""
    if not isinstance(obstacle_id, str) or not OPAQUE_ID.fullmatch(obstacle_id):
        return None
    for public_id, source, key, obstacle in _entries(map_data):
        if public_id != obstacle_id:
            continue
        manager = _photo_manager(device, obstacle, source)
        if not manager:
            return None
        policy = camera_status(device, refresh=True)
        if policy["photo_capture"] is not True or policy["privacy_confirmed"] is not True:
            return None
        if (map_data.history_map is not True or type(map_data.obstacles) is not dict
                or map_data.obstacles.get(key) is not obstacle or _photo_manager(device, obstacle, source) is not manager
                or _public_id(map_data, source, key, obstacle) != public_id):
            return None
        with _quiet_vendor():
            try:
                data, found = manager.get_obstacle_image(map_data, key)
            except Exception:
                return None
        if data is None and found is None:
            raise LookupError("Dreame не вернул фотографию для этой метки. Карта и маршрут доступны.")
        mime = _image_type(data)
        if map_data.history_map is True and found is obstacle and _public_id(map_data, source, key, obstacle) == public_id and mime:
            return data, mime
        return None
    return None
