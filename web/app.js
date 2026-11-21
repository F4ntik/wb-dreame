import { adjustUnderlay, cleaningCoverage, dragZone, jobBlock, jobRequest, mapInfo, mapOverlays, mapView, maxZones, pendingRequest, pointOnMap, preferenceInfo,
  preferenceRequest, rectanglePaths, requestIdentity, roomStatusLabels, safeMapSvg, svgNS, underlayInfo, underlayUrl, zoneHitsNoGo, zoneInBounds,
  zoneListInBounds, zoneMeasures, zoneProblem } from './controls.js';
import { scenarios } from './scenarios.js';
import { confirmMessage, initViews, panelNavigation } from './views.js';
import { resourceWidget } from './resources.js';
import { historyRows, historyView } from './history.js';

const byId = id => document.getElementById(id);
const mapHost = byId('map');
const robotMarker = byId('robot-marker');
const livePosition = byId('live-position');
const liveFrameTime = byId('live-frame-time');
const roomList = byId('room-list');
const roomAnchors = new WeakMap();
const roomPins = document.createElement('div');
roomPins.className = 'room-order-map';
roomPins.setAttribute('aria-hidden', 'true');
const form = byId('job-form');
const zoneFields = ['zone-x1', 'zone-y1', 'zone-x2', 'zone-y2'].map(byId);
const zoneBadge = byId('zone-measure');
const zoneSummary = byId('zone-measure-summary');
const zoneTools = byId('zone-tools');
const zoneList = byId('zone-list');
const addZone = byId('add-zone');
const removeZone = byId('remove-zone');
const removeMapZone = byId('remove-map-zone');
const undoZoneRemoval = byId('undo-zone-removal');
const clearZone = byId('clear-zone');
const zonePins = make('div', undefined, 'zone-pins');
zonePins.setAttribute('aria-hidden', 'true');
const zoneHandles = make('div', undefined, 'zone-handles');
zoneHandles.setAttribute('aria-hidden', 'true');
const zoneCorners = [0, 1, 2, 3].map(corner => {
  const handle = make('span', undefined, 'zone-handle');
  handle.dataset.corner = corner;
  return handle;
});
const zoneSides = ['x1', 'y1', 'x2', 'y2'].map(side => {
  const handle = make('span', undefined, 'zone-handle');
  handle.dataset.side = side;
  return handle;
});
const zoneMove = make('span', undefined, 'zone-handle zone-handle-move');
zoneHandles.append(...zoneSides, ...zoneCorners, zoneMove);
const orientation = byId('map-orientation');
const selects = ['suction', 'water', 'repeats'];
const submit = byId('submit-job');
const reclean = byId('reclean');
const recleanResult = byId('reclean-result');
const notice = byId('job-result');
const modal = byId('confirmation');
const motion = byId('motion-controls');
const layers = byId('map-layers'), layerPanel = byId('map-layers-panel');
const coordinatesPanel = byId('zone-coordinates');
const targetSelector = byId('target-selector');
const jobMode = byId('job-mode');

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
const renderResources = resourceWidget(byId('metrics'));
const setNavigation = panelNavigation(document.querySelector('.shell'));
window.addEventListener('DOMContentLoaded', initViews, { once: true });
const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const measures = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const frameTime = new Intl.DateTimeFormat('ru-RU', { timeStyle: 'medium' });
const reportTime = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' });

const motionLabels = {
  pause: 'Приостановить уборку?', resume: 'Продолжить уборку?',
  stop: 'Остановить уборку?', dock: 'Отправить пылесос на базу?',
};
const overlayLabels = { no_go: 'Запретная зона', no_mop: 'Без швабры', wall: 'Виртуальная стена', path: 'Пройденный путь', robot: 'Пылесос' };
const cleaningStates = new Set(['SWEEPING', 'MOPPING', 'SWEEPING_AND_MOPPING', 'CLEANING', 'SPOT_CLEANING', 'SHORTCUT', 'SECOND_CLEANING', 'EXTRA_CLEANING']);
const cleanGeniusLabels = ['Выключен', 'Обычная уборка', 'Глубокая уборка'];

let telemetry = {};
let scenarioPanel;
let pollTimer;
let polling = false;
let map = null;
let draftMap = null;
let svg = null;
let scene = null;
let zoneLayer = null;
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
let zones = [];
let selectedZone = -1;
let removedZone = null;
let drag = null;
let zoneEpoch = 0;
let confirmingZone = false;
let checkJobConfirmation = null;
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

function dockStatus(data) {
  if (data.online !== true) return '—';
  return ({ docked: 'На базе', returning: 'Возвращается', away: 'Вне базы' })[data.dock_status] || 'Нет данных о положении';
}

function readZone() {
  return zoneFields.map(field => field.valueAsNumber);
}

function selectZone(index) {
  selectedZone = index;
  const region = zones[index];
  zoneFields.forEach((field, i) => { field.value = Number.isFinite(region?.[i]) ? region[i] : ''; });
}

function jobZones() {
  const rectangles = map && zoneListInBounds(zones, map.bounds);
  return rectangles && rectangles.every(zone => !zoneProblem(zone, map.floor)) ? rectangles : null;
}

function replaceZoneDraft(next) {
  const rectangles = next.length ? map && zoneListInBounds(next, map.bounds) : [];
  if (!rectangles) return false;
  stopZoneDrag();
  zones = rectangles;
  removedZone = null;
  selectZone(zones.length - 1);
  zoneEpoch++;
  if (confirmingZone && modal.open) modal.close('cancel');
  renderJob();
  return true;
}

function tuning() {
  return Object.fromEntries(selects.filter(id => byId(id).value !== '').map(id => [id, Number(byId(id).value)]));
}

