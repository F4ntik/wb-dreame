import { adjustUnderlay, cleaningCoverage, dragZone, jobRequest, mapInfo, mapOverlays, mapView, pendingRequest, pointOnMap, preferenceInfo,
  preferenceRequest, rectanglePaths, requestIdentity, safeMapSvg, svgNS, underlayInfo, underlayUrl, zoneHitsNoGo, zoneInBounds,
  zoneMeasures } from './controls.js';
import { historyView } from './history.js';

const byId = id => document.getElementById(id);
const mapHost = byId('map');
const robotMarker = byId('robot-marker');
const livePosition = byId('live-position');
const liveFrameTime = byId('live-frame-time');
const roomList = byId('room-list');
const roomPins = document.createElement('div');
roomPins.className = 'room-order-map';
roomPins.setAttribute('aria-hidden', 'true');
const form = byId('job-form');
const zoneFields = ['zone-x1', 'zone-y1', 'zone-x2', 'zone-y2'].map(byId);
const zoneBadge = byId('zone-measure');
const zoneSummary = byId('zone-measure-summary');
const clearZone = byId('clear-zone');
const zoneHandles = make('div', undefined, 'zone-handles');
zoneHandles.setAttribute('aria-hidden', 'true');
const zoneCorners = [0, 1, 2, 3].map(corner => {
  const handle = make('span', undefined, 'zone-handle');
  handle.dataset.corner = corner;
  return handle;
});
const zoneMove = make('span', undefined, 'zone-handle');
zoneHandles.append(...zoneCorners, zoneMove);
const orientation = byId('map-orientation');
const selects = ['suction', 'water', 'repeats'];
const submit = byId('submit-job');
const notice = byId('job-result');
const modal = byId('confirmation');
const motion = byId('motion-controls');
const underlayToggle = byId('underlay-toggle');
const underlayOpacity = byId('underlay-opacity');
const underlayStatus = byId('underlay-status');
const alignmentFields = ['align-x', 'align-y', 'align-angle', 'align-scale'].map(byId);
const alignmentReset = byId('alignment-reset');
const alignmentSave = byId('alignment-save');
const alignmentResult = byId('alignment-result');
const forgetRequest = byId('forget-request');
const retryStorageKey = 'dreame.pending-request';
const preferenceList = byId('preference-list');
const cleaningModeOptions = byId('cleaning-mode-options');
const preferenceKeys = new WeakMap();
const requestHeaders = { 'Content-Type': 'application/json', 'X-Dreame-Request': '1' };
const liveCoverageSwitch = byId('live-coverage-switch');
const coverageSwitches = [liveCoverageSwitch, byId('history-coverage-switch')];
const coverageStorageKey = 'dreame.coverage-visible';
let coverageVisible = true;
try { coverageVisible = localStorage.getItem(coverageStorageKey) !== '0'; } catch { /* The controls still work for this page. */ }
mapHost.dataset.coverage = String(coverageVisible);
for (const input of coverageSwitches) {
  input.checked = coverageVisible;
  input.addEventListener('change', () => {
    for (const option of coverageSwitches) option.checked = input.checked;
    mapHost.dataset.coverage = String(input.checked);
    try { localStorage.setItem(coverageStorageKey, input.checked ? '1' : '0'); } catch { /* In-memory state is already updated. */ }
  });
}
const renderHistory = historyView(coverageSwitches);
const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const measures = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const frameTime = new Intl.DateTimeFormat('ru-RU', { timeStyle: 'medium' });

const states = {
  CHARGING_COMPLETED: 'Зарядка завершена', CHARGING: 'Зарядка', IDLE: 'Ожидание',
  SWEEPING: 'Подметает', MOPPING: 'Моет пол', SWEEPING_AND_MOPPING: 'Убирает и моет пол',
  CLEANING: 'Уборка', SPOT_CLEANING: 'Убирает участок', PAUSED: 'Пауза', RETURNING: 'Возвращается на базу', ERROR: 'Ошибка',
  WASHING: 'Моет салфетки', DRYING: 'Сушит салфетки', AUTO_EMPTYING: 'Очищает контейнер',
  SMART_CHARGING: 'Зарядка по расписанию', BUILDING: 'Строит карту', WAITING_FOR_TASK: 'Ожидает задание',
};
const motionLabels = {
  pause: 'Приостановить уборку?', resume: 'Продолжить уборку?',
  stop: 'Остановить уборку?', dock: 'Отправить пылесос на базу?',
};
const overlayLabels = { no_go: 'Запретная зона', no_mop: 'Без швабры', wall: 'Виртуальная стена', path: 'Пройденный путь', robot: 'Пылесос' };
const cleaningStates = new Set(['SWEEPING', 'MOPPING', 'SWEEPING_AND_MOPPING', 'CLEANING', 'SPOT_CLEANING']);
const cleanGeniusLabels = ['Выключен', 'Обычная уборка', 'Глубокая уборка'];

let telemetry = {};
let pollTimer;
let polling = false;
let map = null;
let svg = null;
let scene = null;
let overlay = null;
let overlaysGroup = null;
let overlaysKey = '';
let features = [];
let underlayKey = '';
let underlayImage = null;
let underlayReady = false;
let underlayCalibration = null;
let alignmentSaving = false;
let alignmentVersion = 0;
let target = 'all';
const rooms = new Set();
let drag = null;
let zoneEpoch = 0;
let confirmingZone = false;
let busy = false;
let retry = readRetry();
let lastDetails = '';
let lastCamera = '';
let preferences = [];
let preferenceBusy = null;
let cleanGenius = null;
let awaitingMode = null;

byId('back-link').href = `${location.origin}/#/dashboards/svg/view/dreame-control`;
byId('back-link').hidden = window.self !== window.top;

function label(node, value) {
  if (node.textContent !== value) node.textContent = value;
}

function readRetry() {
  try { return pendingRequest(sessionStorage.getItem(retryStorageKey)); }
  catch { return null; }
}

function rememberRetry(value) {
  retry = value;
  try {
    if (value) sessionStorage.setItem(retryStorageKey, JSON.stringify(value));
    else sessionStorage.removeItem(retryStorageKey);
  } catch { /* Keep the identity in memory if storage is blocked. */ }
}

