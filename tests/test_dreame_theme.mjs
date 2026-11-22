import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { runInNewContext } from 'node:vm';

const script = readFileSync(new URL('../web/theme.js', import.meta.url), 'utf8');

test('тема WB важнее системной, смена темы снимает прежние цвета, отдельно действует выбор системы', () => {
  const values = new Map();
  const panel = { dataset: {}, style: {
    setProperty: (k, v) => values.set(k, v), removeProperty: k => values.delete(k),
  } };
  let refreshHost, refreshSystem;
  let primary = '#5cb300';
  const view = { getComputedStyle: () => ({
    getPropertyValue: name => name === '--primary-color' ? primary : '',
  }) };
  const host = { dataset: { theme: 'light' }, ownerDocument: { defaultView: view } };
  const media = { matches: true, addEventListener: (_, fn) => { refreshSystem = fn; } };
  let preference = 'bootstrap', storageChanged;
  const window = { frameElement: { ownerDocument: { documentElement: host } }, matchMedia: () => media,
    localStorage: { getItem: () => preference }, addEventListener: (_, fn) => { storageChanged = fn; } };
  class MutationObserver {
    constructor(fn) { refreshHost = fn; }
    observe(target, options) {
      assert.equal(target, host);
      assert.ok(options.attributeFilter.includes('data-theme'));
    }
  }
  const context = { window, document: { documentElement: panel }, MutationObserver, CSS: { supports: () => true } };
  runInNewContext(script, context);
  assert.equal(panel.dataset.theme, 'light');
  assert.equal(values.get('--primary-color'), '#5cb300');
  host.dataset.theme = 'bootstrap';
  primary = '#337ab7';
  refreshHost();
  refreshSystem();
  assert.equal(panel.dataset.theme, 'bootstrap');
  assert.equal(values.get('--primary-color'), '#337ab7');
  primary = '';
  host.dataset.theme = 'dark';
  refreshHost();
  assert.equal(panel.dataset.theme, 'dark');
  assert.equal(values.size, 0);
  window.frameElement = null;
  runInNewContext(script, context);
  assert.equal(panel.dataset.theme, 'bootstrap');
  preference = 'system';
  storageChanged({ key: 'theme' });
  assert.equal(panel.dataset.theme, 'dark');
  media.matches = false;
  refreshSystem();
  assert.equal(panel.dataset.theme, 'light');
});
