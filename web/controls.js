export const svgNS = 'http://www.w3.org/2000/svg';
export const underlayUrl = '/dreame/api/underlay';
export const maxZones = 10;
export const roomStatusLabels = {
  completed: 'Убрана', skipped: 'Пропущена', blocked: 'Не удалось попасть', unknown: 'Статус неизвестен',
};
const actions = new Set(['start', 'pause', 'resume', 'stop', 'dock']);
const roomId = /^\d{1,3}$/;

export function mapInfo(map, names = {}) {
  if (!map || !Number.isSafeInteger(map.id) || typeof map.revision !== 'string' || !map.revision ||
      !Array.isArray(map.bounds) || map.bounds.length !== 4 || !map.bounds.every(Number.isSafeInteger)) return null;
  const [left, bottom, right, top] = map.bounds;
  if (left >= right || bottom >= top || typeof map.svg !== 'string') return null;
  const rooms = Object.fromEntries(Object.entries(map.rooms || {}).filter(([key, value]) =>
    roomId.test(key) && Number(key) >= 1 && String(Number(key)) === key && typeof value === 'string'));
  for (const id of Object.keys(rooms)) {
    const entry = names?.[id];
    if (typeof entry?.key !== 'string' || !/^[a-f0-9]{64}$/.test(entry.key)) continue;
    const alias = typeof entry.alias === 'string' && entry.alias ? entry.alias : null;
    if (alias) rooms[id] = alias;
  }
  const units = map.units_per_metre;
  const roomAreas = Object.fromEntries(Object.entries(map.room_area_m2 || {}).filter(([key, area]) =>
    Object.hasOwn(rooms, key) && Number.isFinite(area) && area >= 0));
  return { id: map.id, revision: map.revision, bounds: map.bounds, rooms, svg: map.svg, roomAreas,
    unitsPerMetre: Number.isFinite(units) && units > 0 ? units : null, floor: floorInfo(map.floor, map.bounds) };
}

function floorInfo(floor, [left, bottom, right, top]) {
  if (!floor || !Number.isSafeInteger(floor.grid_size) || floor.grid_size < 1 ||
      !Array.isArray(floor.runs) || floor.runs.length > 262_144) return null;
  const gridSize = floor.grid_size;
  const runs = [];
  for (const run of floor.runs) {
    if (!Array.isArray(run) || run.length !== 3 || !run.every(Number.isSafeInteger)) return null;
    const [y, x0, x1] = run;
    if (y < bottom || y + gridSize > top || x0 < left || x1 > right || x0 >= x1) return null;
    runs.push([y, x0, x1]);
  }
  runs.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const merged = [];
  for (const run of runs) {
    const previous = merged.at(-1);
    if (!previous) merged.push(run);
    else if (previous[0] === run[0] && run[1] <= previous[2]) previous[2] = Math.max(previous[2], run[2]);
    else if (previous[0] !== run[0] && previous[0] + gridSize > run[0]) return null;
    else merged.push(run);
  }
  return { gridSize, runs: merged };
}

export function zoneMeasures([x0, y0, x1, y1], units, floor) {
  if (!units) return null;
  const width = (x1 - x0) / units;
  const length = (y1 - y0) / units;
  let area = null;
  if (floor) {
    area = 0;
    for (const [y, a, b] of floor.runs) {
      if (y >= y1) break;
      const height = Math.min(y1, y + floor.gridSize) - Math.max(y0, y);
      if (height > 0) area += height * Math.max(0, Math.min(x1, b) - Math.max(x0, a));
    }
    area /= units ** 2;
  }
  return { width, length, rectangle: width * length, floor: area };
}

export function zoneHitsNoGo([x0, y0, x1, y1], features) {
  const edges = [[0, x0, 1], [0, x1, -1], [1, -y1, 1], [1, -y0, -1]];
  return features.some(({ kind, points }) => {
    if (kind !== 'no_go') return false;
    let polygon = points;
    for (const [axis, bound, sense] of edges) {
      if (!polygon.length) return false;
      const clipped = [];
      let previous = polygon.at(-1);
      for (const point of polygon) {
        const inside = (point[axis] - bound) * sense >= 0;
        if (inside !== ((previous[axis] - bound) * sense >= 0)) {
          const t = (bound - previous[axis]) / (point[axis] - previous[axis]);
          clipped.push(previous.map((value, index) => value + t * (point[index] - value)));
        }
        if (inside) clipped.push(point);
        previous = point;
      }
      polygon = clipped;
    }
    let twiceArea = 0;
    for (let i = 1; i < polygon.length - 1; i++) {
      const [x, y] = polygon[0];
      twiceArea += (polygon[i][0] - x) * (polygon[i + 1][1] - y) - (polygon[i][1] - y) * (polygon[i + 1][0] - x);
    }
    return Math.abs(twiceArea) > 1e-6;
  });
}

