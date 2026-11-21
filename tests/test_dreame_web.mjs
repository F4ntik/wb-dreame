import assert from 'node:assert/strict';
import { test } from 'node:test';
import { adjustUnderlay, cleaningCoverage, dragZone, jobBlock, jobRequest, mapInfo, mapOverlays, mapView, maxZones, pendingRequest, pointOnMap, preferenceInfo,
  preferenceRequest, rectanglePaths, requestIdentity, roomContour, underlayInfo, zoneHitsNoGo, zoneInBounds, zoneMeasures, zoneProblem } from '../web/controls.js';

const map = mapInfo({ id: 7, revision: 'r2', bounds: [-300, -100, 500, 650],
  rooms: { 1: 'Кухня', 3: 'Холл', wrong: 'Не комната' }, svg: '<svg/>' });
const automatic = { suction: '', water: '', repeats: '1' };

test('dock clearance enlarges only the displayed plan, including a calibrated underlay', () => {
  const base = { ...map, bounds: [0, 0, 3600, 2400] };
  const flush = mapInfo({ ...base, view_padding: 102 });
  assert.deepEqual(flush.bounds, base.bounds);
  assert.deepEqual(flush.viewBounds, [-102, -102, 3702, 2502]);
  const underlay = { width: 360, height: 240, matrix: [10, 0, 0, 10, 0, -2400] };
  for (const backdrop of [null, underlay]) {
    assert.deepEqual(mapView(flush.viewBounds, backdrop).viewBox, [-102, -2502, 3804, 2604]);
    assert.deepEqual(mapView(mapInfo(base).viewBounds, backdrop).viewBox, [0, -2400, 3600, 2400]);
  }
  assert.equal(pointOnMap(-1, 0, flush.bounds), null);
});

test('panel uses state availability for commands and preserves preview on a read-only older controller', () => {
  const online = {online: true, allow_commands: true};
  const available = ['start', 'pause', 'resume', 'stop', 'dock'];
  const actions = data => available.filter(action => !jobBlock({...online, ...data}, action));
  const ready = {job_availability: {start: true, dock: true}};
  assert.deepEqual(actions(ready), ['start', 'dock']);
  assert.deepEqual(actions({...ready, dock_status: 'docked'}), ['start']);
  assert.deepEqual(actions({...ready, online: false}), []);
  assert.deepEqual(actions({...ready, allow_commands: false}), []);
  assert.deepEqual(actions({tracking_active: true, state: 'HEADING_TO_EXTRA_CLEANING', job_availability: {}}), []);
  assert.deepEqual(actions({state: 'PAUSED', job_availability: {stop: true, dock: true}}), ['stop', 'dock']);
  assert.deepEqual(actions({state: 'RETURNING', job_availability: {stop: true}}), ['stop']);
  assert.deepEqual(actions({state: 'IDLE'}), []);
  const readOnly = {...online, allow_commands: false};
  assert.equal(jobBlock(readOnly, 'start', true), '');
  assert.equal(jobBlock({...readOnly, ...ready}, 'start', true), '');
  assert.equal(jobBlock({...readOnly, job_availability: {start: false, start_reason: 'Промывка'}}, 'start', true), 'Промывка');
  assert.ok(jobBlock({...readOnly, online: false}, 'start', true));
});

test('room aliases supply labels without changing the ordered request or saved map', () => {
  const key = 'a'.repeat(64);
  const named = mapInfo(map, { 1: { key, alias: 'Моя кухня' }, 9: { key, alias: 'Чужая комната' } });
  assert.equal(named.rooms[1], 'Моя кухня');
  assert.equal(map.rooms[1], 'Кухня');
  assert.equal(named.rooms[9], undefined);
  const selection = { kind: 'rooms', rooms: new Set([3, 1]) };
  assert.deepEqual(jobRequest('start', named, selection, automatic), jobRequest('start', map, selection, automatic));
});