function applyTuning(settings = {}) {
  for (const id of selects) byId(id).value = settings[id] ?? (id === 'repeats' ? 1 : '');
}

function tuningValues(settings = {}) {
  return selects.map(id => {
    const value = settings[id] ?? (id === 'repeats' ? 1 : '');
    return Array.from(byId(id).options).find(option => option.value === String(value))?.textContent || 'Как у робота';
  });
}

function tuningSummary(settings) {
  const [suction, water, repeats] = tuningValues(settings);
  return `Всасывание — ${suction.toLocaleLowerCase('ru')}. Вода — ${water.toLocaleLowerCase('ru')}. Проходов — ${repeats}.`;
}

function prepared(action = 'start') {
  return jobRequest(action, map, { kind: target, rooms, zones: action === 'start' && target === 'zone' ? jobZones() : null },
    Object.fromEntries(selects.map(id => [id, byId(id).value])), cleanGenius);
}

function targetText() {
  if (target === 'all') return 'Вся доступная площадь';
  if (!map) return 'Ждём актуальную карту робота.';
  if (target === 'rooms') return rooms.size ? [...rooms].map((id, index) => `${index + 1}. ${map.rooms[id]}`).join(' · ')
    : 'Выберите комнаты на карте или в списке.';
  if (cleanGenius > 0) return 'Выделение можно сохранить. Для запуска уборки зон выключите CleanGenius.';
  if (!zones.length) return 'Выделите зону на карте или введите координаты.';
  return jobZones() ? `Зон в задании — ${zones.length}` : 'Проверьте отмеченные зоны.';
}

function updateOverlay() {
  const visible = zoneLayer && map && mapHost.dataset.mode === 'zone';
  const active = drag ? drag.index : selectedZone;
  const matrix = visible ? scene.getScreenCTM() : null;
  const host = matrix ? mapHost.getBoundingClientRect() : null;
  const rectangles = [];
  const pins = [];
  let zone = null;
  if (visible) {
    const displayed = zones.map(region => zoneInBounds(region, map.bounds));
    if (drag) displayed[drag.index] = drag.preview;
    zone = displayed[active];
    displayed.forEach((region, index) => {
      if (!region) return;
      const [x1, y1, x2, y2] = region;
      const invalid = Boolean(zoneProblem(region, map.floor));
      const rectangle = document.createElementNS(svgNS, 'rect');
      rectangle.classList.add('zone-overlay');
      rectangle.dataset.zoneIndex = index;
      rectangle.dataset.selected = String(index === active);
      rectangle.dataset.invalid = String(invalid);
      for (const [key, value] of Object.entries({ x: x1, y: -y2, width: x2 - x1, height: y2 - y1 })) {
        rectangle.setAttribute(key, value);
      }
      rectangles.push(rectangle);
      if (!matrix || Math.min((x2 - x1) * Math.hypot(matrix.a, matrix.b), (y2 - y1) * Math.hypot(matrix.c, matrix.d)) < 35) return;
      const point = new DOMPoint(x1 + (x2 - x1) * .2, -y2 + (y2 - y1) * .2).matrixTransform(matrix);
      const pin = make('span', String(index + 1), 'zone-pin');
      pin.dataset.invalid = String(invalid);
      pin.style.left = `${point.x - host.left - mapHost.clientLeft}px`;
      pin.style.top = `${point.y - host.top - mapHost.clientTop}px`;
      pins.push(pin);
    });
  }
  zoneLayer?.replaceChildren(...rectangles);
  zonePins.replaceChildren(...pins);
  zoneBadge.hidden = !zone;
  zoneHandles.hidden = !zone;
  removeMapZone.hidden = !zone || !matrix || Boolean(drag);
  zoneSummary.hidden = !zone || Boolean(drag);
  if (!zone) return;
  const [x1, y1, x2, y2] = zone;
  const size = zoneMeasures(zone, map.unitsPerMetre, map.floor);
  const dimensions = size ? `${measureText(size.width)} × ${measureText(size.length)} м · ${measureText(size.rectangle)} м²` : 'Масштаб карты не подтверждён';
  const floor = size?.floor == null ? 'Пол по карте: нет данных' : `Пол по карте: ${measureText(size.floor)} м²`;
  const warning = zoneHitsNoGo(zone, features) ? 'Пересекает запретную зону' : '';
  label(byId('zone-dimensions'), dimensions);
  label(byId('zone-floor'), floor);
  label(byId('zone-warning'), warning);
  if (!drag) label(zoneSummary, [dimensions, floor, warning].filter(Boolean).join('. '));

  if (!matrix) return;
  const corners = [[x1, -y1], [x1, -y2], [x2, -y1], [x2, -y2]]
    .map(([x, y]) => new DOMPoint(x, y).matrixTransform(matrix));
  const margin = 7;
  const centreX = (Math.min(...corners.map(point => point.x)) + Math.max(...corners.map(point => point.x))) / 2 - host.left;
  const top = Math.min(...corners.map(point => point.y)) - host.top;
  const x = Math.max(margin, Math.min(host.width - zoneBadge.offsetWidth - margin, centreX - zoneBadge.offsetWidth / 2));
  const y = Math.max(margin, Math.min(host.height - zoneBadge.offsetHeight - margin, top - zoneBadge.offsetHeight - margin));
  zoneBadge.style.transform = `translate(${x}px, ${y}px)`;
  positionZoneHandles(corners, host);
}

