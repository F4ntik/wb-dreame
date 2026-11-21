import assert from 'node:assert/strict';
import { test } from 'node:test';
import { archiveFrame, archiveMapSource, archiveObstacles, archiveRoomStatuses, archiveRoute, historyRows, historyTotals } from '../web/history.js';

test('archive room legend preserves absent evidence and ignores invalid status rows', () => {
  assert.deepEqual(archiveRoomStatuses([{ id: 1, status: 'unknown' }]), []);
  assert.deepEqual(archiveRoomStatuses([
    { id: 1, status: 'skipped', name: 'Кухня' },
    { id: 2, status: 'unknown' },
    { id: 2, status: 'completed' },
    { id: 3, status: 'constructor' },
    { id: 99, status: 'completed' },
  ]), [{ name: 'Кухня', status: 'skipped' }, { name: 'Комната 2', status: 'unknown' }]);
});

test('history admits up to 30 distinct records and totals only confirmed measurements', () => {
  const rows = historyRows([
    { timestamp: 120, duration_min: 0, area_m2: null, status: 'Завершена', map_available: true, task_label: 'Вся карта' },
    { timestamp: 121, area_m2: 9.2, status: 'Прервана', map_available: false },
    { timestamp: 122, duration_min: 12, area_m2: 18, status: 'Завершена', map_available: 'yes' },
    { timestamp: 120, duration_min: 888, area_m2: 888 }, { timestamp: '../../api/state' },
  ]);
  assert.deepEqual(rows.map(record => record.timestamp), [122, 121, 120]);
  assert.deepEqual(rows.map(record => record.mapAvailable), [false, false, true]);
  assert.deepEqual(rows.at(-1).details, ['Вся карта', '', '']);
  assert.deepEqual(historyTotals(rows), { minutes: 12, area: 27.2, durationCount: 2, areaCount: 2 });
  assert.deepEqual(historyTotals(rows.filter(record => record.status === 'Завершена')),
    { minutes: 12, area: 18, durationCount: 2, areaCount: 1 });
  assert.equal(historyRows(Array.from({ length: 35 }, (_, index) => ({ timestamp: index + 1 }))).length, 30);
});

test('archive SVG is an image URL confined to the requested historical timestamp', () => {
  const svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>Карта &amp; детали</text></svg>';
  const source = archiveMapSource({ timestamp: 120, svg }, 120);
  assert.ok(source.startsWith('data:image/svg+xml;charset=utf-8,'));
  assert.ok(!source.includes('<'));
  assert.equal(decodeURIComponent(source.split(',')[1]), svg);
  assert.equal(archiveMapSource({ timestamp: 121, svg }, 120), null);
  assert.equal(archiveMapSource({ timestamp: 120, svg: '<!DOCTYPE svg> ' + svg }, 120), null);
  assert.equal(archiveMapSource({ timestamp: 120, svg: '<html>...' }, 120), null);
});

test('obstacle markers preserve real in-bounds coordinates and safe local photo keys only', () => {
  assert.deepEqual(archiveObstacles([
    { id: 'wire_1', kind: 'wire', label: 'Провод', position: [25, 90], photo_available: true },
    { id: 'sock', kind: 'sock', position: [123, 50], photo_available: false },
    { id: 'wire_1', label: 'Дубликат', photo_available: false }, { id: '../state', position: [5, 15] },
    { id: 17, kind: 'some_vendor_kind', label: 'Игрушка', position: [0, 100], photo_available: true },
  ]), [
    { id: 'wire_1', kind: 'wire', label: 'Провод', position: [25, 90], photoAvailable: true },
    { id: 'sock', kind: 'sock', label: 'Носок', position: null, photoAvailable: false },
    { id: '17', kind: 'other', label: 'Игрушка', position: [0, 100], photoAvailable: true },
  ]);
});

test('archive replay keeps recorded point order and does not approximate gaps', () => {
  assert.deepEqual(archiveRoute([
    { mode: 'S', points: [[10, 20], [11, 25], [NaN, 73]] },
    { mode: 'W', points: [[80, 78], [77, 79]] },
    { mode: 'bogus', points: [[20, 99]] },
    { mode: 'L', points: [[25, 101], [0, 100]] },
  ]), [
    { mode: 'S', position: [10, 20] }, { mode: 'S', position: [11, 25] },
    { mode: 'W', position: [80, 78] }, { mode: 'W', position: [77, 79] },
    { mode: 'L', position: [0, 100] },
  ]);
});

test('archive fits both dimensions before applying relative zoom', () => {
  const [width, height] = archiveFrame(590, 370, 125, 196, 100);
  assert.ok(Math.abs(height - 370) < 1e-8);
  assert.ok(Math.abs(width / height - 125 / 196) < 1e-8);
  assert.deepEqual(archiveFrame(590, 370, 125, 196, 150), [width * 1.5, 555]);
  assert.deepEqual(archiveFrame(250, 390, 500, 200, 100), [250, 100]);
  assert.deepEqual(archiveFrame(250, 390, 500, 200, 200), [500, 200]);
  assert.equal(archiveFrame(0, 390, 500, 200, 100), null);
});
