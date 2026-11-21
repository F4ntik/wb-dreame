import { adjustUnderlay, cleaningCoverage, dragZone, jobBlock, jobRequest, mapInfo, mapOverlays, mapView, maxZones, pendingRequest, pointOnMap, preferenceInfo,
  preferenceRequest, rectanglePaths, requestIdentity, roomStatusLabels, safeMapSvg, svgNS, underlayInfo, underlayUrl, zoneHitsNoGo, zoneInBounds,
  zoneHandleLayout, zoneListInBounds, zoneMeasurePlacement, zoneMeasures, zoneProblem, zoneResizeCursor } from './controls.js';
import { scenarios } from './scenarios.js';
import { confirmMessage, glyph, initViews, mapRobotIcon, panelNavigation, robotToDockScale } from './views.js';
import { resourceWidget } from './resources.js';
import { historyRows, historyView } from './history.js';

const byId = id => document.getElementById(id);
const mapHost = byId('map');
const robotMarker = byId('robot-marker');
robotMarker.append(mapRobotIcon());
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
const zoneShelf = byId('zone-measure-shelf');
const zoneSummary = byId('zone-measure-summary');
const zoneTools = byId('zone-tools');
const compactZoneTools = byId('compact-zone-tools');
const zoneResizeTool = byId('zone-resize-tool');
const coarsePointer = matchMedia('(any-pointer: coarse)');
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
const cleaningMode = byId('cleaning-mode');
const cleaningModeOptions = byId('cleaning-mode-options');
const preferenceKeys = new WeakMap();
const preferenceDrafts = new Map();
const preferenceMessages = new Map();
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
let dockMarker = null;
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
let preferenceEpoch = 0;
let cleanGenius = null;

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
  if (index !== selectedZone) byId('zone-move-tool').checked = true;
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
  zoneBadge.hidden = !zone || !matrix || !mapHost.clientWidth;
  zoneHandles.hidden = zoneBadge.hidden || Boolean(drag && !drag.zone);
  if (zoneHandles.hidden) compactZoneTools.hidden = true;
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

  if (zoneBadge.hidden) return;
  const left = host.left + mapHost.clientLeft;
  const top = host.top + mapHost.clientTop;
  if (!zoneHandles.hidden) {
    const corners = [[x1, -y1], [x1, -y2], [x2, -y1], [x2, -y2]].map(([x, y]) => {
      const point = new DOMPoint(x, y).matrixTransform(matrix);
      return { x: point.x - left, y: point.y - top };
    });
    positionZoneHandles(corners, zone);
  }
  const frame = { left, top, right: left + mapHost.clientWidth, bottom: top + mapHost.clientHeight };
  const selected = rectangles.find(rect => rect.dataset.selected === 'true');
  const others = rectangles.filter(rect => rect !== selected).map(rect => rect.getBoundingClientRect());
  const controls = zoneHandles.hidden ? [] : [...zoneHandles.children].filter(node => !node.hidden);
  if (!removeMapZone.hidden) controls.push(removeMapZone);
  const position = zoneMeasurePlacement(frame, selected.getBoundingClientRect(), controls.map(node => node.getBoundingClientRect()),
    zoneBadge.offsetWidth, zoneBadge.offsetHeight, others);
  const parent = position ? mapHost : zoneShelf;
  if (zoneBadge.parentElement !== parent) parent.append(zoneBadge);
  zoneBadge.style.transform = position ? `translate(${position[0] - left}px, ${position[1] - top}px)` : '';
}