function make(tag, value, className) {
  const element = document.createElement(tag);
  if (value !== undefined) element.textContent = value;
  if (className) element.className = className;
  return element;
}

function stateLabel(value) {
  return typeof value === 'string' && value ? (Object.hasOwn(states, value) ? states[value] : value) : '—';
}

function dockStatus(data) {
  if (data.online !== true) return '—';
  return ({ docked: 'На базе', returning: 'Возвращается', away: 'Вне базы' })[data.dock_status] || 'Нет данных о положении';
}

function readZone() {
  return zoneFields.map(field => field.value.trim() ? field.valueAsNumber : NaN);
}

function prepared(action = 'start') {
  return jobRequest(action, map, { kind: target, rooms, zone: readZone() },
    Object.fromEntries(selects.map(id => [id, byId(id).value])), cleanGenius);
}

function targetText() {
  if (target === 'all') return 'Вся доступная площадь';
  if (target === 'rooms') return rooms.size ? [...rooms].map((id, index) => `${index + 1}. ${map.rooms[id]}`).join(' · ')
    : 'Выберите комнаты на карте или в списке.';
  if (cleanGenius > 0) return 'При включённом CleanGenius зоны недоступны. Выберите комнаты или всю квартиру.';
  const zone = map && zoneInBounds(readZone(), map.bounds);
  if (zone) return `Зона от (${zone[0]}, ${zone[1]}) до (${zone[2]}, ${zone[3]})`;
  return zoneFields.some(field => field.value) ? 'Укажите два разных угла в границах карты.' : 'Выделите зону на карте или введите координаты.';
}

function updateOverlay() {
  const zone = overlay && map && mapHost.dataset.mode === 'zone' && (drag ? drag.preview : zoneInBounds(readZone(), map.bounds));
  if (overlay) overlay.style.display = zone ? '' : 'none';
  zoneBadge.hidden = !zone;
  zoneHandles.hidden = !zone;
  zoneSummary.hidden = !zone || Boolean(drag);
  if (!zone) return;
  const [x1, y1, x2, y2] = zone;
  for (const [key, value] of Object.entries({ x: x1, y: -y2, width: x2 - x1, height: y2 - y1 })) {
    overlay.setAttribute(key, value);
  }
  const size = zoneMeasures(zone, map.unitsPerMetre, map.floor);
  const dimensions = size ? `${measureText(size.width)} × ${measureText(size.length)} м · ${measureText(size.rectangle)} м²` : 'Масштаб карты не подтверждён';
  const floor = size?.floor == null ? 'Пол по карте: нет данных' : `Пол по карте: ${measureText(size.floor)} м²`;
  const warning = zoneHitsNoGo(zone, features) ? 'Пересекает запретную зону' : '';
  label(byId('zone-dimensions'), dimensions);
  label(byId('zone-floor'), floor);
  label(byId('zone-warning'), warning);
  if (!drag) label(zoneSummary, [dimensions, floor, warning].filter(Boolean).join('. '));

  const matrix = scene.getScreenCTM();
  if (!matrix) return;
  const corners = [[x1, -y1], [x1, -y2], [x2, -y1], [x2, -y2]]
    .map(([x, y]) => new DOMPoint(x, y).matrixTransform(matrix));
  const host = mapHost.getBoundingClientRect();
  positionZoneHandles(corners, host);
  const margin = 7;
  const centreX = (Math.min(...corners.map(point => point.x)) + Math.max(...corners.map(point => point.x))) / 2 - host.left;
  const top = Math.min(...corners.map(point => point.y)) - host.top;
  const x = Math.max(margin, Math.min(host.width - zoneBadge.offsetWidth - margin, centreX - zoneBadge.offsetWidth / 2));
  const y = Math.max(margin, Math.min(host.height - zoneBadge.offsetHeight - margin, top - zoneBadge.offsetHeight - margin));
  zoneBadge.style.transform = `translate(${x}px, ${y}px)`;
}

function positionZoneHandles([first, upper, right, last], host) {
  const offset = (dx, dy) => {
    const scale = Math.max(.5, 19 / Math.hypot(dx, dy));
    return [dx * scale, dy * scale];
  };
  // Keep all handles reachable when either side is very short on screen.
  const [xx, xy] = offset(right.x - first.x, right.y - first.y);
  const [yx, yy] = offset(upper.x - first.x, upper.y - first.y);
  const fit = (value, size, extent) => {
    const inset = Math.min(size / 2, extent + 8);
    return Math.max(inset, Math.min(size - inset, value));
  };
  const centreX = fit((first.x + last.x) / 2 - host.left - mapHost.clientLeft,
    mapHost.clientWidth, Math.abs(xx) + Math.abs(yx));
  const centreY = fit((first.y + last.y) / 2 - host.top - mapHost.clientTop,
    mapHost.clientHeight, Math.abs(xy) + Math.abs(yy));
  const place = (handle, x, y) => {
    handle.style.left = `${centreX + x}px`;
    handle.style.top = `${centreY + y}px`;
  };
  zoneCorners.forEach((handle, corner) => {
    const x = corner < 2 ? -1 : 1;
    const y = corner % 2 === 0 ? -1 : 1;
    place(handle, x * xx + y * yx, x * xy + y * yy);
  });
  place(zoneMove, 0, 0);
}

function measureText(value) {
  return value > 0 && value < .01 ? '< 0,01' : measures.format(value);
}

function updateRoomPins() {
  if (!svg) return;
  const bounds = mapHost.getBoundingClientRect();
  const pins = [];
  for (const path of svg.querySelectorAll('path[role="button"][aria-pressed="true"]')) {
    const matrix = path.getScreenCTM();
    if (!matrix) continue;
    const { x, y, width, height } = path.getBBox();
    const samples = [.5, .25, .75];
    const point = samples.flatMap(v => samples.map(u => new DOMPoint(x + u * width, y + v * height)))
      .find(point => path.isPointInFill?.(point));
    if (!point) continue;
    const screen = point.matrixTransform(matrix);
    const pin = make('span', path.dataset.order);
    pin.style.left = `${screen.x - bounds.left - mapHost.clientLeft}px`;
    pin.style.top = `${screen.y - bounds.top - mapHost.clientTop}px`;
    pins.push(pin);
  }
  roomPins.replaceChildren(...pins);
}

