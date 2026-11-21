from __future__ import annotations
import io
import math
import time
import threading
import base64
import json
import zlib
import re
import logging
import traceback
import copy
import numpy as np
import hashlib
import textwrap
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa, padding as asym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding, serialization, hashes
from functools import cmp_to_key
from typing import Any
from time import sleep
from io import BytesIO
from typing import Optional, Tuple
from threading import Timer
from .protocol import DreameVacuumProtocol
from .exceptions import DeviceException
from .types import (
    PIID,
    DIID,
    DreameVacuumProperty,
    DreameVacuumAction,
    DreameVacuumActionMapping,
    DreameVacuumDeviceCapability,
    RobotType,
    CleansetType,
    ObstacleType,
    Obstacle,
    FurnitureType,
    Furniture,
    PathType,
    Point,
    Coordinate,
    MapDataPartial,
    MapData,
    MapFrameType,
    MapPixelType,
    Path,
    Area,
    Wall,
    Line,
    Carpet,
    Polygon,
    Segment,
    StartupMethod,
    CleanupMethod,
    TaskEndType,
    RecoveryMapType,
    ObstacleIgnoreStatus,
    ObstacleReason,
    MapImageDimensions,
    MapRendererLayer,
    MapRendererColorScheme,
    MapRendererConfig,
    MapRendererData,
    MapRendererResources,
    RecoveryMapInfo,
    DreameVacuumFloorMaterial,
    DreameVacuumFloorMaterialDirection,
    MAP_COLOR_SCHEME_LIST,
    SEGMENT_TYPE_CODE_TO_NAME,
    FURNITURE_TYPE_TO_DIMENSIONS,
    FURNITURE_V2_TYPE_TO_DIMENSIONS,
    FURNITURE_V2_TYPE_MIJIA_TO_DIMENSIONS,
    ALine,
    CLine,
    Paths,
    Angle,
    RestartableTimer,
)
from .const import (
    MAP_PARAMETER_NAME,
    MAP_PARAMETER_VALUE,
    MAP_PARAMETER_TIME,
    MAP_PARAMETER_CODE,
    MAP_PARAMETER_OUT,
    MAP_PARAMETER_MAP,
    MAP_PARAMETER_ANGLE,
    MAP_PARAMETER_MAPSTR,
    MAP_PARAMETER_CURR_ID,
    MAP_PARAMETER_VACUUM,
    MAP_PARAMETER_EXPIRES_TIME,
    MAP_PARAMETER_URL,
    MAP_REQUEST_PARAMETER_MAP_ID,
    MAP_REQUEST_PARAMETER_FRAME_ID,
    MAP_REQUEST_PARAMETER_FRAME_TYPE,
    MAP_REQUEST_PARAMETER_REQ_TYPE,
    MAP_REQUEST_PARAMETER_FORCE_TYPE,
    MAP_REQUEST_PARAMETER_TYPE,
    MAP_REQUEST_PARAMETER_INDEX,
    MAP_REQUEST_PARAMETER_ROOM_ID,
    MAP_DATA_JSON_CLASS,
    MAP_DATA_JSON_PARAMETER_CLASS,
    MAP_DATA_JSON_PARAMETER_SIZE,
    MAP_DATA_JSON_PARAMETER_X,
    MAP_DATA_JSON_PARAMETER_Y,
    MAP_DATA_JSON_PARAMETER_PIXEL_SIZE,
    MAP_DATA_JSON_PARAMETER_LAYERS,
    MAP_DATA_JSON_PARAMETER_ENTITIES,
    MAP_DATA_JSON_PARAMETER_META_DATA,
    MAP_DATA_JSON_PARAMETER_VERSION,
    MAP_DATA_JSON_PARAMETER_ROTATION,
    MAP_DATA_JSON_PARAMETER_TYPE,
    MAP_DATA_JSON_PARAMETER_POINTS,
    MAP_DATA_JSON_PARAMETER_PIXELS,
    MAP_DATA_JSON_PARAMETER_SEGMENT_ID,
    MAP_DATA_JSON_PARAMETER_ACTIVE,
    MAP_DATA_JSON_PARAMETER_NAME,
    MAP_DATA_JSON_PARAMETER_DIMENSIONS,
    MAP_DATA_JSON_PARAMETER_MIN,
    MAP_DATA_JSON_PARAMETER_MAX,
    MAP_DATA_JSON_PARAMETER_MID,
    MAP_DATA_JSON_PARAMETER_AVG,
    MAP_DATA_JSON_PARAMETER_PIXEL_COUNT,
    MAP_DATA_JSON_PARAMETER_COMPRESSED_PIXELS,
    MAP_DATA_JSON_PARAMETER_ROBOT_POSITION,
    MAP_DATA_JSON_PARAMETER_CHARGER_POSITION,
    MAP_DATA_JSON_PARAMETER_NO_MOP_AREA,
    MAP_DATA_JSON_PARAMETER_NO_GO_AREA,
    MAP_DATA_JSON_PARAMETER_ACTIVE_ZONE,
    MAP_DATA_JSON_PARAMETER_VIRTUAL_WALL,
    MAP_DATA_JSON_PARAMETER_PATH,
    MAP_DATA_JSON_PARAMETER_FLOOR,
    MAP_DATA_JSON_PARAMETER_WALL,
    MAP_DATA_JSON_PARAMETER_SEGMENT,
)

_LOGGER = logging.getLogger(__name__)


def _saved_layout_changed(previous: MapData, incoming: MapData) -> bool:
    # Map IDs survive edits. Compare decoded room geometry only for complete incoming grids.
    if incoming.dimensions is None or incoming.pixel_type is None or incoming.empty_map:
        return False
    if previous.dimensions != incoming.dimensions or not np.array_equal(previous.pixel_type, incoming.pixel_type):
        return True
    old_rooms, new_rooms = previous.segments or {}, incoming.segments or {}
    if old_rooms.keys() != new_rooms.keys():
        return True
    return any(
        (room.name, room.visibility, room.unmapped) !=
        (new_rooms[key].name, new_rooms[key].visibility, new_rooms[key].unmapped)
        for key, room in old_rooms.items()
    )


def _command_map_signature(saved):
    if (saved is None or saved.saved_map is not True or saved.empty_map or saved.dirty or saved.restored_map
            or saved.wifi_map or saved.cleaning_map or saved.history_map or saved.temporary_map or saved.new_map):
        return None
    dim, pixels = saved.dimensions, saved.pixel_type
    if (dim is None or pixels is None or type(saved.map_id) is not int or saved.map_id <= 0
            or not (dim.grid_size > 0 and dim.width > 0 and dim.height > 0 and dim.width * dim.height <= 262_144)
            or pixels.shape != (dim.width, dim.height) or not pixels.any()):
        return None
    rooms = tuple((key, room.name, room.visibility, room.unmapped, room.unique_id)
                  for key, room in sorted((saved.segments or {}).copy().items()))
    charger = saved.charger_position
    station = (charger.x, charger.y) if charger else None
    restrictions = tuple(tuple((tuple(shape.as_list()), getattr(shape, "angle", None), getattr(shape, "hidden", False))
                               for shape in shapes or ())
                         for shapes in (saved.no_go_areas, saved.no_mopping_areas, saved.virtual_walls))
    return (saved.map_id, dim.left, dim.top, dim.grid_size, dim.width, dim.height, saved.rotation, station,
            rooms, restrictions, hashlib.blake2s(pixels.tobytes()).digest())