function positionZoneHandles([first, upper, right, last], host) {
  const offset = (dx, dy) => {
    const scale = Math.max(.5, 28 / Math.hypot(dx, dy));
    return [dx * scale, dy * scale];
  };
  // Keep all handles reachable when either side is very short on screen.
  const [xx, xy] = offset(right.x - first.x, right.y - first.y);
  const [yx, yy] = offset(upper.x - first.x, upper.y - first.y);
  const extentX = Math.abs(xx) + Math.abs(yx);
  const extentY = Math.abs(xy) + Math.abs(yy);
  const fit = (value, size, extent) => {
    const inset = Math.min(size / 2, extent + 12);
    return Math.max(inset, Math.min(size - inset, value));
  };
  const width = mapHost.clientWidth;
  const height = mapHost.clientHeight;
  const centreX = fit((first.x + last.x) / 2 - host.left - mapHost.clientLeft, width, extentX);
  const centreY = fit((first.y + last.y) / 2 - host.top - mapHost.clientTop, height, extentY);
  const place = (handle, x, y) => {
    handle.style.left = `${fit(centreX + x, width, 0)}px`;
    handle.style.top = `${fit(centreY + y, height, 0)}px`;
  };
  zoneCorners.forEach((handle, corner) => {
    const x = corner < 2 ? -1 : 1;
    const y = corner % 2 === 0 ? -1 : 1;
    place(handle, x * xx + y * yx, x * xy + y * yy);
  });
  const [left, bottom, rightSide, top] = zoneSides;
  place(left, -xx, -xy);
  place(rightSide, xx, xy);
  place(bottom, -yx, -yy);
  place(top, yx, yy);
  const xAngle = `${Math.atan2(xy, xx)}rad`;
  const yAngle = `${Math.atan2(yy, yx)}rad`;
  left.style.setProperty('--side-angle', yAngle);
  rightSide.style.setProperty('--side-angle', yAngle);
  bottom.style.setProperty('--side-angle', xAngle);
  top.style.setProperty('--side-angle', xAngle);
  place(zoneMove, 0, 0);

  if (removeMapZone.hidden) return;
  const inset = removeMapZone.offsetWidth / 2 + 4;
  const gap = inset + zoneMove.offsetWidth / 2;
  const candidates = [[extentX + gap, 0], [-extentX - gap, 0], [0, extentY + gap], [0, -extentY - gap]];
  const badge = zoneBadge.getBoundingClientRect();
  const point = candidates.find(([dx, dy]) => {
    const x = centreX + dx;
    const y = centreY + dy;
    const screenX = x + host.left + mapHost.clientLeft;
    const screenY = y + host.top + mapHost.clientTop;
    return x >= inset && x <= width - inset && y >= inset && y <= height - inset &&
      (screenX + inset < badge.left || screenX - inset > badge.right || screenY + inset < badge.top || screenY - inset > badge.bottom);
  });
  // A zone filling the map leaves room for the button between the handles.
  place(removeMapZone, ...(point ?? [(xx - yx) / 2, (xy - yy) / 2]));
}

function measureText(value) {
  return value > 0 && value < .01 ? '< 0,01' : measures.format(value);
}

function updateRoomPins() {
  if (!svg || !mapHost.clientWidth) return;
  const bounds = mapHost.getBoundingClientRect();
  const pins = [];
  const occupied = [];
  const paths = [...svg.querySelectorAll('path[role="button"]')].sort((a, b) =>
    Number(Boolean(b.dataset.order)) - Number(Boolean(a.dataset.order)));
  for (const path of paths) {
    const order = path.dataset.order;
    if (!order && target === 'zone') continue;
    const matrix = path.getScreenCTM();
    if (!matrix) continue;
    if (!roomAnchors.has(path)) {
      const { x, y, width, height } = path.getBBox();
      if (!width || !height) continue;
      const samples = [.5, .25, .75, .12, .88];
      const anchor = samples.flatMap(v => samples.map(u => new DOMPoint(x + u * width, y + v * height)))
        .find(point => path.isPointInFill(point));
      roomAnchors.set(path, anchor);
    }
    const local = roomAnchors.get(path);
    if (!local) continue;
    const screen = local.matrixTransform(matrix);
    const x = screen.x - bounds.left - mapHost.clientLeft, y = screen.y - bounds.top - mapHost.clientTop;
    const roomName = map.rooms[path.dataset.room];
    const width = Math.min(108, (order ? 35 : 14) + roomName.length * 7);
    const box = [x - width / 2, y - 10, x + width / 2, y + 10];
    const overlap = occupied.some(([a, b, c, d]) => box[0] < c && a < box[2] && box[1] < d && b < box[3]);
    if (!order && (overlap || box[0] < 0 || box[1] < 0 || box[2] > mapHost.clientWidth || box[3] > mapHost.clientHeight)) continue;
    const showName = !order || (!overlap && box[0] >= 0 && box[2] <= mapHost.clientWidth && path.getBoundingClientRect().width >= width);
    const pin = make('span', showName ? roomName : '', order ? 'selected-room-pin' : 'room-name-pin');
    if (order && !showName) pin.classList.add('number-only');
    if (order) pin.prepend(make('b', order, 'order-pin'));
    pin.style.left = `${x}px`;
    pin.style.top = `${y}px`;
    occupied.push(box);
    pins.push(pin);
  }
  roomPins.replaceChildren(...pins);
}