function renderJob() {
  const drawZone = target === 'zone' && !(cleanGenius > 0);
  if (!drawZone) stopZoneDrag();
  mapHost.dataset.mode = target === 'zone' && !drawZone ? 'rooms' : target;
  byId('zone-fields').hidden = !drawZone;
  clearZone.disabled = !drag && !zoneFields.some(field => field.value);
  byId('tuning').hidden = target === 'all' || cleanGenius > 0;
  for (const id of selects) {
    const field = byId(id);
    field.dataset.custom = String(field.value !== (id === 'repeats' ? '1' : ''));
  }
  label(byId('target-summary'), targetText());
  label(byId('map-hint'), !map ? 'Ожидаем актуальную сохранённую карту.' : drawZone
    ? (zoneInBounds(readZone(), map.bounds)
      ? 'Тяните зону за середину, а для изменения размера — за угловой маркер. Новую зону можно нарисовать снаружи текущей.'
      : 'Проведите от одного угла зоны до другого или укажите координаты.') : 'Выберите комнаты на карте или в списке.');
  const order = new Map(target === 'rooms' ? [...rooms].map((id, index) => [id, index + 1]) : []);
  for (const element of [mapHost, roomList].flatMap(host => [...host.querySelectorAll('[data-room]')])) {
    const position = order.get(Number(element.dataset.room));
    element.setAttribute('aria-pressed', String(Boolean(position)));
    if (position) element.setAttribute('aria-description', `Номер в задании — ${position}`);
    else element.removeAttribute('aria-description');
    if (element.matches('path[role="button"]')) {
      element.setAttribute('tabindex', drawZone ? '-1' : '0');
      element.dataset.order = position || '';
    } else if (element.matches('button')) label(element.firstElementChild, position ? String(position) : '');
  }
  updateRoomPins();
  updateOverlay();
  submit.disabled = busy || Boolean(drag) || preferenceBusy === 'cleangenius' || awaitingMode !== null || telemetry.online !== true || !prepared();
  label(submit, telemetry.allow_commands === true ? 'Начать уборку' : 'Проверить задание');
  label(byId('submit-hint'), preferenceBusy === 'cleangenius' || awaitingMode !== null ? 'Ждём обновления режима CleanGenius.' :
    target === 'zone' && !drawZone ? '' : telemetry.online !== true ? 'Робот должен быть на связи.' : !map
    ? 'Для задания нужна сохранённая карта.' : telemetry.allow_commands === true
      ? 'Перед запуском появится подтверждение.' : 'Проверка не запускает пылесос.');
  motion.hidden = telemetry.allow_commands !== true;
  for (const button of motion.querySelectorAll('button')) button.disabled = busy || preferenceBusy === 'cleangenius' || telemetry.online !== true;
  forgetRequest.hidden = !retry;
  forgetRequest.disabled = busy || preferenceBusy === 'cleangenius';
}

function changeTarget(kind) {
  target = kind;
  form.elements.target.value = kind;
  renderJob();
}

function replaceMap(next) {
  if (next && map?.id === next.id && map.revision === next.revision) {
    Object.assign(map, { unitsPerMetre: next.unitsPerMetre, floor: next.floor, roomAreas: next.roomAreas });
    showRoomAreas();
    return;
  }
  if (!next && !map) return;
  svg = next && safeMapSvg(next, document);
  map = svg ? next : null;
  rooms.clear();
  clearAlignmentZone('Карта обновилась. Выберите зону заново.');
  roomList.replaceChildren();
  scene = null;
  overlay = null;
  overlaysGroup = null;
  overlaysKey = '';
  features = [];
  underlayKey = '';
  underlayImage = null;
  underlayReady = false;
  underlayCalibration = null;
  resetAlignmentFields();
  byId('map-legend').replaceChildren();
  label(byId('map-id'), map ? `Карта №${map.id}` : '');
  if (!map) {
    mapHost.replaceChildren(make('p', 'Карта пока недоступна', 'muted'), zoneBadge);
    return;
  }
  for (const [id, name] of Object.entries(map.rooms)) {
    const button = make('button');
    button.type = 'button';
    button.dataset.room = id;
    const swatch = make('span', '', 'room-swatch');
    swatch.setAttribute('aria-hidden', 'true');
    const color = svg.querySelector(`path[data-room="${CSS.escape(id)}"]`)?.getAttribute('fill');
    if (color) swatch.style.setProperty('--room-color', color);
    button.append(swatch, make('span', name || `Комната ${id}`), make('small'));
    roomList.append(button);
  }
  showRoomAreas();
  scene = document.createElementNS(svgNS, 'g');
  scene.classList.add('map-scene');
  scene.append(...svg.childNodes);
  svg.append(scene);
  overlaysGroup = document.createElementNS(svgNS, 'g');
  overlaysGroup.classList.add('map-overlays');
  overlaysGroup.setAttribute('aria-hidden', 'true');
  overlay = document.createElementNS(svgNS, 'rect');
  overlay.classList.add('zone-overlay');
  overlay.setAttribute('aria-hidden', 'true');
  scene.append(overlaysGroup, overlay);
  mapHost.replaceChildren(svg, roomPins, robotMarker, zoneBadge, zoneHandles);
}

function showRoomAreas() {
  for (const button of roomList.querySelectorAll('button[data-room]')) {
    const area = map.roomAreas[button.dataset.room];
    const tag = button.lastElementChild;
    tag.hidden = area === undefined;
    if (!tag.hidden) label(tag, `${measureText(area)} м²`);
  }
}

function clearAlignmentZone(message) {
  if (!drag && !confirmingZone && !zoneFields.some(field => field.value)) return;
  zoneEpoch++;
  stopZoneDrag();
  for (const field of zoneFields) field.value = '';
  if (confirmingZone && modal.open) modal.close('cancel');
  if (message && (target === 'zone' || confirmingZone)) notify(message, false);
  renderJob();
}

