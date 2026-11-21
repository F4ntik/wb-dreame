"""Render a decoded Dreame history frame without using the current map."""
from collections import defaultdict
from itertools import groupby

import numpy as np

from room_status import room_outcomes
from vendor.types import MapPixelType, PathType

MAX_AXIS = 1_024
MAX_CELLS = 262_144
MAX_FLOOR_SPANS = 20_000
MAX_COVERAGE_SPANS = 8_000
MAX_PATH_POINTS = 12_000
MAX_SVG_BYTES = 1_048_576
PADDING = 3
MOP_TRACE_WIDTH_MM = 250

FLOOR_COLORS = (
    "", "#365762", "#B5C5C8", "#DDE9E9",
    "#AADCCB", "#B9D5EE", "#EED0A8", "#DBCAED", "#C7DCAB", "#F0C4CB", "#A8E0E5", "#E6DCA8",
)
COVERAGE_COLORS = ("", "#20A58E", "#F0A347")
ROUTE_COLORS = {
    PathType.LINE: "#234857",
    PathType.SWEEP: "#174B61",
    PathType.SWEEP_AND_MOP: "#00685F",
    PathType.MOP: "#61438D",
}


def _coordinate(value):
    return type(value) in (int, float) and -1_000_000 <= value <= 1_000_000


def _layout(map_data):
    if map_data is None or map_data.history_map is not True or map_data.wifi_map or map_data.empty_map:
        return None
    dim = map_data.dimensions
    if dim is None or not (_coordinate(dim.left) and _coordinate(dim.top)):
        return None
    width, height, step = dim.width, dim.height, dim.grid_size
    if not all(type(n) is int and 0 < n <= MAX_AXIS for n in (width, height)):
        return None
    if width * height > MAX_CELLS or type(step) is not int or not 0 < step <= 32_767:
        return None
    rotation = map_data.rotation if map_data.rotation is not None else 0
    if type(rotation) is not int or rotation % 90:
        return None
    return dim, rotation // 90 % 4


def _pixels(frame, dim):
    points = frame.pixel_type
    if not isinstance(points, np.ndarray) or points.shape != (dim.width, dim.height):
        return None
    if points.dtype == np.uint8:
        return points
    if points.dtype.kind in "iu" and points.min() >= 0 and points.max() <= 255:
        return points.astype(np.uint8)
    return None


def _screen_size(dim, turns):
    return (dim.height, dim.width) if turns % 2 else (dim.width, dim.height)


def _project(dim, turns, x, y):
    if not (_coordinate(x) and _coordinate(y)):
        return None
    width, height, step = dim.width, dim.height, dim.grid_size
    x, y = (x - dim.left) / step, y - dim.top
    if not (0 <= x < width and 0 <= y < height * step):
        return None
    y = (height * step - 1 - y) / step
    for _ in range(turns):
        x, y, width, height = y, width - x, height, width
    return x, y


def history_position(map_data, x, y):
    """Return [left %, top %] relative to the rendered archive SVG, or None."""
    layout = _layout(map_data)
    if layout is None:
        return None
    dim, turns = layout
    pixels = _pixels(map_data, dim)
    if pixels is None or not pixels.any():
        return None
    point = _project(dim, turns, x, y)
    if point is None:
        return None
    return _image_percent(point, _screen_size(dim, turns))


def _image_percent(point, screen_size):
    return [round(100 * (p + PADDING) / (size + 2 * PADDING), 5) for p, size in zip(point, screen_size)]


def _floor_palette(version):
    colors = np.full(256, 2, dtype=np.uint8)
    colors[MapPixelType.OUTSIDE] = 0
    colors[[MapPixelType.WALL, MapPixelType.OBSTACLE_WALL]] = 1
    colors[201:232] = 1  # v3 wall tagged with a segment number
    colors[[MapPixelType.FLOOR, MapPixelType.NEW_SEGMENT, MapPixelType.CLEAN_AREA, MapPixelType.DIRTY_AREA]] = 3
    for segment in range(1, 32 if version == 3 else 64):
        color = 4 + (segment - 1) % (len(FLOOR_COLORS) - 4)
        colors[segment] = colors[100 + segment] = color
    return colors


def _rect_paths(pixels, turns, palette, limit):
    spans = defaultdict(list)
    count = 0
    for y, row in enumerate(np.rot90(pixels.T[::-1, :], turns)):
        x = 0
        for color, run in groupby(row.tolist()):
            width = sum(1 for _ in run)
            if color:
                count += 1
                if count > limit:
                    return None
                spans[color].append(f"M{x} {y}h{width}v1h-{width}z")
            x += width
    return "".join(f'<path fill="{palette[color]}" d="{"".join(parts)}"/>' for color, parts in spans.items())