test('room contour retains a recess, a hole and a detached patch without row seams', () => {
  const rows = ['M1 1h3v1h-3z', 'M1 2h1v1h-1zM3 2h1v1h-1z', 'M1 3h3v1h-3z', 'M1 4h1v1h-1zM5 4h2v1h-2z'];
  const perimeter = ['M1 1H4', 'M2 2H3', 'M2 3H3', 'M2 4H4', 'M5 4H7', 'M1 5H2', 'M5 5H7',
    'M1 1V5', 'M4 1V4', 'M2 2V3', 'M3 2V3', 'M2 4V5', 'M5 4V5', 'M7 4V5'];
  assert.deepEqual(roomContour(rows.join('')).match(/M[^M]+/g).sort(), perimeter.sort());
  const polygon = 'M0 0h3v1h-3zM6 0L5 2H8Z';
  assert.equal(roomContour(polygon), polygon);
});

test('SVG coordinates preserve native X, reverse Y and clamp only after drag begins', () => {
  assert.deepEqual(pointOnMap(-290, -625, map.bounds), [-290, 625]);
  assert.deepEqual(pointOnMap(500, 100, map.bounds), [500, -100]);
  assert.equal(pointOnMap(-301, -625, map.bounds), null);
  assert.equal(pointOnMap(20, -651, map.bounds), null);
  assert.deepEqual(pointOnMap(-400, 101, map.bounds, true), [-300, -100]);
});

test('keyboard corners normalize in either order and validate bounds', () => {
  assert.deepEqual(zoneInBounds([200, 480, -230, -10], map.bounds), [-230, -10, 200, 480]);
  assert.equal(zoneInBounds([200, 48, 200, -10], map.bounds), null);
  assert.equal(zoneInBounds([-301, 48, 200, -10], map.bounds), null);
  assert.equal(zoneInBounds([NaN, 48, 200, -10], map.bounds), null);
});

test('dragging clamps the rectangle and each corner, keeping the opposite sides fixed', () => {
  const zone = [-230, -10, 200, 480];
  assert.deepEqual(dragZone(zone, null, [-1000, 1000], map.bounds), [-300, 160, 130, 650]);
  assert.deepEqual(dragZone(zone, 0, [-1000, 1000], map.bounds), [-300, 479, 200, 480]);
  assert.deepEqual(dragZone(zone, 1, [1000, 1000], map.bounds), [199, -10, 200, 650]);
  assert.deepEqual(dragZone(zone, 2, [1000, -1000], map.bounds), [-230, -100, 500, 480]);
  assert.deepEqual(dragZone(zone, 3, [-1000, -1000], map.bounds), [-230, -10, -229, -9]);
});

test('a zone job preserves all rectangles in order and rejects incomplete or oversized lists', () => {
  const settings = { suction: '0', water: '3', repeats: '2' };
  const first = [200, 480, -230, -10], second = [250, 45, 470, 400];
  const build = zones => jobRequest('start', map, { kind: 'zone', zones }, settings, 0);
  assert.deepEqual(build([first, second]), {
    action: 'start', map_id: map.id, revision: map.revision, kind: 'zone',
    zones: [[-230, -10, 200, 480], second], suction: 0, water: 3, repeats: 2,
  });
  assert.deepEqual(build([first]).zone, [-230, -10, 200, 480]);
  for (const zones of [[], [first, [true, -10, 200, 480]], [first, [NaN, -10, 200, 480]],
    [first, [0, -10, 200]], [first, [0, -10, 600, 480]], Array(maxZones + 1).fill(first), Array(2)]) {
    assert.equal(build(zones), null);
  }
  const floor = { gridSize: 50, runs: [[0, -200, 220]] };
  assert.equal(zoneProblem(build([first]).zone, floor), '');
  assert.match(zoneProblem(second, floor), /Нет доступного пола/);
  assert.match(zoneProblem(second, null), /Нет данных/);
  assert.match(zoneProblem([0, 0, 100, 200], floor), /Слишком маленькая/);
});

