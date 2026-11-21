import { glyph } from './views.js';

const supplies = {
  main_brush_left: ['robot', 'Основная щётка', 'brush', 'Заменить'],
  side_brush_left: ['robot', 'Боковая щётка', 'spin', 'Заменить'],
  filter_left: ['robot', 'Фильтр пылесоса', 'filter', 'Заменить'],
  mop_pad_left: ['robot', 'Салфетки', 'drop', 'Заменить'],
  sensor_dirty_left: ['robot', 'Датчики', 'sensor', 'Очистить'],
  tank_filter_left: ['dock', 'Фильтр бака', 'filter', 'Проверить фильтр'],
  detergent_left: ['dock', 'Моющее средство', 'drop', 'Пополнить'],
  silver_ion_left: ['dock', 'Модуль с ионами серебра', 'tank', 'Заменить'],
  washboard_cleaning_left: ['dock', 'Поддон станции', 'dock', 'Очистить'],
  dirty_water_channel_dirty_left: ['dock', 'Слив грязной воды', 'drop', 'Очистить'],
  scale_inhibitor_left: ['dock', 'Картридж от накипи', 'filter', 'Заменить'],
  dust_bag_status: ['dock', 'Мешок для пыли', 'bag', 'Заменить'],
  clean_water_tank_status: ['dock', 'Бак чистой воды', 'tank'],
  dirty_water_tank_status: ['dock', 'Бак грязной воды', 'tank'],
};
const tankKeys = new Set(['clean_water_tank_status', 'dirty_water_tank_status']);
const statusActions = {
  dust_bag_status: { 'Не установлен': 'Установить мешок', 'Требует проверки': 'Проверить мешок' },
  detergent_left: { 'Мало средства': 'Пополнить' },
  clean_water_tank_status: { 'Не установлен': 'Установить бак', 'Мало воды': 'Долить воду' },
  dirty_water_tank_status: { 'Полон или не установлен': 'Проверить бак' },
};
const totals = {
  cleaning_count: ['Уборок', 'clean'], total_cleaned_area: ['Площадь', 'area'], total_cleaning_time: ['Время уборок', 'time'],
};
const rank = { due: 0, low: 1, normal: 2 };
const numbers = new Intl.NumberFormat('ru-RU');
const measure = (value, unit) => numbers.format(value) + (unit ? '\u00a0' + unit : '');
const nonnegative = value => Number.isFinite(value) && value >= 0;
const present = key => typeof key === 'string' && key.trim().length > 0;

function reading(row) {
  if (row.key === 'total_cleaning_time' && row.unit === 'мин' && Number.isInteger(row.value) && row.value >= 60) {
    const remaining = row.value % 60;
    return measure(Math.floor(row.value / 60), 'ч') + (remaining ? ' ' + measure(remaining, 'мин') : '');
  }
  return typeof row.value === 'number' ? measure(row.value, row.unit) : row.value;
}

function supplyReading(key, row, reportedStatus) {
  const [device, label, icon, service] = supplies[key];
  const percent = row.unit === '%' && nonnegative(row.value) && row.value <= 100 ? row.value : null;
  const days = nonnegative(row.days) ? row.days : row.unit === 'дн.' && nonnegative(row.value) ? row.value : null;
  const hours = nonnegative(row.hours) ? row.hours : row.unit === 'ч' && nonnegative(row.value) ? row.value : null;
  const estimate = days === null && nonnegative(row.estimated_days) ? row.estimated_days : null;
  const status = reportedStatus || (typeof row.value === 'string' ? row.value : '');
  const statusAction = Object.hasOwn(statusActions[key] || {}, status) ? statusActions[key][status] : '';
  const action = statusAction || (percent === 0 || days === 0 || hours === 0 ? service : '');
  const band = action ? 'due' : percent !== null && percent <= 15 ? 'low' : 'normal';
  let value;
  if (percent !== null) value = measure(percent, '%');
  else if (days !== null) value = measure(days, 'дн.');
  else if (hours !== null) value = measure(hours, 'ч работы');
  else value = reading(row);
  let duration;
  if (percent !== null && days !== null) duration = measure(days, 'дн.');
  else if (estimate !== null) duration = '≈ ' + measure(estimate, 'дн.');
  else if (percent !== null && hours !== null) duration = measure(hours, 'ч работы');
  const note = [];
  if (status && status !== value) note.push(status);
  if (hours !== null && (days !== null || estimate !== null) && percent !== null) note.push(measure(hours, 'ч работы'));
  return { key, device, label, icon, action, band, value, duration, note, percent, timed: days !== null || hours !== null, forecast: estimate !== null };
}