export function mapView([left, bottom, right, top], underlay, align = true) {
  const center = [(left + right) / 2, -(top + bottom) / 2];
  if (!underlay) return { angle: 0, center, viewBox: [left, -top, right - left, top - bottom] };
  const [a, b, c, d, e, f] = underlay.matrix;
  // Wall axes are unoriented. Ignore a mirror in the calibration when choosing the rigid rotation.
  const angle = align ? -Math.atan2(2 * a * b, a * a - b * b) / 2 : 0;
  const points = [[left, -top], [left, -bottom], [right, -top], [right, -bottom]];
  for (const x of [0, underlay.width]) {
    for (const y of [0, underlay.height]) points.push([a * x + c * y + e, b * x + d * y + f]);
  }
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const [cx, cy] = center;
  const rotated = points.map(([x, y]) => [cx + cos * (x - cx) - sin * (y - cy), cy + sin * (x - cx) + cos * (y - cy)]);
  const minX = Math.min(...rotated.map(point => point[0]));
  const minY = Math.min(...rotated.map(point => point[1]));
  const maxX = Math.max(...rotated.map(point => point[0]));
  const maxY = Math.max(...rotated.map(point => point[1]));
  return { angle: angle * 180 / Math.PI, center, viewBox: [minX, minY, maxX - minX, maxY - minY] };
}

export function underlayInfo(item) {
  if (!item || item.url !== underlayUrl || !Number.isSafeInteger(item.width) || !Number.isSafeInteger(item.height) ||
      item.width < 1 || item.height < 1 || item.width > 16_000 || item.height > 16_000 || item.width * item.height > 64_000_000 ||
      !Array.isArray(item.matrix) || item.matrix.length !== 6 ||
      !item.matrix.every(value => typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 100_000_000)) return null;
  const [a, b, c, d] = item.matrix;
  if (a * d - b * c === 0) return null;
  return { width: item.width, height: item.height, matrix: item.matrix };
}

export function adjustUnderlay(base, [x, y, angle, percent]) {
  if (!base || ![x, y, angle, percent].every(Number.isFinite) || Math.abs(x) > 5000 || Math.abs(y) > 5000 ||
      Math.abs(angle) > 20 || percent < 75 || percent > 125) return null;
  const [a, b, c, d, e, f] = base.matrix;
  if (!angle && percent === 100) return [a, b, c, d, e + x, f - y];
  const midX = base.width / 2;
  const midY = base.height / 2;
  const radians = angle * Math.PI / 180;
  const cos = Math.cos(radians) * percent / 100;
  const sin = Math.sin(radians) * percent / 100;
  const p = cos * a - sin * b;
  const q = sin * a + cos * b;
  const r = cos * c - sin * d;
  const s = sin * c + cos * d;
  // Keep the current image center fixed before applying the explicit translation.
  return [p, q, r, s, e + x + (a - p) * midX + (c - r) * midY,
    f - y + (b - q) * midX + (d - s) * midY];
}

export function pendingRequest(raw) {
  try {
    const { key, request_id } = JSON.parse(raw);
    if (typeof key === 'string' && key.length > 0 && key.length <= 5000 &&
        typeof request_id === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/.test(request_id)) return { key, request_id };
  } catch { /* Storage can be unavailable or stale. */ }
  return null;
}

export function requestIdentity(key, prior, createId) {
  return prior?.key === key ? prior : { key, request_id: createId() };
}