test('each side follows only its map axis and stops at the bounds and its opposite side', () => {
  const zone = [-230, -10, 200, 480];
  assert.deepEqual(dragZone(zone, 'x1', [-1000, 1000], map.bounds), [-300, -10, 200, 480]);
  assert.deepEqual(dragZone(zone, 'y1', [1000, -1000], map.bounds), [-230, -100, 200, 480]);
  assert.deepEqual(dragZone(zone, 'x2', [1000, -1000], map.bounds), [-230, -10, 500, 480]);
  assert.deepEqual(dragZone(zone, 'y2', [-1000, 1000], map.bounds), [-230, -10, 200, 650]);
  assert.deepEqual(dragZone(zone, 'x1', [1000, -1000], map.bounds), [199, -10, 200, 480]);
  assert.deepEqual(dragZone(zone, 'y1', [-1000, 1000], map.bounds), [-230, 479, 200, 480]);
  assert.deepEqual(dragZone(zone, 'x2', [-1000, 1000], map.bounds), [-230, -10, -229, 480]);
  assert.deepEqual(dragZone(zone, 'y2', [1000, -1000], map.bounds), [-230, -10, 200, -9]);
});

test('selection only sends applicable options, including zero suction', () => {
  assert.deepEqual(jobRequest('start', map, { kind: 'all' }, { suction: '0', water: '3', repeats: '3' }),
    { action: 'start', map_id: 7, revision: 'r2', kind: 'all' });
  assert.deepEqual(jobRequest('start', map, { kind: 'rooms', rooms: new Set([3, 1]) }, automatic),
    { action: 'start', map_id: 7, revision: 'r2', kind: 'rooms', rooms: [3, 1], repeats: 1 });
  assert.deepEqual(jobRequest('start', map, { kind: 'zone', zone: [200, 480, -230, -10] },
    { suction: '0', water: '3', repeats: '2' }),
  { action: 'start', map_id: 7, revision: 'r2', kind: 'zone', zone: [-230, -10, 200, 480], suction: 0, water: 3, repeats: 2 });
  assert.deepEqual(jobRequest('dock', null, {}, automatic), { action: 'dock' });
});

test('missing map or removed rooms cannot prepare a stale job', () => {
  const next = mapInfo({ ...map, revision: 'r3', rooms: { 2: 'Кухня', 3: 'Холл' } });
  const selected = { kind: 'rooms', rooms: new Set([1]) };
  assert.equal(jobRequest('start', next, selected, automatic), null);
  assert.equal(jobRequest('start', null, { kind: 'all' }, automatic), null);
  assert.equal(jobRequest('start', map, { kind: 'rooms', rooms: new Set() }, automatic), null);
  assert.equal(jobRequest('start', map, { kind: 'rooms', rooms: new Set([1]) }, { ...automatic, water: '0' }), null);
  assert.equal(jobRequest('start', map, { kind: 'rooms', rooms: new Set([1]) }, { ...automatic, repeats: '4' }), null);
  assert.equal(mapInfo({ ...map, bounds: [0, 5, 400, 4] }), null);
});

test('room requests keep insertion order and only the same order reuses the pending request', () => {
  const plan = { ...map, rooms: { 2: 'Холл', 5: 'Спальня', 6: 'Кухня' } };
  const selection = { kind: 'rooms', rooms: new Set([6, 2, 5]) };
  const job = () => jobRequest('start', plan, selection, automatic);
  const key = () => JSON.stringify(['./api/job', job()]);
  assert.deepEqual(job().rooms, [6, 2, 5]);
  const first = requestIdentity(key(), null, () => 'first');
  assert.equal(requestIdentity(key(), first, () => 'unexpected').request_id, 'first');
  selection.rooms.delete(2);
  assert.deepEqual(job().rooms, [6, 5]);
  selection.rooms.add(2);
  assert.deepEqual(job().rooms, [6, 5, 2]);
  assert.equal(requestIdentity(key(), first, () => 'second').request_id, 'second');
  selection.rooms.clear();
  assert.equal(job(), null);
});

test('CleanGenius rooms ignore manual settings and zones require manual mode', () => {
  const rooms = { kind: 'rooms', rooms: new Set([3, 1]) };
  const zone = { kind: 'zone', zone: [200, 480, -230, -10] };
  const settings = { suction: '0', water: '3', repeats: '3' };
  for (const mode of [1, 2]) {
    assert.deepEqual(jobRequest('start', map, rooms, settings, mode),
      { action: 'start', map_id: 7, revision: 'r2', kind: 'rooms', rooms: [3, 1], expected_cleangenius: mode, repeats: 1 });
    assert.deepEqual(jobRequest('start', map, { kind: 'all' }, settings, mode),
      { action: 'start', map_id: 7, revision: 'r2', kind: 'all' });
    assert.equal(jobRequest('start', map, zone, settings, mode), null);
  }
  assert.deepEqual(jobRequest('start', map, rooms, settings, 0), {
    action: 'start', map_id: 7, revision: 'r2', kind: 'rooms', rooms: [3, 1], expected_cleangenius: 0,
    suction: 0, water: 3, repeats: 3,
  });
  assert.deepEqual(jobRequest('start', map, zone, settings, 0), jobRequest('start', map, zone, settings));
});

