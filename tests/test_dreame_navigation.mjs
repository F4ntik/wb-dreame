import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nextTab } from '../web/views.js';

test('горизонтальные и вертикальные вкладки реагируют на стрелки своей оси', () => {
  assert.equal(nextTab('ArrowRight', 5, 6, false, 0), 0);
  assert.equal(nextTab('ArrowLeft', 0, 6, false, 0), 5);
  assert.equal(nextTab('ArrowUp', 2, 6, false, -1), undefined);
  assert.equal(nextTab('ArrowDown', 5, 6, true, 0), 0);
  assert.equal(nextTab('ArrowUp', 0, 6, true, 0), 5);
  assert.equal(nextTab('ArrowRight', 2, 6, true, 0), undefined);
  assert.equal(nextTab('Home', 4, 6, true, 0), 0);
  assert.equal(nextTab('End', 2, 6, false, 0), 5);
});

test('на телефоне стрелки вверх и вниз сохраняют колонку, на внешнем краю прокручивают страницу', () => {
  assert.equal(nextTab('ArrowDown', 1, 6, false, 3), 4);
  assert.equal(nextTab('ArrowUp', 5, 6, false, 3), 2);
  assert.equal(nextTab('ArrowDown', 5, 6, false, 3), undefined);
  assert.equal(nextTab('ArrowUp', 1, 6, false, 3), undefined);
});