function updateRobotMarker() {
  const point = features.find(feature => feature.kind === 'robot')?.points[0];
  const matrix = scene?.getScreenCTM();
  robotMarker.hidden = !matrix || !point;
  if (robotMarker.hidden) return null;
  const screen = new DOMPoint(...point).matrixTransform(matrix);
  const bounds = mapHost.getBoundingClientRect();
  robotMarker.style.left = `${screen.x - bounds.left - mapHost.clientLeft}px`;
  robotMarker.style.top = `${screen.y - bounds.top - mapHost.clientTop}px`;
  return screen;
}

function trackingActive() {
  return telemetry.online === true && (telemetry.tracking_active ?? cleaningStates.has(telemetry.state)) === true;
}

function roomAtPosition(point) {
  const matched = new Set();
  for (const path of svg.querySelectorAll('path[data-room]')) {
    const matrix = path.getScreenCTM();
    if (!matrix) continue;
    const local = point.matrixTransform(matrix.inverse());
    if (Number.isFinite(local.x) && Number.isFinite(local.y) && path.isPointInFill?.(local)) {
      matched.add(path.dataset.room);
    }
  }
  return matched.size === 1 ? map.rooms[matched.values().next().value] : null;
}

function renderLivePosition() {
  const point = updateRobotMarker();
  const roomId = telemetry.live_room_id;
  const timestamp = telemetry.live_frame_at_ms;
  const confirmedTime = Number.isSafeInteger(timestamp) && timestamp > 0 && timestamp <= 8.64e15;
  const suppliedRoom = confirmedTime && map && Number.isSafeInteger(roomId) && Object.hasOwn(map.rooms, roomId)
    ? map.rooms[roomId] : null;
  const dated = confirmedTime && (point || suppliedRoom || features.some(feature => ['path', 'coverage'].includes(feature.kind)));
  liveFrameTime.hidden = !dated;
  if (dated) {
    liveFrameTime.dateTime = new Date(timestamp).toISOString();
    label(liveFrameTime, `Данные на ${frameTime.format(timestamp)}`);
  }
  livePosition.hidden = !point && !suppliedRoom && !trackingActive();
  if (livePosition.hidden) return;
  if (!point) {
    label(livePosition, suppliedRoom ? `Пылесос в комнате «${suppliedRoom}» · ждём координаты`
      : map ? 'Ждём свежую позицию пылесоса.' : 'Ждём карту текущей уборки.');
    return;
  }
  const room = suppliedRoom || roomAtPosition(point);
  label(livePosition, room ? `Пылесос в комнате «${room}»` : 'Пылесос на карте · комната не определена');
}

function drawCoverage(coverage) {
  const layer = document.createElementNS(svgNS, 'g');
  layer.classList.add('coverage');
  if (coverage.source === 'frame') {
    const area = document.createElementNS(svgNS, 'path');
    area.setAttribute('d', rectanglePaths(coverage.rects));
    layer.append(area);
  } else {
    if (!scene.querySelector('#live-coverage-clip')) {
      const clip = document.createElementNS(svgNS, 'clipPath');
      clip.id = 'live-coverage-clip';
      const floor = document.createElementNS(svgNS, 'path');
      const { gridSize, runs } = coverage.floor;
      floor.setAttribute('d', rectanglePaths(runs.map(([y, x0, x1]) => [x0, -y - gridSize, x1 - x0, gridSize])));
      clip.append(floor);
      scene.prepend(clip);
    }
    layer.classList.add('coverage-strokes');
    layer.setAttribute('clip-path', 'url(#live-coverage-clip)');
    for (const { points } of coverage.sections) {
      const pass = document.createElementNS(svgNS, 'polyline');
      pass.setAttribute('stroke-width', coverage.width);
      pass.setAttribute('points', points.map(point => point.join(',')).join(' '));
      layer.append(pass);
    }
  }
  overlaysGroup.append(layer);
}

function renderOverlays(items, savedItems) {
  const control = byId('live-coverage-control');
  if (!overlaysGroup) {
    control.hidden = true;
    return;
  }
  const [left, bottom, right, top] = map.bounds;
  features = mapOverlays(items, savedItems).filter(({ kind, points }) => {
    if (kind !== 'robot') return true;
    const [[x, y]] = points;
    return x >= left && x <= right && y >= -top && y <= -bottom;
  });
  const coverage = cleaningCoverage(features, map);
  control.hidden = !trackingActive() && !coverage;
  liveCoverageSwitch.disabled = !coverage;
  label(byId('live-coverage-note'), coverage?.source === 'frame' ? 'По данным Dreame'
    : coverage ? 'По влажным проходам' : 'Нет данных о покрытии');
  const key = JSON.stringify([coverage?.source, features]);
  if (key === overlaysKey) return;
  overlaysKey = key;
  overlaysGroup.replaceChildren();
  const legend = byId('map-legend');
  legend.replaceChildren();
  for (const kind of new Set(features.map(item => item.kind))) {
    if (Object.hasOwn(overlayLabels, kind)) legend.append(make('span', overlayLabels[kind], kind));
  }
  if (coverage) drawCoverage(coverage);
  for (const { kind, points } of features) {
    if (kind === 'robot' || kind === 'coverage') continue;
    const tag = ['no_go', 'no_mop'].includes(kind) ? 'polygon' : 'polyline';
    const node = document.createElementNS(svgNS, tag);
    node.classList.add(kind);
    node.setAttribute('points', points.map(point => point.join(',')).join(' '));
    overlaysGroup.append(node);
  }
}

function showUnderlay() {
  const visible = underlayReady && underlayToggle.checked;
  mapHost.dataset.underlay = String(visible);
  underlayOpacity.disabled = !visible;
  mapHost.style.setProperty('--room-opacity', Number(underlayOpacity.value) / 100);
  label(byId('underlay-value'), `${underlayOpacity.value} %`);
  if (underlayImage) underlayImage.style.display = visible ? '' : 'none';
  showAlignment();
  orientMap();
}