test('overlay coordinates invert only Y and reject malformed points', () => {
  assert.deepEqual(mapOverlays([
    { kind: 'wall', points: [[-40, 12], [30, -50]] }, { kind: 'robot', points: [[-20, -10]] },
    { kind: 'path', points: [[0, 15], [NaN, 20]] }, { kind: 'no_mop', points: [[100, 90], [25, 64]] },
    { kind: 'script', points: [[0, 0]] },
  ]), [{ kind: 'wall', points: [[-40, -12], [30, 50]] }, { kind: 'robot', points: [[-20, 10]] }]);
});

test('dense live overlays preserve the robot and the end of separate recorded path sections', () => {
  const walls = Array.from({ length: 136 }, () => ({ kind: 'wall', points: [[1, 2], [20, 30]] }));
  const points = offset => Array.from({ length: 7000 }, (_, index) => [offset + index, 20]);
  const overlays = mapOverlays([...walls, { kind: 'path', points: points(0) }, { kind: 'path', points: points(12_000) },
    { kind: 'robot', points: [[19_000, 20]] }]);
  const paths = overlays.filter(item => item.kind === 'path');
  assert.equal(overlays.filter(item => item.kind === 'wall').length, 128);
  assert.deepEqual(paths.map(item => item.points.length), [5000, 7000]);
  assert.deepEqual(paths[0].points[0], [2000, -20]);
  assert.deepEqual(paths[1].points.at(-1), [18_999, -20]);
  assert.deepEqual(overlays.at(-1), { kind: 'robot', points: [[19_000, -20]] });
});

test('top-level live overlays override saved overlays, including explicit absence of live geometry', () => {
  const saved = [{ kind: 'robot', points: [[11, 12]] }];
  const fresh = [{ kind: 'path', points: [[2, 3], [4, 5]] }, { kind: 'robot', points: [[4, 5]] }];
  assert.deepEqual(mapOverlays(fresh, saved), [
    { kind: 'path', points: [[2, -3], [4, -5]] }, { kind: 'robot', points: [[4, -5]] },
  ]);
  assert.deepEqual(mapOverlays([], saved), []);
  assert.deepEqual(mapOverlays(undefined, saved), [{ kind: 'robot', points: [[11, -12]] }]);
});

test('coverage prefers Dreame cells and estimates only wet sections over a calibrated floor', () => {
  const plan = mapInfo({ ...map, units_per_metre: 1000, floor: { grid_size: 50, runs: [[-100, -50, 150]] } });
  const points = [[0, 0], [100, 100]];
  const paths = ['S', 'L', undefined, 'M', 'W'].map(mode => ({ kind: 'path', points, mode }));
  const rects = [[10, 20, 30, 40]];
  const frame = mapOverlays([{ kind: 'coverage', source: 'frame', rects }, ...paths]);
  assert.deepEqual(cleaningCoverage(frame, plan), { source: 'frame', rects: [[10, -60, 30, 40]] });
  assert.equal(rectanglePaths(cleaningCoverage(frame, plan).rects), 'M10 -60h30v40h-30z');
  const route = cleaningCoverage(mapOverlays(paths), plan);
  assert.equal(route.source, 'route');
  assert.equal(route.width, 250);
  assert.equal(route.floor, plan.floor);
  assert.deepEqual(route.sections.map(section => section.mode), ['M', 'W']);
  assert.equal(cleaningCoverage(mapOverlays(paths.slice(0, 3)), plan), null);
  assert.equal(cleaningCoverage(mapOverlays(paths), { ...plan, floor: null }), null);
  assert.equal(cleaningCoverage(mapOverlays(paths), { ...plan, unitsPerMetre: null }), null);
  assert.equal(cleaningCoverage(mapOverlays([{ kind: 'coverage', source: 'frame', rects: [[499, 10, 2, 50]] }]), plan), null);
  assert.equal(cleaningCoverage(mapOverlays([{ kind: 'coverage', source: 'frame', rects: [[0, 0, 0, 50]] }]), plan), null);
});

