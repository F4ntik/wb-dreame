import assert from 'node:assert/strict';
import { test } from 'node:test';
import { zoneMeasurePlacement } from '../web/controls.js';

const rect = (left, top, right, bottom) => ({ left, top, right, bottom });
const frame = rect(100, 40, 500, 300);

test('размеры над нижней зоной и под верхней оставляют место для сенсорных ручек', () => {
  assert.deepEqual(zoneMeasurePlacement(frame, rect(140, 42, 180, 65), [rect(120, 26, 200, 85)], 120, 45, []), [106, 91]);
  assert.deepEqual(zoneMeasurePlacement(frame, rect(430, 260, 470, 295), [rect(410, 240, 490, 315)], 120, 45, []), [374, 189]);
});

test('высокая зона оставляет подпись сбоку, соседние зоны и кнопка удаления занимают место', () => {
  const zone = rect(225, 40, 260, 300), remove = rect(280, 140, 320, 180);
  assert.deepEqual(zoneMeasurePlacement(frame, zone, [remove], 80, 40, []), [326, 150]);
  assert.deepEqual(zoneMeasurePlacement(frame, zone, [remove], 80, 40, [rect(344, 145, 366, 180)]), [139, 150]);
  assert.equal(zoneMeasurePlacement(frame, frame, [remove], 80, 40, []), null);
  assert.equal(zoneMeasurePlacement(frame, zone, [remove], 410, 40, []), null);
});
