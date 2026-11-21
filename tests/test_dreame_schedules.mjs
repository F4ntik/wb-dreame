import assert from 'node:assert/strict';
import { test } from 'node:test';
import { copyConflicts, recurrence } from '../web/schedules-data.js';

test('Russian recurrence follows calendar order and respects one-time jobs', () => {
  assert.equal(recurrence({ weekdays: '1100001' }), 'Пн, Сб, Вс');
  assert.equal(recurrence({ weekdays: '1111111' }), 'Каждый день');
  assert.equal(recurrence({ once: true, weekdays: '0000000' }), 'Один раз');
  assert.equal(recurrence({ weekdays: null }), 'Дни уточните в Dreamehome');
});

test('copy blocks overlapping days and conservatively handles unknown recurrence at the same time', () => {
  const monday = { time: '08:00', weekdays: '0100000' };
  const sunday = { ...monday, weekdays: '1000000' };
  assert.equal(copyConflicts([monday], monday), true);
  assert.equal(copyConflicts([sunday], monday), false);
  assert.equal(copyConflicts([{ time: '08:00', weekdays: null }], monday), true);
  assert.equal(copyConflicts([{ time: '08:00', weekdays: '0000000' }], monday), true);
  assert.equal(copyConflicts([{ ...sunday, once: true }], monday), true);
  assert.equal(copyConflicts([{ ...monday, time: '08:01' }], monday), false);
});