export function preferenceInfo(items) {
  if (!Array.isArray(items)) return [];
  const keys = new Set();
  return items.slice(0, 32).flatMap(item => {
    if (!item || typeof item.key !== 'string' || !/^[\w.-]{1,64}$/.test(item.key) || keys.has(item.key) ||
        typeof item.label !== 'string' || !item.label || ![item.value, item.min, item.max].every(Number.isSafeInteger) ||
        item.min > item.value || item.value > item.max) return [];
    const preference = { key: item.key, label: item.label, value: item.value, min: item.min, max: item.max,
      unit: typeof item.unit === 'string' ? item.unit : '' };
    if (typeof item.disabled_reason === 'string' && item.disabled_reason.trim()) {
      preference.disabled_reason = item.disabled_reason;
    }
    if (item.choices !== undefined) {
      const choices = item.choices;
      if (!Array.isArray(choices) || choices.length > 32 || !choices.every(choice => choice &&
          Number.isSafeInteger(choice.value) && choice.value >= item.min && choice.value <= item.max &&
          typeof choice.label === 'string' && choice.label.trim())) return [];
      const values = new Set(choices.map(choice => choice.value));
      if (values.size !== choices.length || !values.has(item.value)) return [];
      preference.choices = choices.map(({ value, label }) => ({ value, label }));
    }
    keys.add(item.key);
    return [preference];
  });
}

export function preferenceRequest(preference, value) {
  if (!preference || preference.disabled_reason || !Number.isSafeInteger(value) ||
      value < preference.min || value > preference.max || value === preference.value ||
      (preference.choices && !preference.choices.some(choice => choice.value === value))) return null;
  return { key: preference.key, value, expected: preference.value };
}

export function pointOnMap(x, svgY, bounds, clamp = false) {
  if (!Number.isFinite(x) || !Number.isFinite(svgY)) return null;
  const [left, bottom, right, top] = bounds;
  const y = -svgY;
  if (!clamp && (x < left || x > right || y < bottom || y > top)) return null;
  return [Math.round(Math.min(right, Math.max(left, x))), Math.round(Math.min(top, Math.max(bottom, y)))];
}

export function zoneInBounds(corners, bounds) {
  if (!Array.isArray(corners) || corners.length !== 4 ||
      !Array.from(corners).every(value => Number.isInteger(value) && Math.abs(value) <= 999_999)) return null;
  const [a, b, c, d] = corners;
  const [left, bottom, right, top] = bounds;
  const [x1, y1, x2, y2] = [Math.min(a, c), Math.min(b, d), Math.max(a, c), Math.max(b, d)];
  if (x1 < left || y1 < bottom || x2 > right || y2 > top || x1 === x2 || y1 === y2) return null;
  return [x1, y1, x2, y2];
}

export function zoneListInBounds(zones, bounds) {
  if (!Array.isArray(zones) || !zones.length || zones.length > maxZones) return null;
  const rectangles = Array.from(zones, zone => zoneInBounds(zone, bounds));
  return rectangles.every(Boolean) ? rectangles : null;
}

export function zoneProblem(zone, floor) {
  if (!zone) return 'Укажите разные углы в границах карты';
  if (!floor) return 'Нет данных о доступном поле';
  const [x1, y1, x2, y2] = zone;
  if (Math.min(x2 - x1, y2 - y1) <= 2 * floor.gridSize) return 'Слишком маленькая зона';
  for (const [y, left, right] of floor.runs) {
    if (y >= y2) break;
    if (y + floor.gridSize > y1 && left < x2 && right > x1) return '';
  }
  return 'Нет доступного пола';
}

export function dragZone(zone, handle, [dx, dy], [left, bottom, right, top]) {
  const [x1, y1, x2, y2] = zone;
  const clamp = (value, min, max) => Math.min(max, Math.max(min, value));
  if (handle === null) {
    const x = clamp(dx, left - x1, right - x2);
    const y = clamp(dy, bottom - y1, top - y2);
    return [x1 + x, y1 + y, x2 + x, y2 + y];
  }

  const corner = typeof handle === 'number';
  return [handle === 'x1' || (corner && handle < 2) ? clamp(x1 + dx, left, x2 - 1) : x1,
    handle === 'y1' || (corner && handle % 2 === 0) ? clamp(y1 + dy, bottom, y2 - 1) : y1,
    handle === 'x2' || (corner && handle >= 2) ? clamp(x2 + dx, x1 + 1, right) : x2,
    handle === 'y2' || (corner && handle % 2 === 1) ? clamp(y2 + dy, y1 + 1, top) : y2];
}