function positionZoneHandles(corners, [x1, y1, x2, y2]) {
  const [first, upper, right] = corners;
  const xAngle = Math.atan2(right.y - first.y, right.x - first.x);
  const yAngle = Math.atan2(upper.y - first.y, upper.x - first.x);
  const layout = zoneHandleLayout(corners, coarsePointer.matches);
  const [left, bottom, rightBound, top] = map.bounds;
  // On a tiny zone, offer the corner with room to grow towards the map interior.
  const cornerToResize = Number.isInteger(drag?.handle) ? drag.handle :
    (rightBound - x2 >= x1 - left ? 2 : 0) + (top - y2 > y1 - bottom ? 1 : 0);
  const resizeOnly = layout.compact && zoneResizeTool.checked;
  const show = (key, available) => drag?.zone ? key === drag.handle : available;
  compactZoneTools.hidden = !layout.compact;
  compactZoneTools.disabled = Boolean(drag) || busy;
  zoneHandles.style.setProperty('--zone-hit', `${layout.size}px`);
  const place = (handle, { x, y }) => {
    handle.style.left = `${x}px`;
    handle.style.top = `${y}px`;
  };
  zoneCorners.forEach((handle, corner) => {
    handle.hidden = !show(corner, layout.compact ? resizeOnly && corner === cornerToResize :
      layout.fourCorners || corner === 1 || corner === 2);
    const diagonal = corner === 1 || corner === 2 ? Math.PI / 4 : -Math.PI / 4;
    handle.style.setProperty('--handle-cursor', zoneResizeCursor(xAngle + diagonal));
    place(handle, corners[corner]);
  });
  mapHost.style.setProperty('--selected-zone-cursor', resizeOnly ? zoneCorners[cornerToResize].style.getPropertyValue('--handle-cursor') : 'grab');
  zoneSides.forEach((handle, index) => {
    handle.hidden = !show(handle.dataset.side, layout.sides);
    const alongX = index % 2 === 0;
    handle.style.setProperty('--handle-cursor', zoneResizeCursor(alongX ? xAngle : yAngle));
    handle.style.setProperty('--side-angle', `${alongX ? yAngle : xAngle}rad`);
    place(handle, layout.edges[index]);
  });
  zoneMove.hidden = !show(null, !resizeOnly);
  place(zoneMove, layout.centre);

  if (removeMapZone.hidden) return;
  const { x: centreX, y: centreY } = layout.centre;
  const width = mapHost.clientWidth, height = mapHost.clientHeight;
  const inset = removeMapZone.offsetWidth / 2 + 4;
  const fit = (value, size) => Math.max(inset, Math.min(size - inset, value));
  const dx = Math.max(...corners.map(p => Math.abs(p.x - centreX))) + inset + layout.size / 2;
  const dy = Math.max(...corners.map(p => Math.abs(p.y - centreY))) + inset + layout.size / 2;
  const fitX = fit(centreX, width), fitY = fit(centreY, height);
  const candidates = [[centreX + dx, fitY], [centreX - dx, fitY], [fitX, centreY + dy], [fitX, centreY - dy]];
  const point = candidates.find(([x, y]) => x >= inset && x <= width - inset && y >= inset && y <= height - inset);
  // A zone filling the map leaves room for the button between the handles.
  const [x, y] = point ?? [fit((centreX + right.x) / 2, width), fit((centreY + right.y) / 2, height)];
  place(removeMapZone, { x, y });
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
    if (order) {
      pin.style.setProperty('--room-color', path.style.getPropertyValue('--room-color'));
      pin.prepend(make('b', order, 'order-pin'));
    }
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
  jobMode.hidden = editing || cleaningMode.hidden;
  if (!jobMode.hidden) {
    const reading = cleanGenius === null ? 'нет данных' : cleanGeniusText();
    label(byId('job-mode-value'), `${telemetry.online === true ? '' : 'До потери связи — '}${reading}`);
  }
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
      : zones.length ? 'Перетащите зону за середину или измените размер за ручку на контуре. Нажмите ×, чтобы удалить зону. Новую можно нарисовать на свободном месте.'
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
  submit.disabled = Boolean(startReason) || busy || Boolean(drag) || Boolean(preferenceBusy) || !prepared();
  label(submit, writable ? 'Начать уборку' : 'Проверить задание');
  label(byId('submit-hint'), preferenceBusy ? 'Ждём ответа о сохранении настройки.' :
    telemetry.online === true && !map ? 'Для задания нужна сохранённая карта.' :
      startReason || (writable ? 'Перед запуском появится подтверждение.' : 'Проверка не запускает пылесос.'));
  byId('motion-panel').hidden = editing || !actions.length;
  label(byId('motion-title'), actions.includes('stop') ? 'Текущая уборка' : 'Управление роботом');
  for (const button of motion.querySelectorAll('button')) {
    button.hidden = !actions.includes(button.dataset.action);
    button.disabled = busy || Boolean(preferenceBusy);
  }
  forgetRequest.hidden = !retry;
  forgetRequest.disabled = busy || Boolean(preferenceBusy);
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
  dockMarker = null;
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
    if (color) button.style.setProperty('--room-color', color);
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
  dockMarker = scene.querySelector(':scope > [data-dock]');
  if (dockMarker) {
    const drawing = document.createElementNS(svgNS, 'g');
    drawing.append(...dockMarker.children);
    const parked = mapRobotIcon();
    const size = 24 * robotToDockScale;
    parked.classList.add('parked-robot');
    parked.setAttribute('width', size);
    parked.setAttribute('height', size);
    parked.setAttribute('x', -size / 2);
    parked.setAttribute('y', 2.5 - size / 2);
    // The disk slides under the front edge of the lid in the plan view.
    drawing.insertBefore(parked, drawing.querySelector(':scope > g:last-child'));
    dockMarker.append(drawing);
  }
  scene.insertBefore(overlaysGroup, dockMarker);
  scene.append(zoneLayer);
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
  const matrix = point && scene?.getScreenCTM();
  const screen = point && matrix ? new DOMPoint(...point).matrixTransform(matrix) : null;
  robotMarker.hidden = !screen;
  if (dockMarker) {
    const pose = dockMarker.getScreenCTM();
    const unit = pose && Math.hypot(pose.a, pose.b);
    if (unit) {
      const size = parseFloat(getComputedStyle(robotMarker).width);
      const transform = `scale(${size / (24 * robotToDockScale * unit)})`;
      const drawing = dockMarker.firstElementChild;
      if (drawing.getAttribute('transform') !== transform) drawing.setAttribute('transform', transform);
    }
    const parked = telemetry.online === true && telemetry.dock_status === 'docked' && telemetry.map?.geometry_key !== null;
    if (parked !== dockMarker.hasAttribute('data-occupied')) {
      dockMarker.toggleAttribute('data-occupied', parked);
      dockMarker.setAttribute('aria-label', parked ? 'Пылесос на зарядной станции' : 'Зарядная станция');
    }
    // A live point can pass through the station before the robot confirms docking.
    const box = !parked && screen && dockMarker.getBoundingClientRect();
    const overlaps = box && Math.hypot(Math.max(box.left - screen.x, screen.x - box.right, 0),
      Math.max(box.top - screen.y, screen.y - box.bottom, 0)) < robotMarker.offsetWidth / 2;
    dockMarker.style.visibility = overlaps ? 'hidden' : '';
  }
  if (robotMarker.hidden) return null;
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
  const suppliedRoom = telemetry.dock_status !== 'docked' && confirmedTime && map && Number.isSafeInteger(roomId) && Object.hasOwn(map.rooms, roomId)
    ? map.rooms[roomId] : null;
  const outcomes = Object.keys(telemetry.live_room_status || {}).length;
  const dated = confirmedTime && (point || suppliedRoom || outcomes || features.some(feature => ['path', 'coverage'].includes(feature.kind)));
  liveFrameTime.hidden = !dated;
  if (dated) {
    liveFrameTime.dateTime = new Date(timestamp).toISOString();
    label(liveFrameTime, `Данные на ${frameTime.format(timestamp)}`);
  }
  const parked = dockMarker?.hasAttribute('data-occupied');
  livePosition.hidden = !parked && !point && !suppliedRoom && !trackingActive();
  if (livePosition.hidden) return;
  if (parked) {
    label(livePosition, 'Пылесос на зарядной станции');
    return;
  }
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
  features = mapOverlays(items, savedItems, telemetry.dock_status).filter(({ kind, points }) => {
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
    if (!Object.hasOwn(overlayLabels, kind)) continue;
    const entry = make('span', overlayLabels[kind], kind);
    if (kind === 'robot') entry.prepend(mapRobotIcon());
    legend.append(entry);
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
  const { angle, center, viewBox } = mapView(map.viewBounds, visible ? underlayCalibration : null, orientation.value === 'plan');
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
compactZoneTools.addEventListener('change', updateOverlay);
coarsePointer.addEventListener('change', updateOverlay);
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
document.addEventListener('dreame:view', ({detail}) => {
  if (detail === 'map' && cleaningMode.parentElement !== jobMode) jobMode.append(cleaningMode);
  if (detail === 'settings' && cleaningMode.parentElement === jobMode) byId('settings').before(cleaningMode);
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
  mapHost.style.removeProperty('--zone-drag-cursor');
  if (mapHost.hasPointerCapture(id)) mapHost.releasePointerCapture(id);
}

mapHost.addEventListener('pointerdown', event => {
  if (mapHost.dataset.mode !== 'zone' || event.button !== 0 || !event.isPrimary || drag || busy || removeMapZone.contains(event.target)) return;
  const point = pointerPoint(event);
  if (!point) return;
  const { corner, side, zoneIndex } = event.target.dataset;
  let handle = side ?? (corner === undefined ? null : Number(corner));
  if (Number(zoneIndex) === selectedZone && !compactZoneTools.hidden && zoneResizeTool.checked) {
    handle = zoneCorners.findIndex(node => !node.hidden);
  }
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
  mapHost.style.setProperty('--zone-drag-cursor', zone ? (handle === null ? 'grabbing' : getComputedStyle(event.target).cursor) : 'crosshair');
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

function canSavePreference() {
  return telemetry.online === true && telemetry.allow_commands === true && !preferenceBusy && !busy;
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
    tile.append(input, make('span', choice.label));
    tiles.append(tile);
  }
  group.append(make('legend', entry.label), tiles);
  return group;
}

function renderPreferenceRows(host, entries) {
  const key = JSON.stringify(entries.map(({key, label, min, max, choices}) => [key, label, choices ? null : min, choices ? null : max, choices]));
  if (key !== preferenceKeys.get(host)) {
    preferenceKeys.set(host, key);
    host.replaceChildren();
    for (const entry of entries) {
      const row = make('form', undefined, entry.choices ? 'preference-row preference-choice-row' : 'preference-row');
      row.dataset.key = entry.key;
      row.tabIndex = -1;
      const readings = make('div', undefined, 'preference-readings');
      readings.append(make('p', undefined, 'preference-actual'), make('p', undefined, 'preference-draft muted'));
      if (entry.choices) {
        row.append(preferenceChoices(entry), readings);
      } else {
        readings.prepend(make('p', entry.label, 'preference-label'));
        const input = make('input');
        input.type = 'number';
        input.name = 'value';
        input.required = true;
        input.step = '1';
        input.min = entry.min;
        input.max = entry.max;
        input.setAttribute('aria-label', `Новое значение: ${entry.label}`);
        row.append(readings, input);
      }
      const button = make('button', 'Сохранить');
      button.type = 'submit';
      const reason = make('p', undefined, 'preference-reason muted');
      reason.id = `preference-reason-${entry.key}`;
      row.setAttribute('aria-describedby', reason.id);
      const feedback = make('p', undefined, 'preference-feedback job-result');
      feedback.setAttribute('role', 'status');
      row.append(button, reason, feedback);
      host.append(row);
    }
  }
  for (const [index, row] of [...host.children].entries()) {
    const entry = entries[index];
    const disabled = Boolean(entry.disabled_reason) || !canSavePreference();
    const draft = preferenceDrafts.get(entry.key) ?? String(entry.value);
    for (const field of row.querySelectorAll('input')) {
      field.disabled = disabled;
      if (field.type === 'radio') field.checked = field.value === draft;
      else if (field.value !== draft) field.value = draft;
    }
    const text = value => entry.choices ? entry.choices.find(choice => String(choice.value) === value)?.label || 'Недоступный вариант'
      : value === '' ? '—' : `${value}${entry.unit ? ` ${entry.unit}` : ''}`;
    const source = telemetry.online === true ? 'На роботе' : 'До потери связи';
    label(row.querySelector('.preference-actual'), `${source} — ${text(String(entry.value))}`);
    const draftReading = row.querySelector('.preference-draft');
    draftReading.hidden = entry.key === 'cleangenius' && draft === String(entry.value);
    label(draftReading, `Выбрано — ${text(draft)}`);
    const button = row.querySelector('button');
    label(button, preferenceBusy === entry.key ? 'Сохраняем…' : 'Сохранить');
    button.disabled = disabled || !preferenceRequest(entry, draft === '' ? NaN : Number(draft));
    const reason = row.querySelector('.preference-reason');
    reason.hidden = !entry.disabled_reason || telemetry.online !== true;
    label(reason, entry.disabled_reason || '');
    const feedback = row.querySelector('.preference-feedback'), message = preferenceMessages.get(entry.key);
    feedback.hidden = !message;
    feedback.dataset.error = String(message?.error === true);
    label(feedback, message?.text || '');
  }
}

function setPreferences(items, rawMode) {
  preferences = preferenceInfo(items);
  const mode = preferences.find(entry => entry.key === 'cleangenius')?.value ?? rawMode;
  if (mode !== cleanGenius && preferenceMessages.get('cleangenius')?.error === false) preferenceMessages.delete('cleangenius');
  cleanGenius = [0, 1, 2].includes(mode) ? mode : null;
}

function renderPreferences() {
  const others = preferences.filter(entry => entry.key !== 'cleangenius');
  const mode = preferences.find(entry => entry.key === 'cleangenius' && entry.choices);
  const accessHint = telemetry.online !== true ? 'Нет связи с роботом. Выбор останется на странице.'
    : telemetry.allow_commands !== true ? 'Изменение настроек отключено в драйвере.' : '';
  const modeMessage = preferenceMessages.get('cleangenius');
  const missingError = !mode && modeMessage?.error ? modeMessage.text : '';
  cleaningMode.hidden = !mode && cleanGenius === null && !missingError;
  const readonlyMode = byId('cleaning-mode-readonly');
  const reading = cleanGenius === null ? 'Режим CleanGenius не подтверждён.' : `CleanGenius — ${cleanGeniusText()}.`;
  const modeHint = mode ? accessHint : `${reading} ${missingError || accessHint || 'Доступен только просмотр.'}`;
  readonlyMode.hidden = !modeHint;
  label(readonlyMode, modeHint);
  byId('preferences').hidden = !others.length;
  renderPreferenceRows(preferenceList, others);
  renderPreferenceRows(cleaningModeOptions, mode ? [mode] : []);
  const unlisted = [...preferenceMessages].findLast(([key, message]) =>
    key !== 'cleangenius' && message.error && !preferences.some(entry => entry.key === key))?.[1];
  const fallback = byId('preference-unavailable-result');
  fallback.hidden = !unlisted;
  label(fallback, unlisted?.text || '');
  label(byId('preference-hint'), accessHint);
  byId('preference-hint').hidden = !accessHint || !cleaningMode.hidden;
}

async function savePreference(event) {
  event.preventDefault();
  const row = event.target;
  if (!canSavePreference()) return;
  const preference = preferences.find(item => item.key === row.dataset.key);
  const value = row.elements.namedItem('value').value;
  const payload = preferenceRequest(preference, value === '' ? NaN : Number(value));
  if (!payload) return;
  const focusView = row.contains(document.activeElement) && row.closest('[role="tabpanel"]');
  const feedback = (text, error = true) => preferenceMessages.set(payload.key, {text, error});
  preferenceBusy = payload.key;
  preferenceMessages.delete(payload.key);
  renderPreferences();
  renderJob();
  try {
    const response = await fetch('./api/preference', {
      method: 'POST', headers: requestHeaders, body: JSON.stringify(payload), signal: AbortSignal.timeout(45000),
    });
    const result = await response.json();
    const sameKey = result.key === payload.key;
    const confirmed = response.ok && result.status === 'confirmed' && sameKey && result.value === payload.value;
    if (sameKey && Array.isArray(result.preferences)) {
      preferenceEpoch++;
      setPreferences(result.preferences, result.cleangenius);
    } else if (confirmed) {
      preferenceEpoch++;
      setPreferences(preferences.map(entry => entry.key === payload.key ? {...entry, value: result.value} : entry), cleanGenius);
    }
    if (confirmed) {
      preferenceDrafts.delete(payload.key);
      feedback('Сохранено.', false);
    } else if (response.status === 409 && result.status === 'conflict' && sameKey) {
      feedback(`${result.message || 'Настройка изменилась'}. Проверьте выбранное и нажмите «Сохранить» ещё раз.`);
    } else {
      feedback(!response.ok && typeof result.message === 'string' ? result.message : 'Не удалось подтвердить сохранение.');
    }
  } catch {
    feedback('Ответ не получен. Проверьте значение после восстановления связи.');
  } finally {
    preferenceBusy = null;
    renderPreferences();
    renderJob();
    if (focusView && !focusView.hidden && document.activeElement === document.body) {
      const currentRow = focusView.querySelector(`.preference-row[data-key="${payload.key}"]`);
      const control = currentRow?.querySelector('button:enabled') || currentRow?.querySelector('input:checked:enabled') || currentRow;
      if (focusView.contains(jobMode) && !jobMode.hidden && (!currentRow || !jobMode.open)) {
        jobMode.querySelector('summary').focus({preventScroll: true});
      } else control?.focus({preventScroll: true});
    }
  }
}

for (const host of [preferenceList, cleaningModeOptions]) {
  host.addEventListener('input', event => {
    const row = event.target.form;
    preferenceDrafts.set(row.dataset.key, event.target.value);
    preferenceMessages.delete(row.dataset.key);
    renderPreferences();
  });
  host.addEventListener('keydown', event => {
    if (event.key !== 'Enter' || !event.target.matches('input')) return;
    event.preventDefault();
    event.target.form.querySelector('button:not(:disabled)')?.focus();
  });
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

function render(data, keepAlignment = false, controllerReached = true, includePreferences = true) {
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
  const drying = online && Array.isArray(data.details?.metrics)
    ? data.details.metrics.find(row => row?.key === 'drying_progress' && row.unit === '%')?.value : null;
  const hasProgress = Number.isFinite(drying) && drying >= 0 && drying <= 100;
  const passive = ['IDLE', 'CHARGING', 'CHARGING_COMPLETED'].includes(data.state);
  const dryingActivity = online && (data.state === 'DRYING' || (hasProgress && passive && data.dock_status === 'docked'));
  label(byId('robot-state'), dryingActivity ? 'Сушит швабры' : online ? (data.state_title || data.state || '—') : '—');
  const activityIcon = byId('activity-icon');
  const iconName = dryingActivity ? 'dry' : 'robot';
  if (activityIcon.dataset.icon !== iconName) {
    activityIcon.dataset.icon = iconName;
    activityIcon.replaceChildren(glyph(iconName));
  }
  const dryingRow = byId('mop-drying');
  dryingRow.hidden = !hasProgress;
  byId('mop-drying-label').hidden = !hasProgress || dryingActivity;
  if (hasProgress) {
    dryingRow.querySelector('progress').value = drying;
    label(dryingRow.lastElementChild, `${numbers.format(drying)} %`);
  }
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
  if (online && includePreferences) setPreferences(data.preferences, data.cleangenius);
  const editable = new Set(preferences.map(entry => entry.label));
  if (cleanGenius !== null) editable.add('CleanGenius');
  const detailKey = JSON.stringify([details, [...editable], data.model, online, data.state_title]);
  if (lastDetails !== detailKey) {
    renderResources(details.metrics, data.model, online, data.state_title);
    const settings = Array.isArray(details.settings) ? details.settings.filter(item => !editable.has(item?.label)) : [];
    byId('settings').hidden = !settings.length && preferences.length > 0;
    renderRecords('settings', settings);
    lastDetails = detailKey;
  }
  renderPreferences();
  renderCamera(data.camera);
  renderJob();
}

async function poll() {
  clearTimeout(pollTimer);
  if (document.hidden || polling) return;
  polling = true;
  byId('retry-connection').disabled = true;
  const currentAlignment = alignmentVersion;
  const currentPreferences = preferenceEpoch;
  try {
    const result = await fetch('./api/state', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
    if (!result.ok) throw new Error('state');
    const data = await result.json();
    render(data, alignmentVersion !== currentAlignment, true, preferenceEpoch === currentPreferences);
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
  reclean.disabled = Boolean(reason) || busy || Boolean(preferenceBusy);
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
  if (busy || preferenceBusy ||
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
  if (!retry || busy || preferenceBusy) return;
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
  startBlock: () => jobBlock(telemetry, 'start', telemetry.allow_commands !== true),
  confirm: confirmMessage, send: scene => send('start', scene),
});
void poll();
