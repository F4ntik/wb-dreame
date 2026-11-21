from base64 import b64encode
from collections import defaultdict
from hashlib import blake2s, sha256
from html import escape
from itertools import groupby
import json

import numpy as np
from overlays import read_overlays

FRAME_LIMIT = 1_048_576
ROOM_COLORS = ("#9EDCCA", "#B0CFF1", "#F4CE9D", "#D7C4F0", "#BFD997", "#EDB9C5", "#9EDDE9", "#E4DAA3")
SAFE_TEXT = 'String(val == null ? "—" : val).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")'
METRICS = (
    ("connection", "Связь", "state_connection", SAFE_TEXT, 30, 71, 103, 420, 18),
    ("state", "Состояние", "state_robot",
     'val === "CHARGING_COMPLETED" ? "Зарядка завершена" : val === "CHARGING" ? "Зарядка" : val === "IDLE" ? "Ожидание" : ' + SAFE_TEXT,
     30, 132, 163, 300, 17),
    ("battery", "Заряд", "level_battery", '/^(?:100|[1-9]?[0-9])$/.test(String(val)) ? val + " %" : "—"', 365, 132, 163, 85, 24),
)


def map_snapshot(saved):
    if saved is None or type(saved.map_id) is not int or saved.empty_map or saved.wifi_map or saved.cleaning_map or saved.history_map:
        return None
    dim, pixels = saved.dimensions, saved.pixel_type
    if dim is None or pixels is None or dim.grid_size <= 0:
        return None
    w, h = dim.width, dim.height
    if not (w > 0 and h > 0 and w * h <= 262_144 and pixels.shape == (w, h) and pixels.any()):
        return None
    turns = (saved.rotation or 0) // 90 % 4
    rows = np.rot90(pixels.T[::-1, :], turns).copy()
    charger = saved.charger_position
    station = None
    if charger is not None:
        x, y = (charger.x - dim.left) / dim.grid_size, (charger.y - dim.top) / dim.grid_size
        if 0 <= x < w and 0 <= y < h:
            y = h - y - 1 / dim.grid_size
            for _ in range(turns):
                x, y, w, h = y, w - x, h, w
            station = (x, y)
    segments = {key: (part.name or "", part.visibility is not False and not part.unmapped)
                for key, part in (saved.segments or {}).copy().items()}
    name = saved.custom_name or saved.map_name or ""
    max_segment = 31 if saved.version == 3 else 63
    key = (saved.map_id, name, rows.shape, max_segment, station, tuple(sorted(segments.items())), blake2s(rows.tobytes()).digest())
    return key, rows, station, segments, max_segment


def _floor_paths(rows, segments, max_segment):
    def kind(pixel):
        if not pixel:
            return None
        if pixel in (251, 255) or 201 <= pixel <= 231:
            return "wall"
        segment = pixel - 100 if 100 < pixel < 200 else pixel
        if 1 <= segment <= max_segment:
            return segment if segments.get(segment, ("", True))[1] else "floor"
        return "floor" if pixel in (253, 254) else "unknown"

    paths = defaultdict(list)
    anchors = {}
    for y, row in enumerate(rows):
        x = 0
        for key, group in groupby(map(kind, row.tolist())):
            width = len(list(group))
            if key is not None:
                paths[key].append(f"M{x} {y}h{width}v1h-{width}z")
                if isinstance(key, int) and (key not in anchors or width > anchors[key][0]):
                    anchors[key] = (width, x + width / 2, y + 0.5)
            x += width
    return paths, anchors


def _map_svg(parts):
    _, rows, station, segments, max_segment = parts
    paths, anchors = _floor_paths(rows, segments, max_segment)

    h, w = rows.shape
    scale = min(408 / w, 288 / h)
    left, top = (480 - w * scale) / 2, 51 + (304 - h * scale) / 2
    ids = sorted(anchors)
    colors = {key: ROOM_COLORS[i % len(ROOM_COLORS)] for i, key in enumerate(ids)}
    colors.update(wall="#365762", floor="#DDE9E9", unknown="#B5C5C8")
    svg = [f'<g transform="translate({left:.3f} {top:.3f}) scale({scale:.6f})" shape-rendering="crispEdges">']
    for key, path in paths.items():
        svg.append(f'<path fill="{colors[key]}" d="{"".join(path)}"/>')
    svg.append('</g>')

    station_xy = None
    if station is not None:
        station_xy = (left + station[0] * scale, top + station[1] * scale)
    legend = []
    for key in ids:
        width, x, y = anchors[key]
        x, y = left + x * scale, top + y * scale
        if width * scale >= 22 and (station_xy is None or (x - station_xy[0])**2 + (y - station_xy[1])**2 > 24**2):
            svg.append(f'<text x="{x:.2f}" y="{y + 5:.2f}" text-anchor="middle" font-size="14" font-weight="700">{key}</text>')
        legend.append((str(key), segments.get(key, ("", True))[0] or f"Комната {key}", colors[key]))
    if station_xy is not None:
        x, y = station_xy
        svg.append(f'<g transform="translate({x:.2f} {y:.2f})"><circle r="10" fill="#183E49" stroke="white" stroke-width="2"/>'
                   '<path d="M-1 -5L-4 1H0L-1 5L4 -1H0L1 -5Z" fill="white"/></g>')
        legend.append(("↯", "Зарядная станция", "#B1DEE2"))
    return "".join(svg), legend