def floor_mask(frame, dim):
    """Identify known floor cells, excluding walls, map exterior and unknown cells."""
    pixels = _pixels(frame, dim)
    if pixels is not None:
        return _floor_palette(frame.version)[pixels] >= 3
    return None


def coverage_pixels(frame, dim):
    """Return the explicit Dreame coverage grid: 0 unmarked, 1 cleaned, 2 dirty."""
    overlay = frame.cleaning_map_data
    if overlay is None:
        overlay = frame
    elif (not overlay.history_map or not overlay.cleaning_map or overlay.dimensions != dim
          or overlay.rotation != frame.rotation):
        return None
    if overlay.has_cleaned_area is not True and overlay.has_dirty_area is not True:
        return None
    pixels = _pixels(overlay, dim)
    if pixels is None:
        return None
    marked = np.zeros_like(pixels)
    if overlay.has_cleaned_area is True:
        marked[pixels == MapPixelType.CLEAN_AREA] = 1
    if overlay.has_dirty_area is True:
        marked[pixels == MapPixelType.DIRTY_AREA] = 2
    return marked


def _coverage(marked, base, turns):
    if marked is None:
        return ""
    marked[base < 3] = 0
    return _rect_paths(marked, turns, COVERAGE_COLORS, MAX_COVERAGE_SPANS) or ""


def iter_route_sections(points, *, project=None):
    """Yield continuous (PathType, [(x, y), ...]) sections in native or projected coordinates."""
    if not isinstance(points, (list, tuple)) or len(points) > MAX_PATH_POINTS:
        return
    mode = PathType.LINE
    section = []
    for point in points:
        x, y = getattr(point, "x", None), getattr(point, "y", None)
        kind = getattr(point, "path_type", None)
        position = None
        if _coordinate(x) and _coordinate(y) and type(kind) in (PathType, str) and kind in ROUTE_COLORS:
            position = project(x, y) if project is not None else (x, y)
        if position is None or kind != PathType.LINE:
            if len(section) >= 2:
                yield mode, section
            mode = PathType.LINE if position is None else PathType(kind)
            section = [] if position is None else [position]
        elif not section or section[-1] != position:
            section.append(position)
    if len(section) >= 2:
        yield mode, section


def _route(points, dim, turns):
    parts = defaultdict(list)
    route_sections = []
    screen_size = _screen_size(dim, turns)
    for mode, section in iter_route_sections(points, project=lambda x, y: _project(dim, turns, x, y)):
        (x, y), *rest = section
        parts[mode].append(f"M{x:.3f} {y:.3f}")
        parts[mode].extend(f"L{x:.3f} {y:.3f}" for x, y in rest)
        route_sections.append({
            "mode": mode.value,
            "points": [_image_percent(point, screen_size) for point in section],
        })
    svg = "".join(f'<path stroke="{ROUTE_COLORS[mode]}" d="{"".join(commands)}"/>' for mode, commands in parts.items())
    mop = "".join(f'<path d="{"".join(commands)}"/>' for mode, commands in parts.items()
                  if mode in (PathType.MOP, PathType.SWEEP_AND_MOP))
    return svg, route_sections, mop


def _mop_trace(route, floor, dim, turns):
    clip = _rect_paths(floor, turns, ("", "white"), MAX_COVERAGE_SPANS)
    if not clip:
        return ""
    width = MOP_TRACE_WIDTH_MM / dim.grid_size
    return (f'<defs><clipPath id="cleaning-floor">{clip}</clipPath></defs>'
            f'<g clip-path="url(#cleaning-floor)" fill="none" stroke="#20A58E" stroke-width="{width:.4f}" '
            f'stroke-linecap="round" stroke-linejoin="round" shape-rendering="auto">{route}</g>')