function orientMap() {
  const visible = underlayReady && underlayToggle.checked;
  orientation.disabled = !visible;
  if (!scene || !map) return;
  const { angle, center, viewBox } = mapView(map.bounds, visible ? underlayCalibration : null, orientation.value === 'plan');
  const transform = `rotate(${angle} ${center.join(' ')})`;
  const box = viewBox.join(' ');
  const changedDuringDrag = drag && (svg.getAttribute('viewBox') !== box || scene.getAttribute('transform') !== transform);
  if (changedDuringDrag) stopZoneDrag();
  scene.setAttribute('transform', transform);
  svg.setAttribute('viewBox', box);
  updateRobotMarker();
  if (changedDuringDrag) renderJob();
  else {
    updateRoomPins();
    updateOverlay();
  }
}

orientation.addEventListener('change', orientMap);
new ResizeObserver(() => {
  updateRoomPins();
  updateRobotMarker();
  updateOverlay();
}).observe(mapHost);

function renderUnderlay(metadata) {
  const calibrated = map && underlayInfo(metadata);
  byId('underlay-controls').hidden = !calibrated;
  const note = byId('alignment-note');
  note.hidden = !calibrated || typeof metadata?.note !== 'string' || !metadata.note;
  if (!note.hidden) label(note, metadata.note);
  const key = calibrated ? JSON.stringify(calibrated) : '';
  if (key === underlayKey && calibrated) return;
  if (key !== underlayKey) {
    clearAlignmentZone('Подложка изменилась. Выберите зону заново.');
    if (alignmentHasChanges()) alignmentFeedback('Подложка обновилась. Подгонку можно начать заново.', true);
    resetAlignmentFields();
  }
  underlayKey = key;
  underlayCalibration = calibrated;
  underlayImage?.remove();
  underlayImage = null;
  underlayReady = false;
  underlayStatus.hidden = true;
  showUnderlay();
  if (!calibrated) return;

  const layer = document.createElementNS(svgNS, 'image');
  layer.setAttribute('aria-hidden', 'true');
  layer.setAttribute('pointer-events', 'none');
  layer.setAttribute('width', calibrated.width);
  layer.setAttribute('height', calibrated.height);
  layer.setAttribute('preserveAspectRatio', 'none');
  layer.setAttribute('transform', `matrix(${calibrated.matrix.join(' ')})`);
  layer.addEventListener('load', () => {
    if (underlayImage !== layer) return;
    underlayReady = true;
    underlayStatus.hidden = true;
    showUnderlay();
  });
  layer.addEventListener('error', () => {
    if (underlayImage !== layer) return;
    underlayStatus.hidden = false;
    label(underlayStatus, 'План квартиры не загрузился. Карта Dreame доступна.');
  });
  underlayImage = layer;
  scene.prepend(layer);
  label(underlayStatus, 'Загружаем план квартиры.');
  underlayStatus.hidden = false;
  showUnderlay();
  layer.setAttribute('href', underlayUrl);
}

function alignmentHasChanges() {
  return alignmentFields.some(field => field.valueAsNumber !== Number(field.defaultValue));
}

function resetAlignmentFields() {
  for (const field of alignmentFields) field.value = field.defaultValue;
}

function alignmentMatrix() {
  const matrix = adjustUnderlay(underlayCalibration, alignmentFields.map(field => field.valueAsNumber));
  return underlayInfo({ ...underlayCalibration, url: underlayUrl, matrix })?.matrix || null;
}

function showAlignment() {
  const enabled = underlayReady && underlayToggle.checked && !alignmentSaving;
  const matrix = alignmentMatrix();
  for (const field of alignmentFields) field.disabled = !enabled;
  alignmentReset.disabled = !enabled || !alignmentHasChanges();
  alignmentSave.disabled = alignmentReset.disabled || !matrix;
  if (underlayImage && underlayCalibration) {
    underlayImage.setAttribute('transform', `matrix(${(matrix || underlayCalibration.matrix).join(' ')})`);
  }
}

function alignmentFeedback(message, error) {
  label(alignmentResult, message);
  alignmentResult.dataset.error = String(error);
  alignmentResult.hidden = false;
}

byId('underlay-adjust').addEventListener('toggle', event => {
  if (event.currentTarget.open) clearAlignmentZone('Подгонка началась. После неё выберите зону заново.');
});
for (const field of alignmentFields) field.addEventListener('input', () => {
  clearAlignmentZone('Подгонка изменилась. Выберите зону заново.');
  alignmentResult.hidden = true;
  showAlignment();
});
alignmentReset.addEventListener('click', () => {
  resetAlignmentFields();
  alignmentResult.hidden = true;
  showAlignment();
});
alignmentSave.addEventListener('click', async () => {
  const matrix = alignmentMatrix();
  if (alignmentSave.disabled || !matrix || !map) return;
  const base = underlayCalibration;
  const revision = map.revision;
  alignmentSaving = true;
  showAlignment();
  try {
    const response = await fetch('./api/underlay', {
      method: 'POST', headers: requestHeaders, signal: AbortSignal.timeout(15000),
      body: JSON.stringify({ revision, expected: base.matrix, matrix }),
    });
    const result = await response.json();
    if (response.ok && result.status === 'saved') {
      if (map?.revision === revision && underlayCalibration === base) {
        clearAlignmentZone('Фон обновился. Выберите зону заново.');
        underlayCalibration = { ...base, matrix };
        underlayKey = JSON.stringify(underlayCalibration);
        alignmentVersion++;
        resetAlignmentFields();
      }
      alignmentFeedback(typeof result.message === 'string' && result.message ? result.message : 'Положение фона сохранено.', false);
    } else {
      alignmentFeedback(typeof result.message === 'string' && result.message ? result.message : 'Сохранение фона не подтверждено.', true);
    }
  } catch {
    alignmentFeedback('Ответ не получен. Проверьте положение фона перед повтором.', true);
  } finally {
    alignmentSaving = false;
    showAlignment();
    orientMap();
  }
});

underlayToggle.addEventListener('change', showUnderlay);
underlayOpacity.addEventListener('input', showUnderlay);

function toggleRoom(key) {
  if (!map || !Object.hasOwn(map.rooms, key)) return;
  const id = Number(key);
  if (rooms.has(id)) rooms.delete(id);
  else rooms.add(id);
  changeTarget('rooms');
}