const overlayMinimum = { no_go: 3, no_mop: 3, wall: 2, path: 2, robot: 1 };
const routeModes = new Set(['S', 'M', 'W', 'L']);
const maxCoverageRuns = 32_768;

export function mapOverlays(items, savedItems) {
  items ??= savedItems;
  if (!Array.isArray(items)) return [];
  const objects = [];
  const route = [];
  let robot = null;
  let coverage = null;
  let budget = 12_000;
  const geometry = (kind, points) => {
    if (!Object.hasOwn(overlayMinimum, kind) || !Array.isArray(points) || points.length < overlayMinimum[kind] ||
        points.length > 12_000 || !points.every(point => Array.isArray(point) && point.length === 2 &&
          point.every(value => Number.isFinite(value) && Math.abs(value) <= 10_000_000))) return null;
    return { kind, points: points.map(([x, y]) => [x, -y]) };
  };
  for (const item of items) {
    if (!item || item.kind === 'path') continue;
    if (item.kind === 'coverage') {
      const rects = item.rects;
      if (!coverage && item.source === 'frame' && Array.isArray(rects) && rects.length && rects.length <= maxCoverageRuns &&
          rects.every(rect => Array.isArray(rect) && rect.length === 4 &&
            rect.every(value => Number.isFinite(value) && Math.abs(value) <= 10_000_000) && rect[2] > 0 && rect[3] > 0)) {
        coverage = { kind: 'coverage', rects: rects.map(([x, y, width, height]) => [x, -y - height, width, height]) };
      }
    } else if (item.kind === 'robot') robot = geometry('robot', Array.isArray(item.points) ? item.points.slice(0, 1) : null) || robot;
    else if (objects.length < 128) {
      const shape = geometry(item.kind, item.points);
      if (shape) objects.push(shape);
    }
  }
  // Keep the most recent recorded path sections when the combined point budget is exhausted.
  for (let i = items.length - 1; i >= 0 && budget >= 2; i--) {
    const item = items[i];
    if (item?.kind !== 'path' || !Array.isArray(item.points)) continue;
    const shape = geometry('path', item.points.slice(-budget));
    if (!shape) continue;
    if (routeModes.has(item.mode)) shape.mode = item.mode;
    route.push(shape);
    budget -= shape.points.length;
  }
  return [...(coverage ? [coverage] : []), ...objects, ...route.reverse(), ...(robot ? [robot] : [])];
}

export function cleaningCoverage(features, map) {
  const [left, bottom, right, top] = map.bounds;
  const exact = features.find(feature => feature.kind === 'coverage');
  if (exact && exact.rects.every(([x, y, width, height]) =>
    x >= left && x + width <= right && y >= -top && y + height <= -bottom)) {
    return { source: 'frame', rects: exact.rects };
  }
  const floor = map.floor;
  if (!map.unitsPerMetre || !floor?.runs.length || floor.runs.length > maxCoverageRuns) return null;
  const sections = features.filter(feature => feature.kind === 'path' && ['W', 'M'].includes(feature.mode));
  return sections.length ? { source: 'route', width: map.unitsPerMetre * .25, sections, floor } : null;
}

export function rectanglePaths(rects) {
  return rects.map(([x, y, width, height]) => `M${x} ${y}h${width}v${height}h${-width}z`).join('');
}

export function jobRequest(action, map, selection, settings, cleanGenius = null) {
  if (!actions.has(action)) return null;
  if (action !== 'start') return { action };
  if (!map || !['all', 'rooms', 'zone'].includes(selection.kind)) return null;
  const { kind } = selection;
  const intelligent = cleanGenius === 1 || cleanGenius === 2;
  const result = { action, map_id: map.id, revision: map.revision, kind };
  if (kind === 'all') return result;
  if (kind === 'rooms') {
    const rooms = [...new Set(Array.from(selection.rooms, Number))];
    if (!rooms.length || !rooms.every(id => Number.isSafeInteger(id) && Object.hasOwn(map.rooms, id))) return null;
    result.rooms = rooms;
    if (intelligent || cleanGenius === 0) result.expected_cleangenius = cleanGenius;
    if (intelligent) return { ...result, repeats: 1 };
  } else {
    if (intelligent) return null;
    const zones = zoneListInBounds(Object.hasOwn(selection, 'zones') ? selection.zones : [selection.zone], map.bounds);
    if (!zones) return null;
    if (zones.length === 1) result.zone = zones[0];
    else result.zones = zones;
  }
  for (const [key, min] of [['suction', 0], ['water', 1], ['repeats', 1]]) {
    const value = settings[key];
    if (value === '' && key !== 'repeats') continue;
    if (!/^\d$/.test(value) || Number(value) < min || Number(value) > 3) return null;
    result[key] = Number(value);
  }
  return result;
}