function renderZoneFields(drawZone) {
  const editing = drawZone && map && !drag && !busy;
  for (const field of zoneFields) field.disabled = !editing;
  addZone.disabled = !editing || zones.length >= maxZones ||
    (selectedZone >= 0 && zones[selectedZone].every(Number.isNaN));
  removeZone.disabled = !editing || selectedZone < 0;
  removeMapZone.disabled = removeZone.disabled;
  const removeLabel = `Удалить зону ${selectedZone + 1}`;
  removeMapZone.setAttribute('aria-label', removeLabel);
  removeMapZone.title = removeLabel;
  zoneTools.hidden = !drawZone || (!zones.length && !removedZone);
  undoZoneRemoval.hidden = !removedZone;
  undoZoneRemoval.disabled = !editing || zones.length >= maxZones;
  clearZone.disabled = !editing || !zones.length;
  while (zoneList.children.length > zones.length) zoneList.lastElementChild.remove();
  while (zoneList.children.length < zones.length) {
    const index = zoneList.children.length;
    const button = make('button');
    button.type = 'button';
    button.dataset.zoneIndex = index;
    button.append(make('span', `Зона ${index + 1}`), make('small'));
    const item = make('li');
    item.append(button);
    zoneList.append(item);
  }
  zones.forEach((coordinates, index) => {
    const button = zoneList.children[index].firstElementChild;
    const zone = map && zoneInBounds(coordinates, map.bounds);
    button.setAttribute('aria-pressed', String(index === selectedZone));
    button.disabled = !editing;
    label(button.lastElementChild, zoneProblem(zone, map?.floor));
  });
  label(byId('zone-edit-hint'), selectedZone < 0 ? 'Укажите противоположные углы первой зоны.' :
    `Укажите противоположные углы зоны ${selectedZone + 1}.`);
}

function renderJob() {
  if (modal.open) checkJobConfirmation?.();
  scenarioPanel?.render();
  const editing = scenarioPanel?.editing() || false;
  byId('job-title').closest('.job-card').classList.toggle('editing-entrance', editing);
  label(byId('map-title'), editing ? 'Песок у входа' : 'Карта уборки');
  label(byId('job-title'), editing ? 'Настройки сценария' : 'Задание уборки');
  targetSelector.hidden = editing;
  targetSelector.disabled = !map;
  byId('map-editor-mode').hidden = !editing;
  byId('room-choices').hidden = target !== 'rooms' || !map || editing;
  byId('to-job').hidden = !map;
  label(byId('to-job'), editing ? 'К сохранению ↓' : 'К заданию ↓');
  jobMode.hidden = editing || cleanGenius === null;
  if (!jobMode.hidden) label(jobMode, `CleanGenius · ${cleanGeniusText()}`);
  label(byId('tuning-title'), editing ? 'Параметры сценария' : 'Параметры задания');
  label(byId('tuning-hint'), editing ? 'Сохраняются на контроллере. «Как у робота» использует настройки на момент запуска.' : 'Действуют для этого запуска.');
  byId('entrance-editor-actions').hidden = !editing;
  const drawZone = target === 'zone';
  if (!drawZone) stopZoneDrag();
  mapHost.dataset.mode = target;
  mapHost.tabIndex = drawZone && map ? 0 : -1;
  byId('zone-fields').hidden = !drawZone;
  renderZoneFields(drawZone);
  byId('tuning').hidden = !editing && (target === 'all' || cleanGenius > 0);
  for (const id of selects) {
    const field = byId(id);
    field.dataset.custom = String(field.value !== (id === 'repeats' ? '1' : ''));
  }
  label(byId('target-summary'), targetText());
  byId('map-hint').hidden = !map;
  label(byId('map-hint'), !map ? '' : drawZone
    ? (zones.length >= maxZones ? `В задании может быть до ${maxZones} зон. Нажмите × рядом с выбранной зоной, чтобы удалить её.`
      : zones.length ? 'Потяните зону за середину, сторону или угол. Нажмите ×, чтобы удалить выбранную зону. Новую зону можно нарисовать на свободном месте.'
        : 'Проведите от одного угла зоны до другого или укажите координаты.') : target === 'rooms' ? 'Нажимайте комнаты в порядке уборки. Повторное нажатие снимает выбор.'
      : 'Нажмите комнату на карте, чтобы убрать её отдельно.');
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
  const writable = telemetry.allow_commands === true;
  const startReason = jobBlock(telemetry, 'start', !writable);
  const actions = Object.keys(motionLabels).filter(action => !jobBlock(telemetry, action));
  submit.disabled = Boolean(startReason) || busy || Boolean(drag) || preferenceBusy === 'cleangenius' || awaitingMode !== null || !prepared();
  label(submit, writable ? 'Начать уборку' : 'Проверить задание');
  label(byId('submit-hint'), preferenceBusy === 'cleangenius' || awaitingMode !== null ? 'Ждём обновления режима CleanGenius.' :
    telemetry.online === true && !map ? 'Для задания нужна сохранённая карта.' :
      startReason || (writable ? 'Перед запуском появится подтверждение.' : 'Проверка не запускает пылесос.'));
  byId('motion-panel').hidden = editing || !actions.length;
  label(byId('motion-title'), actions.includes('stop') ? 'Текущая уборка' : 'Управление роботом');
  for (const button of motion.querySelectorAll('button')) {
    button.hidden = !actions.includes(button.dataset.action);
    button.disabled = busy || preferenceBusy === 'cleangenius';
  }
  forgetRequest.hidden = !retry;
  forgetRequest.disabled = busy || preferenceBusy === 'cleangenius';
  const canChooseLayers = map && (!byId('underlay-controls').hidden || !byId('route-choice').hidden || !byId('live-coverage-control').hidden);
  layers.hidden = !canChooseLayers;
  layerPanel.hidden = !canChooseLayers || layers.getAttribute('aria-expanded') !== 'true';
  renderReclean();
}

function changeTarget(kind) {
  if (notice.dataset.draft && target !== kind) notice.hidden = true;
  target = kind;
  form.elements.target.value = kind;
  renderJob();
}

