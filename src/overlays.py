"""Map overlays in the robot's native coordinate system."""
from math import cos, radians, sin
from time import time

import numpy as np

from history_map import MAX_COVERAGE_SPANS, coverage_pixels, floor_mask, iter_route_sections
from room_status import room_outcomes
from vendor.types import Area, Line, MapFrameType, Point

MAX_PER_KIND = 64
MAX_GRID_CELLS = 262_144
ROBOT_POSITION_MAX_AGE_MS = 120_000
FUTURE_CLOCK_SKEW_MS = 15_000
NON_LIVE_FLAGS = ("history_map", "cleaning_map", "wifi_map", "recovery_map", "empty_map", "temporary_map", "new_map")


def _coord(value):
    return type(value) in (int, float) and -1_000_000 <= value <= 1_000_000


def _grid(frame):
    dim = frame.dimensions
    if dim is None or not (_coord(dim.left) and _coord(dim.top)):
        return None
    width, height, size = dim.width, dim.height, dim.grid_size
    if not all(type(n) is int and 0 < n <= 32_767 for n in (width, height, size)):
        return None
    if width * height > MAX_GRID_CELLS:
        return None
    right, bottom = dim.left + width * size, dim.top + height * size
    if not (_coord(right) and _coord(bottom)):
        return None
    return dim.left, dim.top, right, bottom, size


def _in_grid(point, grid, *, closed=False):
    x, y = point
    left, top, right, bottom, _ = grid
    if not (_coord(x) and _coord(y) and left <= x and top <= y):
        return False
    return (x <= right and y <= bottom) if closed else (x < right and y < bottom)


def _xy(point):
    if isinstance(point, Point) and _coord(point.x) and _coord(point.y):
        return point.x, point.y
    return None


def _polygon(area):
    if not isinstance(area, Area) or area.hidden:
        return []
    points = [(area.x0, area.y0), (area.x1, area.y1), (area.x2, area.y2), (area.x3, area.y3)]
    if not all(_coord(value) for point in points for value in point) or len(set(points)) != 4:
        return []
    a, b, c, d = points
    if sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in ((a, b), (b, c), (c, d), (d, a))) == 0:
        return []
    if area.angle is None:
        return points
    if not _coord(area.angle) or abs(area.angle) > 360:
        return []
    if not area.angle:
        return points

    cx = sum(x for x, _ in points) / 4
    cy = sum(y for _, y in points) / 4
    angle = radians(area.angle)
    cos_angle, sin_angle = cos(angle), sin(angle)
    # Area.to_img applies this rotation before inverting the display Y axis.
    return [(round(cx + (x - cx) * cos_angle - (y - cy) * sin_angle, 6),
             round(cy + (x - cx) * sin_angle + (y - cy) * cos_angle, 6)) for x, y in points]


def _wall(line):
    if not isinstance(line, Line):
        return []
    start, end = (line.x0, line.y0), (line.x1, line.y1)
    return [start, end] if start != end else []


def _aligned(saved, current, saved_grid, current_grid):
    if saved_grid == current_grid:
        return True
    left, top, _, _, size = saved_grid
    current_left, current_top, _, _, current_size = current_grid
    if size != current_size or (left - current_left) % size or (top - current_top) % size:
        return False
    # Dreame refines the station pose during cleaning. Both anchors must agree within one grid cell.
    station, live_station = _xy(saved.charger_position), _xy(current.charger_position)
    return (station is not None and live_station is not None
            and _in_grid(station, saved_grid) and _in_grid(live_station, current_grid)
            and (station[0] - live_station[0]) ** 2 + (station[1] - live_station[1]) ** 2 <= size ** 2)


def _live_grid(saved, current, saved_grid):
    if (current is None or current is saved or current.saved_map or current.restored_map or current.clean_log
            or any(getattr(current, flag) for flag in NON_LIVE_FLAGS)
            or type(current.saved_map_id) is not int or current.saved_map_id != saved.map_id
            or current.saved_map_status != 2 or current.version != saved.version
            or current.frame_type not in (MapFrameType.I.value, MapFrameType.P.value)
            or type(current.frame_id) is not int or current.frame_id < 0):
        return None
    timestamp = current.timestamp_ms
    if type(timestamp) is not int or timestamp <= 0:
        return None
    age = time() * 1000 - timestamp
    if not -FUTURE_CLOCK_SKEW_MS <= age <= ROBOT_POSITION_MAX_AGE_MS:
        return None
    if type(saved.timestamp_ms) is int and timestamp < saved.timestamp_ms:
        return None

    current_grid = _grid(current)
    if current_grid is not None and _aligned(saved, current, saved_grid, current_grid):
        return current_grid
    return None