export function resourceReadings(metrics) {
  const rows = new Map();
  for (const row of Array.isArray(metrics) ? metrics : []) {
    if (!row || !present(row.key) || !present(row.label) || !(nonnegative(row.value) || present(row.value)) ||
        (row.unit === '%' && !(nonnegative(row.value) && row.value <= 100)) || rows.has(row.key)) continue;
    rows.set(row.key, row);
  }
  const items = [], statistics = [], equipment = { robot: { status: [] }, dock: { status: [] } };
  for (const [key, meta] of Object.entries(supplies)) {
    const row = rows.get(key) || (key === 'detergent_left' && rows.get('detergent_status'));
    if (!row) continue;
    const reported = key === 'detergent_left' ? rows.get('detergent_status')?.value : null;
    const item = supplyReading(key, row, typeof reported === 'string' ? reported : '');
    if (tankKeys.has(key) && !item.action) equipment[meta[0]].status.push(row);
    else items.push(item);
    rows.delete(key);
    if (key === 'detergent_left') rows.delete('detergent_status');
  }
  for (const [key, labels] of Object.entries(totals)) {
    if (rows.has(key)) statistics.push({ label: labels[0], icon: labels[1], value: reading(rows.get(key)) });
    rows.delete(key);
  }
  for (const row of rows.values()) {
    if (row.key === 'charging_status') equipment.robot.primary = reading(row);
    else if (row.key === 'self_wash_base_status') equipment.dock.primary = reading(row);
    else equipment.dock.status.push(row);
  }
  items.sort((a, b) => rank[a.band] - rank[b.band] || (a.percent ?? 101) - (b.percent ?? 101));
  return { items, statistics, equipment };
}

const modelName = 'Dreame L40s Pro Ultra';
const robotPhotos = 'https://api.ru.dreametech.com/media/catalog/product/cache/74c1057f7991b4edb2bc7bdaa94de933/s/';
const partPhotos = 'https://fr.dreametech.com/cdn/shop/files/';
const photographs = {
  robot: robotPhotos + 'xaAkaRJMGwWN5Bx/download/03.jpg?w=256',
  dock: robotPhotos + 'EPRqR47CysdkPBc/download/05.jpg?w=256',
  main_brush_left: partPhotos + 'RollerBrush-_02_1_160x.jpg?v=1736847097',
  side_brush_left: partPhotos + 'output_160x.jpg?v=1716261506',
  filter_left: partPhotos + 'output_7ecbea13-05d4-4e0e-8147-3d161e37029a_160x.jpg?v=1736847458',
  mop_pad_left: partPhotos + '1_d09c295f-0c12-4a23-9a99-50c7e22121d5_160x.jpg?v=1698127445',
  dust_bag_status: partPhotos + 'output_2_edff9d8a-7f55-4426-af76-7935a11acdda_160x.jpg?v=1729757344',
  detergent_left: partPhotos + '14_160x.png?v=1703064036',
  scale_inhibitor_left: partPhotos + 'Scale_inhibitor_160x.jpg?v=1762325107',
};
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function photoSlot(key, icon) {
  const slot = el('span', 'service-photo supply-photo');
  slot.setAttribute('aria-hidden', 'true');
  slot.dataset.photo = key;
  slot.append(glyph(icon));
  return slot;
}

function supplyRow(item) {
  const row = el('div', 'supply-row');
  row.dataset.resource = item.key;
  const title = el('dt', 'supply-name');
  const photo = photoSlot(item.key, item.icon);
  const name = el('span', 'supply-label');
  const meta = el('small', 'supply-context');
  if (item.action) meta.append(el('strong', 'supply-action', item.action), ' · ');
  meta.append([item.device === 'robot' ? 'Робот' : 'Станция', ...item.note].join(' · '));
  name.append(el('strong', '', item.label), meta);
  title.append(photo, name);
  const value = el('dd', 'supply-reading');
  const line = el('span', 'supply-numbers');
  line.append(el('strong', '', item.value));
  if (item.duration) line.append(el('small', '', item.duration));
  value.append(line);
  if (item.percent !== null) {
    const meter = el('meter');
    meter.max = 100;
    meter.value = item.percent;
    meter.setAttribute('aria-hidden', 'true');
    value.append(meter);
  } else if (!item.timed) {
    value.classList.add('supply-text');
  }
  row.append(title, value);
  return row;
}

function supplyGroup(band, items, hasPriority) {
  const section = el('section', 'service-group');
  section.dataset.band = band;
  const heading = el('h4');
  const [icon, label] = band === 'due' ? ['alert', 'Обслужить сейчас'] :
    band === 'low' ? ['time', 'Малый остаток'] : ['layers', hasPriority ? 'Остальные детали' : 'Детали и расходники'];
  heading.append(glyph(icon), el('span', '', label));
  if (band === 'low') heading.append(el('small', '', 'до 15 %'));
  const list = el('dl', 'supply-list');
  list.append(...items.map(supplyRow));
  section.append(heading, list);
  return section;
}