class DreameMapVacuumMapManager:
    def __init__(self, _protocol: DreameVacuumProtocol) -> None:
        self._map_list_lock = threading.RLock()
        self._map_list_source = (None, None)
        self._recovery_map_list_object_name: str = None
        self._update_callback = None
        self._change_callback = None
        self._error_callback = None
        self._update_timer: Timer = None
        self._update_running: bool = False
        self._update_lock = threading.Lock()
        self._live_map_demand_until = 0.0
        self._next_live_map_request = 0.0
        self._update_interval: float = 10
        self._device_running: bool = False
        self._device_docked: bool = False
        self._available: bool = False
        self._disconnected: bool = False
        self._ready: bool = False
        self._connected: bool = True
        self._aes_iv: str = None
        self._capability: DreameVacuumDeviceCapability = None

        self._init_data()

        self._protocol = _protocol
        self.editor = DreameMapVacuumMapEditor(self)

    def _init_data(self) -> None:
        with self._map_list_lock:
            self._map_list_fetch = None
            self._validated_map_list = None
        self._map_data: MapData = None
        self._current_frame_id: int = None
        self._current_map_id: int = None
        self._current_timestamp_ms: int = None
        self._file_urls: dict[str, str] = {}
        self._saved_map_data: dict[int, MapData] = {}
        self._map_list: list[int] = []
        self._need_map_request: bool = False
        self._need_new_map: bool = False
        self._need_map_list_request: bool = None
        self._need_recovery_map_list_request: bool = None
        self._map_data_queue: dict[int, MapData] = {}
        self._updated_frame_id: int = None
        self._selected_map_id: int = None
        self._request_queue: dict[str, bool] = {}
        self._latest_map_data_time: int = None
        self._latest_object_name_time: int = None
        self._latest_map_timestamp_ms: int = None
        self._latest_map_id: int = None
        self._last_p_request_map_id: int = None
        self._last_p_request_frame_id: int = None
        self._last_p_request_time: int = None
        self._last_robot_time: int = None
        self._map_request_time: int = None
        self._map_request_count: int = 0
        self._new_map_request_time: int = None
        self._aes_key: str = None

    def _request_map_from_cloud(self) -> bool:
        if self._protocol.cloud.dreame_cloud:
            return True

        if self._current_timestamp_ms is not None:
            start_time = self._current_timestamp_ms
            request_start_time = int(math.floor(start_time / 1000.0))
        else:
            request_start_time = 0
            if self._latest_object_name_time is not None:
                request_start_time = self._latest_object_name_time
            elif self._map_request_time is not None:
                request_start_time = self._map_request_time
            elif self._last_robot_time is not None:
                request_start_time = int(self._last_robot_time / 1000)

        if self._latest_map_data_time is None or self._latest_map_data_time < request_start_time:
            self._latest_map_data_time = request_start_time

        if self._latest_object_name_time is None or self._latest_object_name_time < request_start_time:
            self._latest_object_name_time = request_start_time

        map_data_result = self._protocol.cloud.get_device_property(
            DIID(DreameVacuumProperty.MAP_DATA), 20, self._latest_map_data_time
        )

        if not self._protocol.cloud.connected:
            if self._connected:
                self._connected = False
                self._map_data_changed()
            return False
        elif not self._connected:
            self._connected = True
            self._map_data_changed()

        if map_data_result is None:
            _LOGGER.warning("Getting map_data from cloud failed")
            map_data_result = []

        object_name_result = self._protocol.cloud.get_device_property(
            DIID(DreameVacuumProperty.OBJECT_NAME), 1, self._latest_object_name_time
        )
        if object_name_result is None:
            _LOGGER.warning("Getting object_name from cloud failed")

        partial_map_data = None
        if len(map_data_result):
            partial_map_data = []
            self._latest_map_data_time = map_data_result[0][MAP_PARAMETER_TIME] + 1

            for data in map_data_result:
                partial_map_data.append(
                    self._decode_map_partial(
                        json.loads(data[MAP_PARAMETER_VALUE if MAP_PARAMETER_VALUE in data else "val"])[0],
                        data[MAP_PARAMETER_TIME] * 1000 if data.get(MAP_PARAMETER_TIME) else None,
                    )
                )

        object_name = None
        object_name_timestamp = None
        if object_name_result:
            data = object_name_result[0]
            if MAP_PARAMETER_TIME in data:
                timestamp = data[MAP_PARAMETER_TIME]
                self._latest_object_name_time = timestamp + 1

            if len(object_name_result) == 1:
                object_name = json.loads(data[MAP_PARAMETER_VALUE if MAP_PARAMETER_VALUE in data else "val"])[0]
                if timestamp:
                    object_name_timestamp = timestamp * 1000

        self._add_cloud_map_data(partial_map_data, object_name, object_name_timestamp)
        return len(map_data_result) or object_name is not None

    def _request_map(self, parameters: dict[str, Any] = None) -> dict[str, Any] | None:
        if parameters is None:
            parameters = {
                MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.I.name,
            }

        payload = [
            {
                "piid": PIID(DreameVacuumProperty.FRAME_INFO),
                MAP_PARAMETER_VALUE: str(json.dumps(parameters, separators=(",", ":"))).replace(" ", ""),
            }
        ]

        try:
            _LOGGER.info("Request map from device %s", payload)
            mapping = DreameVacuumActionMapping[DreameVacuumAction.REQUEST_MAP]
            return self._protocol.action(mapping["siid"], mapping["aiid"], payload, 0)
        except Exception as ex:
            _LOGGER.warning("Send request map failed: %s", ex)
        return None

    def _request_i_map(self, start_time: int = None, *, schedule_retry: bool = True) -> bool:
        if not self._request_i_map_available and not self._protocol.dreame_cloud:
            return self.request_new_map()

        parameters = {
            MAP_REQUEST_PARAMETER_REQ_TYPE: 1,
            MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.I.name,
            MAP_REQUEST_PARAMETER_FORCE_TYPE: 1,
        }

        if start_time:
            parameters[MAP_PARAMETER_TIME] = start_time

        result = self._request_map(parameters)
        if result and MAP_PARAMETER_CODE in result and result[MAP_PARAMETER_CODE] == 0:
            out = result[MAP_PARAMETER_OUT]
            _LOGGER.info("Response from device %s", out)
            has_map = False
            object_name = None
            raw_map_data = None
            for prop in out:
                value = prop[MAP_PARAMETER_VALUE]
                if value != "":
                    piid = prop["piid"]
                    if piid == PIID(DreameVacuumProperty.OBJECT_NAME):
                        has_map = True
                        object_name = value
                    elif piid == PIID(DreameVacuumProperty.MAP_DATA):
                        has_map = True
                        raw_map_data = value
                    elif piid == PIID(DreameVacuumProperty.ROBOT_TIME):
                        self._last_robot_time = int(value)
                        if start_time is None and schedule_retry:
                            self._map_request_time = self._last_robot_time
                            self._map_request_count = 1
                    elif piid == PIID(DreameVacuumProperty.OLD_MAP_DATA):
                        if not has_map:
                            values = value.split(",")
                            if values[0] == "0":
                                raw_map_data = values[1]
                            else:
                                object_name = values[1]
                                if len(values) == 3:
                                    object_name = f"{object_name},{values[2]}"

            if has_map:
                self._latest_object_name_time = int(self._last_robot_time / 1000) + 1
                self._map_request_time = None

            if object_name:
                self._add_map_data_file(object_name, self._last_robot_time)
                return True
            if raw_map_data:
                self._add_raw_map_data(raw_map_data, self._last_robot_time)
                return True
            return False

        self._request_map_from_cloud()
        return False

    def _request_missing_p_map(self) -> bool:
        if self._map_data is None:
            return

        if self._partial_map_queue_size() == 0:
            return

        frame_id = self._current_frame_id + 1
        map_id = self._current_map_id

        if (
            self._last_p_request_time is not None
            and self._last_p_request_map_id == map_id
            and self._last_p_request_frame_id == frame_id
            and (time.time() - self._last_p_request_time) < 3
        ):
            return

        self._last_p_request_map_id = map_id
        self._last_p_request_frame_id = frame_id
        self._last_p_request_time = time.time()

        _LOGGER.info("Request missing P map: %s", frame_id)
        result = self._request_map(
            {
                MAP_REQUEST_PARAMETER_MAP_ID: map_id,
                MAP_REQUEST_PARAMETER_FRAME_ID: frame_id,
                MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.P.name,
            }
        )
        return bool(result and result[MAP_PARAMETER_CODE] == 0)

    def _request_next_p_map(self, map_id: int, frame_id: int) -> bool:
        key = f"{map_id}:{frame_id}"
        if key in self._request_queue and self._request_queue[key]:
            return

        self._request_queue[key] = True
        _LOGGER.info("Request next P map: %s", frame_id)
        result = self._request_map(
            {
                MAP_REQUEST_PARAMETER_MAP_ID: map_id,
                MAP_REQUEST_PARAMETER_REQ_TYPE: 1,
                MAP_REQUEST_PARAMETER_FRAME_ID: frame_id,
                MAP_REQUEST_PARAMETER_FRAME_TYPE: MapFrameType.P.name,
            }
        )
        if result and result[MAP_PARAMETER_CODE] == 0:
            del self._request_queue[key]
            object_name = None
            raw_map_data = None
            timestamp = None

            for prop in result[MAP_PARAMETER_OUT]:
                value = prop[MAP_PARAMETER_VALUE]
                if value != "":
                    piid = prop["piid"]
                    if piid == PIID(DreameVacuumProperty.OBJECT_NAME):
                        object_name = value
                    elif piid == PIID(DreameVacuumProperty.MAP_DATA):
                        raw_map_data = value
                    elif piid == PIID(DreameVacuumProperty.ROBOT_TIME):
                        timestamp = int(value)

            if object_name:
                self._add_map_data_file(object_name, timestamp)
            if raw_map_data:
                _LOGGER.info("Lost P map received: %s:%s", map_id, frame_id)
                self._add_raw_map_data(raw_map_data, timestamp)

            if not raw_map_data and self.map_version == 0 and not object_name:
                self.request_new_map()
                return False
            return True
        return False

    def _request_t_map(self) -> None:
        result = self._request_map({MAP_REQUEST_PARAMETER_FRAME_TYPE: "T"})
        if result and result[MAP_PARAMETER_CODE] == 0:
            self.request_map_list()

    def _request_w_map(self) -> None:
        try:
            _LOGGER.info("Request wifi map from device")
            mapping = DreameVacuumActionMapping[DreameVacuumAction.WIFI_MAP]
            return self._protocol.action(mapping["siid"], mapping["aiid"], None, 0)
        except Exception as ex:
            _LOGGER.warning("Send request map failed: %s", ex)
        return None

    def _request_current_map(self, map_request_time: int = None, *, schedule_retry: bool = True) -> bool:
        if self._request_i_map_available or self._protocol.dreame_cloud:
            return self._request_i_map(map_request_time, schedule_retry=schedule_retry)

        return self._request_map_from_cloud()

    def _map_data_updated(self) -> None:
        if self._update_callback:
            _LOGGER.debug("Update callback")
            self._update_callback()

    def _map_data_changed(self, saved_map=False) -> None:
        if self._change_callback:
            _LOGGER.debug("Change callback")
            self._change_callback(saved_map)

    def _update_task(self) -> None:
        if self._update_timer is not None:
            self._update_timer.cancel()

        start = time.time()
        self.update()
        self.schedule_update(max(self._update_interval - (time.time() - start), 1))

    def _queue_partial_map(self, map_data) -> None:
        if map_data.map_id != self._latest_map_id:
            return
        next_frame_id = 0

        if self._current_map_id is not None and self._current_map_id == self._latest_map_id:
            next_frame_id = self._current_frame_id + 1

        if map_data.map_id not in self._map_data_queue:
            self._map_data_queue[map_data.map_id] = {}

        if map_data.frame_id < next_frame_id:
            return
        self._map_data_queue[map_data.map_id][map_data.frame_id] = map_data

    def _delete_invalid_partial_maps(self) -> None:
        if self._latest_map_id is None:
            return

        if self._current_frame_id is None:
            return

        frame_id = self._current_frame_id
        for k in list(self._map_data_queue.keys()):
            if k != self._latest_map_id:
                del self._map_data_queue[k]

        if self._latest_map_id not in self._map_data_queue or not self._map_data_queue[self._latest_map_id]:
            return

        for k in list(self._map_data_queue[self._latest_map_id].keys()):
            if k <= frame_id:
                del self._map_data_queue[self._latest_map_id][k]

    def _unqueue_next_partial_map(self) -> MapData | None:
        if (
            self._latest_map_id is None
            or self._current_frame_id is None
            or self._current_map_id != self._latest_map_id
        ):
            return

        frame_id = self._current_frame_id + 1
        if (
            self._latest_map_id not in self._map_data_queue
            or not self._map_data_queue[self._latest_map_id]
            or frame_id not in self._map_data_queue[self._latest_map_id]
        ):
            return

        map_data = self._map_data_queue[self._latest_map_id][frame_id]

        if map_data:
            del self._map_data_queue[self._latest_map_id][frame_id]
            return map_data

    def _unqueue_partial_map(self, map_id: int, frame_id: int) -> MapData | None:
        if (
            map_id in self._map_data_queue
            and self._map_data_queue[map_id]
            and frame_id in self._map_data_queue[map_id]
        ):
            map_data = self._map_data_queue[map_id][frame_id]
            del self._map_data_queue[map_id][frame_id]
            return map_data

    def _partial_map_queue_size(self) -> int:
        if self._latest_map_timestamp_ms is None:
            return 0

        if self._latest_map_id not in self._map_data_queue or not self._map_data_queue[self._latest_map_id]:
            return 0

        return len(self._map_data_queue[self._latest_map_id])

    def _get_object_file_data(self, object_name: str = "", timestamp=None) -> Tuple[Any, Optional[str]]:
        key = None
        if object_name and "," in object_name:
            values = object_name.split(",")
            object_name = values[0]
            key = values[1]
        response = self._get_interim_file_data(object_name, timestamp)
        return response, key

    def _get_interim_file_data(self, object_name: str = "", timestamp=None) -> str | None:
        if self._protocol.cloud.logged_in:
            if object_name is None or object_name == "":
                _LOGGER.info("Get object name from cloud")
                if self._protocol.cloud.dreame_cloud:
                    object_name_result = self._protocol.cloud.get_properties(DIID(DreameVacuumProperty.OBJECT_NAME))
                    if object_name_result:
                        object_name_result = object_name_result[0][MAP_PARAMETER_VALUE]
                        object_name = object_name_result[0]
                else:
                    object_name_result = self._protocol.cloud.get_device_property(
                        DIID(DreameVacuumProperty.OBJECT_NAME)
                    )
                    if object_name_result:
                        object_name_result = json.loads(object_name_result[0][MAP_PARAMETER_VALUE])
                        object_name = object_name_result[0]

            if object_name is None or object_name == "":
                object_name = self._protocol.cloud.object_name

            url = self._get_file_url(object_name)
            if url:
                _LOGGER.info("Request map data from cloud %s", url)
                response = self._protocol.cloud.get_file(url)
                if response is not None:
                    return response
                _LOGGER.warning("Request map data from cloud failed %s", url)
                if self._file_urls.get(object_name):
                    del self._file_urls[object_name]

    def _get_file_url(self, object_name: str, interim: bool = True) -> str | None:
        url = None
        now = int(round(time.time()))
        if self._file_urls and self._file_urls.get(object_name):
            object = self._file_urls[object_name]
            if object[MAP_PARAMETER_EXPIRES_TIME] - now > 60:
                url = f"{object[MAP_PARAMETER_URL]}&current={str(now)}"

        if url is None:
            response = (
                self._protocol.cloud.get_interim_file_url(object_name)
                if interim
                else self._protocol.cloud.get_file_url(object_name)
            )
            if response:
                self._file_urls[object_name] = {
                    MAP_PARAMETER_URL: response,
                    MAP_PARAMETER_EXPIRES_TIME: now + (30 * 60),
                }
                url = self._file_urls[object_name][MAP_PARAMETER_URL]
        return url

    def _decode_map_partial(self, raw_map, timestamp=None, key=None) -> MapDataPartial | None:
        partial_map = DreameVacuumMapDecoder.decode_map_partial(raw_map, self.map_version, self._aes_iv, key)
        if partial_map is not None:
            # After restart or unsuccessful start robot returns timestamp_ms as uptime and that messes up with the latest map/frame id detection.
            # I could not figure out how app handles with this issue but i have added this code to update time stamp as request/object time.

            if timestamp and (partial_map.timestamp_ms is None or partial_map.timestamp_ms < 1577826000000):
                partial_map.timestamp_ms = timestamp

            if self._latest_map_timestamp_ms is None or partial_map.timestamp_ms > self._latest_map_timestamp_ms:
                self._latest_map_timestamp_ms = partial_map.timestamp_ms
                self._latest_map_id = partial_map.map_id

        return partial_map

    def _add_cloud_map_data(self, partial_map_data, object_name, object_name_timestamp):
        if partial_map_data:
            for partial_map in partial_map_data:
                if partial_map.frame_type == MapFrameType.I.value:
                    self._add_map_data(partial_map)
                else:
                    self._queue_partial_map(partial_map)

        next_frame_id = 1
        if self._current_frame_id:
            next_frame_id = self._current_frame_id + 1

        if (
            not self._add_map_data(self._unqueue_partial_map(self._latest_map_id, next_frame_id))
            and object_name is None
        ):
            self._delete_invalid_partial_maps()
            tmpLen = self._partial_map_queue_size()
            if tmpLen > 8:
                if self._protocol.dreame_cloud:
                    self._request_map()
                else:
                    self.request_new_map()
            elif tmpLen > 4:
                self._request_missing_p_map()
            elif tmpLen > 0 and partial_map_data:
                self._request_next_p_map(self._latest_map_id, next_frame_id)

        if object_name is not None:
            self._need_new_map = False
            _LOGGER.info("New object name received: %s", object_name)
            response, key = self._get_object_file_data(object_name, object_name_timestamp)
            if response:
                partial_map = self._decode_map_partial(response.decode(), object_name_timestamp, key)
                if partial_map:
                    if self._map_data is None or partial_map.frame_type == MapFrameType.I.value:
                        return self._add_map_data(partial_map)

                    self._queue_partial_map(partial_map)
                    next_partial_map = self._unqueue_next_partial_map()
                    if next_partial_map:
                        self._add_map_data(next_partial_map)
                    else:
                        self._delete_invalid_partial_maps()
                        if self._partial_map_queue_size() > 8:
                            if self._protocol.dreame_cloud:
                                self._request_map()
                            else:
                                self.request_new_map()

    def _add_map_data_file(self, object_name: str, timestamp) -> None:
        response, key = self._get_object_file_data(object_name, timestamp)
        if response is not None:
            self._add_raw_map_data(response.decode(), timestamp, key)

    def _add_raw_map_data(self, raw_map: str, timestamp=None, key=None) -> bool:
        return self._add_map_data(self._decode_map_partial(raw_map, timestamp, key))

    def _add_map_data(self, partial_map: MapDataPartial) -> None:
        if partial_map is None:
            return False

        if (
            partial_map.timestamp_ms is not None
            and self._current_timestamp_ms is not None
            and self._current_frame_id
            and self._current_timestamp_ms > partial_map.timestamp_ms
        ):
            _LOGGER.info(
                "Skip frame %s, timestamp %s:%s < %s:%s",
                partial_map.frame_type,
                partial_map.frame_id,
                partial_map.timestamp_ms,
                self._current_frame_id,
                self._current_timestamp_ms,
            )
            return True

        if self._current_map_id is not None and self._current_map_id != self._latest_map_id:
            _LOGGER.info(
                "Map ID Changed: %s -> %s",
                self._current_map_id,
                self._latest_map_id,
            )

            self._current_frame_id = None
            self._current_map_id = None
            self._updated_frame_id = None
            # self.request_next_map_list()

        if partial_map.map_id != self._latest_map_id:
            _LOGGER.info(
                "Skip frame, map_id %s != %s",
                partial_map.map_id,
                self._latest_map_id,
            )
            # self._add_next_map_data()
            return True

        if (
            self._current_frame_id is not None
            and self._current_frame_id is not None
            and partial_map.frame_id < self._current_frame_id
        ):
            if (
                partial_map.frame_type != MapFrameType.I.value
                or partial_map.timestamp_ms <= self._current_timestamp_ms
            ):
                _LOGGER.info(
                    "Skip frame, frame id %s:%s < %s:%s",
                    partial_map.map_id,
                    partial_map.frame_id,
                    self._current_map_id,
                    self._current_frame_id,
                )
                # self._add_next_map_data()
                return True

        if partial_map.frame_type == MapFrameType.P.value:
            if self._current_frame_id is not None and self._map_data is not None and self._map_data.restored_map:
                _LOGGER.debug("Current map data removed")
                self._map_data = None
                self._current_frame_id = None
                self._current_map_id = None

            if self._current_frame_id is None or self._map_data is None:
                self._queue_partial_map(partial_map)

                if self._map_request_time is None:
                    self._request_i_map()
                    return True

            if partial_map.frame_id != self._current_frame_id + 1:
                if partial_map.frame_id <= self._current_frame_id:
                    self._add_next_map_data()
                    return True

                self._queue_partial_map(partial_map)
                self._delete_invalid_partial_maps()

                tmpLen = self._partial_map_queue_size()
                if tmpLen > 0:
                    if self._protocol.dreame_cloud:
                        if tmpLen > 8:
                            self._request_map()
                        elif tmpLen > 4:
                            self._request_missing_p_map()
                        else:
                            next_frame_id = 1
                            if self._current_frame_id:
                                next_frame_id = self._current_frame_id + 1
                            self._request_next_p_map(self._latest_map_id, next_frame_id)
                    else:
                        self._request_next_p_map(partial_map.map_id, self._current_frame_id + 1)
                else:
                    self._add_next_map_data()
                return True

            map_data = DreameVacuumMapDecoder.decode_p_map_data_from_partial(partial_map, self._map_data)
            if map_data:
                if map_data.carpet_pixels:
                    map_data.carpet_pixels = DreameVacuumMapDecoder.get_carpets(map_data, self.selected_map)

                DreameVacuumMapDecoder.set_carpet_cleanset(map_data, map_data.carpet_cleanset, self._capability)

                self._map_data = map_data
                self._map_data.last_updated = time.time()
                self._updated_frame_id = None
                self._current_frame_id = map_data.frame_id
                self._current_map_id = map_data.map_id
                self._current_timestamp_ms = map_data.timestamp_ms

                _LOGGER.info("Decode P map %d %d", map_data.map_id, map_data.frame_id)
                self._map_data_changed()
        elif partial_map.frame_type == MapFrameType.I.value:
            self._need_map_request = False
            self._delete_invalid_partial_maps()

            (
                map_data,
                saved_map_data,
            ) = DreameVacuumMapDecoder.decode_map_data_from_partial(partial_map)
            if map_data is None:
                self._add_next_map_data()
                return True

            if map_data.empty_map:
                if self._map_data is None or not self._map_data.empty_map:
                    self._init_data()
                    self._map_data = map_data
                    self._current_frame_id = map_data.frame_id
                    self._current_map_id = map_data.map_id
                    self._current_timestamp_ms = map_data.timestamp_ms

                    self._map_data_changed()
                self._add_next_map_data()
                return True

            saved_map_changed = False
            if saved_map_data is not None and saved_map_data.saved_map:
                if not saved_map_data.carpet_cleanset:
                    saved_map_data.carpet_cleanset = map_data.carpet_cleanset
                DreameVacuumMapDecoder.set_capability(saved_map_data, self._capability)
                DreameVacuumMapDecoder.set_carpet_cleanset(
                    saved_map_data, saved_map_data.carpet_cleanset, self._capability
                )
                DreameVacuumMapDecoder.set_floor_material(saved_map_data, self._capability)
                DreameVacuumMapDecoder.set_segment_cleanset(
                    saved_map_data,
                    map_data.cleanset,
                    self._capability,
                )

                if saved_map_data.map_id in self._saved_map_data:
                    map_data.temporary_map = False
                    self._selected_map_id = saved_map_data.map_id
                    saved_map_data.map_name = self._saved_map_data[saved_map_data.map_id].map_name
                    saved_map_data.custom_name = self._saved_map_data[saved_map_data.map_id].custom_name
                    saved_map_data.rotation = self._saved_map_data[saved_map_data.map_id].rotation
                    saved_map_data.map_index = self._saved_map_data[saved_map_data.map_id].map_index
                    saved_map_data.recovery_map_list = self._saved_map_data[saved_map_data.map_id].recovery_map_list

                    saved_map_data.timestamp_ms = map_data.timestamp_ms
                    if (
                        saved_map_data != self._saved_map_data[saved_map_data.map_id]
                        or saved_map_data.segments != self._saved_map_data[saved_map_data.map_id].segments
                        or _saved_layout_changed(self._saved_map_data[saved_map_data.map_id], saved_map_data)
                        or self._saved_map_data[saved_map_data.map_id].dirty
                    ):
                        saved_map_data.last_updated = time.time()
                        if saved_map_data.wifi_map_data:
                            saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                        self._saved_map_data[saved_map_data.map_id] = saved_map_data
                        self._map_data_changed(True)
                        saved_map_changed = True
                        if not self._protocol.dreame_cloud:
                            self.request_next_map_list()

                        _LOGGER.debug(
                            "Decode saved map %s: %s",
                            saved_map_data.map_id,
                            saved_map_data.map_name,
                        )
                elif not map_data.temporary_map:
                    if not self._map_list:
                        saved_map_data.last_updated = time.time()
                        if saved_map_data.wifi_map_data:
                            saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                        self._saved_map_data[saved_map_data.map_id] = saved_map_data

                        _LOGGER.info("Add saved map from new map %s", saved_map_data.map_id)
                        self._refresh_map_list()
                        if self._map_data:
                            self._map_data_changed(True)

                    if not self._protocol.dreame_cloud:
                        if self._device_running:
                            self.request_next_map_list()
                        else:
                            self.request_map_list()
            elif (
                map_data.version == 3
                and map_data.saved_map_id
                and map_data.saved_map_id in self._saved_map_data
                and map_data.saved_map_status != 0
            ):
                self._selected_map_id = map_data.saved_map_id

            DreameVacuumMapDecoder.set_capability(map_data, self._capability)
            DreameVacuumMapDecoder.set_floor_material(map_data, self._capability, saved_map_data)
            DreameVacuumMapDecoder.set_segment_cleanset(map_data, map_data.cleanset, self._capability)
            DreameVacuumMapDecoder.set_carpet_cleanset(map_data, map_data.carpet_cleanset, self._capability)
            if not map_data.mop_type and not map_data.saved_map and map_data.saved_map_id in self._saved_map_data:
                map_data.mop_type = self._saved_map_data[map_data.saved_map_id].mop_type

            if not map_data.saved_map:
                if map_data.saved_map_id and map_data.saved_map_id in self._saved_map_data:
                    map_data.map_index = self._saved_map_data[map_data.saved_map_id].map_index

                if map_data.version == 0:
                    if map_data.saved_map_status == 1 and saved_map_data and self._device_docked:
                        map_data.segments = copy.deepcopy(saved_map_data.segments)
                        map_data.data = copy.deepcopy(saved_map_data.data)
                        map_data.pixel_type = copy.deepcopy(saved_map_data.pixel_type)
                        map_data.dimensions = copy.deepcopy(saved_map_data.dimensions)
                        map_data.charger_position = copy.deepcopy(saved_map_data.charger_position)
                        map_data.no_go_areas = saved_map_data.no_go_areas
                        map_data.no_mopping_areas = saved_map_data.no_mopping_areas
                        map_data.virtual_walls = saved_map_data.virtual_walls
                        map_data.robot_position = None
                        map_data.docked = True
                        # map_data.restored_map = True
                        map_data.path = None
                        map_data.need_optimization = False
                        map_data.saved_map_status = 2
                    elif (
                        map_data.robot_position is None
                        and map_data.restored_map
                        and not self._device_docked
                        and self._map_data
                        and not map_data.docked
                    ):
                        map_data.robot_position = self._map_data.robot_position

                changed = (
                    self._current_frame_id is None
                    or self._map_data is None
                    or map_data != self._map_data
                    or map_data.rotation != self._map_data.rotation
                    or map_data.segments != self._map_data.segments
                    or self._map_data.dirty
                )

                if (
                    changed
                    or self._current_frame_id != map_data.frame_id
                    or self._current_timestamp_ms != map_data.timestamp_ms
                ):
                    if (
                        self._current_frame_id is not None
                        and self._map_data is not None
                        and self._updated_frame_id is not None
                        and not self._map_data.dirty
                    ):
                        if map_data.frame_id <= self._updated_frame_id + 1:
                            if not self._map_data.empty_map and (
                                self._map_data.saved_map_status == 2
                                or (map_data.version == 0 and self._map_data.saved_map_status == 1)
                            ):
                                map_data.active_segments = self._map_data.active_segments
                                map_data.active_areas = self._map_data.active_areas
                                map_data.active_points = self._map_data.active_points
                                map_data.active_cruise_points = self._map_data.active_cruise_points
                                map_data.path = self._map_data.path
                                if not saved_map_changed:
                                    map_data.segments = self._map_data.segments
                                    map_data.floor_material = self._map_data.floor_material
                                    map_data.hidden_segments = self._map_data.hidden_segments
                                    map_data.unmapped_segments = self._map_data.unmapped_segments
                                    map_data.carpet_cleanset = self._map_data.carpet_cleanset
                                    map_data.mop_type = self._map_data.mop_type
                                map_data.cleanset = self._map_data.cleanset
                                map_data.cleaning_sequence = self._map_data.cleaning_sequence
                                changed = map_data != self._map_data or (
                                    saved_map_changed and map_data.segments != self._map_data.segments
                                )
                            else:
                                changed = False
                                map_data.empty_map = True
                        else:
                            self._updated_frame_id = None

                    if (
                        self._map_data
                        and not changed
                        and map_data.need_optimization
                        and not self._map_data.need_optimization
                    ):
                        map_data.need_optimization = False
                        map_data.optimized_pixel_type = copy.deepcopy(self._map_data.optimized_pixel_type)
                        map_data.optimized_dimensions = copy.deepcopy(self._map_data.optimized_dimensions)
                        map_data.optimized_charger_position = copy.deepcopy(self._map_data.optimized_charger_position)

                    if (
                        changed
                        and not saved_map_data
                        and map_data.version == 3
                        and map_data.saved_map_id
                        and map_data.saved_map_id in self._saved_map_data
                        and map_data.saved_map_status == 2
                        and self._map_data
                        and self._map_data.saved_map_id == map_data.saved_map_id
                    ):
                        saved_map_data = self._saved_map_data[map_data.saved_map_id]
                        if saved_map_data.dirty:
                            saved_map_changed = True
                            saved_map_data.dirty = False

                        if self._map_data.walls != map_data.walls:
                            saved_map_changed = True
                            saved_map_data.walls = map_data.walls

                        if self._map_data.hidden_segments != map_data.hidden_segments:
                            saved_map_changed = True
                            saved_map_data.hidden_segments = map_data.hidden_segments

                        if self._map_data.floor_material != map_data.floor_material:
                            saved_map_changed = True
                            saved_map_data.floor_material = map_data.floor_material

                        if self._map_data.furnitures != map_data.furnitures:
                            saved_map_changed = True
                            saved_map_data.furnitures = map_data.furnitures
                            saved_map_data.saved_furnitures = map_data.saved_furnitures

                        for k, v in map_data.segments.items():
                            if k in saved_map_data.segments:
                                seg = (
                                    copy.deepcopy(saved_map_data.segments[k])
                                    if not saved_map_changed
                                    else saved_map_data.segments[k]
                                )
                                seg.icon = v.icon
                                seg.name = v.name
                                seg.custom_name = v.custom_name
                                seg.type = v.type
                                seg.index = v.index
                                seg.unique_id = v.unique_id
                                seg.color_index = v.color_index
                                seg.neighbors = v.neighbors
                                seg.floor_material = v.floor_material
                                seg.floor_material_direction = v.floor_material_direction
                                seg.visibility = v.visibility
                                seg.carpet_cleaning = v.carpet_cleaning
                                seg.carpet_preferences = v.carpet_preferences
                                if not v.unmapped:
                                    seg.x = v.x
                                    seg.y = v.y

                                if not saved_map_changed and seg != saved_map_data.segments[k]:
                                    saved_map_data.segments[k] = seg
                                    saved_map_changed = True
                            elif not v.unmapped:
                                saved_map_data.segments[k] = copy.deepcopy(v)
                                saved_map_changed = True

                        for k in list(saved_map_data.segments):
                            if k not in map_data.segments:
                                del saved_map_data.segments[k]
                                saved_map_changed = True

                        if (
                            self._map_data.cleanset != map_data.cleanset
                            or self._map_data.cleaning_sequence != map_data.cleaning_sequence
                        ):
                            saved_map_changed = True
                            DreameVacuumMapDecoder.set_segment_cleanset(
                                saved_map_data, map_data.cleanset, self._capability
                            )

                        if self._map_data.carpet_cleanset != map_data.carpet_cleanset:
                            saved_map_changed = True
                            DreameVacuumMapDecoder.set_carpet_cleanset(
                                saved_map_data, map_data.carpet_cleanset, self._capability
                            )

                        if saved_map_changed:
                            saved_map_data.last_updated = time.time()
                            self._map_data_changed(True)

                    self._map_data = map_data
                    self._current_frame_id = map_data.frame_id
                    self._current_map_id = map_data.map_id
                    self._current_timestamp_ms = map_data.timestamp_ms

                    if changed:
                        _LOGGER.info("Decode I map %d %d", map_data.map_id, map_data.frame_id)
                        self._map_data.last_updated = time.time()
                        self._map_data_changed()
                    else:
                        _LOGGER.info(
                            "Decode map %d %d not changed",
                            map_data.map_id,
                            map_data.frame_id,
                        )

        if self._current_frame_id is None and self._map_data is not None:
            self._map_data = None
            self._map_data_changed()

        self._add_next_map_data()
        return True

    def _add_next_map_data(self) -> None:
        next_partial_map = self._unqueue_next_partial_map()
        if next_partial_map is not None:
            _LOGGER.debug("Continue to next map data")
            self._add_map_data(next_partial_map)

    def _refresh_map_list(self) -> None:
        index = 1
        new_map_list = []
        for map_id, saved_map_data in sorted(self._saved_map_data.items()):
            new_map_list.append(map_id)
            if saved_map_data.custom_name is None:
                saved_map_data.map_name = f"Map {str(index)}"
            else:
                saved_map_data.map_name = saved_map_data.custom_name
            saved_map_data.map_index = index
            index = index + 1
        self._map_list = new_map_list

    def _refresh_recovery_map_list(self) -> None:
        index = 1
        for map_id, saved_map_data in sorted(self._saved_map_data.items()):
            if saved_map_data.recovery_map_list:
                for recovery_map_data in saved_map_data.recovery_map_list:
                    map_type = recovery_map_data.map_type.name.replace("_", " ").title()
                    if saved_map_data.custom_name is None:
                        recovery_map_data.map_name = f"Recovery Map {str(index)} ({map_type})"
                    else:
                        recovery_map_data.map_name = (
                            f"{saved_map_data.custom_name} Recovery Map {str(index)} ({map_type})"
                        )
                    recovery_map_data.map_index = index
                    index = index + 1

    def handle_properties(self, properties):
        if not self._ready:
            return

        has_map = False
        object_name = None
        raw_map_data = None

        for prop in properties:
            value = prop[MAP_PARAMETER_VALUE]
            if value != "":
                piid = prop["piid"]
                if piid == PIID(DreameVacuumProperty.OBJECT_NAME):
                    has_map = True
                    object_name = value
                elif piid == PIID(DreameVacuumProperty.MAP_DATA):
                    has_map = True
                    raw_map_data = value
                elif piid == PIID(DreameVacuumProperty.OLD_MAP_DATA):
                    if not has_map:
                        values = value.split(",")
                        if values[0] == "0":
                            raw_map_data = values[1]
                        else:
                            object_name = values[1]
                            if len(values) == 3:
                                object_name = f"{object_name},{values[2]}"

        if has_map:
            self._map_request_time = None

        if object_name or raw_map_data:
            partial_map_data = None
            timestamp = int(time.time() * 1000)

            if raw_map_data:
                partial_map_data = [self._decode_map_partial(raw_map_data, timestamp)]
            self._add_cloud_map_data(partial_map_data, object_name, timestamp)

    def get_map(self, map_index: int = 0) -> MapData | None:
        if map_index:
            if map_index <= len(self._map_list):
                return self._saved_map_data[self._map_list[map_index - 1]]
            return None
        return self._map_data

    def get_aes_key(self):
        if self._aes_key is None:
            private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
            response = self._protocol.action(
                14,
                1,
                [
                    {
                        "piid": 5,
                        "value": base64.b64encode(
                            private_key.public_key().public_bytes(
                                encoding=serialization.Encoding.DER,
                                format=serialization.PublicFormat.SubjectPublicKeyInfo,
                            )
                        ).decode(),
                    }
                ],
            )

            if not (
                response is None or "out" not in response or not response["out"] or "value" not in response["out"][0]
            ):
                response = response["out"][0]["value"].replace("-", "+").replace("_", "/")
                while len(response) % 4 != 0:
                    response += "="

                self._aes_key = base64.b64decode(
                    base64.b64encode(
                        serialization.load_der_private_key(
                            private_key.private_bytes(
                                encoding=serialization.Encoding.DER,
                                format=serialization.PrivateFormat.PKCS8,
                                encryption_algorithm=serialization.NoEncryption(),
                            ),
                            password=None,
                        ).decrypt(
                            base64.b64decode(response),
                            asym_padding.OAEP(
                                mgf=asym_padding.MGF1(algorithm=hashes.SHA1()),
                                algorithm=hashes.SHA1(),
                                label=None,
                            ),
                        )
                    ).decode()
                ).decode("utf-8")
        return self._aes_key

    def get_obstacle_image(self, map_data, index):
        index = str(index)
        if map_data and map_data.obstacles and index in map_data.obstacles:
            obstacle = map_data.obstacles[index]
            if (
                obstacle.file_name
                and len(obstacle.file_name) > 1
                and (obstacle.picture_status is None or obstacle.picture_status.value == 2)
            ):
                try:
                    if self._protocol.dreame_cloud and self._capability.local_storage:
                        _LOGGER.info(
                            "Obstacle image local file name: %s",
                            obstacle.file_name,
                        )
                        file = self._protocol.cloud.get_device_file(obstacle.file_name, "obstacle")
                        if file:
                            key = self.get_aes_key()
                            if not key:
                                _LOGGER.warning("Failed to get obstacle aes key! (%s)", obstacle.file_name)
                                return None, None

                            cipher = Cipher(
                                algorithms.AES(hashlib.md5(key.encode("utf-8")).hexdigest().encode("utf-8")),
                                modes.CBC(base64.b64decode(self._capability.resources_aes_iv)),
                            )
                            decryptor = cipher.decryptor()
                            unpadder = padding.PKCS7(128).unpadder()
                            return (
                                (unpadder.update(decryptor.update(file) + decryptor.finalize()) + unpadder.finalize()),
                                obstacle,
                            )
                    elif obstacle.key and len(obstacle.key) > 1:
                        object_name = (
                            f"{obstacle.file_name}-{obstacle.object_id}"
                            if self._protocol.dreame_cloud and obstacle.object_id
                            else obstacle.file_name
                        )
                        _LOGGER.info(
                            "Obstacle image object name: %s",
                            object_name,
                        )
                        response = self._get_file_url(object_name, False)
                        if response:
                            response = self._protocol.cloud.get_file(response)
                            if response:
                                response = base64.b64encode(response).decode("utf-8")

                                cipher = Cipher(
                                    algorithms.AES(
                                        bytearray.fromhex(hashlib.md5((obstacle.key).encode("utf-8")).hexdigest())
                                    ),
                                    modes.ECB(),
                                    backend=default_backend(),
                                )
                                decryptor = cipher.decryptor()
                                unpadder = padding.PKCS7(128).unpadder()
                                return (
                                    (
                                        unpadder.update(
                                            decryptor.update(base64.b64decode(response[response.find(",") + 1 :]))
                                            + decryptor.finalize()
                                        )
                                        + unpadder.finalize()
                                    ),
                                    obstacle,
                                )
                except:
                    _LOGGER.warning(
                        "Obstacle (%s) image decryption failed: %s",
                        index,
                        traceback.format_exc(),
                    )
        return (None, None)

    def get_history_map(self, object_name, key=None):
        if object_name and len(object_name):
            try:
                _LOGGER.info(
                    "History map object name: %s",
                    object_name,
                )
                response = self._get_file_url(object_name, self._protocol.cloud.dreame_cloud)
                if response:
                    response = self._protocol.cloud.get_file(response, retry_count=0)
                    if response:
                        map_data, saved_map_data = DreameVacuumMapDecoder.decode_map(
                            response.decode(), self.map_version, None, self._aes_iv, key
                        )
                        if saved_map_data:
                            DreameVacuumMapDecoder.set_capability(saved_map_data, self._capability)
                            DreameVacuumMapDecoder.set_floor_material(saved_map_data, self._capability)
                        if map_data:
                            DreameVacuumMapDecoder.set_capability(map_data, self._capability)
                            DreameVacuumMapDecoder.set_segment_cleanset(
                                map_data,
                                map_data.cleanset,
                                self._capability,
                            )
                            DreameVacuumMapDecoder.set_carpet_cleanset(
                                map_data, map_data.carpet_cleanset, self._capability
                            )
                            DreameVacuumMapDecoder.set_floor_material(map_data, self._capability, saved_map_data)

                            map_data.history_map = True
                            return map_data
                    else:
                        self._file_urls.pop(object_name, None)
            except Exception as ex:
                _LOGGER.warning(
                    "History map decoding failed: %s",
                    traceback.format_exc(),
                )

    def get_recovery_map(self, map_id, index):
        if map_id in self._map_list:
            recovery_map_list = self._saved_map_data[map_id].recovery_map_list
            index = int(index) - 1
            if recovery_map_list and len(recovery_map_list) > index:
                if recovery_map_list[index].map_data is None:
                    if (
                        recovery_map_list[index].raw_map is None
                        and recovery_map_list[index].map_object_name is not None
                    ):
                        try:
                            response = self._get_interim_file_data(recovery_map_list[index].map_object_name)
                            if response:
                                recovery_map_list[index].raw_map = response.decode()
                        except Exception as ex:
                            _LOGGER.warning("Get Recovery Map Object failed: %s", ex)
                            return

                    if recovery_map_list[index].raw_map:
                        saved_map_data = DreameVacuumMapDecoder.decode_saved_map(
                            recovery_map_list[index].raw_map,
                            self.map_version,
                            self._saved_map_data[map_id].rotation,
                            self._aes_iv,
                        )
                        if saved_map_data:
                            DreameVacuumMapDecoder.set_capability(saved_map_data, self._capability)
                            DreameVacuumMapDecoder.set_segment_cleanset(
                                saved_map_data,
                                saved_map_data.cleanset,
                                self._capability,
                            )
                            DreameVacuumMapDecoder.set_floor_material(saved_map_data, self._capability)

                            if saved_map_data.saved_furnitures is not None:
                                saved_map_data.furnitures = saved_map_data.saved_furnitures

                            DreameVacuumMapDecoder.set_carpet_cleanset(
                                saved_map_data, saved_map_data.carpet_cleanset, self._capability
                            )

                            saved_map_data.last_updated = recovery_map_list[index].date.timestamp()
                            saved_map_data.recovery_map_type = recovery_map_list[index].map_type
                            saved_map_data.recovery_map = True

                            recovery_map_list[index].map_data = saved_map_data
                return recovery_map_list[index].map_data

    def get_recovery_map_file(self, map_id, index):
        if map_id in self._map_list:
            recovery_map_list = self._saved_map_data[map_id].recovery_map_list
            index = int(index) - 1
            if recovery_map_list and len(recovery_map_list) > index:
                object_name = recovery_map_list[index].object_name
                if object_name and object_name != "":
                    _LOGGER.info(
                        "Recovery map object name: %s",
                        object_name,
                    )
                    map_url = self._get_file_url(
                        object_name,
                        not (object_name.endswith("mb.tbz2") and not self._protocol.dreame_cloud),
                    )
                    _LOGGER.info("Recovery map file url: %s = %s", object_name, map_url)
                    if map_url:
                        return (
                            self._protocol.cloud.get_file(map_url),
                            map_url,
                            object_name,
                        )
        return None, None, None

    def listen(self, change_callback, update_callback) -> None:
        self._change_callback = change_callback
        self._update_callback = update_callback

    def listen_error(self, callback) -> None:
        self._error_callback = callback

    def disconnect(self) -> None:
        """Disconnect from map and cancel timers"""
        self._disconnected = True
        self.schedule_update(-1)
        if self._update_timer is not None:
            self._update_timer.stop()
        self._update_callback = None
        self._change_callback = None
        self._error_callback = None

    def schedule_update(self, wait: float = None) -> None:
        if wait == None:
            wait = self._update_interval
        if self._update_timer is not None:
            self._update_timer.cancel()
        if wait >= 0 and not self._disconnected:
            if self._update_timer is None:
                self._update_timer = RestartableTimer()
            self._update_timer.start(wait, self._update_task)

    def note_live_map_demand(self) -> None:
        with self._update_lock:
            self._live_map_demand_until = time.monotonic() + 5

    def _take_live_map_demand(self) -> bool:
        timestamp = self._current_timestamp_ms
        if timestamp and 0 <= time.time() - timestamp / 1000 < 6:
            return False
        with self._update_lock:
            now = time.monotonic()
            if self._disconnected or now >= self._live_map_demand_until or now < self._next_live_map_request:
                return False
            self._live_map_demand_until = 0.0
            self._next_live_map_request = now + 6
            return True

    def update(self) -> None:
        with self._update_lock:
            if self._update_running:
                return
            self._update_running = True

        _LOGGER.debug("Map update: %s", self._update_interval)
        try:
            if (self._map_list_source[0] and self._need_map_list_request is None) or (
                self._need_map_list_request and not self._device_running
            ):
                self.request_map_list()

            if self._recovery_map_list_object_name and self._need_recovery_map_list_request:
                self.request_recovery_map_list()

            if self._need_new_map:
                self.request_new_map()
                self._need_new_map = False
            elif self._protocol.dreame_cloud and self._take_live_map_demand():
                # Read the latest frame instead of repeating a stale request timestamp.
                self._request_current_map(schedule_retry=False)
            elif self._map_request_time is not None or self._need_map_request:
                self._updated_frame_id = None
                self._map_request_count = self._map_request_count + 1
                if self._map_request_count >= 6:
                    self._map_request_time = None
                    self._need_map_request = False
                elif (
                    not self._request_current_map(self._map_request_time)
                    and self._protocol.dreame_cloud
                    and self._map_request_count == 2
                    and self._map_data is None
                ):
                    object_name_result = self._protocol.cloud.get_properties(DIID(DreameVacuumProperty.OBJECT_NAME))
                    if object_name_result and MAP_PARAMETER_VALUE in object_name_result[0]:
                        self._add_cloud_map_data(
                            None, object_name_result[0][MAP_PARAMETER_VALUE], object_name_result[0].get("updateDate")
                        )
            elif not self._protocol.dreame_cloud:
                if self._map_data is None or (
                    self._device_running
                    and (time.time() - (self._current_timestamp_ms / 1000.0) > 15 or self._map_data.empty_map)
                ):
                    self._updated_frame_id = None
                    if self._map_data and not self._map_data.empty_map:
                        _LOGGER.info(
                            "Need map request: %.2f",
                            time.time() - (self._current_timestamp_ms / 1000.0),
                        )
                    if self._protocol.cloud.logged_in:
                        self._request_current_map()
                elif not self._request_map_from_cloud() and self._device_running:
                    _LOGGER.debug("No new map data received, retrying")
                    sleep(1)
                    if not self._request_map_from_cloud():
                        self.schedule_update(1)
                        _LOGGER.debug("No new map data received on second try")
            elif self._protocol.cloud.connected:
                if not self._connected:
                    self._connected = True
                    self._map_data_changed()

                if self._map_data is None or (
                    self._device_running
                    and (
                        (self._map_data.last_updated and time.time() - (self._map_data.last_updated) > 60)
                        or self._map_data.empty_map
                    )
                ):
                    if self._map_data and not self._map_data.empty_map:
                        _LOGGER.info(
                            "Need map request: %.2f",
                            time.time() - (self._map_data.last_updated),
                        )
                        self._request_map()
                    else:
                        self._request_current_map()
            elif self._connected:
                self._connected = False
                self._map_data_changed()

            if not self._available and self._connected:
                self._available = True
                self._map_data_changed()
        except Exception as ex:
            if self._available:
                _LOGGER.warning("Map update Failed: %s", traceback.format_exc())
                self._available = False
                if self._error_callback:
                    self._error_callback(DeviceException(ex))
        finally:
            self._ready = True
            with self._update_lock:
                self._update_running = False

    def set_capability(self, capability) -> None:
        self._capability = capability
        self._aes_iv = capability.key

    def set_update_interval(self, update_interval: float) -> None:
        if self._update_interval != update_interval:
            self._update_interval = update_interval
            self.schedule_update()

    def set_device_running(self, running: bool, docked: bool) -> None:
        if self._device_running != running:
            self._device_running = running

        if self._device_docked != docked:
            if docked:
                if self.map_version != 0:
                    self._request_map()
                elif self._map_data and self._map_data.saved_map_status == 1:
                    saved_map_data = self.selected_map
                    self._map_data.segments = copy.deepcopy(saved_map_data.segments)
                    self._map_data.data = copy.deepcopy(saved_map_data.data)
                    self._map_data.pixel_type = copy.deepcopy(saved_map_data.pixel_type)
                    self._map_data.dimensions = copy.deepcopy(saved_map_data.dimensions)
                    self._map_data.charger_position = copy.deepcopy(saved_map_data.charger_position)
                    self._map_data.no_go_areas = saved_map_data.no_go_areas
                    self._map_data.no_mopping_areas = saved_map_data.no_mopping_areas
                    self._map_data.virtual_walls = saved_map_data.virtual_walls
                    self._map_data.robot_position = self._map_data.charger_position
                    self._map_data.docked = True
                    # self._map_data.restored_map = True
                    self._map_data.path = None
                    self._map_data.need_optimization = False
                    self._map_data.saved_map_status = 2
                    self._map_data.last_updated = time.time()
                    self._map_data.optimized_pixel_type = None
                    self._map_data.optimized_charger_position = None
                    self._map_data_changed()

            self._device_docked = docked
            self.schedule_update(2)

    def set_device_docked(self, device_docked: bool) -> None:
        if self._device_docked != device_docked:
            self.schedule_update(2)
        self._device_docked = device_docked

    def request_new_map(self) -> None:
        if (
            self._new_map_request_time
            and time.time() - self._new_map_request_time < 10
            and not self._protocol.dreame_cloud
        ):
            if time.time() - self._new_map_request_time > 3:
                self._new_map_request_time = time.time()
                self._request_map_from_cloud()
            return

        self._new_map_request_time = time.time()
        if self._map_data is None:
            return self._request_i_map()
        else:
            result = self._request_map()
            if result and result[MAP_PARAMETER_CODE] == 0 and not self._protocol.dreame_cloud:
                self._request_map_from_cloud()

    def request_next_map(self, request_new=False, delay=2) -> None:
        self._map_request_count = 0
        self._need_map_request = True
        if request_new:
            self._need_new_map = True
        self.schedule_update(delay)

    def request_next_map_list(self) -> None:
        self._need_map_list_request = True

    def request_next_recovery_map_list(self) -> None:
        self._need_recovery_map_list_request = True

    def set_map_list_object_name(self, object_name: str, md5: str = None) -> bool:
        source = (object_name, md5)
        with self._map_list_lock:
            if not object_name or source == self._map_list_source:
                return False
            existing = self._map_list_source[0]
            self._map_list_source = source
            self._validated_map_list = None
            if existing:
                self.request_next_map_list()
        if existing and not self._device_running:
            self.schedule_update(3)
        return True

    def command_map_matches(self, saved, source) -> bool:
        with self._map_list_lock:
            if (self._validated_map_list is None or self._map_list_source != source
                    or self._need_map_list_request or self._update_running or saved is not self.selected_map):
                return False
            return self._validated_map_list == (source, _command_map_signature(saved))

    def set_recovery_map_list_object_name(self, object_name: str) -> bool:
        if object_name and object_name != "":
            if self._recovery_map_list_object_name != object_name:
                self._recovery_map_list_object_name = object_name
                self._need_recovery_map_list_request = True
                return True
        return False

    def request_map_list(self) -> None:
        with self._map_list_lock:
            source = self._map_list_source
            object_name, checksum = source
            if not object_name or not self._protocol.cloud.logged_in:
                return
            fetch = self._map_list_fetch = object()
            self._validated_map_list = None
            self._need_map_list_request = True

        try:
            response = self._get_interim_file_data(object_name)
            if not response:
                return
            map_info = json.loads(response)
            entries = map_info[MAP_PARAMETER_MAPSTR]
            selected_map_id = map_info[MAP_PARAMETER_CURR_ID]
            if type(entries) is not list or type(selected_map_id) is not int:
                return
            map_list = {}
            selected_inline = False
            for entry in entries:
                raw_map = entry.get(MAP_PARAMETER_MAP)
                inline = bool(raw_map)
                if not raw_map and map_info.get("server") == 1 and entry.get("rismobj"):
                    stored = self._get_interim_file_data(entry["rismobj"])
                    raw_map = stored.decode() if stored else None
                if not isinstance(raw_map, str) or not raw_map:
                    return
                saved = DreameVacuumMapDecoder.decode_saved_map(
                    raw_map, self.map_version, int(entry.get(MAP_PARAMETER_ANGLE) or 0), self._aes_iv,
                )
                if saved is None or type(saved.map_id) is not int or saved.map_id in map_list:
                    return
                saved.object_name = entry.get("mapobj")
                name = entry.get(MAP_PARAMETER_NAME)
                if name:
                    saved.custom_name = saved.map_name = name
                map_list[saved.map_id] = saved
                if saved.map_id == selected_map_id:
                    selected_inline = inline
        except Exception as exc:
            _LOGGER.warning("Get Map List failed (%s)", type(exc).__name__)
            return

        signature = _command_map_signature(map_list.get(selected_map_id))
        checksum_matches = (isinstance(checksum, str)
                            and hashlib.md5(response, usedforsecurity=False).hexdigest() == checksum.lower())
        with self._map_list_lock:
            if source is not self._map_list_source or fetch is not self._map_list_fetch:
                return
            if map_list and signature is None:
                return
            self._apply_map_list(map_list, selected_map_id)
            if source is not self._map_list_source or fetch is not self._map_list_fetch:
                return
            self._need_map_list_request = bool(checksum and not checksum_matches)
            # The list checksum also covers the selected plan when its bytes are inline.
            if (checksum_matches and selected_inline and signature is not None and self._selected_map_id == selected_map_id
                    and signature == _command_map_signature(self._saved_map_data.get(selected_map_id))):
                self._validated_map_list = (source, signature)

    def _apply_map_list(self, map_list, selected_map_id):
        changed = False
        now = time.time()
        for map_id, saved_map_data in sorted(map_list.items()):
            DreameVacuumMapDecoder.set_capability(saved_map_data, self._capability)
            DreameVacuumMapDecoder.set_floor_material(saved_map_data, self._capability)

            if saved_map_data.saved_furnitures is not None:
                saved_map_data.furnitures = saved_map_data.saved_furnitures

            if saved_map_data.version > 0 and saved_map_data.version < 3:
                if self._selected_map_id == map_id and self._map_data:
                    saved_map_data.cleanset = self._map_data.cleanset
                    saved_map_data.carpet_cleanset = self._map_data.carpet_cleanset
                elif map_id in self._saved_map_data:
                    saved_map_data.cleanset = self._saved_map_data[map_id].cleanset
                    saved_map_data.carpet_cleanset = self._saved_map_data[map_id].carpet_cleanset

            DreameVacuumMapDecoder.set_carpet_cleanset(
                saved_map_data, saved_map_data.carpet_cleanset, self._capability
            )
            DreameVacuumMapDecoder.set_segment_cleanset(
                saved_map_data,
                saved_map_data.cleanset,
                self._capability,
            )

            if map_id in self._saved_map_data:
                previous = self._saved_map_data[map_id]
                layout_changed = _saved_layout_changed(previous, saved_map_data)
                if previous != saved_map_data or previous.dirty or layout_changed:
                    _LOGGER.info("Saved map changed: %s", map_id)
                    self._saved_map_data[map_id].dirty = False
                    changed = True
                    saved_map_data.last_updated = now
                    if saved_map_data.wifi_map_data:
                        saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                    saved_map_data.recovery_map_list = self._saved_map_data[map_id].recovery_map_list
                    if (
                        self._map_data is None
                        or self._selected_map_id != map_id
                        or saved_map_data.version == 3
                        or layout_changed
                    ):
                        self._saved_map_data[map_id] = saved_map_data
                    else:
                        self._saved_map_data[map_id].custom_name = saved_map_data.custom_name
                        self._saved_map_data[map_id].rotation = saved_map_data.rotation
                        self._saved_map_data[map_id].last_updated = time.time()

                    if (
                        self._map_data
                        and map_id == self._map_data.saved_map_id
                        and self._map_data.floor_material != saved_map_data.floor_material
                    ):
                        DreameVacuumMapDecoder.set_floor_material(
                            self._map_data, self._capability, saved_map_data
                        )
                        self._map_data.last_updated = self._saved_map_data[map_id].last_updated
                        self._map_data_changed()
                else:
                    _LOGGER.info("Saved map not changed: %s", map_id)
            else:
                saved_map_data.last_updated = now
                if saved_map_data.wifi_map_data:
                    saved_map_data.wifi_map_data.last_updated = saved_map_data.last_updated
                self._saved_map_data[map_id] = saved_map_data
                _LOGGER.info("Add saved map: %s", map_id)
                changed = True

        current_map_list = self._saved_map_data.copy()
        for map_id in current_map_list.keys():
            if map_id not in map_list and map_id != selected_map_id:
                del self._saved_map_data[map_id]
                changed = True

        if selected_map_id in self._saved_map_data and self._selected_map_id != selected_map_id:
            self._selected_map_id = selected_map_id
            changed = True

        if changed == True:
            self._refresh_map_list()
            if self._map_data:
                self._map_data_changed(True)
            self.request_next_recovery_map_list()

    def request_recovery_map_list(self) -> None:
        if self._recovery_map_list_object_name:
            if self.map_version == 0:
                self._need_recovery_map_list_request = False
                return
            _LOGGER.info("Get Recovery Map List: %s", self._recovery_map_list_object_name)
            response = self._get_file_url(self._recovery_map_list_object_name)
            if response:
                self._need_recovery_map_list_request = False
                response = self._protocol.cloud.get_file(response)
                if response:
                    try:
                        recovery_map_list = json.loads(response.decode())
                    except:
                        _LOGGER.warning("Get Recovery Map List json parse failed")
                        return

                    changed = False
                    for recovery_map in recovery_map_list:
                        map_id = recovery_map["id"]
                        if map_id in self._map_list:
                            recovery_map_list = []
                            map_info_list = recovery_map["info"]
                            for map_info in map_info_list:
                                recovery_map_list.append(
                                    RecoveryMapInfo(
                                        map_id,
                                        map_info.get("time"),
                                        map_info.get("thb"),
                                        map_info.get("rismobj"),
                                        map_info.get("objname"),
                                        map_info.get("first", -1),
                                    )
                                )
                            if len(recovery_map_list) > 2:
                                recovery_map_list.sort(
                                    key=cmp_to_key(
                                        lambda a, b: (
                                            int(a.map_type) - int(b.map_type)
                                            if a.map_type is RecoveryMapType.EDITED
                                            and b.map_type is RecoveryMapType.BACKUP
                                            else 1 if a.date > b.date else -1
                                        )
                                    )
                                )

                            if (
                                not self._saved_map_data[map_id].recovery_map_list
                                or len(self._saved_map_data[map_id].recovery_map_list) != len(recovery_map_list)
                                or not all(
                                    self._saved_map_data[map_id].recovery_map_list[i].__dict__
                                    == recovery_map_list[i].__dict__
                                    for i in range(len(self._saved_map_data[map_id].recovery_map_list))
                                )
                            ):
                                self._saved_map_data[map_id].last_updated = time.time()
                                self._saved_map_data[map_id].recovery_map_list = recovery_map_list
                                _LOGGER.info("Saved recovery map list changed: %s", map_id)
                                changed = True

                    if changed:
                        self._refresh_recovery_map_list()
                        if self._connected:
                            self._map_data_changed(True)
                    else:
                        _LOGGER.info("Saved recovery map list not changed: %s", map_id)

    @property
    def _request_i_map_available(self) -> bool:
        return bool(
            not (
                self._map_data is not None
                and (
                    (self._map_data.saved_map_status == 0 and not self._map_data.empty_map)
                    or self._map_data.saved_map_status == 1
                    or self._map_data.restored_map
                    or self._map_data.temporary_map
                )
            )
        )

    @property
    def map_version(self) -> int:
        if not self._capability:
            return 1
        if not self._capability.lidar_navigation:
            return 0
        version = 1
        if self._capability.cleaning_sequence_v2:
            version = 2
        if self._capability.map_v2:
            version = 3
        if self._map_data and self._map_data.version > version:
            version = self._map_data.version
        return version

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def map_list(self) -> list[int] | None:
        return self._saved_map_data.keys()

    @property
    def map_data_list(self) -> dict[int, MapData] | None:
        return self._saved_map_data

    @property
    def selected_map(self) -> MapData | None:
        if self._map_data:
            if self._selected_map_id is not None and self._selected_map_id in self._saved_map_data:
                return self._saved_map_data[self._selected_map_id]

            if self._map_list and len(self._map_list) == 1 and self._map_list[0] in self._saved_map_data:
                return self._saved_map_data[self._map_list[0]]

    @property
    def cleaning_sequence(self) -> list | None:
        selected_map = self.selected_map if self._map_data.version > 1 else None
        map_data = selected_map if selected_map is not None else self._map_data
        return (
            [
                (k)
                for k, v in sorted(
                    map_data.segments.items(),
                    key=lambda s: (s[1].order if s[1].order != None else (64 if map_data.version > 1 else 0)),
                )
                if (v.order or map_data.version > 1) and v.visibility != False
            ]
            if map_data and map_data.segments
            else []
        )