def _archive_room_layers(frame, pixels, dim, turns, marked):
    if frame.cleaning_map:
        return "", [], []
    outcomes = room_outcomes(frame, archived=True)
    max_room = 31 if frame.version == 3 else 63
    label_lookup = np.zeros(256, dtype=np.uint8)
    label_lookup[1:max_room + 1] = label_lookup[101:101 + max_room] = np.arange(1, max_room + 1, dtype=np.uint8)
    labels = label_lookup[pixels]
    covered = np.bincount(labels[marked == 1], minlength=max_room + 1) if marked is not None else None
    step = dim.grid_size

    def in_grid(x, y):
        return (x, y) if (0 <= x - dim.left < dim.width * step and 0 <= y - dim.top < dim.height * step) else None

    visited = set()
    for mode, points in iter_route_sections(frame.path, project=in_grid):
        if mode != PathType.LINE:
            for x, y in points:
                visited.add(int(labels[int((x - dim.left) // step), int((y - dim.top) // step)]))

    statuses = ("unknown", "completed", "skipped", "blocked", "observed")
    palette = ("", "#1E9A78", "#DEA044", "#DE8079", "#7463A8")
    shades = np.zeros(max_room + 1, dtype=np.uint8)
    rooms = []
    observations = []
    for room in np.unique(labels).tolist():
        if not room:
            continue
        segment = (frame.segments or {}).get(room)
        if segment is not None and (segment.visibility is False or segment.unmapped):
            continue
        status = outcomes.get(room, "unknown")
        covered_m2 = round(int(covered[room]) * step ** 2 / 1_000_000, 6) if covered is not None and covered[room] else None
        observed = room in visited or covered_m2 is not None
        shades[room] = statuses.index("observed" if observed and status == "unknown" else status)
        name = getattr(segment, "name", None)
        name = "".join(ch for ch in name if ch.isprintable()).strip()[:100] if isinstance(name, str) else ""
        identity = {"id": room, "name": name or f"Комната {room}"}
        rooms.append({**identity, "status": status})
        if observed:
            observations.append({**identity, "route": room in visited, "covered_m2": covered_m2})
    layer = _rect_paths(shades[labels], turns, palette, MAX_COVERAGE_SPANS) if shades.any() else ""
    svg = f'<g id="room-statuses" opacity=".78" shape-rendering="crispEdges">{layer}</g>' if layer else ""
    return svg, rooms if any(room["status"] != "unknown" for room in rooms) else [], observations


def render_history(map_data):
    """Return a standalone SVG and evidence flags for a decoded original archive frame."""
    layout = _layout(map_data)
    if layout is None:
        return None
    dim, turns = layout
    pixels = _pixels(map_data, dim)
    if pixels is None:
        return None
    floor = _floor_palette(map_data.version)[pixels]
    floor_svg = _rect_paths(floor, turns, FLOOR_COLORS, MAX_FLOOR_SPANS)
    if not floor_svg:
        return None

    marked = coverage_pixels(map_data, dim)
    room_layer, room_statuses, observations = _archive_room_layers(map_data, pixels, dim, turns, marked)
    coverage = _coverage(marked, floor, turns)
    route, route_sections, mop_route = _route(map_data.path, dim, turns)
    has_coverage = bool(coverage)
    coverage_source = "frame" if has_coverage else None
    if not coverage and mop_route:
        coverage = _mop_trace(mop_route, floor >= 3, dim, turns)
        if coverage:
            coverage_source = "route"
    station = ""
    if map_data.charger_position is not None:
        position = map_data.charger_position
        charger = _project(dim, turns, getattr(position, "x", None), getattr(position, "y", None))
        if charger is not None:
            x, y = charger
            station = (f'<circle cx="{x:.3f}" cy="{y:.3f}" r="2.1" fill="#163A49" stroke="white" stroke-width=".55"/>'
                       f'<rect x="{x - .95:.3f}" y="{y - .60:.3f}" width="1.9" height="1.2" rx=".24" fill="white"/>')

    width, height = _screen_size(dim, turns)
    svg_width, svg_height = width + 2 * PADDING, height + 2 * PADDING
    prefix = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-PADDING} {-PADDING} {svg_width} {svg_height}">'
        '<title>Карта исторической уборки Dreame</title>'
        f'<rect x="{-PADDING}" y="{-PADDING}" width="{svg_width}" height="{svg_height}" fill="#F6F9F9"/>'
        f'<g shape-rendering="crispEdges">{floor_svg}</g>'
    )
    coverage_start = '<g id="cleaning-coverage" opacity=".55" shape-rendering="crispEdges">'
    suffix = (f'</g><g fill="none" stroke-width=".7" stroke-linecap="round" stroke-linejoin="round">{route}</g>'
              f'{station}</svg>')
    base_size = len(prefix.encode("utf-8")) + len(coverage_start) + len(suffix)
    if coverage_source == "route" and base_size + len(coverage) > MAX_SVG_BYTES:
        coverage, coverage_source = "", None
    if base_size + len(coverage) > MAX_SVG_BYTES:
        return None
    if base_size + len(coverage) + len(room_layer) > MAX_SVG_BYTES:
        room_layer = ""
    return {"svg": prefix + room_layer + coverage_start + coverage + suffix,
            "has_path": bool(route), "has_coverage": has_coverage,
            "coverage_source": coverage_source, "route_sections": route_sections, "room_statuses": room_statuses,
            "room_observations": observations}