def _coordinate_frame(saved, bounds):
    dim, station = saved.dimensions, saved.charger_position
    pose = None
    if station and (bounds[0] <= station.x < bounds[2] and bounds[1] <= station.y < bounds[3]):
        pose = [station.x, station.y, station.a]
    try:
        restrictions = [[(shape.as_list(), getattr(shape, "angle", None), getattr(shape, "hidden", False))
                         for shape in shapes or ()]
                        for shapes in (saved.no_go_areas, saved.no_mopping_areas, saved.virtual_walls)]
        frame = json.dumps([saved.map_id, bounds, dim.grid_size, dim.original_left, dim.original_top,
                            saved.rotation, saved.version, pose, restrictions], separators=(",", ":"), allow_nan=False)
    except (AttributeError, TypeError, ValueError):
        return None, None
    confirmed = pose is not None and not (saved.dirty or saved.restored_map or saved.temporary_map or saved.new_map)
    return frame, sha256(frame.encode()).hexdigest() if confirmed else None


def interactive_map(saved, previous=None):
    snapshot = map_snapshot(saved)
    if snapshot is None:
        return None
    dim = saved.dimensions
    step = dim.grid_size
    bounds = [dim.left, dim.top, dim.left + dim.width * step, dim.top + dim.height * step]
    _, _, _, segments, max_segment = snapshot
    rooms = {str(k): label or f"Комната {k}" for k, (label, shown) in segments.items() if shown}
    identities = {k: getattr(v, "unique_id", None) for k, v in sorted((saved.segments or {}).copy().items())}
    pixels = saved.pixel_type.T[::-1, :].copy()
    digest = sha256(pixels.tobytes())
    exclusions = read_overlays(saved, None)
    station = saved.charger_position
    frame, geometry_key = _coordinate_frame(saved, bounds)
    digest.update(json.dumps([saved.map_id, bounds, step, pixels.shape, rooms, list(identities.items()), saved.rotation,
                              snapshot[2], exclusions, frame, geometry_key], sort_keys=True).encode())
    revision = digest.hexdigest()
    if previous and previous["revision"] == revision:
        return previous
    paths, anchors = _floor_paths(pixels, segments, max_segment)
    colors = {key: ROOM_COLORS[i % len(ROOM_COLORS)] for i, key in enumerate(sorted(anchors))}
    colors.update(wall="#365762", floor="#DDE9E9", unknown="#B5C5C8")
    left, bottom, right, top = bounds
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left} {-top} {right-left} {top-bottom}">',
           f'<g transform="translate({left} {-top}) scale({step})">']
    for kind, parts in paths.items():
        room = f' data-room="{kind}"' if str(kind) in rooms else ""
        svg.append(f'<path fill="{colors[kind]}"{room} d="{"".join(parts)}"/>')
    svg.append('</g>')
    if station and left <= station.x <= right and bottom <= station.y <= top:
        svg.append(f'<circle cx="{station.x}" cy="{-station.y}" r="{step*2}" fill="#164B50"/>')
    svg.append('</svg>')
    footprint_bits = np.packbits(pixels != 0).tobytes()
    footprint = sha256(footprint_bits).hexdigest()
    # Dreame stores distance in millimetres. Use cell intersections for the floor inside a zone.
    native = pixels[::-1, :]
    labels = np.where((native > 100) & (native <= 100 + max_segment), native - 100, native)
    room_keys = {}
    for room in rooms:
        segment = int(room)
        cells = labels == segment
        ys, xs = np.nonzero(cells)
        if not xs.size:
            continue
        x, y = int(xs.min()), int(ys.min())
        shape = cells[y:ys.max() + 1, x:xs.max() + 1]
        # Absolute grid coordinates keep the key stable when the map is cropped elsewhere.
        key = [saved.map_id, segment, identities.get(segment), rooms[room], step,
               left + x * step, bottom + y * step, shape.shape]
        digest = sha256(json.dumps(key).encode())
        digest.update(np.packbits(shape).tobytes())
        room_keys[room] = digest.hexdigest()
    walkable = ((labels >= 1) & (labels <= max_segment)) | (native == 253) | (native == 254)
    for feature in exclusions:
        if feature["kind"] == "no_go":
            inside = np.zeros_like(walkable)
            xs = left + (np.arange(dim.width) + 0.5) * step
            ys = (bottom + (np.arange(dim.height) + 0.5) * step)[:, None]
            points = feature["points"]
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
                if ay != by:
                    inside ^= ((ay > ys) != (by > ys)) & (xs < ax + (ys-ay)*(bx-ax)/(by-ay))
            walkable &= ~inside
    runs = []
    for row, values in enumerate(walkable):
        changes = np.flatnonzero(np.diff(np.pad(values, 1))).tolist()
        runs.extend([bottom + row*step, left + start*step, left + end*step]
                    for start, end in zip(changes[::2], changes[1::2]))
    room_area = {key: round(np.count_nonzero(walkable & (labels == int(key))) * (step/1000)**2, 4) for key in rooms}
    return {"id": saved.map_id, "revision": revision, "bounds": bounds, "rooms": rooms, "svg": "".join(svg),
            "footprint": footprint, "_footprint_bits": footprint_bits, "geometry_key": geometry_key,
            "units_per_metre": 1000, "floor": {"grid_size": step, "runs": runs},
            "room_area_m2": room_area, "_room_keys": room_keys}