class DreameMapVacuumMapEditor:
    """Every map change must be handled on memory before actually requesting it to the device because it takes too much time to get the updated map from the cloud.
    This class handles user edits on stored map data like updating customized cleaning settings or setting active segments on segment cleaning.
    Original app has a similar class to handle the same issue (Works optimistically)"""

    def __init__(self, map_manager) -> None:
        self.map_manager: DreameMapVacuumMapManager = map_manager

    def _set_updated_frame_id(self, frame_id) -> None:
        self.map_manager._updated_frame_id = frame_id

    def _refresh_map(self, map_id: int = None) -> None:
        if map_id:
            if self._saved_map_data and map_id in self._saved_map_data:
                self._saved_map_data[map_id].last_updated = time.time()
                self.map_manager._map_data_updated()
            return
        if self._map_data is not None:
            self._map_data.last_updated = time.time()
            self.map_manager._map_data_updated()

    def refresh_map(self, map_id: int = None) -> None:
        timer = Timer(0.2, self._refresh_map, [map_id])
        timer.start()

    def set_active_areas(self, active_areas: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_cruise_points = None
            map_data.active_areas = []
            for area in active_areas:
                x_coords = sorted([area[0], area[2]])
                y_coords = sorted([area[1], area[3]])
                map_data.active_areas.append(
                    Area(
                        x_coords[0],
                        y_coords[0],
                        x_coords[1],
                        y_coords[0],
                        x_coords[1],
                        y_coords[1],
                        x_coords[0],
                        y_coords[1],
                    )
                )
            if map_data.active_areas:
                map_data.zone_cleaning = True
                map_data.dirty = True
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def set_active_segments(self, active_segments: list[int]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_segments = active_segments
            map_data.active_areas = []
            map_data.active_cruise_points = None
            map_data.dirty = True
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def set_active_points(self, active_points: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_segments = None
            map_data.active_points = []
            map_data.active_areas = []
            for point in active_points:
                map_data.active_points.append(
                    Point(
                        point[0],
                        point[1],
                    )
                )
            if map_data.active_points:
                map_data.zone_cleaning = True
            map_data.dirty = True
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def set_cruise_points(self, active_cruise_points: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.active_cruise_points = {}
            index = 0
            if active_cruise_points:
                map_data.path = None
                map_data.obstacles = None
                map_data.active_areas = None
                map_data.active_segments = None
                for point in active_cruise_points:
                    index = index + 1
                    map_data.active_cruise_points[index] = Coordinate(
                        point[0],
                        point[1],
                        bool(point[2]),
                        point[3],
                    )
            if active_cruise_points:
                map_data.dirty = True
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def clear_path(self) -> None:
        map_data = self._map_data
        if map_data is not None:
            map_data.path = None
            map_data.obstacles = None
            map_data.active_cruise_points = None
            map_data.active_areas = None
            map_data.active_segments = None
            map_data.dirty = True
            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

    def reset_map(self) -> None:
        map_data = self._map_data
        if map_data is not None and not map_data.new_map and not map_data.empty_map:
            map_data.dimensions.width = 0
            map_data.dimensions.height = 0
            map_data.segments = {}
            map_data.floor_material = None
            map_data.carpet_cleanset = None
            map_data.hidden_segments = None
            map_data.unmapped_segments = None
            map_data.path = None
            map_data.carpets = None
            map_data.detected_carpets = None
            map_data.deleted_carpets = None
            map_data.carpet_pixels = None
            map_data.obstacles = None
            map_data.empty_map = True
            map_data.saved_map_status = 0
            map_data.dirty = True
            self._set_updated_frame_id(map_data.frame_id + 1)
            self.refresh_map()

    def set_map(self, map_data) -> None:
        if map_data is not None:
            self.map_manager._map_data = map_data
            self.map_manager._updated_frame_id = None
            self.refresh_map()

    def set_rotation(self, map_id: int, rotation: int) -> None:
        if map_id in self._saved_map_data:
            self._saved_map_data[map_id].rotation = rotation
            if self.map_manager._capability.floor_material:
                DreameVacuumMapDecoder.set_floor_material(self._saved_map_data[map_id], self.map_manager._capability)
            if self._map_data is not None and map_id == self._selected_map_id:
                self._map_data.rotation = rotation
                self._map_data.dirty = True
                if self.map_manager._capability.floor_material:
                    DreameVacuumMapDecoder.set_floor_material(
                        self._map_data, self.map_manager._capability, self._saved_map_data[map_id]
                    )
                self.refresh_map()
            self.refresh_map(map_id)

    def set_map_name(self, map_id: int, name: str) -> None:
        if map_id in self._saved_map_data:
            if name and len(name):
                self._saved_map_data[map_id].custom_name = name
                self._saved_map_data[map_id].map_name = name
            else:
                self._saved_map_data[map_id].custom_name = None
                self._saved_map_data[map_id].map_name = f"Map {str(self._saved_map_data[map_id].map_index)}"
            self.refresh_map(map_id)
            self.refresh_map()

    def set_selected_map(self, map_id: int) -> None:
        if map_id != self._selected_map_id:
            self.set_current_map(map_id)

    def set_current_map(self, map_id: int) -> None:
        if map_id and map_id in self._saved_map_data:
            saved_map_data = copy.deepcopy(self._saved_map_data[map_id])
            saved_map_data.docked = self._map_data.docked
            saved_map_data.timestamp_ms = self._current_timestamp_ms
            saved_map_data.frame_id = None
            saved_map_data.map_name = None
            saved_map_data.saved_map_id = map_id
            saved_map_data.custom_name = None
            saved_map_data.saved_map = False
            saved_map_data.restored_map = True
            saved_map_data.temporary_map = False
            saved_map_data.empty_map = False
            saved_map_data.saved_map_status = 2
            DreameVacuumMapDecoder.set_segment_cleanset(
                saved_map_data,
                saved_map_data.cleanset,
                self.map_manager._capability,
            )
            DreameVacuumMapDecoder.set_carpet_cleanset(
                saved_map_data, saved_map_data.carpet_cleanset, self.map_manager._capability
            )
            DreameVacuumMapDecoder.set_floor_material(saved_map_data, self.map_manager._capability)
            self.map_manager._map_data = saved_map_data
            self.map_manager._current_frame_id = None
            self.map_manager._current_map_id = map_id
            self.map_manager._selected_map_id = map_id
            self.refresh_map()
            self.refresh_map(map_id)

    def set_carpet_area(self, carpets, deleted_carpets):
        map_data = self._map_data
        if (
            not map_data
            or not self._selected_map_id
            or self._selected_map_id not in self._saved_map_data
            or (map_data.carpets is None and map_data.deleted_carpets is None)
        ):
            return

        new_carpets = []
        carpet_types = [] if self.map_manager._capability.carpet_type else None
        carpet_tassels = [] if self.map_manager._capability.carpet_tassel else None
        if carpets:
            carpet_id = self.map_manager._capability.carpet_id
            if not carpet_id:  ## Fallback for older vacuum plugins that does not have carpet id indicator
                for map_carpet in map_data.carpets:
                    if map_carpet.id is not None:
                        carpet_id = True
                        break

            if carpet_id:
                carpet_id = 1
                for i in range(len(carpets)):
                    carpet = carpets[i]
                    if len(carpet) < 5 or carpet[4] < 0:
                        if self.map_manager._capability.carpet_material:
                            carpet_id = -1
                        else:
                            flag = True
                            found = False

                            while flag:
                                found = False
                                for map_carpet in map_data.carpets:
                                    if map_carpet.id == carpet_id:
                                        carpet_id = carpet_id + 1
                                        found = True
                                        break
                                flag = found

                        if len(carpet) < 5:
                            carpets[i].append(carpet_id)
                        else:
                            carpets[i][4] = carpet_id
                        carpet_id = carpet_id + 1
            else:
                for i in range(len(carpets)):
                    if len(carpets[i]) > 4:
                        carpets[i] = carpets[i][:4]

            for i in range(len(carpets)):
                carpet = carpets[i]
                carpet_type = None
                if carpet_types is not None:
                    carpet_type = 7
                    if len(carpet) > 6:
                        if (
                            carpet[6] == 5
                            or carpet[6] == 6
                            or (carpet[6] == 8 and self.map_manager._capability.floor_mats)
                        ):
                            carpet_type = carpet[6]
                    elif len(carpet) > 4 and carpet[4] >= 0:
                        for map_carpet in map_data.carpets:
                            if map_carpet.id == carpet[4]:
                                carpet_type = map_carpet.carpet_type
                                break

                    carpet_types.append([carpet[4], carpet_type])

                carpet_tassel = None
                hidden = None
                if carpet_tassels is not None:
                    hidden = 0
                    carpet_tassel = [0, 0, 0, 0, 0]
                    if len(carpet) > 4 and carpet[4] >= 0:
                        for map_carpet in map_data.carpets:
                            if map_carpet.id == carpet[4]:
                                carpet_tassel = map_carpet.tassel
                                hidden = map_carpet.hidden
                                break
                    if len(carpet) > 7 and carpet[7] and len(carpet[7]) == 5:
                        carpet_tassel = carpet[7]

                    carpet_tassels.append([carpet[4], carpet_tassel])

                new_carpets.append(
                    Carpet(
                        carpet[4] if len(carpet) > 4 else None,
                        carpet[0],
                        carpet[1],
                        carpet[2],
                        carpet[1],
                        carpet[2],
                        carpet[3],
                        carpet[0],
                        carpet[3],
                        carpet[5] if len(carpet) > 5 else None,
                        carpet_type,
                        None,
                        None,
                        None,
                        hidden,
                        carpet_tassel,
                    )
                )

                if len(carpet) > 6:  ## Remove carpet type from array to be sent to device
                    carpets[i] = carpet[:6]

        map_data.carpets = new_carpets
        DreameVacuumMapDecoder.set_carpet_cleanset(map_data, map_data.carpet_cleanset, self.map_manager._capability)

        new_deleted_carpets = []
        if deleted_carpets:
            for carpet in deleted_carpets:
                x_coords = sorted([carpet[0], carpet[2]])
                y_coords = sorted([carpet[1], carpet[3]])
                new_deleted_carpets.append(
                    Carpet(
                        None,
                        x_coords[0],
                        y_coords[0],
                        x_coords[1],
                        y_coords[0],
                        x_coords[1],
                        y_coords[1],
                        x_coords[0],
                        y_coords[1],
                    )
                )
        map_data.deleted_carpets = new_deleted_carpets

        self._saved_map_data[self._selected_map_id].carpets = map_data.carpets
        self._saved_map_data[self._selected_map_id].deleted_carpets = map_data.deleted_carpets
        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map(self._selected_map_id)
        self.refresh_map()
        return carpets, deleted_carpets, carpet_types, carpet_tassels

    def set_carpet_type(self, carpet_types) -> dict[str, list[int]]:
        map_data = self._map_data
        if (
            not map_data
            or not self._selected_map_id
            or not (map_data.detected_carpets is not None or map_data.carpets is not None)
        ):
            return

        def set_carpet_cleaning(carpet, carpet_type, object_type):
            if map_data.carpet_cleanset is not None and not (
                carpet.carpet_cleaning == 0 or carpet.carpet_cleaning is None or carpet.carpet_cleaning > 0
            ):
                if carpet_type == 7 or not self.map_manager._capability.mop_pad_unmounting:
                    carpet.carpet_cleaning = 2
                elif carpet_type == 5:
                    carpet.carpet_cleaning = 1
                elif carpet_type == 6:
                    carpet.carpet_cleaning = 3
                elif carpet_type == 8:
                    carpet.carpet_cleaning = 7

                for cleanset in map_data.carpet_cleanset:
                    if cleanset[0] == object_type and cleanset[1] == carpet.id:
                        cleanset[2] = carpet.carpet_cleaning

        carpet_type = {}
        key = "origincpt"
        for carpet in map_data.detected_carpets:
            if key in carpet_types:
                for selected_carpet in carpet_types[key]:
                    if selected_carpet[0] == carpet.id:
                        if carpet.carpet_type != selected_carpet[1]:
                            carpet.carpet_type = selected_carpet[1]
                            set_carpet_cleaning(carpet, carpet.carpet_type, 0)
                        break
            if key not in carpet_type:
                carpet_type[key] = []
            carpet_type[key].append([carpet.id, carpet.carpet_type if carpet.carpet_type else 7])

        key = "addcpt"
        for carpet in map_data.carpets:
            if key in carpet_types:
                for selected_carpet in carpet_types[key]:
                    if selected_carpet[0] == carpet.id:
                        if carpet.carpet_type != selected_carpet[1]:
                            carpet.carpet_type = selected_carpet[1]
                            set_carpet_cleaning(carpet, carpet.carpet_type, 1)
                        break
            if key not in carpet_type:
                carpet_type[key] = []
            carpet_type[key].append([carpet.id, carpet.carpet_type if carpet.carpet_type else 7])

        if self.map_manager._capability.carpet_material:
            key = "roomcpt"
            for k, v in map_data.segments.items():
                if key in carpet_types:
                    for selected_carpet in carpet_types[key]:
                        if selected_carpet[0] == k:
                            if v.floor_material != selected_carpet[1]:
                                v.floor_material = selected_carpet[1]
                                set_carpet_cleaning(v, v.floor_material, 2)
                            break
                if key not in carpet_type:
                    carpet_type[key] = []
                carpet_type[key].append([k, v.floor_material if v.floor_material else 0])

        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            if map_data.carpet_cleanset is not None:
                self._saved_map_data[self._selected_map_id].carpet_cleanset = map_data.carpet_cleanset.copy()
                DreameVacuumMapDecoder.set_carpet_cleanset(
                    self._saved_map_data[self._selected_map_id],
                    self._saved_map_data[self._selected_map_id].carpet_cleanset,
                    self.map_manager._capability,
                )
            self.refresh_map(self._selected_map_id)

        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map()
        return carpet_type

    def set_virtual_thresholds(self, virtual_thresholds) -> None:
        map_data = self._map_data
        if (
            not map_data
            or not self._selected_map_id
            or not (map_data.virtual_thresholds is not None or map_data.passable_thresholds is not None)
        ):
            return

        thresholds = []
        if virtual_thresholds:
            for line in virtual_thresholds:
                thresholds.append(
                    Line(
                        line[0],
                        line[1],
                        line[2],
                        line[3],
                    )
                )

        if map_data.passable_thresholds is not None:
            map_data.passable_thresholds = thresholds
        else:
            map_data.virtual_thresholds = thresholds

        self._set_updated_frame_id(map_data.frame_id)
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            self._saved_map_data[self._selected_map_id].virtual_thresholds = map_data.virtual_thresholds
            self._saved_map_data[self._selected_map_id].passable_thresholds = map_data.passable_thresholds
            self.refresh_map(self._selected_map_id)
        self.refresh_map()

    def set_thresholds(self, passable_thresholds, impassable_thresholds, ramps) -> None:
        map_data = self._map_data
        if not map_data or not self._selected_map_id or map_data.passable_thresholds is None:
            return

        if passable_thresholds:
            map_data.passable_thresholds = [
                Line(
                    wall[0],
                    wall[1],
                    wall[2],
                    wall[3],
                )
                for wall in passable_thresholds
            ]
        else:
            map_data.passable_thresholds = []

        if impassable_thresholds:
            map_data.impassable_thresholds = [
                Line(
                    wall[0],
                    wall[1],
                    wall[2],
                    wall[3],
                )
                for wall in impassable_thresholds
            ]
        else:
            map_data.impassable_thresholds = []

        map_data.ramps = []
        if ramps:
            for area in ramps:
                x_coords = sorted([area[0], area[2]])
                y_coords = sorted([area[1], area[3]])
                map_data.ramps.append(
                    Area(
                        x_coords[0],
                        y_coords[0],
                        x_coords[1],
                        y_coords[0],
                        x_coords[1],
                        y_coords[1],
                        x_coords[0],
                        y_coords[1],
                        area[4],
                    )
                )

        self._set_updated_frame_id(map_data.frame_id)
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            self._saved_map_data[self._selected_map_id].passable_thresholds = map_data.passable_thresholds
            self._saved_map_data[self._selected_map_id].impassable_thresholds = map_data.impassable_thresholds
            self._saved_map_data[self._selected_map_id].ramps = map_data.ramps
            self.refresh_map(self._selected_map_id)
        self.refresh_map()

    def set_furnitures(self, furnitures) -> dict[str, list[int | float]]:
        map_data = self._map_data
        if not map_data or not self._selected_map_id or map_data.furnitures is None or not map_data.furniture_version:
            return

        if map_data.furniture_version >= 2:
            furniture_id = 0
            for furniture in furnitures:
                if furniture[0] < 0:
                    flag = True
                    found = False

                    while flag:
                        found = False
                        for map_furniture in map_data.furnitures.values():
                            if map_furniture.furniture_id == furniture_id:
                                furniture_id = furniture_id + 1
                                found = True
                                break
                        flag = found

                    furniture[0] = furniture_id
                    furniture_id = furniture_id + 1

        map_data.furnitures = {}
        index = 0
        for furniture in furnitures:
            if (
                furniture[1] not in FurnitureType._value2member_map_
                or (
                    map_data.furniture_version >= 2
                    and not self.map_manager._capability.extended_furnitures
                    and ((furniture[1] > 13 and furniture[1] != 25) or furniture[1] == 8)
                )
                or (map_data.furniture_version < 2 and furniture[1] >= 10)
                or (not self.map_manager._capability.pet_furniture and furniture[1] >= 10 and furniture[1] <= 13)
            ):
                continue

            map_data.furnitures[index] = Furniture(
                furniture_id=furniture[0] if map_data.furniture_version >= 2 else None,
                type=FurnitureType(furniture[1]),
                segment_id=furniture[2],
                width=furniture[3],
                height=furniture[4],
                x0=int(furniture[5] - (furniture[3] / 2)),
                y0=int(furniture[6] - (furniture[4] / 2)),
                x=furniture[5],
                y=furniture[6],
                angle=furniture[7],
                scale=furniture[8],
                edit_type=furniture[9],
                modified=furniture[10],
            )
            index = index + 1
        if map_data.furniture_version >= 2:
            return {
                "mapfurniture_v2": [
                    [
                        furniture.furniture_id,
                        25 if furniture.type.value == 8 else 8 if furniture.type.value == 25 else furniture.type.value,
                        furniture.segment_id,
                        furniture.width,
                        furniture.height,
                        0,
                        furniture.x,
                        furniture.y,
                        0,
                        furniture.angle,
                        0,
                        0,
                        furniture.scale,
                        furniture.edit_type,
                        furniture.modified,
                    ]
                    for furniture in map_data.furnitures.values()
                ]
            }
        return {
            "mapfurniture": [
                [
                    furniture.x,
                    furniture.y,
                    furniture.type.value,
                    furniture.edit_type,
                    furniture.x0,
                    furniture.y0,
                    furniture.width,
                    furniture.height,
                    furniture.angle,
                    furniture.scale,
                ]
                for furniture in map_data.furnitures.values()
            ],
            "mfet": [1 if furniture.modified else 0 for furniture in map_data.furnitures.values()],
        }

    def set_curtains(self, curtains) -> None:
        map_data = self._map_data
        if not map_data or not self._selected_map_id or map_data.curtains is None:
            return

        map_data.curtains = []
        for line in curtains:
            map_data.curtains.append(
                Line(
                    line[0],
                    line[1],
                    line[2],
                    line[3],
                )
            )

        self._set_updated_frame_id(map_data.frame_id)
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            self._saved_map_data[self._selected_map_id].curtains = map_data.curtains
            self.refresh_map(self._selected_map_id)
        self.refresh_map()

    def set_predefined_points(self, predefined_points) -> None:
        map_data = self._map_data
        if (
            not map_data
            or not self._selected_map_id
            or self._selected_map_id not in self._saved_map_data
            or map_data.predefined_points is None
        ):
            return

        map_data.predefined_points = []
        if predefined_points:
            for point in predefined_points:
                map_data.predefined_points.append(
                    Coordinate(
                        point[0],
                        point[1],
                        bool(point[2]),
                        point[3],
                    )
                )

        self._saved_map_data[self._selected_map_id].predefined_points = map_data.predefined_points
        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map(self._selected_map_id)
        self.refresh_map()
        return

    def set_obstacle_ignore(self, x, y, obstacle_ignored):
        map_data = self._map_data
        if not map_data or not map_data.obstacles:
            return

        for k, v in map_data.obstacles.items():
            if int(v.x) == int(x) and int(v.y) == int(y):
                map_data.obstacles[k].ignore_status = (
                    ObstacleIgnoreStatus.MANUALLY_IGNORED
                    if bool(obstacle_ignored)
                    else ObstacleIgnoreStatus.NOT_IGNORED
                )
                break

        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map()
        return

    def set_router_position(self, x, y):
        map_data = self._map_data
        if (
            not map_data
            or not self._selected_map_id
            or self._selected_map_id not in self._saved_map_data
            or map_data.router_position is None
        ):
            return

        router_position = Point(int(x), int(y))
        self._saved_map_data[self._selected_map_id].router_position = router_position
        if self._saved_map_data[self._selected_map_id].wifi_map_data:
            self._saved_map_data[self._selected_map_id].wifi_map_data.router_position = router_position
        map_data.router_position = router_position
        if map_data.wifi_map_data:
            map_data.wifi_map_data.router_position = router_position
        self._set_updated_frame_id(map_data.frame_id)
        self.refresh_map(self._selected_map_id)
        self.refresh_map()
        return

    def delete_map(self, map_id: int = None) -> None:
        map_data = self._map_data
        if map_data and map_data.temporary_map:
            return

        if map_id is None:
            self.map_manager._map_data = None
            self.map_manager._selected_map_id = None
            self.map_manager._updated_frame_id = None
            self.map_manager._saved_map_data = {}
            self.map_manager._refresh_map_list()
            self.map_manager.request_next_map_list()
        else:
            if self._saved_map_data and map_id not in self._saved_map_data:
                self.map_manager.schedule_update(2)
                return

            if map_data and self._selected_map_id == map_id:
                if len(self.map_manager._map_list) >= 2:
                    for id in reversed(self.map_manager._saved_map_data.keys()):
                        if id != map_id:
                            del self.map_manager._saved_map_data[map_id]
                            self.map_manager._refresh_map_list()
                            self.set_current_map(id)
                            break
                else:
                    del self.map_manager._saved_map_data[map_id]
                    self.map_manager._map_data = None
                    self._updated_frame_id = None
                    self.map_manager._selected_map_id = None
                    self.map_manager._refresh_map_list()
            else:
                del self.map_manager._saved_map_data[map_id]
                self.map_manager._refresh_map_list()

            self.map_manager.request_next_map_list()

    def save_temporary_map(self) -> None:
        if self._map_data and self._map_data.temporary_map:
            self._map_data.temporary_map = False
            self.refresh_map()
            self.map_manager.request_next_map_list()

    def discard_temporary_map(self) -> None:
        if self._map_data and self._map_data.temporary_map and self._selected_map_id:
            self.set_current_map(self._selected_map_id)
            self.map_manager.request_next_map_list()

    def replace_temporary_map(self, map_id: int = None) -> None:
        map_data = self._map_data
        if map_data and map_data.temporary_map:
            if not map_id and self._selected_map_id:
                map_id = self._selected_map_id

            if map_id in self._saved_map_data:
                new_map = copy.deepcopy(map_data)
                new_map.map_id = new_map.saved_map_id
                new_map.saved_map_id = None
                new_map.saved_map_status = -1
                new_map.saved_map = True
                new_map.cleanset = {}
                self.map_manager._saved_map_data[new_map.map_id] = new_map
                del self.map_manager._saved_map_data[map_id]
                self.map_manager._refresh_map_list()

                map_data.saved_map_id = new_map.map_id
                if map_data.saved_map_id and map_data.saved_map_id in self._saved_map_data:
                    saved_map = self._saved_map_data[map_data.saved_map_id]
                    map_data.map_index = saved_map.map_index
                    DreameVacuumMapDecoder.set_floor_material(map_data, self.map_manager._capability, saved_map)
                else:
                    map_data.map_index = 0
                    DreameVacuumMapDecoder.set_floor_material(map_data, self.map_manager._capability)
                map_data.temporary_map = False
                map_data.saved_map = False
                map_data.saved_map_status = 0
                map_data.restored_map = True
                map_data.empty_map = False
                map_data.cleanset = {}
                DreameVacuumMapDecoder.set_segment_cleanset(
                    map_data,
                    map_data.cleanset,
                    self.map_manager._capability,
                )
                DreameVacuumMapDecoder.set_carpet_cleanset(
                    map_data, map_data.carpet_cleanset, self.map_manager._capability
                )
                self.map_manager._map_data = map_data
                self.map_manager._selected_map_id = new_map.map_id
                self.map_manager.request_next_map_list()
                self.refresh_map()

    def restore_map(self, recovery_map_info: RecoveryMapInfo) -> None:
        if recovery_map_info and recovery_map_info.map_id in self.map_manager._map_list:
            self.map_manager.schedule_update(15)

            if recovery_map_info.raw_map is None and recovery_map_info.map_object_name is not None:
                try:
                    response = self._get_interim_file_data(recovery_map_info.map_object_name)
                    if response:
                        recovery_map_info.raw_map = response.decode()
                except Exception as ex:
                    _LOGGER.warning("Get Recovery Map Object failed: %s", ex)
                    return

            recovery_map_data = (
                (
                    DreameVacuumMapDecoder.decode_saved_map(
                        recovery_map_info.raw_map,
                        self.map_manager.map_version,
                        self._saved_map_data[recovery_map_info.map_id].rotation,
                        self.map_manager._aes_iv,
                    )
                )
                if recovery_map_info.map_data is None
                else recovery_map_info.map_data
            )
            recovery_map_data.recovery_map = False
            recovery_map_data.saved_map = True
            recovery_map_data.map_name = self._saved_map_data[recovery_map_info.map_id].map_name
            recovery_map_data.custom_name = self._saved_map_data[recovery_map_info.map_id].custom_name
            recovery_map_data.rotation = self._saved_map_data[recovery_map_info.map_id].rotation
            recovery_map_data.map_index = self._saved_map_data[recovery_map_info.map_id].map_index
            recovery_map_data.recovery_map_list = self._saved_map_data[recovery_map_info.map_id].recovery_map_list
            recovery_map_data.timestamp_ms = self._saved_map_data[recovery_map_info.map_id].timestamp_ms
            recovery_map_data.last_updated = time.time()
            DreameVacuumMapDecoder.set_floor_material(recovery_map_data, self.map_manager._capability)
            if recovery_map_data.wifi_map:
                recovery_map_data.wifi_map.last_updated = time.time()

            self._saved_map_data[recovery_map_info.map_id] = recovery_map_data
            self.refresh_map(recovery_map_info.map_id)
            if recovery_map_info.map_id == self._selected_map_id:
                self.set_current_map(recovery_map_info.map_id)
                # self._map_data.restored_map = False

            self.map_manager._map_request_count = 0
            self.map_manager._map_request_time = None
            self.map_manager._need_map_request = True
            self.map_manager._need_map_list_request = True

    def set_cleaning_sequence(self, cleaning_sequence: list[int]) -> list[int] | None:
        map_data = self._map_data
        if (
            map_data
            and map_data.segments
            and not map_data.temporary_map
            and (map_data.version < 2 or map_data.saved_map_id in self._saved_map_data)
        ):
            new_cleaning_sequence = []
            if map_data.version > 1:
                map_data = self._saved_map_data[map_data.saved_map_id]

            if map_data.version > 1 and not cleaning_sequence:
                cleaning_sequence = [v.id for v in map_data.segments.values()]

            if cleaning_sequence:
                index = len(cleaning_sequence) + 1
                for k in map_data.segments.keys():
                    if k not in cleaning_sequence:
                        if map_data.version > 1:
                            map_data.segments[k].order = index
                            index = index + 1
                        else:
                            map_data.segments[k].order = 0
                            map_data.cleanset[str(k)][3] = 0

                index = 1
                for k in cleaning_sequence:
                    if int(k) in map_data.segments.keys():
                        map_data.segments[k].order = index
                        if map_data.version < 2:
                            map_data.cleanset[str(k)][3] = index
                        new_cleaning_sequence.append(k)
                        index = index + 1
            else:
                for k in map_data.segments.keys():
                    map_data.segments[k].order = 0
                    map_data.cleanset[str(k)][3] = 0

            if map_data.version > 1:
                map_data.cleaning_sequence = {
                    str(k): map_data.segments[k].order
                    for k in sorted(map_data.segments.keys(), key=lambda k: map_data.segments[k].id)
                    if map_data.segments[k].visibility != False
                }

                self._map_data.cleaning_sequence = map_data.cleaning_sequence.copy()
                index = 1
                for k in new_cleaning_sequence:
                    if int(k) in self._map_data.segments.keys():
                        self._map_data.segments[k].order = index
                        index = index + 1
                self.refresh_map(self._map_data.saved_map_id)
            elif self._saved_map_data and map_data.map_id in self._saved_map_data:
                self._saved_map_data[self._map_data.saved_map_id].cleanset = copy.deepcopy(map_data.cleanset)

            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()
            return self.map_manager.cleaning_sequence

    def set_segment_order(self, segment_id: int, order: int) -> list[int] | None:
        if order is None or (isinstance(order, str) and not order.isnumeric()):
            order = 0
        map_data = self._map_data
        if (
            map_data
            and map_data.segments
            and segment_id in map_data.segments
            and not map_data.temporary_map
            and (map_data.version < 2 or map_data.saved_map_id in self._saved_map_data)
        ):
            if map_data.version > 1:
                map_data = self._saved_map_data[map_data.saved_map_id]

            if not order and map_data.version > 1:
                order = max([v.order for v in map_data.segments.values() if v.order]) + 1

            if order > 0:
                current_order = map_data.segments[segment_id].order
                if current_order != order:
                    map_data.segments[segment_id].order = order
                    if map_data.version < 2:
                        map_data.cleanset[str(segment_id)][3] = order
                    for k, v in map_data.segments.items():
                        if k != segment_id and v.order == order:
                            map_data.segments[k].order = (
                                (len(self.map_manager.cleaning_sequence) + 1) if not current_order else current_order
                            )
            else:
                map_data.segments[segment_id].order = 0

            index = 1
            for k in self.map_manager.cleaning_sequence:
                if map_data.segments[k].order or map_data.version > 1:
                    map_data.segments[k].order = index
                    if map_data.version < 2:
                        map_data.cleanset[str(k)][3] = index
                    index = index + 1
                else:
                    map_data.cleanset[str(k)][3] = 0

            if map_data.version > 1:
                map_data.cleaning_sequence = {
                    str(k): map_data.segments[k].order
                    for k in sorted(map_data.segments.keys(), key=lambda k: map_data.segments[k].id)
                }

                self._map_data.cleaning_sequence = map_data.cleaning_sequence.copy()
                for k, v in map_data.segments.items():
                    if k in self._map_data.segments:
                        self._map_data.segments[k].order = v.order
                self.refresh_map(self._map_data.saved_map_id)

            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                if map_data.version > 1:
                    self._saved_map_data[self._selected_map_id].cleaning_sequence = map_data.cleaning_sequence.copy()
                else:
                    self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)

            self._set_updated_frame_id(map_data.frame_id)
            self.refresh_map()

            return self.map_manager.cleaning_sequence

    def set_floor_material(self, floor_material: dict[str, list], map_id) -> Any:
        if self._saved_map_data and map_id in self._saved_map_data:
            map_data = self._saved_map_data[map_id]
            if map_data.segments and not self._map_data.temporary_map:
                carpet_type = {}
                changed = []
                for segment_id in map_data.segments.keys():
                    key = str(segment_id)
                    previous = (
                        map_data.segments[segment_id].floor_material,
                        map_data.segments[segment_id].floor_material_direction,
                    )
                    if key in floor_material and len(floor_material[key]) == 2:
                        map_data.segments[segment_id].floor_material = floor_material[key][0]
                        if self.map_manager._capability.floor_direction_cleaning and floor_material[key][0] == 1:
                            map_data.segments[segment_id].floor_material_direction = floor_material[key][1]
                            map_data.segments[segment_id].floor_material_rotated_direction = (
                                floor_material[key][1]
                                if map_data.rotation == 0 or map_data.rotation == 180
                                else 90 if floor_material[key][1] == 0 else 0
                            )
                        else:
                            map_data.segments[segment_id].floor_material_direction = None
                            map_data.segments[segment_id].floor_material_rotated_direction = None
                    else:
                        map_data.segments[segment_id].floor_material = 0
                        map_data.segments[segment_id].floor_material_direction = None
                        map_data.segments[segment_id].floor_material_rotated_direction = None

                    if (
                        map_data.segments[segment_id].floor_material,
                        map_data.segments[segment_id].floor_material_direction,
                    ) != previous:
                        changed.append(key)

                    DreameVacuumMapDecoder.set_segment_floor_material(
                        map_data, map_data.segments[segment_id], map_data.floor_material, self.map_manager._capability
                    )

                if self._map_data and map_id == self._selected_map_id:
                    map_data = self._map_data
                    if map_data.segments:
                        for segment_id, segment in map_data.segments.items():
                            key = str(segment_id)
                            if key in floor_material and len(floor_material[key]) == 2:
                                material = floor_material[key][0]
                                if (
                                    self.map_manager._capability.carpet_material
                                    and segment.floor_material != material
                                    and (
                                        (material >= 5 and material <= 6)
                                        or (
                                            segment.floor_material >= 5
                                            and segment.floor_material <= 6
                                            and material == 7
                                        )
                                    )
                                ):
                                    carpet_type[segment_id] = material
                                    if map_data.carpet_cleanset is not None and not (
                                        segment.carpet_cleaning == 0
                                        or segment.carpet_cleaning is None
                                        or segment.carpet_cleaning > 0
                                    ):
                                        if material == 7 or not self.map_manager._capability.mop_pad_unmounting:
                                            segment.carpet_cleaning = 2
                                        elif material == 5:
                                            segment.carpet_cleaning = 1
                                        elif material == 6:
                                            segment.carpet_cleaning = 3

                                        for cleanset in map_data.carpet_cleanset:
                                            if cleanset[0] == 2 and cleanset[1] == segment_id:
                                                cleanset[2] = segment.carpet_cleaning

                                segment.floor_material = material
                                if self.map_manager._capability.floor_direction_cleaning and material == 1:
                                    segment.floor_material_direction = floor_material[key][1]
                                    segment.floor_material_rotated_direction = (
                                        floor_material[key][1]
                                        if map_data.rotation == 0 or map_data.rotation == 180
                                        else 90 if floor_material[key][1] == 0 else 0
                                    )
                                else:
                                    segment.floor_material_direction = None
                                    segment.floor_material_rotated_direction = None
                            else:
                                segment.floor_material = 0
                                segment.floor_material_direction = None
                                segment.floor_material_rotated_direction = None

                            DreameVacuumMapDecoder.set_segment_floor_material(
                                map_data,
                                segment,
                                map_data.floor_material,
                                self.map_manager._capability,
                            )
                        if map_data.carpet_cleanset is not None:
                            self._saved_map_data[map_id].carpet_cleanset = map_data.carpet_cleanset.copy()
                            DreameVacuumMapDecoder.set_carpet_cleanset(
                                self._saved_map_data[map_id],
                                self._saved_map_data[map_id].carpet_cleanset,
                                self.map_manager._capability,
                            )

                        self._set_updated_frame_id(map_data.frame_id)
                        self.refresh_map()

                self.refresh_map(map_id)
                material, roomcpt = self.floor_material(self._saved_map_data[map_id], carpet_type)
                return {k: v for k, v in material.items() if k in changed}, roomcpt
        return {}, None

    def set_hidden_segments(self, hidden_segments, map_id):
        if self._saved_map_data and map_id in self._saved_map_data:
            map_data = self._saved_map_data[map_id]
            if map_data.segments and not self._map_data.temporary_map:
                for segment_id, segment in map_data.segments.items():
                    segment.visibility = segment_id not in hidden_segments
                    if not segment.visibility:
                        segment.order = None

                    if self._map_data and map_id == self._selected_map_id:
                        if segment.visibility and segment_id not in self._map_data.segments:
                            self._map_data.segments[segment_id] = copy.deepcopy(segment)

                hidden_segments = [k for k, v in map_data.segments.items() if v.visibility == False]
                hidden_segments.sort()
                map_data.hidden_segments = hidden_segments
                DreameVacuumMapDecoder.set_segment_cleanset(
                    map_data,
                    map_data.cleanset,
                    self.map_manager._capability,
                )
                map_data.dirty = True

                if self._map_data and map_id == self._selected_map_id:
                    map_data = self._map_data
                    if map_data.segments:
                        map_data.hidden_segments = hidden_segments
                        for segment_id, segment in map_data.segments.items():
                            segment.visibility = segment_id not in hidden_segments
                            if not segment.visibility:
                                segment.order = None

                        DreameVacuumMapDecoder.set_segment_cleanset(
                            map_data,
                            map_data.cleanset,
                            self.map_manager._capability,
                        )
                        self._set_updated_frame_id(map_data.frame_id)
                        self.refresh_map()
                self.refresh_map(map_id)
                return hidden_segments
        return []

    def set_mop_type(self, mop_type, map_id):
        if self._saved_map_data and map_id in self._saved_map_data:
            map_data = self._saved_map_data[map_id]
            if map_data.segments and not self._map_data.temporary_map:
                for segment_id, segment in map_data.segments.items():
                    segment_id = str(segment_id)
                    if segment_id in mop_type:
                        mop = mop_type[segment_id].upper()
                        if mop == "A" or mop == "B" or mop == "C":
                            segment.mop_type = mop

                mop_types = {str(k): v.mop_type for k, v in dict(sorted(map_data.segments.items())).items()}
                map_data.mop_type = mop_types

                DreameVacuumMapDecoder.set_segment_cleanset(
                    map_data,
                    map_data.cleanset,
                    self.map_manager._capability,
                )

                if self._map_data and map_id == self._selected_map_id:
                    map_data = self._map_data
                    if map_data.segments:
                        map_data.mop_type = mop_types
                        for segment_id, segment in map_data.segments.items():
                            segment_id = str(segment_id)
                            if segment_id in mop_type:
                                mop = mop_type[segment_id].upper()
                                if mop == "A" or mop == "B" or mop == "C":
                                    segment.mop_type = mop

                        DreameVacuumMapDecoder.set_segment_cleanset(
                            map_data,
                            map_data.cleanset,
                            self.map_manager._capability,
                        )
                        self._set_updated_frame_id(map_data.frame_id)
                        self.refresh_map()
                self.refresh_map(map_id)
                return {k: (1 if v == "C" else 2 if v == "B" else 3) for k, v in mop_types.items()}
        return {}

    def set_segment_type(self, segment_type, map_id):
        if self._saved_map_data and map_id in self._saved_map_data:
            map_data = self._saved_map_data[map_id]
            if map_data.segments and not self._map_data.temporary_map:
                changed = []
                for segment_id, segment in map_data.segments.items():
                    key = str(segment_id)
                    previous = (segment.type, segment.index, segment.custom_name)
                    if key in segment_type and len(segment_type[key]) == 3:
                        type = int(segment_type[key][0])
                        index = int(segment_type[key][1])
                        custom_name = segment_type[key][2]

                        if type == 0:
                            segment.index = 0
                            if custom_name is not None:
                                if custom_name == "":
                                    custom_name = None
                                segment.custom_name = custom_name
                        else:
                            segment.custom_name = None
                            segment.index = index
                        segment.type = type

                    if (segment.type, segment.index, segment.custom_name) != previous:
                        changed.append(segment_id)

                if self._map_data and map_id == self._selected_map_id:
                    map_data = self._map_data
                    if map_data.segments:
                        for segment_id, segment in map_data.segments.items():
                            segment_id = str(segment_id)
                            if segment_id in segment_type and len(segment_type[segment_id]) == 3:
                                type = int(segment_type[segment_id][0])
                                index = int(segment_type[segment_id][1])
                                custom_name = segment_type[segment_id][2]

                                if type == 0:
                                    segment.index = 0
                                    if custom_name is not None:
                                        if custom_name == "":
                                            custom_name = None
                                        segment.custom_name = custom_name
                                else:
                                    segment.custom_name = None
                                    segment.index = index
                                segment.type = type
                        self._set_updated_frame_id(map_data.frame_id)
                        self.refresh_map()

                self.refresh_map(map_id)
                info = self.segment_type(self._saved_map_data[map_id])
                return {k: v for k, v in info.items() if MAP_PARAMETER_NAME not in v or k in changed}
        return {}

    def cleanset(self, map_data: MapData) -> list[list[int]] | None:
        cleanset = []
        for k, v in map_data.segments.items():
            if v.suction_level is None:
                v.suction_level = 1
            if v.water_volume is None:
                v.water_volume = 2
            if v.cleaning_times is None:
                v.cleaning_times = 1

            settings = [
                k,
                v.suction_level,
                v.wetness_level if v.wetness_level != None else v.water_volume + 1,
                v.cleaning_times,
            ]

            if self.map_manager._capability.custom_cleaning_mode:
                settings.append(v.cleaning_mode if v.cleaning_mode is not None else 2)

            if (
                self.map_manager._capability.cleaning_route
                or self.map_manager._capability.segment_mopping_type
                or self.map_manager._capability.segment_mopping_settings
            ):
                settings.append(v.mopping_settings if v.mopping_settings else 0)

            if self.map_manager._capability.mop_temperature:
                settings.append(v.mop_temperature if v.mop_temperature is not None else 0)

            if self.map_manager._capability.mop_pressure:
                if len(settings) == 6:
                    settings.extend([0, 0])
                elif len(settings) == 7:
                    settings.append(0)

                settings[7] = v.mop_pressure if v.mop_pressure is not None else 2

            cleanset.append(settings)
        return cleanset

    def floor_material(self, map_data: MapData, carpet_type=None):
        if carpet_type is None:
            carpet_type = {}
        return {
            str(k): (
                {
                    "material": v.floor_material,
                    "direction": v.floor_material_direction,
                }
                if v.floor_material_direction is not None and self.map_manager._capability.floor_direction_cleaning
                else {"material": v.floor_material if k not in carpet_type else 6 if carpet_type[k] == 7 else 7}
            )
            for k, v in map_data.segments.items()
        }, ([[k, v] for k, v in carpet_type.items()] if carpet_type else None)

    def segment_type(self, map_data: MapData):
        return {
            k: {
                **(
                    {
                        MAP_PARAMETER_NAME: base64.b64encode(v.custom_name.encode("utf-8")).decode("utf-8"),
                        MAP_REQUEST_PARAMETER_TYPE: 0,
                        MAP_REQUEST_PARAMETER_INDEX: 0,
                    }
                    if v.custom_name is not None
                    else ({MAP_REQUEST_PARAMETER_TYPE: v.type, MAP_REQUEST_PARAMETER_INDEX: v.index})
                ),
                **({MAP_REQUEST_PARAMETER_ROOM_ID: v.unique_id} if v.unique_id else {}),
            }
            for k, v in map_data.segments.items()
            if (v.type is not None and v.type > 0) or v.custom_name is not None
        }

    def set_carpet_cleanset(self, carpet_cleanset: list[list[int]]) -> None:
        DreameVacuumMapDecoder.set_carpet_cleanset(self._map_data, carpet_cleanset, self.map_manager._capability)
        self._map_data.carpet_cleanset = carpet_cleanset
        self._set_updated_frame_id(self._map_data.frame_id)
        self.refresh_map()

    def set_custom_carpet_settings(self, carpet_cleanset: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None and map_data.carpet_cleanset:
            cleanset = []
            new_carpet_cleanset = map_data.carpet_cleanset.copy()
            for selected_carpet in carpet_cleanset:
                for carpet in new_carpet_cleanset:
                    if carpet[0] == selected_carpet[0] and carpet[1] == selected_carpet[1]:
                        if len(carpet) > 3:
                            carpet[3] = selected_carpet[3]
                        cleanset.append(carpet.copy())
                        break
            self.set_carpet_cleanset(new_carpet_cleanset)
            carpet_cleanset = cleanset
        return carpet_cleanset

    def set_custom_carpet_cleaning(self, carpet_cleanset: list[list[int]]) -> None:
        map_data = self._map_data
        if map_data is not None and map_data.carpet_cleanset:
            cleanset = []
            new_carpet_cleanset = map_data.carpet_cleanset.copy()
            for selected_carpet in carpet_cleanset:
                for carpet in new_carpet_cleanset:
                    if carpet[0] == selected_carpet[0] and carpet[1] == selected_carpet[1]:
                        carpet[2] = selected_carpet[2]
                        item = [carpet[0], carpet[1], carpet[2]]
                        if len(carpet) > 3 and self.map_manager._capability.carpet_cleanset_v3:
                            if (
                                carpet[2] == -1
                                or len(selected_carpet) < 4
                                or selected_carpet[3] == -1
                                or selected_carpet[3] is None
                            ):
                                carpet[3] = -1
                            else:
                                carpet[3] = selected_carpet[3]
                                item.append(carpet[3])
                        cleanset.append(item)
                        break
            self.set_carpet_cleanset(new_carpet_cleanset)
            carpet_cleanset = cleanset
        return carpet_cleanset

    def set_walls(self, walls: list[list[int]], doors: list[list[int]], map_id: int) -> None:
        if self._saved_map_data and map_id in self._saved_map_data:
            map_data = self._saved_map_data[map_id]
            if map_data.walls and not self._map_data.temporary_map:
                new_wall = {}
                for segment_id in map_data.walls.keys():
                    id = str(segment_id)
                    new_walls = []
                    if id in walls:
                        if len(walls[id]) == len(map_data.walls[segment_id]):
                            for wall in walls[id]:
                                if len(wall) != 11:
                                    return
                                new_walls.append(
                                    Wall(
                                        wall[0],
                                        wall[1],
                                        wall[2],
                                        wall[3],
                                        wall[4],
                                        wall[5],
                                        wall[6],
                                        wall[7],
                                        wall[8],
                                        None,
                                        wall[9],
                                        wall[10],
                                    )
                                )
                            new_wall[segment_id] = new_walls
                        else:
                            new_wall[segment_id] = map_data.walls[segment_id]
                    else:
                        for wall in map_data.walls[segment_id]:
                            new_walls.append(wall)
                        new_wall[segment_id] = new_walls

                new_doors = []
                if len(map_data.doors) == len(doors):
                    for door in doors:
                        if len(door) != 11:
                            return
                        new_doors.append(
                            Wall(
                                door[0],
                                door[1],
                                door[2],
                                door[3],
                                door[4],
                                door[5],
                                door[6],
                                door[7],
                                door[8],
                                door[9],
                                None,
                                None,
                                door[10],
                            )
                        )
                else:
                    new_doors = map_data.doors

                walls_info = (
                    {
                        "wallInfoV3": [
                            {"i": segment_id, "w": walls_info}
                            for segment_id, segment_walls in new_wall.items()
                            if (
                                walls_info := [
                                    [w.initial_x0, w.initial_y0, w.initial_x1, w.initial_y1, w.x0, w.y0, w.x1, w.y1]
                                    for index, w in enumerate(segment_walls)
                                    if w.x0 != map_data.walls[segment_id][index].x0
                                    or w.y0 != map_data.walls[segment_id][index].y0
                                    or w.x1 != map_data.walls[segment_id][index].x1
                                    or w.y1 != map_data.walls[segment_id][index].y1
                                ]
                            )
                        ],
                        "doorInfoV3": [
                            [d.initial_x0, d.initial_y0, d.initial_x1, d.initial_y1, d.x0, d.y0, d.x1, d.y1]
                            for index, d in enumerate(new_doors)
                            if d.x0 != map_data.doors[index].x0
                            or d.y0 != map_data.doors[index].y0
                            or d.x1 != map_data.doors[index].x1
                            or d.y1 != map_data.doors[index].y1
                        ],
                    }
                    if self.map_manager._capability.walls_v3
                    else {
                        "wallInfoV2": {
                            "fixVersion": 1,
                            "version_flag": map_data.walls_version,
                            "storeys": [
                                {
                                    "r": [
                                        {
                                            "i": segment_id,
                                            "f": (
                                                map_data.segments[segment_id].floor_material
                                                if map_data.segments[segment_id].floor_material != 1
                                                or map_data.segments[segment_id].floor_material_direction is None
                                                else (
                                                    (
                                                        3
                                                        if map_data.segments[segment_id].floor_material_direction == 90
                                                        else 4
                                                    )
                                                    if map_data.segments and segment_id in map_data.segments
                                                    else 0
                                                )
                                            ),
                                            "w": [
                                                (
                                                    [
                                                        w.type,
                                                        w.initial_x0,
                                                        w.initial_y0,
                                                        w.initial_x1,
                                                        w.initial_y1,
                                                        float(w.x),
                                                        float(w.y),
                                                        w.x0,
                                                        w.y0,
                                                        w.x1,
                                                        w.y1,
                                                    ]
                                                    if (
                                                        w.x0 != w.initial_x0
                                                        or w.y0 != w.initial_y0
                                                        or w.x1 != w.initial_x1
                                                        or w.y1 != w.initial_y1
                                                    )
                                                    else [
                                                        w.type,
                                                        w.initial_x0,
                                                        w.initial_y0,
                                                        w.initial_x1,
                                                        w.initial_y1,
                                                        float(w.x),
                                                        float(w.y),
                                                    ]
                                                )
                                                for w in segment_walls
                                            ],
                                        }
                                        for segment_id, segment_walls in new_wall.items()
                                    ],
                                    "d": [
                                        (
                                            [
                                                d.type,
                                                d.id,
                                                d.status,
                                                d.initial_x0,
                                                d.initial_y0,
                                                d.initial_x1,
                                                d.initial_y1,
                                                d.x0,
                                                d.y0,
                                                d.x1,
                                                d.y1,
                                            ]
                                            if (
                                                d.x0 != d.initial_x0
                                                or d.y0 != d.initial_y0
                                                or d.x1 != d.initial_x1
                                                or d.y1 != d.initial_y1
                                            )
                                            else [
                                                d.type,
                                                d.id,
                                                d.status,
                                                d.initial_x0,
                                                d.initial_y0,
                                                d.initial_x1,
                                                d.initial_y1,
                                            ]
                                        )
                                        for d in new_doors
                                    ],
                                }
                            ],
                        }
                    }
                )

                map_data.walls = new_wall
                map_data.doors = new_doors

                if self._map_data and map_id == self._selected_map_id:
                    map_data = self._map_data
                    if map_data.walls:
                        map_data.walls = new_wall
                        map_data.doors = new_doors
                        self._set_updated_frame_id(map_data.frame_id)
                        self.refresh_map()

                self.refresh_map(map_id)
                return walls_info

    def set_segment_suction_level(
        self, segment_id: int, suction_level: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            map_data.segments[segment_id].suction_level = suction_level
            map_data.cleanset[str(segment_id)][0] = suction_level
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_water_volume(
        self, segment_id: int, water_volume: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments:
            map_data.segments[segment_id].water_volume = water_volume
            map_data.cleanset[str(segment_id)][1] = water_volume + 1
            if map_data.segments[segment_id].custom_mopping_route is not None:
                values = DreameVacuumMapDecoder.split_mopping_settings(map_data.segments[segment_id].mopping_settings)
                if values:
                    # Set mopping mode or water volume according to the mopping effect switch
                    values[2 if map_data.segments[segment_id].custom_mopping_route == -1 else 1] = water_volume
                    map_data.segments[segment_id].mopping_settings = DreameVacuumMapDecoder.combine_mopping_settings(
                        values
                    )
                    map_data.cleanset[str(segment_id)][5] = map_data.segments[segment_id].mopping_settings

            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_wetness_level(
        self, segment_id: int, wetness_level: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments:
            wetness_level = int(wetness_level)
            map_data.cleanset[str(segment_id)][1] = wetness_level
            map_data.segments[segment_id].wetness_level = wetness_level

            if wetness_level > (14 if self.map_manager._capability.mop_clean_frequency else 26):
                map_data.segments[segment_id].water_volume = 3
            elif wetness_level < 6:
                map_data.segments[segment_id].water_volume = 1
            else:
                map_data.segments[segment_id].water_volume = 2

            if map_data.segments[segment_id].custom_mopping_route is not None:
                map_data.segments[segment_id].custom_mopping_route = 0

                values = DreameVacuumMapDecoder.split_mopping_settings(map_data.segments[segment_id].mopping_settings)
                if values:
                    values[1] = 0
                    values[2] = 0
                    map_data.segments[segment_id].mopping_settings = DreameVacuumMapDecoder.combine_mopping_settings(
                        values
                    )
                    map_data.cleanset[str(segment_id)][5] = map_data.segments[segment_id].mopping_settings

            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_cleaning_times(
        self, segment_id: int, cleaning_times: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            map_data.segments[segment_id].cleaning_times = cleaning_times
            map_data.cleanset[str(segment_id)][2] = cleaning_times
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_cleaning_mode(
        self, segment_id: int, cleaning_mode: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if (
            map_data
            and map_data.segments
            and segment_id in map_data.segments
            and not map_data.temporary_map
            and map_data.segments[segment_id].cleaning_mode is not None
        ):
            map_data.segments[segment_id].cleaning_mode = cleaning_mode
            map_data.cleanset[str(segment_id)][4] = cleaning_mode
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_custom_mopping_route(
        self, segment_id: int, custom_mopping_route: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments:
            if map_data.segments[segment_id].custom_mopping_route is not None:
                map_data.segments[segment_id].custom_mopping_route = custom_mopping_route
                values = DreameVacuumMapDecoder.split_mopping_settings(map_data.segments[segment_id].mopping_settings)
                if values:
                    # Set mopping effect switch or cleaning route
                    if map_data.segments[segment_id].custom_mopping_route == -1:
                        values[2] = map_data.segments[segment_id].water_volume
                        map_data.segments[segment_id].cleaning_route = 1 if values[2] == 2 else values[2]
                    else:
                        values[2] = 0
                        values[0] = custom_mopping_route + 1
                        map_data.segments[segment_id].cleaning_route = values[0]

                    map_data.segments[segment_id].mopping_settings = DreameVacuumMapDecoder.combine_mopping_settings(
                        values
                    )
                    map_data.cleanset[str(segment_id)][5] = map_data.segments[segment_id].mopping_settings

            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_cleaning_route(
        self, segment_id: int, cleaning_route: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments:
            if map_data.segments[segment_id].cleaning_route is not None:
                map_data.segments[segment_id].cleaning_route = cleaning_route
                values = DreameVacuumMapDecoder.split_mopping_settings(map_data.segments[segment_id].mopping_settings)
                if values:
                    values[2] = 0
                    values[0] = cleaning_route
                    map_data.segments[segment_id].custom_mopping_route = values[2] - 1
                    map_data.segments[segment_id].mopping_settings = DreameVacuumMapDecoder.combine_mopping_settings(
                        values
                    )
                    map_data.cleanset[str(segment_id)][5] = map_data.segments[segment_id].mopping_settings

            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_mop_temperature(
        self, segment_id: int, mop_temperature: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            map_data.segments[segment_id].mop_temperature = mop_temperature
            map_data.cleanset[str(segment_id)][6] = mop_temperature
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_mop_pressure(
        self, segment_id: int, mop_pressure: int, refresh_map: bool = True
    ) -> list[list[int]] | None:
        map_data = self._map_data
        if map_data and map_data.segments and segment_id in map_data.segments and not map_data.temporary_map:
            map_data.segments[segment_id].mop_pressure = mop_pressure
            map_data.cleanset[str(segment_id)][7] = mop_pressure
            if (
                self._saved_map_data
                and self._selected_map_id is not None
                and self._selected_map_id in self._saved_map_data
            ):
                self._saved_map_data[self._selected_map_id].cleanset = copy.deepcopy(map_data.cleanset)
            if refresh_map:
                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                return self.cleanset(map_data)

    def set_segment_floor_material(
        self, segment_id: int, floor_material: int, direction: int = None
    ) -> list[list[int]] | None:
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            map_data = self._saved_map_data[self._selected_map_id]
            if map_data and map_data.segments:
                if direction is not None:
                    if floor_material != 1:
                        direction = None
                    elif map_data.rotation == 90 or map_data.rotation == 270:
                        direction = 0 if direction else 90

                carpet_type = {}
                if segment_id in map_data.segments:
                    if (
                        self.map_manager._capability.carpet_material
                        and map_data.segments[segment_id].floor_material != floor_material
                        and (
                            (floor_material >= 5 and floor_material <= 6)
                            or (
                                map_data.segments[segment_id].floor_material >= 5
                                and map_data.segments[segment_id].floor_material <= 6
                                and floor_material == 7
                            )
                        )
                    ):
                        carpet_type[segment_id] = floor_material

                    map_data.segments[segment_id].floor_material = floor_material
                    map_data.segments[segment_id].floor_material_direction = direction
                    if direction is not None:
                        map_data.segments[segment_id].floor_material_rotated_direction = (
                            direction
                            if map_data.rotation == 0 or map_data.rotation == 180
                            else 90 if direction == 0 else 0
                        )
                    else:
                        map_data.segments[segment_id].floor_material_rotated_direction = None
                    DreameVacuumMapDecoder.set_segment_floor_material(
                        map_data,
                        map_data.segments[segment_id],
                        map_data.floor_material,
                        self.map_manager._capability,
                    )
                self.refresh_map(self._selected_map_id)

                map_data = self._map_data
                segment = (
                    map_data.segments[segment_id] if map_data.segments and segment_id in map_data.segments else None
                )
                if map_data and not map_data.temporary_map and segment:
                    segment.floor_material = floor_material
                    segment.floor_material_direction = direction
                    if direction is not None:
                        segment.floor_material_rotated_direction = (
                            direction
                            if map_data.rotation == 0 or map_data.rotation == 180
                            else 90 if direction == 0 else 0
                        )
                    else:
                        segment.floor_material_rotated_direction = None

                    DreameVacuumMapDecoder.set_segment_floor_material(
                        map_data,
                        segment,
                        map_data.floor_material,
                        self.map_manager._capability,
                    )
                    self._set_updated_frame_id(map_data.frame_id)
                    self.refresh_map()

                material, roomcpt = self.floor_material(self._saved_map_data[self._selected_map_id], carpet_type)
                key = str(segment_id)
                return ({key: material[key]} if key in material else {}), roomcpt
        return {}, None

    def set_segment_visibility(self, segment_id: int, visibility: int) -> list[list[int]] | None:
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            map_data = self._saved_map_data[self._selected_map_id]
            if map_data and map_data.segments:
                if segment_id in map_data.segments:
                    map_data.segments[segment_id].visibility = visibility
                    if not visibility:
                        map_data.segments[segment_id].order = None
                    if map_data.segments[segment_id] and segment_id not in self._map_data.segments:
                        self._map_data.segments[segment_id] = copy.deepcopy(map_data.segments[segment_id])

                hidden_segments = [k for k, v in map_data.segments.items() if v.visibility == False]
                hidden_segments.sort()
                map_data.hidden_segments = hidden_segments
                DreameVacuumMapDecoder.set_segment_cleanset(
                    map_data,
                    map_data.cleanset,
                    self.map_manager._capability,
                )
                self.refresh_map(self._selected_map_id)
                map_data.dirty = True

                map_data = self._map_data
                if map_data and map_data.segments and not map_data.temporary_map:
                    map_data.hidden_segments = hidden_segments
                    if segment_id in map_data.segments:
                        map_data.segments[segment_id].visibility = visibility
                        if not visibility:
                            map_data.segments[segment_id].order = None

                    DreameVacuumMapDecoder.set_segment_cleanset(
                        map_data,
                        map_data.cleanset,
                        self.map_manager._capability,
                    )
                    self._set_updated_frame_id(map_data.frame_id)
                    self.refresh_map()
            return hidden_segments
        return []

    def set_segment_mop_type(self, segment_id: int, mop_type: str) -> dict[str, int] | None:
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            map_data = self._saved_map_data[self._selected_map_id]
            if map_data and map_data.segments:
                if segment_id in map_data.segments:
                    map_data.segments[segment_id].mop_type = mop_type

                mop_types = {str(k): v.mop_type for k, v in dict(sorted(map_data.segments.items())).items()}
                map_data.mop_type = mop_types
                self.refresh_map(self._selected_map_id)

                map_data = self._map_data
                if map_data and map_data.segments and not map_data.temporary_map:
                    map_data.mop_type = mop_types
                    if segment_id in map_data.segments:
                        map_data.segments[segment_id].mop_type = mop_type

                    self._set_updated_frame_id(map_data.frame_id)
                    self.refresh_map()
                return {k: (1 if v == "C" else 2 if v == "B" else 3) for k, v in mop_types.items()}
        return {}

    def set_segment_name(self, segment_id: int, segment_type: int, custom_name: str = None) -> dict[str, Any] | None:
        map_data = self._map_data
        if (
            map_data
            and map_data.segments
            and segment_id in map_data.segments
            and self._selected_map_id
            and self._selected_map_id in self._saved_map_data
            and not map_data.temporary_map
        ):
            segment_type = int(segment_type)
            if (
                map_data.segments[segment_id].type != segment_type
                or map_data.segments[segment_id].custom_name != custom_name
            ):
                if segment_type == 0:
                    map_data.segments[segment_id].index = 0
                    if custom_name is not None:
                        if custom_name == "":
                            custom_name = None
                        map_data.segments[segment_id].custom_name = custom_name
                else:
                    map_data.segments[segment_id].custom_name = None
                    map_data.segments[segment_id].index = map_data.segments[segment_id].next_type_index(
                        segment_type, map_data.segments
                    )
                map_data.segments[segment_id].type = segment_type
                map_data.segments[segment_id].set_name()

                self._saved_map_data[self._selected_map_id].segments[segment_id].custom_name = map_data.segments[
                    segment_id
                ].custom_name
                self._saved_map_data[self._selected_map_id].segments[segment_id].index = map_data.segments[
                    segment_id
                ].index
                self._saved_map_data[self._selected_map_id].segments[segment_id].type = map_data.segments[
                    segment_id
                ].type
                self._saved_map_data[self._selected_map_id].segments[segment_id].set_name()
                self.refresh_map(self._selected_map_id)

                self._set_updated_frame_id(map_data.frame_id)
                self.refresh_map()
                info = self.segment_type(self._saved_map_data[self._selected_map_id])
                if segment_type == 0:
                    return {segment_id: info[segment_id]} if segment_id in info else {}
                return {k: v for k, v in info.items() if MAP_PARAMETER_NAME not in v}

    def set_zones(self, virtual_walls, no_go_areas, no_mopping_areas) -> None:
        map_data = self._map_data
        if not map_data or not self._selected_map_id:
            return

        map_data.no_mopping_areas = []
        if no_mopping_areas:
            for area in no_mopping_areas:
                x_coords = sorted([area[0], area[2]])
                y_coords = sorted([area[1], area[3]])
                map_data.no_mopping_areas.append(
                    Area(
                        x_coords[0],
                        y_coords[0],
                        x_coords[1],
                        y_coords[0],
                        x_coords[1],
                        y_coords[1],
                        x_coords[0],
                        y_coords[1],
                    )
                )

        map_data.no_go_areas = []
        if no_go_areas:
            for area in no_go_areas:
                x_coords = sorted([area[0], area[2]])
                y_coords = sorted([area[1], area[3]])
                map_data.no_go_areas.append(
                    Area(
                        x_coords[0],
                        y_coords[0],
                        x_coords[1],
                        y_coords[0],
                        x_coords[1],
                        y_coords[1],
                        x_coords[0],
                        y_coords[1],
                    )
                )

        if virtual_walls:
            map_data.virtual_walls = [
                Line(
                    wall[0],
                    wall[1],
                    wall[2],
                    wall[3],
                )
                for wall in virtual_walls
            ]
        else:
            map_data.virtual_walls = []

        self._set_updated_frame_id(map_data.frame_id)
        if (
            self._saved_map_data
            and self._selected_map_id is not None
            and self._selected_map_id in self._saved_map_data
        ):
            self._saved_map_data[self._selected_map_id].no_go_areas = map_data.no_go_areas
            self._saved_map_data[self._selected_map_id].no_mopping_areas = map_data.no_mopping_areas
            self._saved_map_data[self._selected_map_id].virtual_walls = map_data.virtual_walls
            self.refresh_map(self._selected_map_id)
        self.refresh_map()

    @property
    def _map_data(self) -> MapData | None:
        return self.map_manager._map_data

    @property
    def _saved_map_data(self) -> MapData | None:
        return self.map_manager._saved_map_data

    @property
    def _selected_map_id(self) -> int | None:
        return self.map_manager._selected_map_id

    @property
    def _current_timestamp_ms(self) -> int | None:
        return self.map_manager._current_timestamp_ms


class DreameVacuumMapDecoder:
    HEADER_SIZE = 27

    @staticmethod
    def _read_int_8(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 1], byteorder="big", signed=True)

    @staticmethod
    def _read_int_8_le(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 1], byteorder="little", signed=True)

    @staticmethod
    def _read_int_16(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 2], byteorder="big", signed=True)

    @staticmethod
    def _read_int_16_le(data: bytes, offset: int = 0) -> int:
        return int.from_bytes(data[offset : offset + 2], byteorder="little", signed=True)

    @staticmethod
    def _get_pixel_type(map_data: MapData, pixel) -> tuple[MapPixelType, bool]:
        if map_data.version == 3:
            carpet = bool((pixel & 0x80) != 0)
            segment_id = pixel & 0x1F

            if segment_id == 0:
                return (MapPixelType.OUTSIDE.value, carpet)

            wall = (pixel >> 5) & 0x03
            if wall == 3:
                return (200 + segment_id, carpet)

            if segment_id == 31:
                if wall > 0:
                    return (MapPixelType.WALL.value, carpet)

                if map_data.saved_map_status == 1 or map_data.saved_map_status == 0:
                    return (MapPixelType.NEW_SEGMENT.value, carpet)
                return (MapPixelType.FLOOR.value, carpet)

            if wall > 0:
                return (100 + segment_id, carpet)

            return (segment_id, carpet)

        if map_data.frame_map:
            carpet = bool((pixel & 0x03) == 3)
            segment_id = pixel >> 2

            if 0 < segment_id < 64:
                if segment_id == 63:
                    return (MapPixelType.WALL.value, carpet)
                if segment_id == 62:
                    return (MapPixelType.FLOOR.value, carpet)
                if segment_id == 61:
                    return (MapPixelType.UNKNOWN.value, carpet)
                return (segment_id, carpet)

            segment_id = pixel & 0x03
            # as implemented on the app
            if segment_id == 1 or segment_id == 3:
                return (MapPixelType.NEW_SEGMENT.value, carpet)
            if segment_id == 2:
                return (MapPixelType.WALL.value, carpet)
        elif map_data.version == 0:
            carpet = bool((pixel & 0x03) == 3)
            segment_id = pixel & 0x7F
            if segment_id == 1 or segment_id == 3:
                return (MapPixelType.NEW_SEGMENT.value, carpet)
            elif segment_id == 2:
                return (MapPixelType.WALL.value, carpet)
        else:
            carpet = bool((pixel & 0x40) == 64)
            segment_id = pixel & 0x3F
            if pixel >> 7:
                return (
                    (100 + segment_id) if segment_id else MapPixelType.WALL.value,
                    carpet,
                )

            if segment_id > 0:
                if map_data.saved_map_status == 1 or map_data.saved_map_status == 0:
                    # as implemented on the app
                    if segment_id == 1 or segment_id == 3:
                        return (MapPixelType.NEW_SEGMENT.value, carpet)
                    if segment_id == 2:
                        return (MapPixelType.WALL.value, carpet)
                    return (MapPixelType.OUTSIDE.value, False)

                return (segment_id, carpet)

        return (MapPixelType.OUTSIDE.value, False)

    @staticmethod
    def _get_segment_center(map_data, segment_id: int, center: int, vertical: bool) -> int | None:
        lines = []
        zero_pixels = -1
        segment_pixel = 0
        line = None

        limit = map_data.dimensions.height if vertical else map_data.dimensions.width

        for k in range(limit):
            idx = (k * map_data.dimensions.width + center) if vertical else (center * map_data.dimensions.width + k)
            if idx >= len(map_data.data):
                continue

            pixel_type = map_data.data[idx]

            if map_data.version == 3:
                segment = pixel_type & 0x1F
                if segment == 31 or ((pixel_type >> 5) & 0x03) == 3:
                    segment = 0
            else:
                segment = pixel_type & 0x3F

            if segment == segment_id:
                segment_pixel = k
                zero_pixels = 0
                if line is None:
                    line = [segment_pixel]
            elif segment == 0:
                if zero_pixels >= 0:
                    zero_pixels += 1
                    if zero_pixels >= 4 and line is not None:
                        line.append(segment_pixel)
                        lines.append(line)
                        line = None
            elif line is not None:
                line.append(segment_pixel)
                lines.append(line)
                line = None

        if line is not None:
            line.append(segment_pixel)
            lines.append(line)

        if lines:
            maxLine = max(lines, key=lambda l: l[1] - l[0])
            return int(math.ceil((maxLine[1] - maxLine[0]) / 2 + maxLine[0]))

        return None

    @staticmethod
    def _find_px_type(x, y, map_data: MapData, max_steps=50) -> int:
        pixel_type = map_data.pixel_type
        dimensions = map_data.dimensions
        if map_data.combined_pixel_type is not None:
            pixel_type = map_data.combined_pixel_type
            dimensions = map_data.combined_dimensions

        x = int((x - dimensions.left) / dimensions.grid_size)
        y = int((y - dimensions.top) / dimensions.grid_size)

        max_x = pixel_type.shape[0] - 1
        max_y = pixel_type.shape[1] - 1

        clamped_x = max(0, min(x, max_x))
        clamped_y = max(0, min(y, max_y))

        directions = [(0, 1), (1, 0), (0, -1), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1)]

        def find(xx, yy, distance, steps):
            if steps > max_steps:
                return 0

            for dx, dy in directions:
                nx = xx + dx * distance
                ny = yy + dy * distance

                if 0 <= nx <= max_x and 0 <= ny <= max_y:
                    px = pixel_type[nx, ny]
                    if px is not None:
                        val = int(px)
                        if 100 < val < 200:
                            val -= 100
                        if val != 255 and val != 0:
                            return val

            return find(xx, yy, distance + 1, steps + 1)

        px = pixel_type[clamped_x, clamped_y]
        if px is not None:
            val = int(px)
            if 100 < val < 200:
                val -= 100

            if val == 255 or (val > 200 and val < 232) or val == 0:
                return find(clamped_x, clamped_y, 1, 1) if max_steps else 0
            else:
                return val if not max_steps or int(px) < 100 else 0

        if max_steps:
            return find(clamped_x, clamped_y, 1, 1)
        return 0

    @staticmethod
    def _split_combined_areas(value) -> Tuple[list[int] | None, list[Area] | None]:
        if value is None:
            return None, None
        segments = []
        areas = []
        if value and isinstance(value, list):
            for item in value:
                if not isinstance(item, list):
                    continue
                if len(item) == 1:
                    room = item[0]
                    if type(room) is str and len(room) <= 2 and room.isascii() and room.isdecimal():
                        room = int(room)
                    if type(room) is int and 1 <= room <= 63:
                        segments.append(room)
                elif len(item) >= 4:
                    x_coords = sorted([item[0], item[2]])
                    y_coords = sorted([item[1], item[3]])
                    areas.append(
                        Area(
                            x_coords[0],
                            y_coords[0],
                            x_coords[1],
                            y_coords[0],
                            x_coords[1],
                            y_coords[1],
                            x_coords[0],
                            y_coords[1],
                        )
                    )
        return segments, areas

    @staticmethod
    def decode_map_partial(raw_data, version=1, iv=None, key=None) -> MapDataPartial | None:
        # _LOGGER.debug("raw_map: %s", raw_data)
        raw_map = raw_data.replace("_", "/").replace("-", "+")

        if len(raw_map) < 3:
            return None

        if "," in raw_map and key is None:
            values = raw_map.split(",")
            key = values[1]
            raw_map = values[0]

        raw_map = base64.decodebytes(raw_map.encode("utf8"))

        if key is not None and iv:
            try:
                cipher = Cipher(
                    algorithms.AES(hashlib.sha256(key.encode()).hexdigest()[0:32].encode("utf8")),
                    modes.CBC(iv.encode("utf8")),
                    backend=default_backend(),
                )
                decryptor = cipher.decryptor()
                raw_map = decryptor.update(raw_map) + decryptor.finalize()
            except Exception as ex:
                _LOGGER.error("Map data decryption failed: %s", ex)
                return None

        try:
            raw_map = zlib.decompress(raw_map)
            if not raw_map or len(raw_map) < DreameVacuumMapDecoder.HEADER_SIZE:
                _LOGGER.error("Wrong header size for map")
                return None
        except Exception as ex:
            if key is not None and not iv:
                _LOGGER.error(
                    f"Map data decryption failed: {ex}. Private key might be missing, please report this issue with your device model https://github.com/Tasshack/dreame-vacuum/issues/new?assignees=Tasshack&labels=bug&template=bug_report.md&title=Map%20data%20decryption%20failed"
                )
            else:
                _LOGGER.error("Map data decompression failed: %s\n%s", ex, raw_data)
            return None

        partial_map = MapDataPartial()
        partial_map.version = version
        partial_map.map_id = DreameVacuumMapDecoder._read_int_16_le(raw_map)
        partial_map.frame_id = DreameVacuumMapDecoder._read_int_16_le(raw_map, 2)
        partial_map.frame_type = DreameVacuumMapDecoder._read_int_8(raw_map, 4)
        partial_map.raw = raw_map
        image_size = DreameVacuumMapDecoder.HEADER_SIZE + (
            DreameVacuumMapDecoder._read_int_16_le(raw_map, 19) * DreameVacuumMapDecoder._read_int_16_le(raw_map, 21)
        )
        if len(raw_map) >= image_size:
            try:
                data_json = json.loads(raw_map[image_size:].decode("utf8"))
                if data_json.get("timestamp_ms"):
                    partial_map.timestamp_ms = int(data_json["timestamp_ms"])

                if (
                    version < 3
                    and (
                        "saveMapId" in data_json
                        or "cover" in data_json
                        or "diff" in data_json
                        or "curtain" in data_json
                    )
                    # and not ("rism" in data_json or "rissm" in data_json)
                ):
                    version = 3

                partial_map.data_json = data_json
            except Exception as ex:
                _LOGGER.warning("Failed to parse data: %s", ex)
        return partial_map

    @staticmethod
    def decode_map(
        raw_map: str,
        map_version: int = 1,
        rotation: int = 0,
        iv: str = None,
        key: str = None,
    ) -> Tuple[MapData, Optional[MapData]]:
        return DreameVacuumMapDecoder.decode_map_data_from_partial(
            DreameVacuumMapDecoder.decode_map_partial(raw_map, map_version, iv, key),
            rotation,
        )

    @staticmethod
    def decode_saved_map(raw_map: str, map_version: int = 1, rotation: int = 0, iv: str = None) -> MapData | None:
        return DreameVacuumMapDecoder.decode_map(raw_map, map_version, rotation, iv)[0]

    @staticmethod
    def decode_map_data_from_partial(partial_map: MapDataPartial, rotation: int = 0) -> MapData | None:
        if partial_map is None:
            return

        map_data = MapData()
        map_data.version = partial_map.version
        map_data.map_id = partial_map.map_id
        map_data.frame_id = partial_map.frame_id
        map_data.frame_type = partial_map.frame_type
        map_data.timestamp_ms = partial_map.timestamp_ms

        raw = partial_map.raw
        map_data.robot_position = Point(
            DreameVacuumMapDecoder._read_int_16_le(raw, 5),
            DreameVacuumMapDecoder._read_int_16_le(raw, 7),
            DreameVacuumMapDecoder._read_int_16_le(raw, 9),
        )
        map_data.charger_position = Point(
            DreameVacuumMapDecoder._read_int_16_le(raw, 11),
            DreameVacuumMapDecoder._read_int_16_le(raw, 13),
            DreameVacuumMapDecoder._read_int_16_le(raw, 15),
        )

        grid_size = DreameVacuumMapDecoder._read_int_16_le(raw, 17)
        width = DreameVacuumMapDecoder._read_int_16_le(raw, 19)
        height = DreameVacuumMapDecoder._read_int_16_le(raw, 21)
        left = DreameVacuumMapDecoder._read_int_16_le(raw, 23)
        top = DreameVacuumMapDecoder._read_int_16_le(raw, 25)

        image_size = DreameVacuumMapDecoder.HEADER_SIZE + width * height
        data_json = partial_map.data_json
        if data_json is None:
            data_json = {}

        _LOGGER.debug("Map Data Json: %s", data_json)

        saved_map_data = None
        try:
            if "origin" in data_json and data_json["origin"] and len(data_json["origin"]) > 1:
                left = data_json["origin"][0]
                top = data_json["origin"][1]

            map_data.dimensions = MapImageDimensions(top, left, height, width, grid_size)

            map_data.rotation = rotation

            overrides = None
            changes = None
            if map_data.version == 3 and partial_map.frame_type == MapFrameType.P.value:
                if "cover" in data_json:
                    overrides = data_json["cover"]
                    if "timestamp_ms" in overrides:
                        map_data.timestamp_ms = int(overrides.get("timestamp_ms"))

                if "diff" in data_json:
                    changes = data_json["diff"]

            if map_data.frame_type != MapFrameType.W.value:
                if "mra" in data_json:
                    map_data.rotation = int(data_json["mra"])

                if "cs" in data_json:
                    map_data.cleaned_area = int(data_json["cs"])

                if "ct" in data_json:
                    value = data_json["ct"]
                    if isinstance(value, int) or isinstance(value, float) or isinstance(value, str):
                        map_data.cleaning_time = int(value)

                if "wm" in data_json:
                    map_data.work_status = int(data_json["wm"])

                if "cf" in data_json:
                    map_data.completed = bool(data_json["cf"] == 1)

                if "clean_finish_remain_electricity" in data_json:
                    map_data.remaining_battery = int(data_json["clean_finish_remain_electricity"])

                map_data.customized_cleaning = data_json.get("customeClean")
                map_data.docked = bool("oc" in data_json and data_json["oc"])
                map_data.line_to_robot = bool("l2r" in data_json and data_json["l2r"])
                map_data.frame_map = bool(data_json.get("fsm") and data_json["fsm"] == 1)
                map_data.restored_map = bool(data_json.get("rpur") and data_json["rpur"] == 1)
                map_data.saved_map_status = -1
                if "ris" in data_json:
                    map_data.saved_map_status = data_json["ris"]
                map_data.clean_log = bool(data_json.get("iscleanlog") and data_json["iscleanlog"] == True)
                map_data.recovery_map = bool("us" in data_json and data_json["us"] == 1)
                map_data.new_map = bool("risp" in data_json and data_json["risp"] == 0)
                if "smd" in data_json:
                    map_data.startup_method = (
                        StartupMethod(data_json["smd"])
                        if data_json["smd"] in StartupMethod._value2member_map_
                        else StartupMethod.OTHER
                    )
                if "ctyi" in data_json:
                    map_data.task_end_type = (
                        TaskEndType(data_json["ctyi"])
                        if data_json["ctyi"] in TaskEndType._value2member_map_
                        else TaskEndType.OTHER
                    )
                if "cmc" in data_json:
                    map_data.cleanup_method = (
                        CleanupMethod(data_json["cmc"])
                        if data_json["cmc"] in CleanupMethod._value2member_map_
                        else CleanupMethod.OTHER
                    )
                if "ds" in data_json:
                    map_data.dust_collection_count = int(data_json.get("ds", 0))
                if "wt" in data_json:
                    map_data.mop_wash_count = int(data_json.get("wt", 0))
                map_data.multiple_cleaning_time = data_json.get("multime")
                map_data.dos = data_json.get("dos")
                map_data.mopping_mode = data_json.get("mooClean")
                map_data.temporary_map = bool(
                    data_json.get("suw")
                    and (data_json["suw"] == 6 or data_json["suw"] == 5)
                    and data_json.get("fsm") is None
                )
                map_data.saved_map = bool(
                    map_data.frame_type == MapFrameType.I.value
                    and not map_data.restored_map
                    and not map_data.frame_map
                    and map_data.saved_map_status == -1
                    and not map_data.clean_log
                )

                if (
                    (data_json.get("nc") and data_json["nc"])
                    or map_data.charger_position.a == 32767
                    or (map_data.charger_position.x == 32767 and map_data.charger_position.y == 32767)
                ):
                    map_data.charger_position = None

                if (
                    (data_json.get("nr") and data_json["nr"])
                    or map_data.robot_position.a == 32767
                    or (map_data.robot_position.x == 32767 and map_data.robot_position.y == 32767)
                ):
                    map_data.robot_position = None

                if not map_data.saved_map and not map_data.recovery_map:
                    map_data.index = 0

                path = overrides["tr"] if overrides and "tr" in overrides else data_json.get("tr")
                if path:
                    matches = [
                        m.groupdict()
                        for m in re.compile(r"(?P<operator>[MWSLl])(?P<x>-?\d+),(?P<y>-?\d+)").finditer(path)
                    ]
                    current_position = Point(0, 0)
                    map_data.path = []
                    for match in matches:
                        operator = match["operator"]
                        x = int(match["x"])
                        y = int(match["y"])

                        if operator == "L":
                            current_position = Path(
                                current_position.x + x,
                                current_position.y + y,
                                PathType.LINE,
                            )
                        else:
                            # You will only get "l" paths with in a P frame.
                            # It means path is connected with the path from previous frame and it should be rendered as a line.
                            if operator == "l":
                                operator = "L"
                            current_position = Path(x, y, PathType(operator))

                        map_data.path.append(current_position)

                map_data.small_path = (
                    overrides["small_tr"] if overrides and "small_tr" in overrides else data_json.get("small_tr")
                )
                map_data.current_mop_type = (
                    overrides["moptype"] if overrides and "moptype" in overrides else data_json.get("moptype")
                )

                if data_json.get("sa") and isinstance(data_json["sa"], list):
                    map_data.active_segments = [sa[0] for sa in data_json["sa"]]

                if "delsr" in data_json:
                    map_data.hidden_segments = data_json["delsr"]

                (
                    map_data.skipped_segments,
                    map_data.skipped_areas,
                ) = DreameVacuumMapDecoder._split_combined_areas(data_json.get("combineskipedarea"))

                map_data.completed_segments = DreameVacuumMapDecoder._split_combined_areas(
                    data_json.get("combinecleanedarea")
                )[0]

                map_data.pending_segments = DreameVacuumMapDecoder._split_combined_areas(
                    data_json.get("combinenotcleanedarea")
                )[0]

                (
                    map_data.extra_clean_segments,
                    map_data.extra_clean_areas,
                ) = DreameVacuumMapDecoder._split_combined_areas(data_json.get("combineaddclean"))

                map_data.extra_cleaned_segments = DreameVacuumMapDecoder._split_combined_areas(
                    data_json.get("combineaddcleanedarea")
                )[0]

                if data_json.get("da2"):
                    if data_json["da2"].get("areas"):
                        map_data.active_areas = []
                        for area in data_json["da2"]["areas"]:
                            x_coords = sorted([area[0], area[2]])
                            y_coords = sorted([area[1], area[3]])
                            map_data.active_areas.append(
                                Area(
                                    x_coords[0],
                                    y_coords[0],
                                    x_coords[1],
                                    y_coords[0],
                                    x_coords[1],
                                    y_coords[1],
                                    x_coords[0],
                                    y_coords[1],
                                )
                            )

                if data_json.get("sp"):
                    map_data.active_points = []
                    for point in data_json["sp"]:
                        map_data.active_points.append(Point(point[0], point[1]))

                if "cleanset" in data_json:
                    map_data.cleanset = data_json["cleanset"]
                    if isinstance(map_data.cleanset, str):
                        map_data.cleanset = json.loads(map_data.cleanset)
                    map_data.sequence = True

                if "carpetcleanset" in data_json:
                    map_data.carpet_cleanset = data_json["carpetcleanset"]
                    if isinstance(map_data.carpet_cleanset, str):
                        map_data.carpet_cleanset = json.loads(map_data.carpet_cleanset)

                if "cleanareaorder" in data_json:
                    map_data.cleaning_sequence = {}
                    for item in data_json["cleanareaorder"]:
                        for k, v in item.items():
                            map_data.cleaning_sequence[k] = v
                            break

                    map_data.cleaning_sequence = dict(
                        sorted(map_data.cleaning_sequence.items(), key=lambda item: int(item[0]))
                    )
                    if map_data.version == 1:
                        map_data.version = 2

                if "room_id_type" in data_json:
                    map_data.mop_type = {}
                    for k, v in data_json["room_id_type"].items():
                        if v == 1:
                            map_data.mop_type[k] = "C"
                        elif v == 2:
                            map_data.mop_type[k] = "B"
                        else:
                            map_data.mop_type[k] = "A"
            else:
                map_data.need_optimization = True
                map_data.wifi_map = True

            carpet_pixels = []
            map_data.empty_map = (
                map_data.frame_type == MapFrameType.I.value or map_data.frame_type == MapFrameType.W.value
            )
            if (width * height) > 0:
                map_data.data = raw[DreameVacuumMapDecoder.HEADER_SIZE : image_size]
                map_data.empty_map = bool(width == 2 and height == 2)
                if map_data.empty_map:
                    for y in range(height):
                        for x in range(width):
                            if map_data.data[(width * y) + x] > 0:
                                map_data.empty_map = False
                                break

                np.seterr(over="ignore")
                map_data.pixel_type = np.full((width, height), MapPixelType.OUTSIDE.value, dtype=np.uint8)
                if not map_data.empty_map:
                    map_data.empty_map = True
                    if map_data.frame_type == MapFrameType.W.value:
                        try:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x] & 0x0F
                                    if pixel > 0:
                                        map_data.empty_map = False
                                        map_data.pixel_type[x, y] = MapPixelType(pixel)
                        except:
                            pass
                    elif map_data.frame_type == MapFrameType.I.value:
                        if map_data.version == 3:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel > 0:
                                        map_data.empty_map = False
                                        if (pixel & 0x80) != 0:
                                            carpet_pixels.append((x, y))

                                        segment_id = pixel & 0x1F
                                        wall = (pixel >> 5) & 0x03

                                        if segment_id == 0:
                                            map_data.pixel_type[x, y] = MapPixelType.OUTSIDE.value
                                        elif segment_id == 31:
                                            if wall > 0:
                                                if wall == 3:
                                                    map_data.pixel_type[x, y] = 200 + segment_id
                                                else:
                                                    map_data.pixel_type[x, y] = MapPixelType.WALL.value
                                            else:
                                                if map_data.saved_map_status == 1 or map_data.saved_map_status == 0:
                                                    map_data.pixel_type[x, y] = MapPixelType.NEW_SEGMENT.value
                                                else:
                                                    map_data.pixel_type[x, y] = MapPixelType.FLOOR.value
                                        else:
                                            if wall > 0:
                                                if wall == 3:
                                                    map_data.pixel_type[x, y] = 200 + segment_id
                                                else:
                                                    map_data.pixel_type[x, y] = 100 + segment_id
                                            else:
                                                map_data.pixel_type[x, y] = segment_id
                        elif map_data.frame_map:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel > 0:
                                        if pixel & 0x03 == 3:
                                            carpet_pixels.append((x, y))
                                        map_data.empty_map = False
                                        segment_id = pixel >> 2
                                        if 0 < segment_id < 64:
                                            if segment_id == 63:
                                                map_data.pixel_type[x, y] = MapPixelType.WALL.value
                                            elif segment_id == 62:
                                                map_data.pixel_type[x, y] = MapPixelType.FLOOR.value
                                            elif segment_id == 61:
                                                map_data.pixel_type[x, y] = MapPixelType.UNKNOWN.value
                                            else:
                                                map_data.pixel_type[x, y] = segment_id
                                        else:
                                            segment_id = pixel & 0x3F
                                            if segment_id == 1 or segment_id == 3:
                                                map_data.pixel_type[x, y] = MapPixelType.NEW_SEGMENT.value
                                            elif segment_id == 2:
                                                map_data.pixel_type[x, y] = MapPixelType.WALL.value
                        elif map_data.saved_map_status == 1 or map_data.saved_map_status == 0:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel > 0:
                                        if pixel & 0x03 == 3:
                                            carpet_pixels.append((x, y))
                                        segment_id = pixel & 0x3F
                                        # as implemented on the app
                                        if segment_id == 1 or segment_id == 3:
                                            map_data.empty_map = False
                                            map_data.pixel_type[x, y] = MapPixelType.NEW_SEGMENT.value
                                        elif segment_id == 2:
                                            map_data.empty_map = False
                                            map_data.pixel_type[x, y] = MapPixelType.WALL.value
                        elif (
                            map_data.version == 0 and not map_data.saved_map and not map_data.recovery_map
                        ) or map_data.saved_map_status == 2:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel & 0x03 == 3:
                                        carpet_pixels.append((x, y))
                                    segment_id = pixel & 0x3F
                                    if segment_id > 0:
                                        map_data.empty_map = False
                                        if segment_id == 2:
                                            map_data.pixel_type[x, y] = MapPixelType.WALL.value
                                        else:
                                            map_data.pixel_type[x, y] = MapPixelType.NEW_SEGMENT.value
                        else:
                            for y in range(height):
                                for x in range(width):
                                    pixel = map_data.data[(width * y) + x]
                                    if pixel > 0:
                                        if (pixel & 0x40) == 64:
                                            carpet_pixels.append((x, y))
                                        map_data.empty_map = False
                                        segment_id = pixel & 0x3F
                                        if pixel >> 7:
                                            map_data.pixel_type[x, y] = (
                                                (100 + segment_id) if segment_id else MapPixelType.WALL.value
                                            )
                                        else:
                                            if segment_id > 0:
                                                map_data.pixel_type[x, y] = segment_id

                        if carpet_pixels:
                            map_data.carpet_pixels = carpet_pixels

                        segments = DreameVacuumMapDecoder.get_segments(map_data)
                        segment_info = []

                        if segments and "seg_inf" in data_json:
                            seg_inf = data_json["seg_inf"]
                            for k, v in seg_inf.items():
                                seg_id = int(k)

                                segment_info.append(
                                    (seg_id, v.get("nei_id"), segments[seg_id].area if seg_id in segments else 0)
                                )

                                is_unmmaped = seg_id not in segments
                                if (
                                    is_unmmaped
                                    and not map_data.wifi_map
                                    and map_data.saved_map_status != 2
                                    # and not (not map_data.saved_map and not map_data.history_map and map_data.version == 3)
                                ):
                                    continue

                                target_seg = Segment(seg_id) if is_unmmaped else segments[seg_id]

                                if v.get("nei_id") is not None:
                                    target_seg.neighbors = v["nei_id"]
                                if v.get("type") is not None:
                                    target_seg.type = int(v["type"])
                                if v.get("index") is not None:
                                    target_seg.index = v["index"]
                                if v.get("roomID") is not None:
                                    target_seg.unique_id = v["roomID"]
                                if v.get("direction") is not None:
                                    target_seg.floor_material_direction = v["direction"]

                                if v.get("material") is not None:
                                    material = v["material"]
                                    if material in DreameVacuumFloorMaterial._value2member_map_:
                                        target_seg.floor_material = material
                                    elif material == 3:
                                        target_seg.floor_material = DreameVacuumFloorMaterial.WOOD.value
                                        target_seg.floor_material_direction = (
                                            DreameVacuumFloorMaterialDirection.VERTICAL.value
                                        )
                                    elif material == 4:
                                        target_seg.floor_material = DreameVacuumFloorMaterial.WOOD.value
                                        target_seg.floor_material_direction = (
                                            DreameVacuumFloorMaterialDirection.HORIZONTAL.value
                                        )
                                    else:
                                        target_seg.floor_material = DreameVacuumFloorMaterial.NONE.value

                                if v.get(MAP_PARAMETER_NAME):
                                    target_seg.custom_name = base64.b64decode(v.get(MAP_PARAMETER_NAME)).decode(
                                        "utf-8"
                                    )

                                target_seg.visibility = (
                                    bool(seg_id not in map_data.hidden_segments)
                                    if map_data.hidden_segments is not None
                                    else True
                                )
                                target_seg.set_name()

                                if is_unmmaped:
                                    segments[seg_id] = target_seg
                                    target_seg.unmapped = True
                                    if map_data.unmapped_segments is None:
                                        map_data.unmapped_segments = []
                                    map_data.unmapped_segments.append(seg_id)

                        map_data.segments = segments

            if map_data.wifi_map:
                map_data.robot_position = None
                return map_data, saved_map_data

            restored_map = map_data.restored_map

            if map_data.empty_map and map_data.saved_map_status == 2 and not map_data.frame_map:
                map_data.restored_map = False
                restored_map = True

            if "rism" in data_json:
                _LOGGER.info("Decoding saved map: %s", map_data.map_id)
                saved_map_data = DreameVacuumMapDecoder.decode_saved_map(
                    data_json["rism"],
                    map_data.version,
                    map_data.rotation,
                )

                if saved_map_data is not None:
                    _LOGGER.info("Decoded saved map: %s -> %s", map_data.map_id, saved_map_data.map_id)
                    saved_map_data.timestamp_ms = map_data.timestamp_ms
                    map_data.saved_map_id = saved_map_data.map_id
                    if map_data.version < saved_map_data.version:
                        map_data.version = saved_map_data.version

                    if saved_map_data.temporary_map:
                        map_data.temporary_map = saved_map_data.temporary_map

                    if (
                        restored_map
                        or map_data.recovery_map
                        or (
                            map_data.saved_map_status == 2
                            and (map_data.empty_map or (not map_data.frame_map and map_data.version != 0))
                        )
                    ):
                        map_data.segments = copy.deepcopy(saved_map_data.segments)
                        map_data.cleaning_sequence = saved_map_data.cleaning_sequence
                        map_data.mop_type = saved_map_data.mop_type or map_data.mop_type
                        if saved_map_data.floor_material is not None:
                            map_data.floor_material = copy.deepcopy(saved_map_data.floor_material)
                        if map_data.hidden_segments is None and saved_map_data.hidden_segments is not None:
                            map_data.hidden_segments = copy.deepcopy(saved_map_data.hidden_segments)

                        if map_data.saved_map_status == 2 and not map_data.frame_map:
                            left = min(map_data.dimensions.left, saved_map_data.dimensions.left)
                            top = min(map_data.dimensions.top, saved_map_data.dimensions.top)
                            width = int(
                                (
                                    max(
                                        map_data.dimensions.left
                                        + (map_data.dimensions.width * map_data.dimensions.grid_size),
                                        saved_map_data.dimensions.left
                                        + (saved_map_data.dimensions.width * saved_map_data.dimensions.grid_size),
                                    )
                                    - left
                                )
                                / saved_map_data.dimensions.grid_size
                            )
                            height = int(
                                (
                                    max(
                                        map_data.dimensions.top
                                        + (map_data.dimensions.height * map_data.dimensions.grid_size),
                                        saved_map_data.dimensions.top
                                        + (saved_map_data.dimensions.height * saved_map_data.dimensions.grid_size),
                                    )
                                    - top
                                )
                                / saved_map_data.dimensions.grid_size
                            )
                            si = int((saved_map_data.dimensions.left - left) / saved_map_data.dimensions.grid_size)
                            sj = int((saved_map_data.dimensions.top - top) / saved_map_data.dimensions.grid_size)
                            sim = si + saved_map_data.dimensions.width
                            sjm = sj + saved_map_data.dimensions.height
                            ni = int((map_data.dimensions.left - left) / map_data.dimensions.grid_size)
                            nj = int((map_data.dimensions.top - top) / map_data.dimensions.grid_size)
                            nim = ni + map_data.dimensions.width
                            njm = nj + map_data.dimensions.height
                            pixel_type = np.zeros((width, height), np.uint8)
                            carpet_pixels = []

                            for j in range(height):
                                for i in range(width):
                                    saved_value = None
                                    segment_id = 0

                                    if j >= sj and i >= si and j < sjm and i < sim:
                                        saved_value = int(
                                            saved_map_data.data[
                                                (i - si) + ((j - sj) * saved_map_data.dimensions.width)
                                            ]
                                        )
                                        segment_id = int(saved_value & 0x3F)

                                    clean_value = 0
                                    if not restored_map and j >= nj and i >= ni and j < njm and i < nim:
                                        raw_clean = int(
                                            map_data.data[(i - ni) + ((j - nj) * map_data.dimensions.width)]
                                        )
                                        clean_value = int(raw_clean & 3)

                                    # is_carpet = False
                                    # if clean_value == 3:
                                    #    is_carpet = True
                                    # elif saved_value is not None and (saved_value & 0x40) == 64:
                                    #    is_carpet = True
                                    #
                                    # if is_carpet:
                                    #    carpet_pixels.append((i, j))

                                    if segment_id != 0 or map_data.empty_map:
                                        if restored_map and saved_value is not None:
                                            is_border = (saved_value >> 7) == 1
                                            if is_border:
                                                pixel_type[i, j] = MapPixelType.WALL.value
                                            elif saved_value == 63:
                                                pixel_type[i, j] = MapPixelType.NEW_SEGMENT.value
                                            else:
                                                pixel_type[i, j] = segment_id
                                        else:
                                            if clean_value == 2:
                                                pixel_type[i, j] = MapPixelType.WALL.value
                                            else:
                                                pixel_type[i, j] = segment_id
                                    elif clean_value != 0:
                                        if clean_value == 1:
                                            pixel_type[i, j] = MapPixelType.FLOOR.value
                                        elif clean_value == 2:
                                            pixel_type[i, j] = MapPixelType.WALL.value
                                        elif clean_value == 3:
                                            pixel_type[i, j] = MapPixelType.NEW_SEGMENT.value
                                        else:
                                            pixel_type[i, j] = clean_value

                            map_data.combined_pixel_type = pixel_type
                            map_data.combined_dimensions = MapImageDimensions(
                                top, left, height, width, map_data.dimensions.grid_size
                            )

                            map_data.empty_map = False

                            if carpet_pixels:
                                map_data.carpet_pixels = carpet_pixels
                            elif restored_map:
                                map_data.carpet_pixels = DreameVacuumMapDecoder.get_carpets(map_data, saved_map_data)
                        else:
                            # map_data.data = saved_map_data.data
                            map_data.combined_pixel_type = saved_map_data.pixel_type
                            map_data.combined_dimensions = saved_map_data.dimensions
                            map_data.carpet_pixels = saved_map_data.carpet_pixels
                    else:
                        if saved_map_data.segments is not None:
                            if map_data.segments is None and (
                                map_data.saved_map_status == 1 or map_data.saved_map_status == 0
                            ):
                                map_data.segments = {}

                            map_data.cleaning_sequence = saved_map_data.cleaning_sequence
                            map_data.mop_type = saved_map_data.mop_type or map_data.mop_type

                            for k, v in saved_map_data.segments.items():
                                if map_data.segments and k in map_data.segments:
                                    # as implemented on the app
                                    map_data.segments[k].icon = v.icon
                                    map_data.segments[k].name = v.name
                                    map_data.segments[k].custom_name = v.custom_name
                                    map_data.segments[k].type = v.type
                                    map_data.segments[k].index = v.index
                                    map_data.segments[k].unique_id = v.unique_id
                                    map_data.segments[k].color_index = v.color_index
                                    map_data.segments[k].neighbors = v.neighbors
                                    map_data.segments[k].floor_material = v.floor_material
                                    map_data.segments[k].floor_material_direction = v.floor_material_direction
                                    map_data.segments[k].visibility = v.visibility
                                    map_data.segments[k].carpet_cleaning = v.carpet_cleaning
                                    map_data.segments[k].carpet_preferences = v.carpet_preferences
                                    if map_data.saved_map_status == 2:
                                        map_data.segments[k].x = v.x
                                        map_data.segments[k].y = v.y
                                elif map_data.saved_map_status == 2:
                                    map_data.segments[k] = copy.deepcopy(v)
                                    map_data.segments[k].unmapped = True
                                    if map_data.unmapped_segments is None:
                                        map_data.unmapped_segments = []
                                    map_data.unmapped_segments.append(k)

                    if not saved_map_data.cleanset:
                        saved_map_data.cleanset = copy.deepcopy(map_data.cleanset)

                    if (
                        (map_data.saved_map_status == 2 or map_data.docked)
                        and map_data.charger_position is None
                        and not map_data.saved_map
                        and not map_data.recovery_map
                        and saved_map_data.charger_position
                    ):
                        map_data.charger_position = saved_map_data.charger_position

                    map_data.walls = saved_map_data.walls
                    map_data.walls_version = saved_map_data.walls_version
                    map_data.doors = saved_map_data.doors
                    # map_data.ai_outborders_ar_origin = saved_map_data.ai_outborders_ar_origin
                    # map_data.ai_furniture_ar_origin = saved_map_data.ai_furniture_ar_origin
                    # map_data.ai_furniture_ar_origin_v2 = saved_map_data.ai_furniture_ar_origin_v2

                    if map_data.saved_map_status == 2:
                        map_data.no_go_areas = saved_map_data.no_go_areas
                        map_data.no_mopping_areas = saved_map_data.no_mopping_areas
                        map_data.virtual_walls = saved_map_data.virtual_walls
                        map_data.virtual_thresholds = saved_map_data.virtual_thresholds
                        map_data.passable_thresholds = saved_map_data.passable_thresholds
                        map_data.impassable_thresholds = saved_map_data.impassable_thresholds
                        map_data.ramps = saved_map_data.ramps
                        map_data.carpets = saved_map_data.carpets
                        map_data.deleted_carpets = saved_map_data.deleted_carpets
                        map_data.detected_carpets = saved_map_data.detected_carpets
                        map_data.router_position = saved_map_data.router_position
                        map_data.curtains = saved_map_data.curtains
                        map_data.hidden_segments = saved_map_data.hidden_segments
                        map_data.predefined_points = saved_map_data.predefined_points
                        map_data.cleaning_sequence = saved_map_data.cleaning_sequence
                        if map_data.cleaning_sequence is not None and map_data.version == 1:
                            map_data.version = 2
                        map_data.mop_type = saved_map_data.mop_type or map_data.mop_type
                        if saved_map_data.saved_furnitures is not None:
                            map_data.furnitures = saved_map_data.saved_furnitures
                            map_data.furniture_version = saved_map_data.furniture_version
                            saved_map_data.furnitures = saved_map_data.saved_furnitures

                        if map_data.version == 0:
                            map_data.segments = copy.deepcopy(saved_map_data.segments)
                            map_data.charger_position = copy.deepcopy(saved_map_data.charger_position)

                    if not map_data.carpet_pixels:
                        map_data.carpet_pixels = DreameVacuumMapDecoder.get_carpets(map_data, saved_map_data)

                    if map_data.clean_log:
                        map_data.wifi_map_data = DreameVacuumMapDecoder.decode_wifi_map_data(
                            map_data, saved_map_data.wifi_map_data
                        )

            if "whmp" in data_json:
                router_position = data_json["whmp"]
                if router_position and len(router_position) > 1:
                    map_data.router_position = Point(
                        router_position[0],
                        router_position[1],
                    )

            wifi_map = data_json.get("whm")
            if (map_data.version == 3 or map_data.saved_map) and wifi_map and len(wifi_map) > 1:
                wifi_map_data = DreameVacuumMapDecoder.decode_saved_map(
                    data_json["whm"], map_data.version, map_data.rotation
                )
                if wifi_map_data:
                    map_data.wifi_map_data = wifi_map_data
                    if map_data.wifi_map_data.router_position is None:
                        map_data.wifi_map_data.router_position = map_data.router_position

            if not map_data.saved_map and map_data.version == 3:
                map_data.saved_map_id = data_json.get("saveMapId")

            if (
                not map_data.saved_map
                and map_data.robot_position is None
                and map_data.docked
                and map_data.charger_position
            ):
                map_data.robot_position = copy.deepcopy(map_data.charger_position)

            walls_info = data_json.get("walls_info")
            if walls_info and "walls_info" in walls_info:
                walls_info = walls_info["walls_info"]

            if map_data.segments:
                DreameVacuumMapDecoder.set_station_segment(map_data)

                if not map_data.saved_map:
                    DreameVacuumMapDecoder.set_robot_segment(map_data)

                if map_data.saved_map or map_data.version == 3:
                    DreameVacuumMapDecoder.set_segment_color_index(map_data, segment_info, walls_info)

            if "nopush" in data_json:
                map_data.notified = bool(data_json["nopush"])

            if "funiture_info" in data_json:
                map_data.furniture_version = 2
                map_data.saved_furnitures = {}
                index = 0
                for furniture in data_json["funiture_info"]:
                    index = index + 1
                    furniture_type = int(furniture[1])
                    if furniture_type == 8:
                        furniture_type = 25
                    elif furniture_type == 25:
                        furniture_type = 8

                    if furniture[3] > 0 and furniture[4] > 0:
                        if furniture_type in FurnitureType._value2member_map_:
                            map_data.saved_furnitures[index] = Furniture(
                                int(furniture[6]),
                                int(furniture[7]),
                                int(furniture[6] - (furniture[3] / 2)),
                                int(furniture[7] - (furniture[4] / 2)),
                                furniture[3],
                                furniture[4],
                                FurnitureType(furniture_type),
                                furniture[13] if len(furniture) >= 14 and furniture[13] else 0,
                                furniture[9],
                                furniture[12],
                                furniture[0],
                                furniture[2],
                            )
                        elif furniture_type != 27 and furniture_type != 28:  ## Missing furnitures from app
                            _LOGGER.debug("Unknown furniture type: %s", furniture_type)

            if map_data.version == 3:
                map_data.furnitures = map_data.saved_furnitures

            if map_data.furnitures is None:
                furniture_key = (
                    "ai_furniture_user"
                    if "ai_furniture_user" in data_json and len(data_json["ai_furniture_user"])
                    else (
                        "ai_furniture_new"
                        if "ai_furniture_new" in data_json and len(data_json["ai_furniture_new"])
                        else "ai_furniture"
                    )
                )
                if furniture_key in data_json:
                    map_data.furniture_version = (
                        1 if furniture_key == "ai_furniture_user" or furniture_key == "ai_furniture_new" else 0
                    )
                    map_data.furnitures = {}
                    index = 0
                    for furniture in data_json[furniture_key]:
                        size = len(furniture)
                        if size >= 4:
                            furniture_type = int(furniture[2])
                            index = index + 1
                            if furniture_type in FurnitureType._value2member_map_:
                                center_x = int(furniture[0])
                                center_y = int(furniture[1])
                                start_x0 = center_x
                                start_y0 = center_y
                                rect_width = 0
                                rect_height = 0
                                angle = 0
                                scale = 1.0
                                if size >= 8:
                                    map_data.furniture_version = 1
                                    start_x0 = int(furniture[4])
                                    start_y0 = int(furniture[5])
                                    rect_width = abs(int(furniture[6]))
                                    rect_height = abs(int(furniture[7]))
                                    if size >= 9:
                                        angle = float(furniture[8])
                                        if furniture_key == "ai_furniture":
                                            if angle == 180:
                                                angle = 0
                                            elif angle == 0:
                                                angle = 180
                                    if size >= 10:
                                        scale = float(furniture[9])
                                else:
                                    map_data.furniture_version = 0

                                map_data.furnitures[index] = Furniture(
                                    center_x,
                                    center_y,
                                    start_x0,
                                    start_y0,
                                    rect_width,
                                    rect_height,
                                    FurnitureType(furniture_type),
                                    int(furniture[3]),
                                    angle,
                                    scale,
                                )

            obstacles = (
                changes["ai_obstacle"] if changes and "ai_obstacle" in changes else data_json.get("ai_obstacle")
            )
            if obstacles:
                map_data.obstacles = {}
                index = 1
                for obstacle in obstacles:
                    size = len(obstacle)
                    if size >= 4:
                        obstacle_type = int(obstacle[2])
                        small_object = (
                            size > 17
                            and str(obstacle[17]) == "1"
                            and ((128 <= obstacle_type <= 138) or obstacle_type == 142)
                        )
                        if obstacle_type not in ObstacleType._value2member_map_:
                            if obstacle_type == 160 or obstacle_type == 166 or obstacle_type == 207 or small_object:
                                continue
                            obstacle_type = ObstacleType.OBSTACLE.value

                        possibility = 0 if small_object else int(float(obstacle[3]) * 100)
                        if map_data.version == 3 and possibility > 0 and possibility < 50:
                            continue

                        ignore_status = (
                            int(obstacle[18])
                            if size > 18
                            else (
                                int(obstacle[-1])
                                if len(str(obstacle[-1])) == 1 and (0 <= int(obstacle[-1]) <= 4)
                                else 0
                            )
                        )

                        if (
                            ignore_status == ObstacleIgnoreStatus.HIDDEN.value
                            or ignore_status == ObstacleIgnoreStatus.ERROR.value
                            or (
                                ignore_status == ObstacleIgnoreStatus.AUTOMATICALLY_IGNORED.value
                                and map_data.version == 3
                            )
                        ):
                            continue

                        id = obstacle[4] if size >= 5 and str(obstacle[4]).strip() != "" else None

                        x = float(obstacle[0])
                        y = float(obstacle[1])

                        if size >= 7 and (
                            (id is not None and str(id).replace(".", "", 1).isdigit() and float(id) >= 1000)
                            or obstacle_type == ObstacleType.BLOCKED_ROOM.value
                        ):
                            if size >= 8:
                                map_data.obstacles[str(index)] = Obstacle(
                                    x,
                                    y,
                                    ObstacleType(obstacle_type),
                                    possibility,
                                    id,
                                    obstacle[5],
                                    obstacle[6],
                                    float(obstacle[7]) * 100 if str(obstacle[7]).strip() != "" else 0.0,
                                    float(obstacle[8]) * 100 if str(obstacle[8]).strip() != "" else 0.0,
                                    (
                                        float(obstacle[9]) * 100
                                        if size >= 10 and str(obstacle[9]).strip() != ""
                                        else None
                                    ),
                                    (
                                        float(obstacle[10]) * 100
                                        if size >= 11 and str(obstacle[10]).strip() != ""
                                        else None
                                    ),
                                    int(obstacle[11]) if size >= 13 and str(obstacle[11]).strip() != "" else 2,
                                    ignore_status,
                                    int(obstacle[12]) if size >= 13 else None,
                                )
                            else:
                                map_data.obstacles[str(index)] = Obstacle(
                                    x, y, ObstacleType(obstacle_type), possibility, id, obstacle[6], obstacle[5]
                                )
                        else:
                            map_data.obstacles[str(index)] = Obstacle(
                                x, y, ObstacleType(obstacle_type), possibility, id, ignore_status=ignore_status
                            )

                        index = index + 1

                DreameVacuumMapDecoder.set_obstacle_segment(map_data, map_data.obstacles)

            laser_obstacle = (
                overrides["laser_obstacle"]
                if overrides and "laser_obstacle" in overrides
                else data_json.get("laser_obstacle")
            )
            if laser_obstacle:
                if map_data.laser_obstacles is None:
                    map_data.laser_obstacles = {}

                index = 1
                for obstacle in laser_obstacle:
                    size = len(obstacle)
                    if size >= 3:
                        obstacle_type = int(obstacle[2])
                        if obstacle_type not in ObstacleType._value2member_map_:
                            obstacle_type = ObstacleType.OBSTACLE.value

                        map_data.laser_obstacles[str(index)] = Obstacle(
                            float(obstacle[0]),
                            float(obstacle[1]),
                            ObstacleType(obstacle_type),
                            int(float(obstacle[3]) * 100) if size > 3 else 100,
                            obstacle[4] if size >= 5 else None,
                        )
                        index = index + 1

                DreameVacuumMapDecoder.set_obstacle_segment(map_data, map_data.laser_obstacles)

            def parse_areas(areas):
                if areas is not None:
                    map_areas = []
                    for area in areas:
                        x_coords = sorted([area[0], area[2]])
                        y_coords = sorted([area[1], area[3]])
                        map_areas.append(
                            Area(
                                x_coords[0],
                                y_coords[0],
                                x_coords[1],
                                y_coords[0],
                                x_coords[1],
                                y_coords[1],
                                x_coords[0],
                                y_coords[1],
                                area[4] if len(area) > 4 else None,
                            )
                        )
                    return map_areas

            def parse_lines(lines):
                if lines is not None:
                    return [
                        Line(
                            wall[0],
                            wall[1],
                            wall[2],
                            wall[3],
                        )
                        for wall in lines
                    ]

            def parse_carpets(carpets):
                if carpets is not None:
                    map_carpets = []
                    for carpet in carpets:
                        map_carpets.append(
                            Carpet(
                                int(carpet[4]) if len(carpet) > 4 else None,
                                carpet[0],
                                carpet[1],
                                carpet[2],
                                carpet[1],
                                carpet[2],
                                carpet[3],
                                carpet[0],
                                carpet[3],
                                carpet[5] if len(carpet) > 5 else None,
                                carpet[6] if len(carpet) > 6 else None,
                                None,
                                None,
                                None,
                                carpet[7] if len(carpet) > 7 else None,
                                carpet[8:13] if len(carpet) > 12 else None,
                            )
                        )
                    return map_carpets

            if "vw" in data_json:
                virtual_walls = data_json["vw"]
                if virtual_walls:
                    if not map_data.no_go_areas:
                        map_data.no_go_areas = parse_areas(virtual_walls.get("rect"))
                    if not map_data.no_mopping_areas:
                        map_data.no_mopping_areas = parse_areas(virtual_walls.get("mop"))
                    if not map_data.virtual_walls:
                        map_data.virtual_walls = parse_lines(virtual_walls.get("line"))
                    if not map_data.carpets:
                        map_data.carpets = parse_carpets(virtual_walls.get("addcpt"))
                    if not map_data.deleted_carpets:
                        map_data.deleted_carpets = parse_carpets(virtual_walls.get("nocpt"))

            recommended_virtual_walls = data_json.get("rec_vw")
            if recommended_virtual_walls:
                map_data.recommended_area_type = recommended_virtual_walls.get("type")
                map_data.recommended_no_go_areas = parse_areas(recommended_virtual_walls.get("rect"))
                map_data.recommended_no_mopping_areas = parse_areas(recommended_virtual_walls.get("mop"))
                map_data.recommended_virtual_walls = parse_lines(recommended_virtual_walls.get("line"))
                map_data.recommended_carpets = parse_carpets(recommended_virtual_walls.get("carpet"))

            if "vws" in data_json:
                virtual_thresholds = data_json["vws"]
                if not map_data.virtual_thresholds:
                    map_data.virtual_thresholds = parse_lines(virtual_thresholds.get("vwsl"))
                if "npthrsd" in virtual_thresholds:
                    map_data.passable_thresholds = map_data.virtual_thresholds
                    map_data.virtual_thresholds = None

                    if not map_data.impassable_thresholds:
                        map_data.impassable_thresholds = parse_lines(virtual_thresholds["npthrsd"])
                if not map_data.ramps:
                    map_data.ramps = parse_areas(virtual_thresholds.get("ramp"))
                if not map_data.cliffs:
                    map_data.cliffs = parse_lines(virtual_thresholds.get("cliff"))

            recommended_virtual_thresholds = data_json.get("rec_vws")
            if recommended_virtual_thresholds:
                map_data.recommended_threshold_type = recommended_virtual_walls.get("type")
                map_data.recommended_virtual_thresholds = parse_areas(recommended_virtual_thresholds.get("vwsl"))
                if "npthrsd" in recommended_virtual_thresholds:
                    map_data.recommended_passable_thresholds = map_data.recommended_virtual_thresholds
                    map_data.recommended_virtual_thresholds = None
                    map_data.recommended_impassable_thresholds = parse_lines(recommended_virtual_thresholds["npthrsd"])
                map_data.recommended_ramps = parse_areas(recommended_virtual_thresholds.get("ramp"))
                map_data.recommended_cliffs = parse_lines(recommended_virtual_thresholds.get("cliff"))

            curtains = data_json.get("curtain") if "curtain" in data_json else data_json.get("ct")
            if curtains:
                if isinstance(curtains, dict) and "line" in curtains and not map_data.curtains:
                    map_data.curtains = []
                    for line in curtains["line"]:
                        map_data.curtains.append(
                            Line(
                                line[0],
                                line[1],
                                line[2],
                                line[3],
                            )
                        )

            if "carpet_polygon" in data_json and len(data_json["carpet_polygon"]):
                map_data.detected_carpets = []
                for carpet_id in data_json["carpet_polygon"]:
                    carpet = data_json["carpet_polygon"][carpet_id]
                    if len(carpet) > 0 and len(carpet[0]) >= 8:
                        coords = carpet[0]
                        x_coords = []
                        y_coords = []
                        for k in range(0, len(coords), 2):
                            x_coords.append(coords[k])
                            y_coords.append(coords[k + 1])

                        max_x = max(x_coords)
                        max_y = max(y_coords)
                        min_x = min(x_coords)
                        min_y = min(y_coords)

                        map_data.detected_carpets.append(
                            Carpet(
                                int(carpet_id),
                                min_x,
                                min_y,
                                max_x,
                                min_y,
                                max_x,
                                max_y,
                                min_x,
                                max_y,
                                False,
                                int(carpet[1]) if len(carpet) > 1 else None,
                                None,
                                None,
                                coords,
                                not (len(carpet) <= 2 or carpet[2] == 0),
                            )
                        )
            elif "carpet_info" in data_json:
                map_data.detected_carpets = []
                for carpet_id in data_json["carpet_info"]:
                    carpet = data_json["carpet_info"][carpet_id]
                    map_data.detected_carpets.append(
                        Carpet(
                            int(carpet_id),
                            carpet[0],
                            carpet[1],
                            carpet[2],
                            carpet[1],
                            carpet[2],
                            carpet[3],
                            carpet[0],
                            carpet[3],
                            carpet[6] if len(carpet) > 6 else None,
                            None,
                            carpet[5] if len(carpet) > 5 else None,
                            carpet[4],
                        )
                    )

            low_lying_area_key = "sneak_areas_end" if "sneak_areas_end" in data_json else "sneak_areas"
            low_lying_areas = (
                changes["sneak_areas"] if changes and "sneak_areas" in changes else data_json.get(low_lying_area_key)
            )

            if low_lying_areas and not map_data.low_lying_areas:
                map_data.low_lying_areas = []
                for area in low_lying_areas:
                    coords = area["roi"]
                    x_coords = []
                    y_coords = []
                    for k in range(0, len(coords), 2):
                        x_coords.append(coords[k])
                        y_coords.append(coords[k + 1])

                    max_x = max(x_coords)
                    max_y = max(y_coords)
                    min_x = min(x_coords)
                    min_y = min(y_coords)

                    map_data.low_lying_areas.append(
                        Polygon(
                            area["id"],
                            min_x,
                            min_y,
                            max_x,
                            min_y,
                            max_x,
                            max_y,
                            min_x,
                            max_y,
                            coords,
                            area.get("type"),
                            area.get("hide"),
                            area.get("ms"),
                            area.get("area"),
                        )
                    )

            if "pointinfo" in data_json:
                points_raw = data_json["pointinfo"]
                points = {}

                if isinstance(points_raw, list) and len(points_raw) > 0 and points_raw[0]:
                    points = points_raw[0]
                elif isinstance(points_raw, dict):
                    points = points_raw

                if points:
                    if not map_data.predefined_points:
                        if "spoint" in points:
                            map_data.predefined_points = []
                            for point in points["spoint"]:
                                map_data.predefined_points.append(
                                    Coordinate(
                                        point[0],
                                        point[1],
                                        bool(point[2]),
                                        point[3],
                                    )
                                )
                        else:
                            map_data.predefined_points = []

                    target_points = points.get("tpoint", points.get("spoint"))
                    if target_points and not map_data.active_cruise_points:
                        map_data.active_cruise_points = {}
                        index = 0
                        for point in target_points:
                            index += 1
                            map_data.active_cruise_points[index] = Coordinate(
                                point[0],
                                point[1],
                                bool(point[2]),
                                point[3],
                            )
                elif not map_data.predefined_points:
                    map_data.predefined_points = []

            if "tpointinfo" in data_json:
                map_data.task_cruise_points = {}

                for point in data_json["tpointinfo"]:
                    index = index + 1
                    map_data.task_cruise_points[index] = Coordinate(
                        point[0],
                        point[1],
                        bool(point[2]),
                        point[3],
                    )

            if "area_clean_detail" in data_json and not restored_map:
                values = data_json["area_clean_detail"]
                if values:
                    map_data.blocked_segments = {
                        v[0]: ([ObstacleReason(v[1]), v[2], v[3]] if len(v) > 3 else [ObstacleReason(v[1])])
                        for v in values
                        if v[1] in ObstacleReason._value2member_map_
                    }

            if "decmap" in data_json or map_data.multiple_cleaning_time:
                map_data.cleaning_map_data = DreameVacuumMapDecoder.decode_cleaning_map_data(
                    map_data, data_json.get("decmap")
                )
                if map_data.cleaning_map_data:
                    map_data.cleaned_segments = map_data.cleaning_map_data.cleaned_segments
                    map_data.has_dirty_area = map_data.cleaning_map_data.has_dirty_area
                    map_data.has_cleaned_area = map_data.cleaning_map_data.has_cleaned_area

            if map_data.obstacles and not restored_map:
                obstacles = copy.deepcopy(map_data.obstacles)
                for k, v in obstacles.items():
                    if v.type == ObstacleType.BLOCKED_ROOM:
                        if (
                            map_data.blocked_segments
                            and v.segment_id in map_data.blocked_segments
                            and (
                                len(map_data.blocked_segments[v.segment_id]) <= 4
                                or map_data.blocked_segments[v.segment_id][4] != 0
                            )
                        ):
                            blocked_segment = map_data.blocked_segments[v.segment_id]
                            obstacle = map_data.obstacles[k]
                            obstacle.reason = blocked_segment[0]
                            if len(blocked_segment) > 1:
                                obstacle.x = blocked_segment[1]
                                obstacle.y = blocked_segment[2]
                        else:
                            del map_data.obstacles[k]

            if map_data.blocked_segments and not map_data.cleaning_map and not restored_map:
                for k, v in map_data.blocked_segments.items():
                    if len(v) <= 4 or v[4] != 0:
                        found = False
                        if map_data.obstacles:
                            for obstacle in map_data.obstacles.values():
                                if obstacle.type == ObstacleType.BLOCKED_ROOM and obstacle.segment_id == k:
                                    found = True
                                    break

                        if not found:
                            obstacle = Obstacle(k, 0, ObstacleType.BLOCKED_ROOM, None)
                            obstacle.reason = v[0]
                            if obstacle.segment_id in map_data.segments:
                                segment = map_data.segments[obstacle.segment_id]
                                obstacle.x = segment.x
                                obstacle.y = segment.y
                                obstacle.segment = segment.name
                                obstacle.color_index = segment.color_index

                            if len(v) > 1:
                                obstacle.x = v[1]
                                obstacle.y = v[2]

                            if not map_data.obstacles:
                                map_data.obstacles = {}
                            map_data.obstacles[str(len(map_data.obstacles) + 1 if map_data.obstacles else 1)] = (
                                obstacle
                            )

            if changes:
                deleted_objects = changes.get("deleteContent")
                if deleted_objects:
                    if "ai_obstacle" in deleted_objects:
                        map_data.deleted_obstacles = deleted_objects["ai_obstacle"]
                    if "sneak_areas" in deleted_objects:
                        map_data.deleted_low_lying_areas = deleted_objects["sneak_areas"]
                    if "rec_vw" in deleted_objects:
                        map_data.deleted_recommended_area_type = deleted_objects["rec_vw"]  ## TODO: Handle this

            if not map_data.walls and walls_info and "storeys" in walls_info:
                map_data.walls_version = walls_info.get("version_flag")

                segment_ids = [int(k) for k in map_data.segments.keys()] if map_data.segments else []
                storey = walls_info["storeys"][0]
                walls = {}
                index = 0
                rooms_data = storey.get("rooms", []) or storey.get("r", [])
                for room in rooms_data:
                    segment_id = room.get("room_id") or room.get("i")

                    if segment_id not in map_data.segments and not (
                        map_data.hidden_segments and segment_id in map_data.hidden_segments
                    ):
                        continue

                    walls_data = room.get("walls", []) or room.get("w", [])
                    walls[segment_id] = []
                    for wall in walls_data:
                        if isinstance(wall, list):
                            type = wall[0]
                            initial_x0 = wall[1]
                            initial_y0 = wall[2]
                            initial_x1 = wall[3]
                            initial_y1 = wall[4]
                            x = wall[5]
                            y = wall[6]
                            if len(wall) > 7:
                                x0 = wall[7]
                                y0 = wall[8]
                                x1 = wall[9]
                                y1 = wall[10]
                            else:
                                x0 = initial_x0
                                y0 = initial_y0
                                x1 = initial_x1
                                y1 = initial_y1
                        else:
                            type = wall.get("type")
                            x = wall.get("normal_x")
                            y = wall.get("normal_y")
                            initial_x0 = wall.get("beg_pt_x")
                            initial_y0 = wall.get("beg_pt_y")
                            initial_x1 = wall.get("end_pt_x")
                            initial_y1 = wall.get("end_pt_y")

                            x0 = wall.get("_beg_pt_x", initial_x0)
                            y0 = wall.get("_beg_pt_y", initial_y0)
                            x1 = wall.get("_end_pt_x", initial_x1)
                            y1 = wall.get("_end_pt_y", initial_y1)

                        walls[segment_id].append(
                            Wall(
                                x0,
                                y0,
                                x1,
                                y1,
                                initial_x0,
                                initial_y0,
                                initial_x1,
                                initial_y1,
                                type,
                                None,
                                x,
                                y,
                            )
                        )

                doors = []
                doors_data = storey.get("doors", []) or storey.get("d", [])
                for door in doors_data:
                    if isinstance(door, list):
                        type = door[0]
                        id = door[1]
                        status = door[2]
                        initial_x0 = door[3]
                        initial_y0 = door[4]
                        initial_x1 = door[5]
                        initial_y1 = door[6]
                        if len(door) > 7:
                            x0 = door[7]
                            y0 = door[8]
                            x1 = door[9]
                            y1 = door[10]
                        else:
                            x0 = initial_x0
                            y0 = initial_y0
                            x1 = initial_x1
                            y1 = initial_y1
                    else:
                        type = door.get("door_type")
                        id = door.get("door_id")
                        status = door.get("door_status")
                        initial_x0 = door.get("beg_pt_x")
                        initial_y0 = door.get("beg_pt_y")
                        initial_x1 = door.get("end_pt_x")
                        initial_y1 = door.get("end_pt_y")

                        x0 = door.get("_beg_pt_x", initial_x0)
                        y0 = door.get("_beg_pt_y", initial_y0)
                        x1 = door.get("_end_pt_x", initial_x1)
                        y1 = door.get("_end_pt_y", initial_y1)

                    doors.append(
                        Wall(
                            x0,
                            y0,
                            x1,
                            y1,
                            initial_x0,
                            initial_y0,
                            initial_x1,
                            initial_y1,
                            type,
                            id,
                            None,
                            None,
                            status,
                        )
                    )

                map_data.walls = walls
                map_data.doors = doors
            elif saved_map_data and saved_map_data.walls and map_data.saved_map_status == 2:
                map_data.walls = saved_map_data.walls
                map_data.walls_version = saved_map_data.walls_version
                map_data.doors = saved_map_data.doors

            if map_data.walls:
                if map_data.wifi_map_data:
                    map_data.wifi_map_data.walls = map_data.walls
                    map_data.wifi_map_data.walls_version = map_data.walls_version
                    map_data.wifi_map_data.doors = map_data.doors
                    map_data.wifi_map_data.hidden_segments = map_data.hidden_segments
                if map_data.cleaning_map_data:
                    map_data.cleaning_map_data.walls = map_data.walls
                    map_data.cleaning_map_data.walls_version = map_data.walls_version
                    map_data.cleaning_map_data.doors = map_data.doors
                    map_data.cleaning_map_data.hidden_segments = map_data.hidden_segments
            # map_data.ai_outborders_user = data_json.get("ai_outborders_user")
            # map_data.ai_outborders = data_json.get("ai_outborders")
            # map_data.ai_outborders_new = data_json.get("ai_outborders_new")
            # map_data.ai_outborders_2d = data_json.get("ai_outborders_2d")
            # map_data.ai_outborders_ar_origin = data_json.get("ai_outborders_ar_origin")

            # map_data.ai_furniture_ar_origin = data_json.get("ai_furniture_ar_origin")
            # map_data.ai_furniture_ar_origin_v2 = data_json.get("ai_furniture_ar_origin_v2")
            # map_data.ai_furniture_warning = data_json.get("ai_furniture_warning")

            elif map_data.version == 0 and not map_data.saved_map:
                map_data.need_optimization = not restored_map
        except Exception:
            _LOGGER.error("Map Parse Failed: %s", traceback.format_exc())

        return map_data, saved_map_data

    @staticmethod
    def decode_p_map_data_from_partial(partial_map: MapDataPartial, current_map_data: MapData) -> MapData | None:
        if partial_map.frame_type != MapFrameType.P.value:
            return current_map_data

        partial_map.version = current_map_data.version

        map_data, saved_map_data = DreameVacuumMapDecoder.decode_map_data_from_partial(partial_map)
        if map_data is None:
            return current_map_data

        try:
            current_map_data.frame_id = map_data.frame_id
            current_map_data.robot_position = map_data.robot_position
            current_map_data.timestamp_ms = map_data.timestamp_ms
            current_map_data.docked = map_data.docked
            current_map_data.line_to_robot = map_data.line_to_robot
            current_map_data.temporary_map = map_data.temporary_map
            current_map_data.small_path = map_data.small_path
            current_map_data.mop_type = map_data.mop_type or current_map_data.mop_type
            current_map_data.laser_obstacles = map_data.laser_obstacles
            current_map_data.skipped_segments = map_data.skipped_segments
            current_map_data.skipped_areas = map_data.skipped_areas
            current_map_data.completed_segments = map_data.completed_segments
            current_map_data.pending_segments = map_data.pending_segments
            current_map_data.extra_clean_segments = map_data.extra_clean_segments
            current_map_data.extra_clean_areas = map_data.extra_clean_areas
            current_map_data.extra_cleaned_segments = map_data.extra_cleaned_segments
            current_map_data.saved_map = False
            current_map_data.empty_map = False
            current_map_data.restored_map = False
            current_map_data.recovery_map = False
            current_map_data.clean_log = False
            current_map_data.frame_map = True

            if map_data.docked is not None:
                current_map_data.docked = map_data.docked

            if map_data.charger_position is not None and (
                map_data.version != 0 or current_map_data.saved_map_status != 2
            ):
                current_map_data.charger_position = map_data.charger_position

            if map_data.obstacles is not None:
                if map_data.version == 3:
                    if current_map_data.obstacles is None:
                        current_map_data.obstacles = {}
                    for obstacle in map_data.obstacles.values():
                        index = next(
                            (key for key, item in current_map_data.obstacles.items() if obstacle.index == item.index),
                            -1,
                        )

                        if index != -1:
                            current_map_data.obstacles[str(index)] = obstacle
                        else:
                            current_map_data.obstacles[str(len(current_map_data.obstacles) + 1)] = obstacle

                    if map_data.deleted_obstacles:
                        del_set = set(map(str, map_data.deleted_obstacles))
                        current_map_data.obstacles = {
                            str(i): item
                            for i, item in enumerate(
                                (x for x in current_map_data.obstacles.values() if str(x.index) not in del_set),
                                start=1,
                            )
                        }
                else:
                    current_map_data.obstacles = map_data.obstacles

            if map_data.detected_carpets is not None:
                current_map_data.detected_carpets = map_data.detected_carpets

            if map_data.active_cruise_points is not None:
                current_map_data.active_cruise_points = map_data.active_cruise_points

            if map_data.low_lying_areas is not None:
                if map_data.version == 3:
                    if current_map_data.low_lying_areas is None:
                        current_map_data.low_lying_areas = []
                    for area in map_data.low_lying_areas:
                        index = next(
                            (i for i, item in enumerate(current_map_data.low_lying_areas) if area.id == item.id),
                            -1,
                        )

                        if index != -1:
                            current_map_data.low_lying_areas[index] = area
                        else:
                            current_map_data.low_lying_areas.append(area)

                    if map_data.deleted_low_lying_areas:
                        del_set = set(map(str, map_data.deleted_low_lying_areas))
                        current_map_data.low_lying_areas = list(
                            filter(lambda item: str(item.id) not in del_set, current_map_data.low_lying_areas)
                        )
                else:
                    current_map_data.low_lying_areas = map_data.low_lying_areas

            if map_data.carpet_cleanset is not None:
                current_map_data.carpet_cleanset = map_data.carpet_cleanset

            # P map only returns difference between its previous frame.
            # Calculate new map size and update the buffer according to the received data at received offset.
            if map_data.data:
                current_dimensions = current_map_data.dimensions
                new_dimensions = map_data.dimensions

                # Find max image size
                grid_size = new_dimensions.grid_size
                left = min(new_dimensions.left, current_dimensions.left)
                top = min(new_dimensions.top, current_dimensions.top)
                max_left = max(
                    new_dimensions.left + (new_dimensions.width * grid_size),
                    current_dimensions.left + (current_dimensions.width * current_dimensions.grid_size),
                )
                max_top = max(
                    new_dimensions.top + (new_dimensions.height * grid_size),
                    current_dimensions.top + (current_dimensions.height * current_dimensions.grid_size),
                )

                # Calculate new image size
                width = int((max_left - left) / grid_size)
                height = int((max_top - top) / grid_size)

                # Create new buffer
                data = np.zeros((width * height), np.uint8)
                pixel_type = np.full((width, height), MapPixelType.OUTSIDE.value, dtype=np.uint8)

                # Calculate old image offset
                left_offset = int((current_dimensions.left - left) / current_dimensions.grid_size)
                top_offset = int((current_dimensions.top - top) / current_dimensions.grid_size)

                # Copy old image to buffer
                for y in range(current_dimensions.height):
                    for x in range(current_dimensions.width):
                        data[(width * (top_offset + y)) + left_offset + x] = current_map_data.data[
                            (current_dimensions.width * y) + x
                        ]
                        pixel_type[left_offset + x, top_offset + y] = current_map_data.pixel_type[x, y]

                # Calculate new image offset
                left_offset = int((new_dimensions.left - left) / grid_size)
                top_offset = int((new_dimensions.top - top) / grid_size)

                # Track carpet pixel changes with a set, list lookups are O(n) for every changed pixel
                carpet_set = (
                    set(current_map_data.carpet_pixels) if current_map_data.carpet_pixels is not None else None
                )
                added_carpet_pixels = []

                new_segments = []
                # Copy new image to buffer at calculated offset
                for y in range(new_dimensions.height):
                    for x in range(new_dimensions.width):
                        current_index = (new_dimensions.width * y) + x
                        new_data = map_data.data[current_index]
                        if new_data:
                            new_index = (width * (top_offset + y)) + left_offset + x

                            if map_data.version == 3:
                                # Directly set new pixel value on V3 map
                                new_value = new_data
                            else:
                                # Add current buffer value to new buffer value for finding the new pixel value
                                new_value = data[new_index] + new_data

                            data[new_index] = new_value

                            # Calculate the new pixel type from updated buffer value
                            pixel_type[left_offset + x, top_offset + y], carpet = (
                                DreameVacuumMapDecoder._get_pixel_type(current_map_data, int(new_value))
                            )

                            if carpet and carpet_set is None:
                                carpet_set = set()

                            if carpet_set is not None:
                                coord = (left_offset + x, top_offset + y)
                                if not carpet and coord in carpet_set:
                                    carpet_set.discard(coord)
                                elif carpet and coord not in carpet_set:
                                    carpet_set.add(coord)
                                    added_carpet_pixels.append(coord)

                if carpet_set is not None:
                    current_map_data.carpet_pixels = [
                        coord for coord in (current_map_data.carpet_pixels or []) if coord in carpet_set
                    ] + added_carpet_pixels

                # Update size and buffer
                current_map_data.data = bytes(data)
                current_map_data.pixel_type = pixel_type
                current_map_data.dimensions = MapImageDimensions(top, left, height, width, grid_size)

                current_map_data.combined_dimensions = None
                current_map_data.combined_pixel_type = None

                if current_map_data.segments is None:
                    current_map_data.segments = {}

                # Get new segment coords
                segments = DreameVacuumMapDecoder.get_segments(current_map_data)
                if segments:
                    for k in list(current_map_data.segments.keys()):
                        # Remove missing segments
                        if k not in segments:
                            if current_map_data.saved_map_status == 2:
                                current_map_data.segments[k].unmapped = True
                            else:
                                del current_map_data.segments[k]

                    for k, v in segments.items():
                        # Update current segment coords
                        if k in current_map_data.segments:
                            current_map_data.segments[k].unmapped = False
                            current_map_data.segments[k].coords = v.coords.copy()
                            if current_map_data.saved_map_status != 2:
                                current_map_data.segments[k].x = v.x
                                current_map_data.segments[k].y = v.y
                            current_map_data.segments[k].calculate_coords(current_map_data.dimensions)
                else:
                    if current_map_data.saved_map_status == 2:
                        for k, v in current_map_data.segments.items():
                            v.unmapped = True
                    else:
                        current_map_data.segments = {}

                current_map_data.unmapped_segments = None
                for k, v in current_map_data.segments.items():
                    if v.unmapped:
                        if current_map_data.unmapped_segments is None:
                            current_map_data.unmapped_segments = []
                        current_map_data.unmapped_segments.append(k)

                if map_data.version == 0:
                    current_map_data.need_optimization = True

            if map_data.path:
                # Append new paths received with P frame
                if current_map_data.path:
                    current_map_data.path.extend(map_data.path)
                else:
                    current_map_data.path = map_data.path

            DreameVacuumMapDecoder.set_robot_segment(current_map_data)
            DreameVacuumMapDecoder.set_station_segment(current_map_data)
            DreameVacuumMapDecoder.set_obstacle_segment(current_map_data, current_map_data.obstacles)
            DreameVacuumMapDecoder.set_obstacle_segment(current_map_data, current_map_data.laser_obstacles)

        except Exception:
            _LOGGER.error("P Map Parse Failed: %s", traceback.format_exc())

        return current_map_data

    @staticmethod
    def decode_wifi_map_data(map_data, wifi_map_data):
        if (
            wifi_map_data
            and not map_data.saved_map
            and not map_data.empty_map
            and not wifi_map_data.empty_map
            and wifi_map_data.wifi_map
        ):
            wifi_map = MapData()
            wifi_map.map_id = wifi_map_data.map_id
            wifi_map.frame_id = wifi_map_data.frame_id
            wifi_map.frame_type = wifi_map_data.frame_type
            wifi_map.timestamp_ms = map_data.timestamp_ms
            wifi_map.rotation = wifi_map_data.rotation
            wifi_map.router_position = wifi_map_data.router_position
            wifi_map.charger_position = map_data.charger_position
            wifi_map.robot_position = map_data.robot_position
            wifi_map.docked = map_data.docked
            wifi_map.saved_map_status = map_data.saved_map_status
            wifi_map.need_optimization = True
            wifi_map.saved_map = False
            wifi_map.wifi_map = True
            wifi_map.last_updated = map_data.timestamp_ms / 1000.0
            wifi_map_data.walls = map_data.walls
            wifi_map_data.walls_version = map_data.walls_version
            wifi_map_data.doors = map_data.doors
            wifi_map_data.hidden_segments = map_data.hidden_segments
            if wifi_map.docked and wifi_map.robot_position is None:
                wifi_map.robot_position = map_data.charger_position

            left = min(map_data.dimensions.left, wifi_map_data.dimensions.left)
            top = min(map_data.dimensions.top, wifi_map_data.dimensions.top)
            width = int(
                (
                    max(
                        map_data.dimensions.left + (map_data.dimensions.width * map_data.dimensions.grid_size),
                        wifi_map_data.dimensions.left
                        + (wifi_map_data.dimensions.width * wifi_map_data.dimensions.grid_size),
                    )
                    - left
                )
                / wifi_map_data.dimensions.grid_size
            )
            height = int(
                (
                    max(
                        map_data.dimensions.top + (map_data.dimensions.height * map_data.dimensions.grid_size),
                        wifi_map_data.dimensions.top
                        + (wifi_map_data.dimensions.height * wifi_map_data.dimensions.grid_size),
                    )
                    - top
                )
                / wifi_map_data.dimensions.grid_size
            )
            si = int((wifi_map_data.dimensions.left - left) / wifi_map_data.dimensions.grid_size)
            sj = int((wifi_map_data.dimensions.top - top) / wifi_map_data.dimensions.grid_size)
            sim = si + wifi_map_data.dimensions.width
            sjm = sj + wifi_map_data.dimensions.height
            ni = int((map_data.dimensions.left - left) / map_data.dimensions.grid_size)
            nj = int((map_data.dimensions.top - top) / map_data.dimensions.grid_size)
            nim = ni + map_data.dimensions.width
            njm = nj + map_data.dimensions.height
            pixel_type = np.zeros((width, height), np.uint8)

            for j in range(height):
                for i in range(width):
                    if j >= nj and i >= ni and j < njm and i < nim:
                        value = int(map_data.pixel_type[(i - ni), ((j - nj))])

                        if value > 100 and value < 200:
                            value = value - 100
                        if map_data.hidden_segments and value in map_data.hidden_segments:
                            continue

                        if value == 255 or (value > 200 and value < 232):
                            pixel_type[i, j] = 2
                        elif value:
                            pixel_type[i, j] = max(
                                (
                                    wifi_map_data.data[(i - si) + ((j - sj) * wifi_map_data.dimensions.width)] & 0x0F
                                    if j >= sj and i >= si and j < sjm and i < sim
                                    else 0
                                ),
                                10,
                            )

            wifi_map.pixel_type = pixel_type
            wifi_map.dimensions = MapImageDimensions(top, left, height, width, map_data.dimensions.grid_size)
            wifi_map_data.data = None
            return wifi_map
        return wifi_map_data

    @staticmethod
    def decode_cleaning_map_data(map_data, cleaning_map_str):
        partial_cleaning_map = None
        if cleaning_map_str and len(cleaning_map_str) > 1:
            partial_cleaning_map = DreameVacuumMapDecoder.decode_map_partial(cleaning_map_str, map_data.version)
            if partial_cleaning_map is None:
                return

        cleaning_map = MapData()
        if partial_cleaning_map:
            cleaning_map.map_id = partial_cleaning_map.map_id
            cleaning_map.frame_id = partial_cleaning_map.frame_id
            cleaning_map.frame_type = partial_cleaning_map.frame_type
            cleaning_map.timestamp_ms = partial_cleaning_map.timestamp_ms
            cleaning_map.cleaned_segments = partial_cleaning_map.data_json.get("CleanArea")
        else:
            cleaning_map.map_id = map_data.map_id
            cleaning_map.frame_id = map_data.frame_id
            cleaning_map.frame_type = map_data.frame_type
            cleaning_map.timestamp_ms = map_data.timestamp_ms

        cleaning_map.dimensions = map_data.dimensions
        cleaning_map.charger_position = map_data.charger_position
        cleaning_map.robot_position = map_data.robot_position
        cleaning_map.segments = map_data.segments
        cleaning_map.pixel_type = map_data.pixel_type.copy()
        cleaning_map.rotation = map_data.rotation
        cleaning_map.saved_map_status = map_data.saved_map_status
        cleaning_map.docked = map_data.docked
        cleaning_map.dos = map_data.dos
        cleaning_map.multiple_cleaning_time = map_data.multiple_cleaning_time
        cleaning_map.mop_wash_count = map_data.mop_wash_count
        cleaning_map.dust_collection_count = map_data.dust_collection_count
        cleaning_map.cleanup_method = map_data.cleanup_method
        cleaning_map.startup_method = map_data.startup_method
        cleaning_map.blocked_segments = map_data.blocked_segments
        cleaning_map.last_updated = map_data.timestamp_ms / 1000.0
        cleaning_map.history_map = True
        cleaning_map.saved_map = False
        cleaning_map.cleaning_map = True
        if cleaning_map.docked and cleaning_map.robot_position is None:
            cleaning_map.robot_position = map_data.charger_position

        if partial_cleaning_map:
            grid_size = DreameVacuumMapDecoder._read_int_16_le(partial_cleaning_map.raw, 17)
            width = DreameVacuumMapDecoder._read_int_16_le(partial_cleaning_map.raw, 19)
            height = DreameVacuumMapDecoder._read_int_16_le(partial_cleaning_map.raw, 21)
            left = DreameVacuumMapDecoder._read_int_16_le(partial_cleaning_map.raw, 23)
            top = DreameVacuumMapDecoder._read_int_16_le(partial_cleaning_map.raw, 25)

            data = partial_cleaning_map.raw[
                DreameVacuumMapDecoder.HEADER_SIZE : DreameVacuumMapDecoder.HEADER_SIZE + width * height
            ]

            cleaning_map.has_dirty_area = False
            cleaning_map.has_cleaned_area = False

            for y in range(height):
                for x in range(width):
                    value = data[int(y * width + x)] & 0x03
                    if value > 0:
                        xx = int(((left + (x * grid_size)) - map_data.dimensions.left) / map_data.dimensions.grid_size)
                        yy = int(((top + (y * grid_size)) - map_data.dimensions.top) / map_data.dimensions.grid_size)
                        if cleaning_map.check_point(xx, yy, True):
                            if value == 1:
                                cleaning_map.pixel_type[xx, yy] = 249
                                cleaning_map.has_cleaned_area = True
                            elif value == 2:
                                cleaning_map.pixel_type[xx, yy] = 250
                                cleaning_map.has_dirty_area = True
        return cleaning_map

    @staticmethod
    def optimize_wifi_map(map_data):
        if map_data.wifi_map:
            if map_data.need_optimization:
                map_data.optimized_pixel_type = np.copy(map_data.pixel_type)
                map_data.optimized_dimensions = map_data.dimensions
                map_data.optimized_dimensions.left = (
                    map_data.optimized_dimensions.original_left - map_data.optimized_dimensions.offset
                )
                map_data.optimized_dimensions.top = (
                    map_data.optimized_dimensions.original_top - map_data.optimized_dimensions.offset
                )

                if not map_data.empty_map:
                    for y in range(map_data.dimensions.height):
                        for x in range(map_data.dimensions.width):
                            if int(map_data.pixel_type[x, y]) > 2:
                                max_count = 0
                                max_px = -1
                                value_count = [0, 0, 0, 0]
                                for delta in range(3, 6):
                                    for n in range(y - delta, y + delta + 1):
                                        for m in range(x - delta, x + delta + 1):
                                            if (
                                                n < 0
                                                or n >= map_data.dimensions.height
                                                or m < 0
                                                or m >= map_data.dimensions.width
                                            ):
                                                continue

                                            px = int(map_data.pixel_type[m, n]) - 11
                                            if px >= 0:
                                                value_count[px] = value_count[px] + 1
                                                if value_count[px] > max_count:
                                                    max_count = value_count[px]
                                                    max_px = px

                                    if max_px >= 0:
                                        map_data.optimized_pixel_type[x, y] = MapPixelType(max_px + 11)
                                        break
                map_data.need_optimization = False
        return map_data

    @staticmethod
    def get_segments(map_data: MapData) -> dict[str, Any]:
        segments = {}
        for y in range(map_data.dimensions.height):
            for x in range(map_data.dimensions.width):
                segment_id = int(map_data.pixel_type[x, y])
                if segment_id > 100 and segment_id < 200:
                    segment_id = segment_id - 100

                if segment_id > 0 and segment_id < (32 if map_data.version == 3 else 64):
                    if segment_id not in segments:
                        segments[segment_id] = Segment(segment_id, x, y, x, y)
                        continue

                    if x < segments[segment_id].coords[0]:
                        segments[segment_id].coords[0] = x
                    elif x > segments[segment_id].coords[2]:
                        segments[segment_id].coords[2] = x

                    if y < segments[segment_id].coords[1]:
                        segments[segment_id].coords[1] = y
                    elif y > segments[segment_id].coords[3]:
                        segments[segment_id].coords[3] = y

        if segments:
            for segment in segments.values():
                x = int(math.ceil((segment.coords[2] - segment.coords[0]) / 2 + segment.coords[0]))
                y = int(math.ceil((segment.coords[3] - segment.coords[1]) / 2 + segment.coords[1]))

                if map_data.saved_map or map_data.version == 3:
                    if map_data.version == 0:
                        if map_data.pixel_type[x, y] != segment.id:
                            startI = -1
                            endI = -1
                            for i in range(map_data.dimensions.width):
                                value = map_data.pixel_type[i, y]
                                if startI == -1:
                                    if value == segment.id:
                                        startI = i
                                elif value != segment.id or i == (map_data.dimensions.width - 1):
                                    endI = i - 1
                                    break

                            if startI != -1 and endI != -1:
                                x = int((endI - startI) / 2) + startI
                    else:
                        center_x = DreameVacuumMapDecoder._get_segment_center(map_data, segment.id, y, False)
                        if center_x is not None:
                            center_y = DreameVacuumMapDecoder._get_segment_center(map_data, segment.id, center_x, True)
                            if center_y is not None:
                                x = center_x
                                y = center_y

                segment.x = math.floor(
                    map_data.dimensions.left + (x * map_data.dimensions.grid_size) + (map_data.dimensions.offset)
                )
                segment.y = math.floor(
                    map_data.dimensions.top + (y * map_data.dimensions.grid_size) + (map_data.dimensions.offset)
                )
                segment.calculate_coords(map_data.dimensions)
                segment.set_name()
        return segments

    @staticmethod
    def set_robot_segment(map_data: MapData) -> None:
        if map_data.segments and map_data.saved_map_status == 2 and map_data.robot_position is not None:
            map_data.robot_segment = DreameVacuumMapDecoder._find_px_type(
                map_data.robot_position.x, map_data.robot_position.y, map_data
            )

            if map_data.robot_segment not in map_data.segments:
                map_data.robot_segment = 0
                for k, v in map_data.segments.items():
                    if not v.unmapped and v.check_point(
                        map_data.robot_position.x,
                        map_data.robot_position.y,
                        map_data.dimensions.grid_size * 4,
                    ):
                        map_data.robot_segment = k
                        break
        else:
            map_data.robot_segment = None

    @staticmethod
    def set_station_segment(map_data: MapData) -> None:
        if map_data.segments and map_data.charger_position is not None:
            map_data.station_segment = DreameVacuumMapDecoder._find_px_type(
                map_data.charger_position.x, map_data.charger_position.y, map_data
            )

            if map_data.station_segment not in map_data.segments:
                map_data.station_segment = 0
                for k, v in map_data.segments.items():
                    if not v.unmapped and v.check_point(
                        map_data.charger_position.x,
                        map_data.charger_position.y,
                        map_data.dimensions.grid_size * 4,
                    ):
                        map_data.station_segment = k
                        break
        else:
            map_data.station_segment = None

    @staticmethod
    def set_obstacle_segment(map_data: MapData, obstacles) -> None:
        if map_data.segments and obstacles:
            for k, obstacle in obstacles.items():
                if obstacle.type == ObstacleType.BLOCKED_ROOM:
                    if obstacle.segment_id in map_data.segments:
                        segment = map_data.segments[obstacle.segment_id]
                        obstacles[k].x = segment.x
                        obstacles[k].y = segment.y
                        obstacles[k].segment = segment.name
                        obstacles[k].color_index = segment.color_index
                else:
                    segment = DreameVacuumMapDecoder._find_px_type(obstacle.x, obstacle.y, map_data, 200)

                    if segment not in map_data.segments:
                        for v in map_data.segments.values():
                            if not v.unmapped and v.check_point(
                                obstacle.x,
                                obstacle.y,
                                map_data.dimensions.grid_size * 4,
                            ):
                                obstacles[k].segment = v.name
                                obstacles[k].segment_id = v.id
                                obstacles[k].color_index = v.color_index
                                break
                    else:
                        obstacles[k].segment = map_data.segments[segment].name
                        obstacles[k].segment_id = map_data.segments[segment].id
                        obstacles[k].color_index = map_data.segments[segment].color_index

    @staticmethod
    def set_segment_cleanset(
        map_data: MapData,
        cleanset: dict[str, list[int]],
        capability: DreameVacuumDeviceCapability = None,
    ) -> None:
        if map_data is not None and map_data.segments is not None:
            default_cleanset = [
                1,
                3,
                1,
                0,
            ]  # Cleanset returns empty on restored map but robot uses these default values when that happens

            if capability:
                if capability.custom_cleaning_mode:
                    default_cleanset.append(2)

                if capability.cleaning_route:
                    default_cleanset.append(33)
                elif capability.segment_mopping_type:
                    default_cleanset.append(2)
                elif capability.segment_mopping_settings:
                    default_cleanset.append(546)

                if capability.wetness_level:
                    default_cleanset[1] = 16

                if capability.mop_temperature:
                    default_cleanset.append(0)

                if capability.mop_pressure:
                    if len(default_cleanset) == 6:
                        default_cleanset.extend([0, 0])
                    elif len(default_cleanset) == 7:
                        default_cleanset.append(0)

                    default_cleanset.append(1)

            cleanset_type = CleansetType.NONE
            if cleanset is not None:
                cleanset_type = CleansetType.DEFAULT
                if len(cleanset) == 0:
                    if capability:
                        if capability.wetness_level:
                            cleanset_type = (
                                CleansetType.WETNESS_LEVEL_MAX_15
                                if capability.mop_clean_frequency
                                else CleansetType.WETNESS_LEVEL
                            )
                        elif capability.cleaning_route:
                            cleanset_type = CleansetType.CLEANING_ROUTE
                        elif capability.segment_mopping_settings:
                            cleanset_type = CleansetType.CUSTOM_MOPPING_ROUTE
                        elif capability.custom_cleaning_mode:
                            cleanset_type = CleansetType.CLEANING_MODE
                else:
                    for k, v in cleanset.items():
                        if len(v) > 5 and v[5] > 0:
                            cleanset_type = CleansetType.CLEANING_MODE
                            if capability:
                                if capability.wetness_level:
                                    cleanset_type = (
                                        CleansetType.WETNESS_LEVEL_MAX_15
                                        if capability.mop_clean_frequency
                                        else CleansetType.WETNESS_LEVEL
                                    )
                                elif capability.cleaning_route:
                                    cleanset_type = CleansetType.CLEANING_ROUTE
                                elif capability.segment_mopping_settings:
                                    cleanset_type = CleansetType.CUSTOM_MOPPING_ROUTE
                                break
                        if len(v) > 4:
                            cleanset_type = CleansetType.CLEANING_MODE
                            break

            for k, segment in map_data.segments.items():
                segment.cleanset_type = cleanset_type
                segment_id = str(k)
                if cleanset_type != CleansetType.NONE:
                    if segment_id not in cleanset:
                        cleanset[segment_id] = default_cleanset.copy()

                    item = cleanset[segment_id]
                    segment.suction_level = item[0]
                    segment.water_volume = (
                        item[1] - 1 if item[1] > 1 and item[1] < 5 else 1
                    )  # for some reason cleanset uses different int values for water volume
                    segment.cleaning_times = item[2]

                    if len(item) > 4:
                        segment.cleaning_mode = item[4]
                        if len(item) > 5 and cleanset_type != CleansetType.CLEANING_MODE:
                            segment.mopping_settings = item[5]
                            # Logic for custom room mopping effect settings (mopping effect, mop pad humidity/wetness, route)
                            if item[5] > 0:
                                values = DreameVacuumMapDecoder.split_mopping_settings(segment.mopping_settings)
                                if values:
                                    if values[2] == 0:  # Means custom mopping route enabled
                                        segment.custom_mopping_route = values[0] - 1
                                        segment.water_volume = values[1]
                                        segment.cleaning_route = values[0]
                                    elif values[2] <= 3:
                                        segment.custom_mopping_route = -1
                                        segment.cleaning_route = 1 if values[2] == 2 else values[2]
                                        segment.water_volume = values[2]

                                    if cleanset_type == CleansetType.WETNESS_LEVEL:
                                        segment.custom_mopping_route = 0
                                        if values[2] == 0 and values[1] == 0:
                                            segment.wetness_level = item[1] if item[1] else 16
                                            if segment.wetness_level > 26:
                                                segment.water_volume = 3
                                            elif segment.wetness_level < 6:
                                                segment.water_volume = 1
                                            else:
                                                segment.water_volume = 2
                                        elif segment.water_volume == 1:
                                            segment.wetness_level = 5
                                        elif segment.water_volume == 3:
                                            segment.wetness_level = 27
                                        else:
                                            segment.wetness_level = 16
                                    elif cleanset_type == CleansetType.WETNESS_LEVEL_MAX_15:
                                        segment.custom_mopping_route = 0
                                        if values[2] == 0 and values[1] == 0:
                                            segment.wetness_level = item[1] if item[1] else 10
                                            if segment.wetness_level > 14:
                                                segment.water_volume = 3
                                            elif segment.wetness_level < 6:
                                                segment.water_volume = 1
                                            else:
                                                segment.water_volume = 2
                                        elif segment.water_volume == 1:
                                            segment.wetness_level = 5
                                        elif segment.water_volume == 3:
                                            segment.wetness_level = 15
                                        else:
                                            segment.wetness_level = 10

                        if capability and capability.mop_temperature:
                            segment.mop_temperature = item[6] if len(item) > 6 else 0

                        if capability and capability.mop_pressure:
                            mop_pressure = item[7] if len(item) > 7 else 2
                            segment.mop_pressure = mop_pressure if mop_pressure == 0 else 2
                    else:
                        segment.mopping_settings = None
                        segment.cleaning_route = None
                        segment.custom_mopping_route = None
                        segment.wetness_level = None
                        segment.mop_pressure = None
                        segment.mop_temperature = None
                else:
                    segment.suction_level = None
                    segment.water_volume = None
                    segment.wetness_level = None
                    segment.cleaning_times = None
                    segment.order = None
                    segment.cleaning_mode = None
                    segment.mopping_settings = None
                    segment.cleaning_route = None
                    segment.custom_mopping_route = None
                    segment.mop_pressure = None
                    segment.mop_temperature = None

                if capability and capability.auto_change_mop and map_data.mop_type:
                    segment.mop_type = map_data.mop_type.get(segment_id, "C")

            if cleanset_type != CleansetType.NONE:
                cleaning_sequence_v2 = map_data.version > 1 and map_data.cleaning_sequence
                cleaning_sequence = map_data.cleaning_sequence if cleaning_sequence_v2 else (cleanset or {})
                hidden_segments = set(map_data.hidden_segments or [])

                sequence = sorted(
                    [
                        (int(k), v if cleaning_sequence_v2 else v[3])
                        for k, v in cleaning_sequence.items()
                        if int(k) not in hidden_segments
                        and (
                            (cleaning_sequence_v2 and v is not None)
                            or (not cleaning_sequence_v2 and len(v) > 3 and v[3])
                        )
                    ],
                    key=lambda x: x[1],
                )
                ordered_keys = [k for k, v in sequence]
                for i, k in enumerate(ordered_keys, 1):
                    if k in map_data.segments:
                        map_data.segments[k].order = i

                if cleaning_sequence_v2:
                    index = len(ordered_keys) + 1

                    for k, segment in sorted(map_data.segments.items()):
                        if segment.order is None and segment.visibility != False:
                            segment.order = index
                            index += 1
                else:
                    for k in map_data.segments.keys():
                        if map_data.segments[k].order is None:
                            map_data.segments[k].order = 0

    @staticmethod
    def set_carpet_cleanset(
        map_data: MapData, cleanset: list[list[int]], capability: DreameVacuumDeviceCapability = None
    ):
        if (
            map_data is not None
            and cleanset is not None
            and (
                map_data.detected_carpets
                or map_data.carpets
                or (capability is not None and capability.carpet_material)
            )
        ):
            for setting in cleanset:
                if len(setting) > 1:
                    if setting[0] == 2:
                        if capability is not None and capability.carpet_material:
                            if map_data.segments and setting[1] in map_data.segments:
                                map_data.segments[setting[1]].set_custom_carpet_settings(
                                    setting[2] if len(setting) > 2 else -1, setting[3] if len(setting) > 3 else None
                                )
                    else:
                        carpets = map_data.detected_carpets if setting[0] == 0 else map_data.carpets
                        if carpets:
                            for carpet in carpets:
                                if carpet.id == setting[1]:
                                    carpet.set_custom_carpet_settings(
                                        setting[2] if len(setting) > 2 else -1,
                                        setting[3] if len(setting) > 3 else None,
                                    )
                                    break

    @staticmethod
    def split_mopping_settings(value: int) -> list[int]:
        if value is not None:
            value_list = []
            for i in range(3):
                value_list.append(value & 0x0F)
                value = value >> 4
            return value_list

    @staticmethod
    def combine_mopping_settings(values: list[int]) -> int:
        if values and len(values) == 3:
            value = 0 ^ values[2]
            value = value << 4 ^ values[1]
            return value << 4 ^ values[0]

    @staticmethod
    def set_segment_color_index(map_data: MapData, segment_info, walls_info=None) -> None:
        """Find segment color index as implemented on the app"""
        area_color_index = {}
        if not segment_info:
            return

        if map_data.version == 3:
            if walls_info and walls_info.get("storeys"):
                rooms = walls_info["storeys"][0].get("rooms")
                if rooms:
                    for segment in rooms:
                        walls = segment.get("walls")
                        if not walls:
                            continue

                        n = len(walls)
                        area = sum(
                            walls[i].get("beg_pt_x") * walls[(i + 1) % n].get("beg_pt_y")
                            - walls[(i + 1) % n].get("beg_pt_x") * walls[i].get("beg_pt_y")
                            for i in range(n)
                        )

                        segment_id = segment.get("room_id")
                        for i, inf in enumerate(segment_info):
                            if inf[0] == segment_id:
                                segment_info[i] = inf[:2] + (abs(area / 2),) + inf[3:]
                                break

            segment_info.sort(key=lambda s: int(s[0]))
            max_area_item = max(segment_info, key=lambda s: s[2])
            sorted_segments = [max_area_item] + sorted(
                [s for s in segment_info if s[0] != max_area_item[0]],
                key=lambda s: (-len(s[1]) if s[1] else 0, s[0]),
            )
        else:
            sorted_segments = sorted(segment_info, key=lambda s: (-len(s[1]) if s[1] else 0, s[0]))

        for segment in sorted_segments:
            used_ids = []
            if segment[1] is not None:
                for nid in segment[1]:
                    if nid in area_color_index:
                        used_ids.append(area_color_index[nid])

            area_color_num = {}
            for i in range(4):
                area_color_num[i] = [i, 0]

            for i, j in area_color_index.items():
                area_color_num[j][1] = area_color_num[j][1] + 1

            area_color_num = sorted(
                area_color_num.values(),
                key=lambda x: (x[1], x[0]),
            )

            for area_color in area_color_num:
                color = area_color[0]
                if color not in used_ids:
                    area_color_index[segment[0]] = color
                    break

            if segment[0] not in area_color_index:
                area_color_index[segment[0]] = 0

        for k, v in area_color_index.items():
            color = [0, 2, 3, 1][v] if map_data.version == 3 else v
            if k in map_data.segments:
                map_data.segments[k].color_index = color

    @staticmethod
    def get_carpets(map_data: MapData, saved_map_data: MapData) -> list[tuple]:
        if map_data.saved_map_status == 2 and saved_map_data and saved_map_data.carpet_pixels:
            left_offset = 0
            if saved_map_data.dimensions.left < map_data.dimensions.left:
                left_offset = int(
                    (map_data.dimensions.left - saved_map_data.dimensions.left) / map_data.dimensions.grid_size
                )
            top_offset = 0
            if saved_map_data.dimensions.top < map_data.dimensions.top:
                top_offset = int(
                    (map_data.dimensions.top - saved_map_data.dimensions.top) / map_data.dimensions.grid_size
                )

            if left_offset != 0 or top_offset != 0:
                carpet_pixels = []
                for point in saved_map_data.carpet_pixels:
                    x = point[0] - left_offset
                    y = point[1] - top_offset
                    if x >= 0 and x < map_data.dimensions.width and y >= 0 and y < map_data.dimensions.height:
                        value = int(map_data.pixel_type[x, y])
                        if value > 0:
                            carpet_pixels.append((x, y))

                return carpet_pixels
            else:
                return saved_map_data.carpet_pixels
        return map_data.carpet_pixels

    @staticmethod
    def set_segment_floor_material(
        map_data: MapData, segment: Segment, floor_material, capability: DreameVacuumDeviceCapability
    ) -> None:
        if floor_material is not None and segment is not None:
            segment_id = segment.id
            material = segment.floor_material
            material_direction = segment.floor_material_direction
            if material is not None:
                if material > 4:
                    if material > 7 or (
                        capability is not None and not (capability.carpet_type and capability.carpet_material)
                    ):
                        material = 0

                    floor_material[segment_id] = material
                else:
                    if capability is not None and capability.floor_direction_cleaning and material == 1:
                        if material_direction is None:
                            material_direction = 0
                            segment.floor_material_direction = 0
                    else:
                        material_direction = None
                        segment.floor_material_direction = None

                    if material_direction is not None:
                        segment.floor_material_rotated_direction = (
                            material_direction
                            if map_data.rotation == 0 or map_data.rotation == 180
                            else 90 if material_direction == 0 else 0
                        )

                    floor_material[segment_id] = (
                        0
                        if material <= 0 or material > 2
                        else (
                            3
                            if material == 2
                            else (
                                2
                                if material_direction == 90
                                or (
                                    material_direction != 0
                                    and (
                                        segment.x0 is not None
                                        and segment.x1 is not None
                                        and segment.y0 is not None
                                        and segment.y1 is not None
                                    )
                                    and (segment.x1 - segment.x0) <= (segment.y1 - segment.y0)
                                )
                                else 1
                            )
                        )
                    )
            elif capability and capability.floor_material:
                floor_material[segment_id] = 0
                segment.floor_material = 0
                if capability.floor_direction_cleaning:
                    segment.floor_material_direction = 0
                    segment.floor_material_rotated_direction = (
                        0 if map_data.rotation == 0 or map_data.rotation == 180 else 90
                    )
                else:
                    segment.floor_material_direction = None
                    segment.floor_material_rotated_direction = None

    @staticmethod
    def set_floor_material(
        map_data: MapData, capability: DreameVacuumDeviceCapability = None, saved_map_data: MapData = None
    ) -> None:
        floor_material = {}
        if map_data.segments:
            for k in map_data.segments.values():
                DreameVacuumMapDecoder.set_segment_floor_material(map_data, k, floor_material, capability)
        if map_data.segments:
            if saved_map_data and saved_map_data.floor_material:
                map_data.floor_material = saved_map_data.floor_material
            else:
                map_data.floor_material = floor_material if floor_material else None

    @staticmethod
    def set_capability(map_data: MapData, capability: DreameVacuumDeviceCapability) -> None:
        if capability is not None:
            if map_data.furniture_version == 2 and capability.furnitures_v2:
                if capability.mijia:
                    map_data.furniture_version = 5
                elif capability.furnitures_v3:
                    map_data.furniture_version = 6
                else:
                    map_data.furniture_version = 4 if capability.extended_furnitures else 3

            if not capability.cruising:
                map_data.predefined_points = None

            if not capability.floor_direction_cleaning:
                map_data.virtual_thresholds = None

            if not capability.ramps:
                map_data.virtual_thresholds = map_data.passable_thresholds
                map_data.passable_thresholds = None
                map_data.impassable_thresholds = None
                map_data.ramps = None

            if not capability.curtains:
                map_data.curtains = None

            if not capability.auto_lds_lifting:
                map_data.low_lying_areas = None

            if capability.segment_visibility and map_data.hidden_segments is None:
                map_data.hidden_segments = []

            if capability.cruising and map_data.predefined_points is None:
                map_data.predefined_points = []