export function resourceWidget(host) {
  const panel = host.closest('[role=tabpanel]');
  const groups = host.querySelector('#service-groups');
  const stats = host.querySelector('#resource-totals');
  const empty = host.querySelector('#resource-empty');
  const credit = host.querySelector('#resource-photo-credit');
  const reference = host.querySelector('#service-reference');
  const devices = new Map();
  for (const node of host.querySelectorAll('[data-resource-device]')) {
    const key = node.dataset.resourceDevice;
    const photo = node.querySelector('.service-device-photo');
    photo.dataset.photo = key;
    photo.append(glyph(key));
    devices.set(key, node);
  }
  const images = new Map();
  let model, lastSnapshot;
  function attachPhotos() {
    if (panel.hidden || model !== modelName || credit.hidden) return;
    // A visit requests the same fixed set for this model, independently of the readings.
    for (const [key, src] of Object.entries(photographs)) {
      if (images.has(key)) continue;
      const img = el('img');
      img.alt = '';
      img.referrerPolicy = 'no-referrer';
      img.decoding = 'async';
      const failed = () => { images.set(key, null); img.remove(); };
      img.addEventListener('error', failed, { once: true });
      img.addEventListener('load', async () => {
        try { await img.decode(); img.dataset.loaded = ''; }
        catch { failed(); }
      }, { once: true });
      images.set(key, img);
      img.src = src;
    }
    for (const slot of host.querySelectorAll('[data-photo]')) {
      const img = images.get(slot.dataset.photo);
      if (img && img.parentElement !== slot) slot.prepend(img);
    }
  }
  document.addEventListener('dreame:view', event => { if (event.detail === 'resources') attachPhotos(); });
  window.addEventListener('online', () => {
    for (const [key, img] of images) if (!img) images.delete(key);
    attachPhotos();
  });
  return (metrics, nextModel, online) => {
    const snapshot = JSON.stringify([metrics, nextModel, online]);
    if (lastSnapshot === snapshot) return;
    lastSnapshot = snapshot;
    model = nextModel;
    const { items, statistics, equipment } = resourceReadings(online ? metrics : []);
    const hasData = !!(items.length || statistics.length || Object.values(equipment).some(e => e.primary || e.status.length));
    empty.hidden = hasData;
    empty.textContent = online ? 'Робот пока не передал показания.' : 'Показания робота недоступны.';
    const sections = [];
    const hasPriority = items.some(item => item.band !== 'normal');
    for (const band of Object.keys(rank)) {
      const contents = items.filter(item => item.band === band);
      if (contents.length) sections.push(supplyGroup(band, contents, hasPriority));
    }
    groups.replaceChildren(...sections);
    groups.closest('.service-card').hidden = !items.length;
    host.querySelector('#service-estimate').hidden = !items.some(item => item.forecast);
    for (const [key, node] of devices) {
      const { primary, status } = equipment[key];
      node.hidden = !(model === modelName || primary || status.length);
      node.querySelector('.service-primary').textContent = primary || (key === 'robot' ? 'Состояние не передано' : 'Режим не передан');
      const details = status.map(row => {
        const pair = el('div');
        pair.append(el('dt', '', row.label), el('dd', '', reading(row)));
        return pair;
      });
      node.querySelector('.service-status').replaceChildren(...details);
    }
    host.querySelector('.service-equipment').hidden = !hasData || [...devices.values()].every(node => node.hidden);
    stats.replaceChildren(...statistics.map(row => {
      const tile = el('div', 'service-total');
      const dt = el('dt');
      dt.append(glyph(row.icon), el('span', '', row.label));
      tile.append(dt, el('dd', '', row.value));
      return tile;
    }));
    stats.closest('.service-card').hidden = !statistics.length;
    const missing = model === modelName && hasData ? Object.keys(supplies).filter(key =>
      Object.hasOwn(photographs, key) && !items.some(item => item.key === key)) : [];
    reference.hidden = !missing.length;
    reference.querySelector('ul').replaceChildren(...missing.map(key => {
      const [, label, icon] = supplies[key];
      const item = el('li');
      item.append(photoSlot(key, icon), el('span', '', label));
      return item;
    }));
    credit.hidden = model !== modelName || !hasData;
    if (model !== modelName) host.querySelectorAll('[data-photo] img').forEach(img => img.remove());
    else attachPhotos();
  };
}