def _label(text, limit):
    clean = "".join(ch for ch in text if ch.isprintable()).strip()
    return escape(clean if len(clean) <= limit else clean[:limit - 1].rstrip() + "…")


def render_map_frame(snapshot):
    title = "Карта пока недоступна"
    parts = [f'<text x="240" y="110" font-size="18" text-anchor="middle">{title}</text>']
    height = 180
    if snapshot is not None:
        map_id, name = snapshot[0][:2]
        title = f"Сохранённая карта №{map_id}"
        plan, legend = _map_svg(snapshot)
        columns = 1 if len(legend) <= 7 else 2 if len(legend) <= 24 else 3
        height = 372 + 25 * ((len(legend) + columns - 1) // columns)
        parts = [f'<text x="30" y="25" font-size="17" font-weight="700">{title}</text>', plan]
        if name:
            parts.insert(1, f'<text x="30" y="45" font-size="13" fill="#4C6970">{_label(name, 42)}</text>')
        for index, (key, label, color) in enumerate(legend):
            x = 30 + index % columns * (420 / columns)
            y = 370 + index // columns * 25
            parts.append(f'<rect x="{x:.1f}" y="{y - 15}" width="22" height="21" rx="5" fill="{color}"/>'
                         f'<text x="{x + 11:.1f}" y="{y}" font-size="12" font-weight="700" text-anchor="middle">{key}</text>'
                         f'<text x="{x + 29:.1f}" y="{y}" font-size="14">{_label(label, (45, 21, 13)[columns - 1])}</text>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 480 {height}"><title>{title}</title>'
           '<g font-family="system-ui,sans-serif" fill="#16333B">' + "".join(parts) + "</g></svg>")
    data = b64encode(svg.encode("utf-8")).decode("ascii")
    return data if len(data) <= FRAME_LIMIT else render_map_frame(None)


def make_panel():
    items = ['<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 480 804" aria-label="Пылесос Dreame">',
             '<rect x="1" y="1" width="478" height="802" rx="22" fill="#F5F9F9" stroke="#D9E6E6"/>',
             '<g font-family="system-ui,sans-serif" fill="#16333B">',
             '<text x="30" y="42" font-size="24" font-weight="750">Пылесос</text>',
             '<path d="M30 183H450" stroke="#D9E6E6"/>',
             '<text aria-hidden="true" x="240" y="301" text-anchor="middle" font-size="18" fill="#4C6970">Карта пока недоступна</text>']
    params = []
    for param_id, label, channel, expression, x, label_y, value_y, width, size in METRICS:
        items.append(f'<text x="{x}" y="{label_y}" font-size="13" fill="#4C6970">{label}</text>')
        items.append(f'<svg x="{x}" y="{value_y - 26}" width="{width}" height="29" overflow="hidden">'
                     f'<text x="0" y="26" data-svg-param-id="{param_id}" font-size="{size}" font-weight="650">—</text></svg>')
        params.append({"id": param_id, "read": {"enable": True, "channel": f"dreame_l40_1/{channel}", "value": expression},
                       **{key: {"enable": False} for key in ("write", "visible", "style")}})
    items.append('</g><foreignObject x="0" y="190" width="480" height="596">'
                 '<div xmlns="http://www.w3.org/1999/xhtml" id="dreame-map-frame" data-svg-param-id="map-frame" '
                 'aria-label="Сохранённая карта помещений" style="width:100%;height:100%;background-size:contain;'
                 'background-position:center top;background-repeat:no-repeat"></div></foreignObject></svg>')
    css = (f'typeof val === "string" && val.length <= {FRAME_LIMIT} && '
           r'/^[A-Za-z0-9+\/]+={0,2}$/.test(val) ? '
           "';background-color:#F5F9F9;background-image:url(\"data:image/svg+xml;base64,'"
           " + val + '\");' : ';background-image:none;'")
    params.append({"id": "map-frame", **{key: {"enable": False} for key in ("read", "write", "visible")},
                   "style": {"enable": True, "channel": "dreame_l40_1/map_frame", "value": css}})
    return {"id": "dreame-vacuum", "name": "Пылесос", "isSvg": True, "widgets": [], "svg_fullwidth": True,
            "swipe": {"enable": False, "left": None, "right": None},
            "svg_url": "local", "svg": {"current": "".join(items), "params": params}}
