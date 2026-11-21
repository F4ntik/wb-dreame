import assert from 'node:assert/strict';
import { test } from 'node:test';
import { zoneHandleLayout, zoneMeasurePlacement, zoneResizeCursor } from '../web/controls.js';

const rect = (left, top, right, bottom) => ({ left, top, right, bottom });
const frame = rect(100, 40, 500, 300);

test('центр и ручки не делят одно место на маленьком или узком прямоугольнике', () => {
  const handles = (width, height, touch) => {
    const corners = [{ x: 0, y: height }, { x: 0, y: 0 }, { x: width, y: height }, { x: width, y: 0 }];
    const { size, compact, fourCorners, sides } = zoneHandleLayout(corners, touch);
    return { size, compact, fourCorners, sides };
  };
  assert.deepEqual(handles(24, 18, true), { size: 40, compact: true, fourCorners: false, sides: false });
  assert.deepEqual(handles(24, 18, false), { size: 32, compact: true, fourCorners: false, sides: false });
  assert.deepEqual(handles(32, 24, false), { size: 19, compact: false, fourCorners: true, sides: false });
  assert.deepEqual(handles(14, 120, true), { size: 40, compact: false, fourCorners: false, sides: false });
  assert.deepEqual(handles(100, 160, true), { size: 40, compact: false, fourCorners: true, sides: true });
});

test('центр и середины граней совпадают с контуром у края повёрнутой карты', () => {
  const corners = [{ x: 0, y: 16 }, { x: 8, y: 0 }, { x: 20, y: 26 }, { x: 28, y: 10 }];
  for (const touch of [false, true]) {
    const { centre, edges } = zoneHandleLayout(corners, touch);
    assert.deepEqual(centre, { x: 14, y: 13 });
    assert.deepEqual(edges, [{ x: 4, y: 8 }, { x: 10, y: 21 }, { x: 24, y: 18 }, { x: 18, y: 5 }]);
  }
});

test('стрелки сторон и углов следуют повороту карты в обе стороны', () => {
  const cursors = degrees => [0, 90, 45, -45].map(offset => zoneResizeCursor((degrees + offset) * Math.PI / 180));
  assert.deepEqual(cursors(0), ['ew-resize', 'ns-resize', 'nwse-resize', 'nesw-resize']);
  assert.deepEqual(cursors(-27), ['nesw-resize', 'nwse-resize', 'ew-resize', 'ns-resize']);
  assert.deepEqual(cursors(27), ['nwse-resize', 'nesw-resize', 'ns-resize', 'ew-resize']);
});

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