form.addEventListener('change', event => {
  if (event.target.name === 'target') changeTarget(event.target.value);
  else renderJob();
});
for (const input of zoneFields) input.addEventListener('input', () => {
  stopZoneDrag();
  renderJob();
});
clearZone.addEventListener('click', () => clearAlignmentZone());
roomList.addEventListener('click', event => {
  const room = event.target.closest('button[data-room]');
  if (room) toggleRoom(room.dataset.room);
});
mapHost.addEventListener('click', event => {
  const room = event.target.closest('path[data-room]');
  if (mapHost.dataset.mode !== 'zone' && room) toggleRoom(room.dataset.room);
});
mapHost.addEventListener('keydown', event => {
  const room = event.target.closest('path[data-room][role="button"]');
  if (mapHost.dataset.mode !== 'zone' && room && ['Enter', ' '].includes(event.key)) {
    event.preventDefault();
    if (!event.repeat) toggleRoom(room.dataset.room);
  }
});

function pointerPoint(event) {
  const matrix = scene?.getScreenCTM();
  if (!matrix || !map) return null;
  const { x, y } = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
  return Number.isFinite(x) && Number.isFinite(y) ? [x, -y] : null;
}

function updateZoneDrag(event) {
  const point = pointerPoint(event);
  if (!point) return;
  if (drag.zone) {
    drag.preview = dragZone(drag.zone, drag.corner, point.map((value, index) => Math.round(value - drag.start[index])), map.bounds);
  } else {
    const end = pointOnMap(point[0], -point[1], map.bounds, true);
    drag.preview = zoneInBounds([...drag.start, ...end], map.bounds);
  }
}

function stopZoneDrag() {
  if (!drag) return;
  const { id } = drag;
  drag = null;
  if (mapHost.hasPointerCapture(id)) mapHost.releasePointerCapture(id);
}

mapHost.addEventListener('pointerdown', event => {
  if (mapHost.dataset.mode !== 'zone' || event.button !== 0 || !event.isPrimary || drag) return;
  const point = pointerPoint(event);
  if (!point) return;
  const corner = event.target.dataset.corner;
  const editing = corner !== undefined || event.target === overlay || event.target === zoneMove;
  const zone = editing ? zoneInBounds(readZone(), map.bounds) : null;
  const start = zone ? point : pointOnMap(point[0], -point[1], map.bounds);
  if (!start) return;
  event.preventDefault();
  mapHost.setPointerCapture(event.pointerId);
  drag = { id: event.pointerId, start, zone, corner: corner === undefined ? null : Number(corner), preview: zone };
  renderJob();
});
mapHost.addEventListener('pointermove', event => {
  if (drag?.id !== event.pointerId) return;
  updateZoneDrag(event);
  updateOverlay();
});
mapHost.addEventListener('pointerup', event => {
  if (drag?.id !== event.pointerId) return;
  updateZoneDrag(event);
  const zone = drag.preview;
  stopZoneDrag();
  if (zone) zoneFields.forEach((field, index) => { field.value = zone[index]; });
  renderJob();
});
for (const type of ['pointercancel', 'lostpointercapture']) {
  mapHost.addEventListener(type, event => {
    if (drag?.id === event.pointerId) {
      stopZoneDrag();
      renderJob();
    }
  });
}

function valueText(value) {
  if (typeof value === 'number' && Number.isFinite(value)) return numbers.format(value);
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  return typeof value === 'string' && value ? value : '—';
}

function renderRecords(id, records) {
  const list = byId(id);
  list.replaceChildren();
  for (const entry of Array.isArray(records) ? records : []) {
    if (!entry || typeof entry.label !== 'string') continue;
    const row = make('div');
    const value = valueText(entry.value);
    const reading = make('dd', value !== '—' && typeof entry.unit === 'string' ? `${value} ${entry.unit}` : value);
    if (typeof entry.disabled_reason === 'string' && entry.disabled_reason.trim()) {
      reading.append(make('small', entry.disabled_reason, 'preference-reason muted'));
    }
    row.append(make('dt', entry.label), reading);
    list.append(row);
  }
  if (!list.children.length) {
    const row = make('div');
    row.append(make('dt', 'Нет подтверждённых данных', 'muted'), make('dd', '—'));
    list.append(row);
  }
}

function cleanGeniusText() {
  const mode = preferences.find(entry => entry.key === 'cleangenius');
  return mode?.choices?.find(choice => choice.value === cleanGenius)?.label || cleanGeniusLabels[cleanGenius];
}

function canSavePreference(key) {
  return telemetry.online === true && telemetry.allow_commands === true && !preferenceBusy && !busy &&
    (key !== 'cleangenius' || awaitingMode === null);
}

function preferenceChoices(entry) {
  const group = make('fieldset');
  const tiles = make('div', undefined, 'segmented');
  for (const choice of entry.choices) {
    const tile = make('label');
    const input = make('input');
    input.type = 'radio';
    input.name = 'value';
    input.value = choice.value;
    input.checked = choice.value === entry.value;
    const text = make('span', choice.label);
    if (input.checked) text.append(make('small', 'Сейчас', 'preference-current'));
    tile.append(input, text);
    tiles.append(tile);
  }
  group.append(make('legend', entry.label), tiles);
  return group;
}