function replaceMap(next) {
  if (next && map?.id === next.id && map.revision === next.revision) {
    Object.assign(map, { unitsPerMetre: next.unitsPerMetre, floor: next.floor, roomAreas: next.roomAreas, rooms: next.rooms });
    return;
  }
  if (!next && !map) return;
  svg = next && safeMapSvg(next, document);
  map = svg ? next : null;
  stopZoneDrag();
  if (confirmingZone && modal.open) modal.close('cancel');
  if (map && (map.id !== draftMap?.id || map.revision !== draftMap?.revision)) {
    draftMap = {id: map.id, revision: map.revision};
    if (notice.dataset.draft) notice.hidden = true;
    scenarioPanel?.mapChanged();
    rooms.clear();
    clearAlignmentZone('Карта обновилась. Выберите зону заново.');
    underlayKey = '';
  }
  roomList.replaceChildren();
  scene = null;
  zoneLayer = null;
  overlaysGroup = null;
  overlaysKey = '';
  features = [];
  underlayImage = null;
  underlayReady = false;
  underlayCalibration = null;
  resetAlignmentFields();
  byId('map-legend').replaceChildren();
  label(byId('map-id'), map ? `Карта № ${map.id}` : '');
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
  scene = document.createElementNS(svgNS, 'g');
  scene.classList.add('map-scene');
  scene.append(...svg.childNodes);
  svg.append(scene);
  overlaysGroup = document.createElementNS(svgNS, 'g');
  overlaysGroup.classList.add('map-overlays');
  overlaysGroup.setAttribute('aria-hidden', 'true');
  zoneLayer = document.createElementNS(svgNS, 'g');
  zoneLayer.setAttribute('aria-hidden', 'true');
  scene.append(overlaysGroup, zoneLayer);
  mapHost.replaceChildren(svg, roomPins, robotMarker, zonePins, zoneBadge, zoneHandles, removeMapZone);
}

function renderLiveRoomStatuses(outcomes) {
  const statuses = new Map();
  const legend = byId('live-room-statuses');
  for (const id of Object.keys(map?.rooms || {})) {
    const value = outcomes?.[id];
    if (['completed', 'skipped', 'blocked'].includes(value)) statuses.set(id, value);
  }
  const states = [...new Set(statuses.values())];
  if (!states.length && (trackingActive() || (outcomes && typeof outcomes === 'object'))) {
    label(legend, 'Ждём статусы комнат от робота.');
  } else {
    legend.replaceChildren(...states.map(state => {
      const item = make('span', roomStatusLabels[state]);
      item.dataset.roomStatus = state;
      return item;
    }));
  }
  for (const button of roomList.querySelectorAll('button[data-room]')) {
    const id = button.dataset.room;
    const area = map.roomAreas[id];
    const detail = statuses.size ? [roomStatusLabels[statuses.get(id) || 'unknown']] : [];
    if (area !== undefined) detail.push(`${measureText(area)} м²`);
    label(button.children[1], map.rooms[id]);
    const tag = button.lastElementChild;
    label(tag, detail.join(' · '));
    tag.hidden = !detail.length;
    tag.dataset.roomStatus = statuses.get(id) || '';
  }
  for (const path of svg?.querySelectorAll('path[data-room]') || []) {
    const id = path.dataset.room;
    const status = statuses.get(id);
    path.dataset.roomStatus = status || '';
    path.setAttribute('aria-label', statuses.size
      ? `${map.rooms[id]} · ${roomStatusLabels[status || 'unknown']}` : map.rooms[id]);
  }
}

function clearAlignmentZone(message) {
  if (!drag && !confirmingZone && !zones.length && !removedZone) return;
  const showMessage = target === 'zone' || confirmingZone;
  replaceZoneDraft([]);
  if (message && showMessage) notify(message, false);
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
  const outcomes = Object.keys(telemetry.live_room_status || {}).length;
  const dated = confirmedTime && (point || suppliedRoom || outcomes || features.some(feature => ['path', 'coverage'].includes(feature.kind)));
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
    byId('route-choice').hidden = true;
    return;
  }
  const [left, bottom, right, top] = map.bounds;
  features = mapOverlays(items, savedItems).filter(({ kind, points }) => {
    if (kind !== 'robot') return true;
    const [[x, y]] = points;
    return x >= left && x <= right && y >= -top && y <= -bottom;
  });
  byId('route-choice').hidden = !features.some(item => item.kind === 'path');
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
  if (!map) { showUnderlay(); return; }
  if (key === underlayKey && calibrated && underlayImage) return;
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
  if (scenarioPanel?.editing() || !map || !Object.hasOwn(map.rooms, key)) return;
  const id = Number(key);
  if (rooms.has(id)) rooms.delete(id);
  else rooms.add(id);
  changeTarget('rooms');
}

targetSelector.addEventListener('change', event => changeTarget(event.target.value));
form.addEventListener('change', renderJob);
layers.addEventListener('click', () => {
  layerPanel.hidden = !layerPanel.hidden;
  layers.setAttribute('aria-expanded', String(!layerPanel.hidden));
});
byId('route-switch').addEventListener('change', event => { mapHost.dataset.route = String(event.target.checked); });
byId('to-job').addEventListener('click', () => byId('job-title').focus());
jobMode.addEventListener('click', () => {
  byId('tab-settings').click();
  const control = cleaningModeOptions.querySelector('input:checked:not(:disabled)');
  (control || byId('tab-settings')).focus();
});
byId('retry-connection').addEventListener('click', poll);
for (const input of zoneFields) input.addEventListener('input', () => {
  if (selectedZone < 0) selectedZone = 0;
  zones[selectedZone] = readZone();
  zoneEpoch++;
  renderJob();
});
addZone.addEventListener('click', () => {
  zones.push([NaN, NaN, NaN, NaN]);
  selectZone(zones.length - 1);
  zoneEpoch++;
  renderJob();
  coordinatesPanel.open = true;
  zoneFields[0].focus();
});
function deleteSelectedZone(fromMap) {
  if (removeZone.disabled) return;
  const [coordinates] = zones.splice(selectedZone, 1);
  removedZone = { index: selectedZone, coordinates };
  selectZone(Math.min(selectedZone, zones.length - 1));
  zoneEpoch++;
  renderJob();
  if (fromMap) (selectedZone < 0 ? undoZoneRemoval : mapHost).focus({ preventScroll: true });
  else if (selectedZone < 0) addZone.focus();
}