test('only the calibrated local raster endpoint with a nonsingular transform is accepted', () => {
  const details = { url: '/dreame/api/underlay', width: 800, height: 550, matrix: [1, .02, -.01, .98, -300, -650] };
  assert.deepEqual(underlayInfo(details), { width: 800, height: 550, matrix: details.matrix });
  assert.equal(underlayInfo({ ...details, url: 'https://example.com/floor' }), null);
  assert.equal(underlayInfo({ ...details, url: '//127.0.0.1/dreame/api/underlay' }), null);
  assert.equal(underlayInfo({ ...details, matrix: [1, 2, 2, 4, 0, 0] }), null);
  assert.equal(underlayInfo({ ...details, matrix: [1, 0, 0, 1, Infinity, 0] }), null);
  assert.equal(underlayInfo({ ...details, matrix: ['1', 0, 0, 1, 0, 0] }), null);
  assert.equal(underlayInfo({ ...details, width: 16000, height: 16000 }), null);
});

test('confirmed preferences retain their expected value and reject invalid updates', () => {
  const volume = { key: 'volume', label: 'Громкость', value: 40, min: 0, max: 100, unit: '%' };
  const [confirmed, other] = preferenceInfo([volume, volume, { ...volume, key: 'bad', value: '40' }]);
  assert.deepEqual(confirmed, volume);
  assert.equal(other, undefined);
  assert.deepEqual(preferenceRequest(confirmed, 0), { key: 'volume', expected: 40, value: 0 });
  assert.equal(preferenceRequest(confirmed, 40), null);
  assert.equal(preferenceRequest(confirmed, 101), null);
  assert.equal(preferenceRequest(confirmed, 1.5), null);
});

test('enumerated preferences only accept offered choices and include the confirmed value', () => {
  const choices = [{ value: 0, label: 'Выключен' }, { value: 2, label: 'Глубокая уборка' }];
  const mode = { key: 'cleangenius', label: 'CleanGenius', value: 2, min: 0, max: 2, choices };
  const [confirmed] = preferenceInfo([mode]);
  assert.deepEqual(confirmed.choices, choices);
  assert.deepEqual(preferenceRequest(confirmed, 0), { key: 'cleangenius', expected: 2, value: 0 });
  assert.equal(preferenceRequest(confirmed, 1), null);
  assert.equal(preferenceRequest(confirmed, 2), null);
  const [readonly] = preferenceInfo([{ ...mode, disabled_reason: 'Робот выполняет уборку' }]);
  assert.equal(readonly.disabled_reason, 'Робот выполняет уборку');
  assert.equal(preferenceRequest(readonly, 0), null);
  assert.deepEqual(preferenceInfo([{ ...mode, choices: [choices[0]] }]), []);
  assert.deepEqual(preferenceInfo([{ ...mode, choices: [...choices, choices[1]] }]), []);
  assert.deepEqual(preferenceInfo([{ ...mode, choices: [...choices, { value: 1, label: '' }] }]), []);
});