function renderPreferenceRows(host, entries) {
  const key = JSON.stringify(entries);
  if (key !== preferenceKeys.get(host)) {
    preferenceKeys.set(host, key);
    host.replaceChildren();
    for (const entry of entries) {
      const row = make('form', undefined, entry.choices ? 'preference-row preference-choice-row' : 'preference-row');
      row.dataset.key = entry.key;
      if (entry.choices) {
        row.append(preferenceChoices(entry));
      } else {
        const left = make('div');
        left.append(make('p', entry.label), make('p', `Сейчас — ${entry.value}${entry.unit ? ` ${entry.unit}` : ''}`, 'muted'));
        const input = make('input');
        input.type = 'number';
        input.name = 'value';
        input.required = true;
        input.step = '1';
        input.min = entry.min;
        input.max = entry.max;
        input.value = entry.value;
        input.setAttribute('aria-label', `Новое значение: ${entry.label}`);
        row.append(left, input);
      }
      const button = make('button', entry.key === 'cleangenius' ? 'Сохранить режим' : 'Сохранить');
      button.type = 'submit';
      if (entry.disabled_reason) {
        const reason = make('p', entry.disabled_reason, 'preference-reason muted');
        reason.id = `preference-reason-${entry.key}`;
        row.setAttribute('aria-describedby', reason.id);
        row.append(reason);
      }
      row.append(button);
      host.append(row);
    }
  }
  for (const [index, row] of [...host.children].entries()) {
    const entry = entries[index];
    const disabled = Boolean(entry.disabled_reason) || !canSavePreference(row.dataset.key);
    for (const field of row.querySelectorAll('input')) field.disabled = disabled;
    const draft = row.elements.namedItem('value').value;
    const button = row.querySelector('button');
    button.hidden = !preferenceRequest(entry, draft === '' ? NaN : Number(draft));
    button.disabled = disabled || button.hidden;
  }
}

function renderPreferences(items) {
  preferences = items;
  const others = items.filter(entry => entry.key !== 'cleangenius');
  const mode = items.find(entry => entry.key === 'cleangenius' && entry.choices);
  const readonlyMode = byId('cleaning-mode-readonly');
  byId('cleaning-mode').hidden = !mode && cleanGenius === null;
  readonlyMode.hidden = Boolean(mode) || cleanGenius === null;
  if (!readonlyMode.hidden) label(readonlyMode, `CleanGenius — ${cleanGeniusText()}`);
  byId('preferences').hidden = !others.length;
  renderPreferenceRows(preferenceList, others);
  renderPreferenceRows(cleaningModeOptions, mode ? [mode] : []);
  label(byId('preference-hint'), telemetry.online === true && telemetry.allow_commands === true
    ? 'Изменение вступит в силу после подтверждения роботом.' : 'Показаны текущие значения. Изменение настроек сейчас отключено.');
}

async function savePreference(event) {
  event.preventDefault();
  const row = event.target;
  if (!canSavePreference(row.dataset.key)) return;
  const preference = preferences.find(item => item.key === row.dataset.key);
  const value = row.elements.namedItem('value').value;
  const payload = preferenceRequest(preference, value === '' ? NaN : Number(value));
  if (!payload) return;
  const modeChange = payload.key === 'cleangenius';
  const feedback = byId(modeChange ? 'cleaning-mode-result' : 'preference-result');
  preferenceBusy = payload.key;
  renderPreferences(preferences);
  renderJob();
  try {
    if (modeChange) {
      const choice = preference.choices.find(item => item.value === payload.value);
      if (!(await confirmMessage('Сохранить режим CleanGenius?',
        `Выбран режим «${choice.label}». Запуск уборки — отдельное действие.`, 'Сохранить режим'))) return;
      const current = preferences.find(item => item.key === payload.key);
      if (telemetry.online !== true || telemetry.allow_commands !== true || cleanGenius !== payload.expected ||
          !preferenceRequest(current, payload.value)) {
        feedback.dataset.error = 'true';
        label(feedback, 'Текущий режим или состояние изменились. Проверьте выбранный режим.');
        feedback.hidden = false;
        return;
      }
    }
    const response = await fetch('./api/preference', {
      method: 'POST', headers: requestHeaders, body: JSON.stringify(payload), signal: AbortSignal.timeout(15000),
    });
    const result = await response.json();
    if (typeof result.message !== 'string' && typeof result.status !== 'string') throw new Error('preference');
    if (modeChange && response.ok && result.status === 'confirmed' && result.key === payload.key &&
        result.value === payload.value && cleanGenius !== result.value) awaitingMode = result.value;
    feedback.dataset.error = String(!response.ok);
    label(feedback, result.message || result.status);
    feedback.hidden = false;
  } catch {
    feedback.dataset.error = 'true';
    label(feedback, 'Ответ не получен. Дождитесь обновления текущего значения.');
    feedback.hidden = false;
  } finally {
    preferenceBusy = null;
    renderPreferences(preferences);
    renderJob();
  }
}

for (const host of [preferenceList, cleaningModeOptions]) {
  host.addEventListener('input', () => renderPreferences(preferences));
  host.addEventListener('submit', savePreference);
}

function renderCamera(camera) {
  const shown = camera?.camera_supported === true;
  byId('camera-section').hidden = !shown;
  if (!shown) return;
  const key = JSON.stringify(camera);
  if (lastCamera === key) return;
  lastCamera = key;
  const statuses = { unknown: 'Не подтверждён', unavailable: 'Недоступна', vendor_app_only: 'В Dreamehome',
    disabled: 'Выключена', enabled: 'Включена', active: 'Идёт', idle: 'Ожидание' };
  const values = [['stream_status', 'Трансляция'], ['photo_capture', 'Съёмка препятствий'],
    ['photo_upload', 'Загрузка фото'], ['privacy_confirmed', 'Согласие на съёмку'], ['live_available', 'Прямой эфир в панели']];
  const records = values.flatMap(([field, title]) => {
    const value = camera[field];
    if (typeof value === 'boolean') return [{ label: title, value }];
    if (typeof value === 'string' && value) return [{ label: title, value: Object.hasOwn(statuses, value) ? statuses[value] : value }];
    return [];
  });
  renderRecords('camera-values', records);
  const note = byId('camera-note');
  const messages = [camera.message, camera.note, ...(Array.isArray(camera.notes) ? camera.notes : [])]
    .filter(value => typeof value === 'string' && value).slice(0, 4);
  note.hidden = !messages.length;
  label(note, messages.join(' '));
}