removeZone.addEventListener('click', () => deleteSelectedZone(false));
removeMapZone.addEventListener('click', () => deleteSelectedZone(true));
undoZoneRemoval.addEventListener('click', () => {
  if (!removedZone || undoZoneRemoval.disabled) return;
  const { index, coordinates } = removedZone;
  zones.splice(index, 0, coordinates);
  removedZone = null;
  selectZone(index);
  zoneEpoch++;
  renderJob();
  zoneList.children[index].firstElementChild.focus({ preventScroll: true });
});
clearZone.addEventListener('click', () => clearAlignmentZone());
zoneList.addEventListener('click', event => {
  const button = event.target.closest('button[data-zone-index]');
  if (!button) return;
  selectZone(Number(button.dataset.zoneIndex));
  renderJob();
});
roomList.addEventListener('click', event => {
  const room = event.target.closest('button[data-room]');
  if (room) toggleRoom(room.dataset.room);
});
mapHost.addEventListener('click', event => {
  const room = event.target.closest('path[data-room]');
  if (mapHost.dataset.mode !== 'zone' && room) toggleRoom(room.dataset.room);
});
for (const control of [mapHost, zoneList]) control.addEventListener('keydown', event => {
  if (!removeZone.disabled && event.key === 'Delete' &&
      (control === mapHost || Number(event.target.dataset.zoneIndex) === selectedZone)) {
    event.preventDefault();
    if (!event.repeat) deleteSelectedZone(true);
  }
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
    drag.preview = dragZone(drag.zone, drag.handle, point.map((value, index) => Math.round(value - drag.start[index])), map.bounds);
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
  if (mapHost.dataset.mode !== 'zone' || event.button !== 0 || !event.isPrimary || drag || busy || removeMapZone.contains(event.target)) return;
  const point = pointerPoint(event);
  if (!point) return;
  const { corner, side, zoneIndex } = event.target.dataset;
  const handle = side ?? (corner === undefined ? null : Number(corner));
  const editing = zoneIndex !== undefined || handle !== null || event.target === zoneMove;
  const emptySelected = zones[selectedZone]?.every(Number.isNaN);
  const index = editing ? Number(zoneIndex ?? selectedZone) : emptySelected ? selectedZone : zones.length;
  if (index >= maxZones) return;
  const zone = editing ? zoneInBounds(zones[index], map.bounds) : null;
  const start = zone ? point : pointOnMap(point[0], -point[1], map.bounds);
  if (!start) return;
  if (editing && selectedZone !== index) selectZone(index);
  event.preventDefault();
  mapHost.focus({ preventScroll: true });
  mapHost.setPointerCapture(event.pointerId);
  drag = { id: event.pointerId, start, index, zone, handle, preview: zone };
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
  const { index, preview } = drag;
  stopZoneDrag();
  if (preview) {
    zones[index] = preview;
    selectZone(index);
    zoneEpoch++;
  }
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

function render(data, keepAlignment = false, controllerReached = true) {
  telemetry = data;
  setNavigation(data.panel_navigation);
  const online = data.online === true;
  const connection = byId('connection'), writable = data.allow_commands === true;
  const changed = connection.dataset.online !== String(online) || connection.dataset.writable !== String(writable);
  connection.dataset.online = String(online);
  connection.dataset.writable = String(writable);
  if (changed) document.dispatchEvent(new Event('dreame:connection'));
  label(byId('connection'), online ? 'На связи' : 'Нет связи');
  if (typeof data.model === 'string' && data.model) label(byId('model'), data.model);
  byId('offline-notice').hidden = online;
  if (!online) {
    label(byId('offline-title'), controllerReached ? 'Нет связи с роботом' : 'Контроллер не отвечает');
    label(byId('offline-message'), controllerReached ? 'Ждём ответа Dreamehome. Панель обновится автоматически.'
      : 'Проверьте подключение к сети Wiren Board. Панель повторит запрос.');
  }
  label(byId('robot-state'), online ? (data.state_title || data.state || '—') : '—');
  const battery = online && Number.isFinite(data.battery) && data.battery >= 0 && data.battery <= 100 ? data.battery : null;
  label(byId('battery'), battery === null ? '—' : `${numbers.format(battery)} %`);
  byId('battery-fill').parentElement.hidden = battery === null;
  byId('battery-fill').style.width = `${battery ?? 0}%`;
  label(byId('dock-state'), dockStatus(data));
  byId('command-state').hidden = !online || data.allow_commands === true;
  label(byId('command-state'), 'Доступна только проверка');
  replaceMap(online ? mapInfo(data.map, data.room_names) : null);
  renderLiveRoomStatuses(data.live_room_status);
  if (!keepAlignment || !underlayCalibration) renderUnderlay(data.underlay);
  renderOverlays(data.overlays, data.map?.overlays);
  renderLivePosition();
  const last = byId('last-job');
  last.hidden = typeof data.last_job?.message !== 'string' || !data.last_job.message;
  if (!last.hidden) label(last, `Последний ответ сервиса: ${data.last_job.message}`);
  const details = data.details || {};
  renderHistory(details, online && !trackingActive() && data.live_room_status == null
    && (data.state === 'IDLE' || data.dock_status === 'docked'));
  const supplied = preferenceInfo(data.preferences);
  const mode = supplied.find(entry => entry.key === 'cleangenius');
  const rawMode = mode?.value ?? data.cleangenius;
  cleanGenius = [0, 1, 2].includes(rawMode) ? rawMode : null;
  if (cleanGenius === awaitingMode) awaitingMode = null;
  const editable = new Set(supplied.map(entry => entry.label));
  if (cleanGenius !== null) editable.add('CleanGenius');
  const detailKey = JSON.stringify([details, [...editable], data.model, online]);
  if (lastDetails !== detailKey) {
    renderResources(details.metrics, data.model, online);
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
  byId('retry-connection').disabled = true;
  const currentAlignment = alignmentVersion;
  try {
    const result = await fetch('./api/state', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
    if (!result.ok) throw new Error('state');
    render(await result.json(), alignmentVersion !== currentAlignment);
  } catch {
    render({}, false, false);
  } finally {
    polling = false;
    byId('retry-connection').disabled = false;
    if (!document.hidden) pollTimer = setTimeout(poll, trackingActive() ? 2000 : 3500);
  }
}

document.addEventListener('visibilitychange', poll);

function notify(message, error) {
  delete notice.dataset.draft;
  notice.hidden = false;
  notice.dataset.error = String(error);
  label(notice, message);
}

function newRequestId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  return [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
    .replace(/^(\w{8})(\w{4})(\w{4})(\w{4})(\w{12})$/, '$1-$2-$3-$4-$5');
}

function latestReport() {
  return historyRows(telemetry.details?.history)[0];
}

async function confirmJob(title, message, accept, blocked, feedback, destructive = false) {
  if (modal.open) return false;
  checkJobConfirmation = () => {
    const reason = blocked();
    if (!reason) return true;
    if (modal.open) modal.close('cancel');
    feedback(reason, true);
    return false;
  };
  try {
    return await confirmMessage(title, message, accept, destructive) && checkJobConfirmation();
  } finally {
    checkJobConfirmation = null;
  }
}

function renderReclean() {
  const report = latestReport();
  let reason = '';
  if (telemetry.online !== true) reason = 'Доуборка станет доступна после восстановления связи.';
  else if (!map) reason = 'Для доуборки нужна текущая сохранённая карта.';
  else if (telemetry.reclean_attempt) reason = 'По последнему отчёту доуборку уже запросили. Проверьте состояние робота.';
  else if (!report) reason = 'Ждём последнего отчёта Dreamehome.';
  else if (!report.mapAvailable) reason = 'У последнего отчёта нет архивной карты.';
  else reason = jobBlock(telemetry, 'start', telemetry.allow_commands !== true);
  reclean.disabled = Boolean(reason) || busy || preferenceBusy === 'cleangenius';
  const hint = reason || `Проверить пропуски по отчёту от ${reportTime.format(report.timestamp * 1000)}.`;
  if (recleanResult.dataset.report !== String(report?.timestamp)) {
    recleanResult.hidden = true;
    byId('history-reclean-result').hidden = true;
  }
  label(byId('reclean-hint'), hint);
  label(byId('history-reclean-hint'), hint);
  byId('history-reclean').disabled = reclean.disabled;
}

function showRecleanResult(message, error = false) {
  recleanResult.dataset.report = String(latestReport()?.timestamp);
  for (const result of [recleanResult, byId('history-reclean-result')]) {
    result.hidden = false;
    result.dataset.error = String(error);
    label(result, message);
  }
}

async function postReclean(endpoint, request) {
  const response = await fetch(endpoint, { method: 'POST', headers: requestHeaders,
    body: JSON.stringify(request), signal: AbortSignal.timeout(20000) });
  const result = await response.json();
  return { ...result, ok: response.ok };
}

byId('history-reclean').addEventListener('click', () => reclean.click());
reclean.addEventListener('click', async () => {
  if (reclean.disabled) return;
  const report = latestReport();
  const request = { request_id: newRequestId(), action: 'reclean', timestamp: report.timestamp,
    map_id: map.id, revision: map.revision };
  let sending = false;
  busy = true;
  renderJob();
  showRecleanResult('Проверяем последний отчёт Dreamehome…');
  try {
    const preview = await postReclean('./api/preview', request);
    if (!preview.ok || preview.status !== 'preview') {
      showRecleanResult(preview.message || 'Доуборка по этому отчёту недоступна.', true);
      return;
    }
    const describeRooms = ids => (ids || []).map(id => map?.rooms[id] || `Комната ${id}`).join(', ');
    const names = describeRooms(preview.targets?.rooms);
    const separateRooms = describeRooms(preview.targets?.separate_rooms);
    const ambiguousRooms = describeRooms(preview.targets?.ambiguous_rooms);
    const targets = [names && `Пропущенные комнаты — ${names}`,
      preview.targets?.dirty_areas === true && 'Загрязнённые участки на карте Dreamehome'].filter(Boolean);
    if (!targets.length) throw new Error('reclean targets');
    const notes = [preview.targets?.ordinary_rooms === true && 'Покомнатная уборка с текущими настройками',
      separateRooms && `Dreame запускает уборку комнат ${separateRooms} отдельной командой. Их можно выбрать на карте`,
      preview.targets?.separate_dirty === true && 'Загрязнённые участки проверьте в Dreamehome',
      ambiguousRooms && `Отметки комнат ${ambiguousRooms} противоречат друг другу. Сверьте их с отчётом Dreamehome`];
    const description = [...targets, ...notes.filter(Boolean)].join('. ') + '.';
    const enabled = telemetry.allow_commands === true;
    showRecleanResult(enabled ? description : `${description} Запуск выключен в настройках драйвера.`);
    if (!enabled) return;
    const blocked = () => jobBlock(telemetry, 'start') ||
      (latestReport()?.timestamp !== request.timestamp || telemetry.reclean_attempt ||
        map?.id !== request.map_id || map?.revision !== request.revision
        ? 'Отчёт или карта изменились. Проверьте задание заново.' : '');
    const reason = blocked();
    if (reason) {
      showRecleanResult(reason, true);
      return;
    }
    if (!(await confirmJob('Доубрать по последнему отчёту?',
      `${reportTime.format(report.timestamp * 1000)}. ${description}`, 'Доубрать', blocked, showRecleanResult))) return;
    sending = true;
    const result = await postReclean('./api/job', request);
    showRecleanResult(result.message || 'Ответ на команду не подтверждён.', !result.ok || result.status !== 'acknowledged');
  } catch {
    showRecleanResult(sending ? 'Ответ на команду не получен. Проверьте состояние пылесоса.' :
      'Не удалось проверить последний отчёт. Попробуйте ещё раз.', true);
  } finally {
    busy = false;
    renderJob();
    if (sending) void poll();
  }
});

async function send(action, scenario = null) {
  const feedback = scenario?.report ?? notify;
  const job = scenario?.job ?? prepared(action);
  const preview = action === 'start' && telemetry.allow_commands !== true;
  if (busy || preferenceBusy === 'cleangenius' || (action === 'start' && awaitingMode !== null) ||
      !job || (job.kind === 'zone' && drag)) return;
  const reason = jobBlock(telemetry, action, preview);
  if (reason) {
    feedback(reason, true);
    return;
  }
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
      const areas = job.zones || (job.zone ? [job.zone] : []);
      const areaText = areas.map(([x1, y1, x2, y2], index) => `Зона ${index + 1} · (${x1}, ${y1}) → (${x2}, ${y2})`);
      const verb = { start: 'Начать уборку', pause: 'Приостановить', resume: 'Продолжить', stop: 'Остановить', dock: 'На базу' }[action];
      const message = scenario?.confirmation ?? (action === 'start' ? [targetText() + '.' + modeText, ...areaText,
        ...(['zone', 'rooms'].includes(job.kind) && cleanGenius === 0 ? [tuningSummary(job)] : [])].join('\n')
        : 'Команда будет отправлена пылесосу.');
      const blocked = () => jobBlock(telemetry, action) ||
        ((action === 'start' && (map?.id !== job.map_id || map?.revision !== job.revision || cleanGenius !== preparedMode)) ||
         (job.kind === 'zone' && preparedZoneEpoch !== zoneEpoch)
          ? 'Режим или карта изменились. Проверьте задание ещё раз.' : '');
      const confirmed = await confirmJob(action === 'start' ? 'Начать уборку?' : motionLabels[action],
        message, verb, blocked, feedback, action === 'stop');
      confirmingZone = false;
      if (!confirmed) return;
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
    feedback(result.message || result.status, !response.ok || result.status === 'unknown');
  } catch {
    feedback(preview ? 'Ответ о проверке не получен. Попробуйте проверить ещё раз.' :
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
      'Ответ неизвестен. Проверьте состояние пылесоса: повтор после сброса будет новой командой.', 'Сбросить запрос', true)) {
      rememberRetry(null);
      notify('Ожидающий запрос сброшен.', false);
    }
  } finally {
    busy = false;
    renderJob();
  }
});

form.addEventListener('submit', event => {
  event.preventDefault();
  if (scenarioPanel?.editing()) void scenarioPanel.saveEditor();
  else void send('start');
});
motion.addEventListener('click', event => {
  const action = event.target.closest('button[data-action]')?.dataset.action;
  if (action) void send(action);
});

if (retry) notify('Исход предыдущей команды неизвестен. Повтор с теми же параметрами сохранит её номер.', true);
scenarioPanel = scenarios({
  map: () => map, online: () => telemetry.online === true, busy: () => busy || Boolean(drag) || Boolean(preferenceBusy) || modal.open,
  zones: () => target === 'zone' ? jobZones() : null,
  tuning, tuningValues, zoneCount: () => zones.length,
  draft: () => ({target, zones:zones.map(z => [...z]), selected:selectedZone, removedZone, tuning:tuning(), coordinatesOpen:coordinatesPanel.open}),
  restore: (draft, sameMap) => {
    stopZoneDrag();
    zones = sameMap ? draft.zones : [];
    removedZone = sameMap ? draft.removedZone : null;
    selectZone(sameMap ? draft.selected : -1);
    zoneEpoch++;
    applyTuning(draft.tuning);
    coordinatesPanel.open = sameMap && draft.coordinatesOpen;
    changeTarget(sameMap ? draft.target : 'all');
  },
  openMap: () => {
    byId('tab-map').click();
    byId('map').focus({preventScroll:true});
    byId('map').scrollIntoView({block:'nearest'});
  },
  pick: (saved, settings, editing) => {
    applyTuning(settings);
    changeTarget('zone');
    if (!replaceZoneDraft(saved)) return false;
    coordinatesPanel.open = false;
    if (!editing) {
      notify('На карту перенесены зоны сценария «Песок у входа».', false);
      notice.dataset.draft = 'true';
    }
    return true;
  },
  writable: () => telemetry.allow_commands === true,
  startBlock: () => awaitingMode !== null ? 'Ждём обновления режима CleanGenius.'
    : jobBlock(telemetry, 'start', telemetry.allow_commands !== true),
  confirm: confirmMessage, send: scene => send('start', scene),
});
void poll();