def _saved_grid(saved):
    if (saved is not None and saved.saved_map is True and type(saved.map_id) is int
            and not saved.restored_map and not saved.clean_log
            and not any(getattr(saved, flag) for flag in NON_LIVE_FLAGS)):
        return _grid(saved)
    return None


def live_room_outcomes(saved, current, room_keys, since_ms):
    grid = _saved_grid(saved)
    if grid is None or _live_grid(saved, current, grid) is None or current.timestamp_ms < since_ms:
        return {}
    result = {}
    current_segments = current.segments or {}
    saved_segments = saved.segments or {}
    for room, status in room_outcomes(current).items():
        if str(room) not in room_keys:
            continue
        live_segment = current_segments.get(room)
        saved_uid = getattr(saved_segments.get(room), "unique_id", None)
        live_uid = getattr(live_segment, "unique_id", None)
        if current_segments and (live_segment is None or live_segment.unmapped or live_segment.visibility is False):
            continue
        if saved_uid is not None and live_uid is not None and saved_uid != live_uid:
            continue
        result[str(room)] = status
    return result


def _frame_coverage(saved, current, grid, live_grid):
    current_dim = current.dimensions
    marked = coverage_pixels(current, current_dim)
    if marked is None or not (marked == 1).any():
        return None
    saved_floor = floor_mask(saved, saved.dimensions)
    live_floor = floor_mask(current, current_dim)
    if saved_floor is None or live_floor is None or marked.shape != live_floor.shape:
        return None

    left, top = max(grid[0], live_grid[0]), max(grid[1], live_grid[1])
    right, bottom = min(grid[2], live_grid[2]), min(grid[3], live_grid[3])
    if right <= left or bottom <= top:
        return None

    step = grid[4]

    def overlap(bounds):
        return np.s_[int((left - bounds[0]) / step):int((right - bounds[0]) / step),
                     int((top - bounds[1]) / step):int((bottom - bounds[1]) / step)]

    cells = (marked == 1) & live_floor
    live_cells = cells[overlap(live_grid)]
    saved_cells = saved_floor[overlap(grid)]
    if live_cells.shape != saved_cells.shape:
        return None
    visible = live_cells & saved_cells
    rows, edges = np.nonzero(np.diff(visible.T, axis=1, prepend=False, append=False))
    if not edges.size or edges.size > 2 * MAX_COVERAGE_SPANS:
        return None
    rects = [[left + int(x) * step, top + int(y) * step, int(end - x) * step, step]
             for y, (x, end) in zip(rows[::2], edges.reshape(-1, 2))]
    return {"kind": "coverage", "source": "frame", "rects": rects}


def read_overlays(saved, current, *, cleaning=False, station_pause=False):
    """Return bounded, JSON-ready geometry from one saved plan and a compatible live frame."""
    grid = _saved_grid(saved)
    if grid is None:
        return []

    result = []
    for kind, shapes, read_shape in (
        ("no_go", saved.no_go_areas, _polygon),
        ("no_mop", saved.no_mopping_areas, _polygon),
        ("wall", saved.virtual_walls, _wall),
    ):
        for shape in (shapes or ())[:MAX_PER_KIND]:
            points = read_shape(shape)
            if points and all(_in_grid(point, grid, closed=True) for point in points):
                result.append({"kind": kind, "points": [list(point) for point in points]})

    live_grid = _live_grid(saved, current, grid)
    if live_grid is not None:
        def project(x, y):
            point = (x, y)
            return point if _in_grid(point, grid) and _in_grid(point, live_grid) else None

        # On the dock the decoder may substitute the base for an absent robot position.
        position = _xy(current.robot_position)
        if (not station_pause and position and project(*position)
                and not (current.docked and position == _xy(current.charger_position))):
            result.insert(0, {"kind": "robot", "points": [list(position)]})
        if cleaning and (not current.docked or station_pause):
            coverage = _frame_coverage(saved, current, grid, live_grid)
            if coverage is not None:
                result.append(coverage)
            for mode, points in iter_route_sections(current.path or (), project=project):
                result.append({"kind": "path", "mode": mode.value, "points": points})
    return result