function render(data, keepAlignment = false) {
  telemetry = data;
  const online = data.online === true;
  byId('connection').dataset.online = String(online);
  label(byId('connection'), online ? 'На связи' : 'Нет связи');
  label(byId('model'), typeof data.model === 'string' && data.model ? data.model : 'Dreame');
  label(byId('robot-state'), online ? stateLabel(data.state) : '—');
  const battery = online && Number.isFinite(data.battery) && data.battery >= 0 && data.battery <= 100 ? data.battery : null;
  label(byId('battery'), battery === null ? '—' : `${numbers.format(battery)} %`);
  byId('battery-fill').style.width = `${battery ?? 0}%`;
  label(byId('dock-state'), dockStatus(data));
  label(byId('command-state'), data.allow_commands === true ? 'Команды доступны' : 'Просмотр и проверка');
  replaceMap(online ? mapInfo(data.map) : null);
  if (!keepAlignment || !underlayCalibration) renderUnderlay(data.underlay);
  renderOverlays(data.overlays, data.map?.overlays);
  renderLivePosition();
  const last = byId('last-job');
  last.hidden = typeof data.last_job?.message !== 'string' || !data.last_job.message;
  if (!last.hidden) label(last, `Последний ответ сервиса: ${data.last_job.message}`);
  const details = data.details || {};
  const supplied = preferenceInfo(data.preferences);
  const mode = supplied.find(entry => entry.key === 'cleangenius');
  const rawMode = mode?.value ?? data.cleangenius;
  cleanGenius = [0, 1, 2].includes(rawMode) ? rawMode : null;
  if (cleanGenius === awaitingMode) awaitingMode = null;
  const editable = new Set(supplied.map(entry => entry.label));
  if (cleanGenius !== null) editable.add('CleanGenius');
  const detailKey = JSON.stringify([details, [...editable]]);
  if (lastDetails !== detailKey) {
    renderHistory(details);
    renderRecords('metrics', details.metrics);
    const settings = Array.isArray(details.settings) ? details.settings.filter(item => !editable.has(item?.label)) : [];
    byId('settings').hidden = !settings.length && supplied.length > 0;
    renderRecords('settings', settings);
    lastDetails = detailKey;
  }
  renderPreferences(supplied);
  renderCamera(data.camera);
  renderJob();
}

async function poll() {
  clearTimeout(pollTimer);
  if (document.hidden || polling) return;
  polling = true;
  const currentAlignment = alignmentVersion;
  try {
    const result = await fetch('./api/state', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
    if (!result.ok) throw new Error('state');
    render(await result.json(), alignmentVersion !== currentAlignment);
  } catch {
    render({});
  } finally {
    polling = false;
    if (!document.hidden) pollTimer = setTimeout(poll, trackingActive() ? 2000 : 3500);
  }
}

document.addEventListener('visibilitychange', poll);

function notify(message, error) {
  notice.hidden = false;
  notice.dataset.error = String(error);
  label(notice, message);
}

function confirmMessage(title, message, accept = 'Подтвердить') {
  label(byId('confirmation-title'), title);
  label(byId('confirmation-message'), message);
  label(byId('confirmation-accept'), accept);
  modal.returnValue = 'cancel';
  return new Promise(resolve => {
    modal.addEventListener('close', () => resolve(modal.returnValue === 'send'), { once: true });
    modal.showModal();
  });
}

function newRequestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  return [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
    .replace(/^(\w{8})(\w{4})(\w{4})(\w{4})(\w{12})$/, '$1-$2-$3-$4-$5');
}

async function send(action) {
  const job = prepared(action);
  const preview = action === 'start' && telemetry.allow_commands !== true;
  if (busy || preferenceBusy === 'cleangenius' || (action === 'start' && awaitingMode !== null) ||
      telemetry.online !== true || !job || (job.kind === 'zone' && drag) || (!preview && telemetry.allow_commands !== true)) return;
  const endpoint = preview ? './api/preview' : './api/job';
  const key = JSON.stringify([endpoint, job]);
  const preparedMode = cleanGenius;
  const preparedZoneEpoch = zoneEpoch;
  busy = true;
  renderJob();
  try {
    if (!preview) {
      confirmingZone = job.kind === 'zone';
      const modeText = cleanGenius === null ? '' : ` CleanGenius — ${cleanGeniusText()}.`;
      const confirmed = await confirmMessage('Подтвердите команду',
        action === 'start' ? `Начать уборку? ${targetText()}.${modeText}` : motionLabels[action]);
      confirmingZone = false;
      if (!confirmed) return;
    }
    if (telemetry.online !== true || (!preview && telemetry.allow_commands !== true) ||
        (action === 'start' && (map?.id !== job.map_id || map?.revision !== job.revision || cleanGenius !== preparedMode)) ||
        (job.kind === 'zone' && preparedZoneEpoch !== zoneEpoch)) {
      notify('Состояние робота, режим или карта изменились. Проверьте задание ещё раз.', true);
      return;
    }
    const identity = requestIdentity(key, preview ? null : retry, newRequestId);
    if (!preview) rememberRetry(identity);
    const response = await fetch(endpoint, {
      method: 'POST', headers: requestHeaders,
      body: JSON.stringify({ request_id: identity.request_id, ...job }), signal: AbortSignal.timeout(15000),
    });
    const result = await response.json();
    if (typeof result.status !== 'string' || !result.status) throw new Error('job');
    if (!preview && result.status !== 'unknown') rememberRetry(null);
    notify(result.message || result.status, !response.ok || result.status === 'unknown');
  } catch {
    notify(preview ? 'Ответ о проверке не получен. Попробуйте проверить ещё раз.' :
      'Ответ на команду не получен. Проверьте состояние пылесоса перед повтором.', true);
  } finally {
    confirmingZone = false;
    busy = false;
    renderJob();
  }
}

forgetRequest.addEventListener('click', async () => {
  if (!retry || busy || preferenceBusy === 'cleangenius') return;
  busy = true;
  renderJob();
  try {
    if (await confirmMessage('Сбросить ожидающий запрос?',
      'Ответ неизвестен. Проверьте состояние пылесоса: повтор после сброса будет новой командой.', 'Сбросить запрос')) {
      rememberRetry(null);
      notify('Ожидающий запрос сброшен.', false);
    }
  } finally {
    busy = false;
    renderJob();
  }
});

form.addEventListener('submit', event => { event.preventDefault(); void send('start'); });
motion.addEventListener('click', event => {
  const action = event.target.closest('button[data-action]')?.dataset.action;
  if (action) void send(action);
});

if (retry) notify('Исход предыдущей команды неизвестен. Повтор с теми же параметрами сохранит её номер.', true);
void poll();