test('underlay rotation and scaling preserve the image center in SVG space', () => {
  const base = { width: 800, height: 550, matrix: [2, .3, .5, -1.8, 40, -90] };
  const svgPoint = ([a,b,c,d,e,f], x, y) => [a * x + c * y + e, b * x + d * y + f];
  const center = svgPoint(base.matrix, 400, 275);
  const result = adjustUnderlay(base, [30, 40, .8, 104]);
  const newCenter = svgPoint(result, 400, 275);
  assert.ok(Math.abs(newCenter[0] - center[0] - 30) < 1e-8);
  assert.ok(Math.abs(newCenter[1] - center[1] + 40) < 1e-8);
  const oldEdge = svgPoint(base.matrix, 0, 0);
  const newEdge = svgPoint(result, 0, 0);
  const u = oldEdge.map((value, i) => value - center[i]);
  const v = newEdge.map((value, i) => value - newCenter[i]);
  const angle = Math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]);
  assert.ok(Math.abs(angle - .8 * Math.PI / 180) < 1e-8);
  assert.ok(Math.abs(Math.hypot(newEdge[0] - newCenter[0], newEdge[1] - newCenter[1]) /
    Math.hypot(oldEdge[0] - center[0], oldEdge[1] - center[1]) - 1.04) < 1e-8);
  assert.deepEqual(adjustUnderlay(base, [30, 40, 0, 100]), [2, .3, .5, -1.8, 70, -130]);
  assert.deepEqual(adjustUnderlay(base, [0, 0, 0, 100]), base.matrix);
  assert.equal(adjustUnderlay(base, [0, 0, 0, NaN]), null);
});

test('pending request persists just its identity and reuses it after reloading', () => {
  const first = 'baab73ae-72ff-42d5-9758-cc4f3eba5a36';
  const next = '88f8309e-499b-4f09-8565-7cc1441503d0';
  const saved = requestIdentity('all form key', null, () => first);
  const loaded = pendingRequest(JSON.stringify({ ...saved, irrelevant: true }));
  assert.deepEqual(loaded, { key: 'all form key', request_id: first });
  assert.deepEqual(requestIdentity('all form key', loaded, () => next), saved);
  assert.deepEqual(requestIdentity('different form key', loaded, () => next), { key: 'different form key', request_id: next });
  assert.equal(pendingRequest('{"key":"all form key","request_id":"unsafe"}'), null);
});

test('zone measures partial grid runs, excludes gaps and preserves known zero', () => {
  const measured = mapInfo({ ...map, bounds: [0,0,200,200], units_per_metre: 100,
    floor: { grid_size: 50, runs: [[100,50,175], [50,100,200], [0,0,100], [50,0,50], [50,25,50]] },
    room_area_m2: { 1: 12.5, 3: 0, 9: 100 } });
  assert.deepEqual(measured.roomAreas, { 1: 12.5, 3: 0 });
  assert.deepEqual(zoneMeasures([25,40,175,140], measured.unitsPerMetre, measured.floor),
    { width: 1.5, length: 1, rectangle: 1.5, floor: 1.075 });
  assert.equal(zoneMeasures([0,150,100,190], 100, measured.floor).floor, 0);
  assert.equal(zoneMeasures([0,150,100,190], 100, null).floor, null);
  assert.equal(zoneMeasures([0,150,100,190], null, measured.floor), null);
});

test('forbidden polygons warn for area overlap, including a crossing without vertices inside', () => {
  const zone = [20,20,70,70];
  const crossing = [[-10,40],[100,40],[100,50],[-10,50]];
  const touching = [[0,30],[20,30],[20,60],[0,60]];
  assert.equal(zoneHitsNoGo(zone, mapOverlays([{ kind: 'no_go', points: crossing }])), true);
  assert.equal(zoneHitsNoGo(zone, mapOverlays([{ kind: 'no_go', points: touching }])), false);
  assert.equal(zoneHitsNoGo(zone, mapOverlays([{ kind: 'no_mop', points: crossing }])), false);
});

test('apartment view levels reflected calibration axes using a common rigid rotation', () => {
  const angle = 38 * Math.PI / 180;
  const calibration = { width: 800, height: 750, matrix: [-2*Math.cos(angle), -2*Math.sin(angle),
    -3*Math.sin(angle), 3*Math.cos(angle), -300, -650] };
  const result = mapView(map.bounds, calibration);
  assert.ok(Math.abs(result.angle + 38) < 1e-8);
  const screenAngle = result.angle * Math.PI / 180;
  const [a,b,c,d] = calibration.matrix;
  assert.ok(Math.abs(Math.sin(screenAngle)*a + Math.cos(screenAngle)*b) < 1e-8);
  assert.ok(Math.abs(Math.cos(screenAngle)*c - Math.sin(screenAngle)*d) < 1e-8);
  assert.deepEqual(mapView(map.bounds, null).viewBox, [-300,-650,800,750]);
  assert.equal(mapView(map.bounds, calibration, false).angle, 0);
});