const shapes = new Set(['g', 'path', 'rect', 'circle', 'line', 'polyline', 'polygon']);
const number = /^[-+]?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?$/i;
const lengths = new Set(['x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'width', 'height', 'stroke-width', 'fill-opacity', 'stroke-opacity', 'opacity']);
const paint = /^(?:none|transparent|currentColor|#(?:[a-f0-9]{3,4}|[a-f0-9]{6}|[a-f0-9]{8}))$/i;
const pathData = /^[MmZzLlHhVvCcSsQqTtAaEe+\d.,\s-]+$/;
const points = /^[Ee+\d.,\s-]+$/;
const transform = /^(?:(?:matrix|translate|scale|rotate|skewX|skewY)\s*\([Ee+\d.,\s-]+\)\s*)+$/;
const enumAttrs = {
  'fill-rule': ['evenodd', 'nonzero'],
  'stroke-linecap': ['butt', 'round', 'square'],
  'stroke-linejoin': ['miter', 'round', 'bevel'],
  'vector-effect': ['non-scaling-stroke', 'none'],
};

function safeAttribute(name, value) {
  if (lengths.has(name)) return number.test(value) && Number.isFinite(Number(value));
  if (name === 'fill' || name === 'stroke') return paint.test(value);
  if (name === 'd') return pathData.test(value);
  if (name === 'points') return points.test(value);
  if (name === 'transform') return transform.test(value);
  return Object.hasOwn(enumAttrs, name) && enumAttrs[name].includes(value);
}

export function safeMapSvg(map, document) {
  if (map.svg.length > 2_000_000 || /<!\s*(?:doctype|entity)\b/i.test(map.svg)) return null;
  const xml = new DOMParser().parseFromString(map.svg, 'image/svg+xml');
  const source = xml.documentElement;
  if (source.localName !== 'svg' || source.namespaceURI !== svgNS || xml.querySelector('parsererror')) return null;
  const [left, bottom, right, top] = map.bounds;
  const expected = [left, -top, right - left, top - bottom];
  const viewBox = source.getAttribute('viewBox')?.trim().split(/[\s,]+/).map(Number);
  if (!viewBox || viewBox.length !== 4 || !expected.every((value, i) => value === viewBox[i])) return null;

  const svg = document.createElementNS(svgNS, 'svg');
  svg.setAttribute('viewBox', expected.join(' '));
  svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
  svg.setAttribute('role', 'group');
  svg.setAttribute('aria-label', 'План помещений');
  const keys = new Set();
  const geometry = [];
  const copy = (from, to, depth) => {
    for (const element of from.children) {
      if (element.namespaceURI !== svgNS || !shapes.has(element.localName)) continue;
      if (depth > 24 || geometry.length >= 20_000) return false;
      const clean = document.createElementNS(svgNS, element.localName);
      geometry.push(clean);
      for (const attr of element.attributes) {
        if (!attr.namespaceURI && safeAttribute(attr.name, attr.value)) clean.setAttribute(attr.name, attr.value);
      }
      const key = element.getAttribute('data-room');
      if (element.localName === 'path' && key && Object.hasOwn(map.rooms, key)) {
        clean.dataset.room = key;
        clean.setAttribute('aria-pressed', 'false');
        if (!keys.has(key)) {
          keys.add(key);
          clean.setAttribute('tabindex', '0');
          clean.setAttribute('role', 'button');
          clean.setAttribute('aria-label', map.rooms[key]);
        }
      }
      to.append(clean);
      if (element.localName === 'g' && !copy(element, clean, depth + 1)) return false;
    }
    return true;
  };
  for (const attr of source.attributes) {
    if (!attr.namespaceURI && safeAttribute(attr.name, attr.value)) svg.setAttribute(attr.name, attr.value);
  }
  if (!copy(source, svg, 0) || !geometry.length) return null;
  return svg;
}
