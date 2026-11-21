const strokes = {
  map: 'M3 5l6-2 6 3 6-2v15l-6 2-6-3-6 2z M9 3v15m6-12v15',
  history: 'M3 10a9 9 0 1 1 1 7 M3 4v6h6 M12 7v5l3 2',
  resources: 'M11 3h2l1 3 3 1 3-1 1 2-2 3v3l2 2-1 2-3-1-3 2-1 2h-2l-1-2-3-2-3 1-1-2 2-2v-3L3 8l1-2 3 1 3-1z M15 12a3 3 0 1 1-6 0 3 3 0 1 1 6 0',
  schedules: 'M4 6h16v15H4z M8 3v6m8-6v6M4 11h16m-12 4h2m4 0h2',
  settings: 'M3 7h5m4 0h9M3 17h10m4 0h4M8 4h4v6H8zm5 10h4v6h-4z',
  time: 'M21 12a9 9 0 1 1-18 0 9 9 0 1 1 18 0M12 6v6l4 2',
  area: 'M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6M7 17l10-10',
  check: 'M20 7l-10 10-5-5', stop: 'M6 6h12v12H6z', alert: 'M12 3l10 17H2z M12 9v4m0 3v.1',
  clean: 'M4 13c0-4 16-4 16 0v3c0 5-16 5-16 0z M9 10V8c0-2 6-2 6 0v2 M8 15h3',
  brush: 'M4 7h16v8H4z M7 15v4m3-4v4m4-4v4m3-4v4 M7 7l4 8m2-8 4 8',
  spin: 'M15 12a3 3 0 1 1-6 0 3 3 0 1 1 6 0 M12 9V3m3 10 6 4m-12-4-6 4',
  filter: 'M4 5h16v14H4z M7 8l2 8 3-8 3 8 2-8',
  drop: 'M12 2c-2 4-7 8-7 13a7 7 0 0 0 14 0c0-5-5-9-7-13z M8 15c0 2 1 3 3 3',
  tank: 'M6 5h12l1 16H5z M9 5V3h6v2 M6 13c3-2 6 2 12 0',
  bag: 'M4 8h16l-1 12H5z M8 8V5h8v3m-7 5h6',
  sensor: 'M4 12a8 8 0 0 1 16 0 M7 12a5 5 0 0 1 10 0 M10 12a2 2 0 0 1 4 0 M12 16v4',
  charge: 'M13 2 5 14h6l-1 8 9-13h-6z', dock: 'M5 5h14v16H5z M8 8h8m-9 8h10',
  robot: 'M3 13a9 6 0 0 1 18 0v2a9 6 0 0 1-18 0z M3 13a9 6 0 0 0 18 0 M10 10a2 1 0 1 0 4 0 2 1 0 1 0-4 0 M9 20l-3 2m9-2 3 2',
  dry: 'M4 8h12a2 2 0 1 0-2-2 M4 12h15a2 2 0 1 1-2 2 M4 16h7a2 2 0 1 1-2 2',
};
const ns = 'http://www.w3.org/2000/svg';
export function glyph(name) {
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('ui-glyph');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', strokes[name] || strokes.resources);
  svg.append(path);
  return svg;
}

export function initViews() {
  const tabs = [...document.querySelectorAll('.view-tabs [role=tab]')];
  const select = (tab, remember = true) => {
    for (const item of tabs) {
      const active = item === tab;
      const panel = document.getElementById(item.getAttribute('aria-controls'));
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
      panel.hidden = !active;
      if (active && panel.firstElementChild?.tagName === 'DETAILS') panel.firstElementChild.open = true;
    }
    if (remember) history.replaceState(null, '', '#' + tab.dataset.glyph);
  };
  const fromHash = () => select(tabs.find(item => item.dataset.glyph === location.hash.slice(1)) || tabs[0], false);
  tabs.forEach((tab, index) => {
    tab.prepend(glyph(tab.dataset.glyph));
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length,
        Home: 0, End: tabs.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault();
      select(tabs[next]);
      tabs[next].focus();
    });
  });
  window.addEventListener('hashchange', fromHash);
  fromHash();
}

const totalMetrics = {
  cleaning_count: ['Всего уборок', 'clean'], total_cleaned_area: ['Площадь уборок', 'area'], total_cleaning_time: ['Время уборок', 'time'],
};
const supplies = {
  main_brush_left: ['robot', 'Основная щётка', 'brush'], side_brush_left: ['robot', 'Боковая щётка', 'spin'],
  filter_left: ['robot', 'Фильтр пылесоса', 'filter'], mop_pad_left: ['robot', 'Салфетки', 'drop'],
  sensor_dirty_left: ['robot', 'До очистки датчиков', 'sensor'], tank_filter_left: ['dock', 'Фильтр бака', 'filter'],
  detergent_left: ['dock', 'Остаток моющего средства', 'drop'], silver_ion_left: ['dock', 'Модуль с ионами серебра', 'tank'],
  washboard_cleaning_left: ['dock', 'До очистки поддона', 'dock'], dirty_water_channel_dirty_left: ['dock', 'До очистки слива', 'drop'],
  scale_inhibitor_left: ['dock', 'Средство от накипи', 'drop'],
};
const statusIcons = { self_wash_base_status: 'dock', charging_status: 'charge', clean_water_tank_status: 'tank',
  dirty_water_tank_status: 'tank', dust_bag_status: 'bag', auto_empty_status: 'bag', detergent_status: 'drop',
  drying_progress: 'dry', dust_bag_drying_status: 'dry', station_drainage_status: 'drop' };
const primaryMetrics = { robot: 'charging_status', dock: 'self_wash_base_status' };
const photoBase = 'https://api.ru.dreametech.com/media/catalog/product/cache/74c1057f7991b4edb2bc7bdaa94de933/s/';
const l40sPhotos = { robot: 'xaAkaRJMGwWN5Bx/download/03.jpg?w=256', dock: 'EPRqR47CysdkPBc/download/05.jpg?w=256' };
const numbers = new Intl.NumberFormat('ru-RU');
function el(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined) node.textContent = value;
  return node;
}
function reading(row) {
  if (row.key === 'total_cleaning_time' && row.unit === 'мин' && Number.isInteger(row.value) && row.value >= 60) {
    const remainder = row.value % 60;
    return `${numbers.format(Math.floor(row.value / 60))}\u00a0ч${remainder ? ` ${numbers.format(remainder)}\u00a0мин` : ''}`;
  }
  return (typeof row.value === 'number' ? numbers.format(row.value) : row.value) + (row.unit ? `\u00a0${row.unit}` : '');
}

export function resourceWidget(host) {
  const totals = host.querySelector('#resource-totals');
  const empty = host.querySelector('#resource-empty');
  const credit = host.querySelector('#resource-photo-credit');
  const parts = Object.fromEntries(['robot', 'dock'].map(name => {
    const section = host.querySelector(`[data-resource-device=${name}]`);
    const photo = section.querySelector('.service-device-photo');
    photo.append(glyph(name));
    return [name, { photo, primary: section.querySelector('.service-primary'), status: section.querySelector('.service-status'),
      supplies: section.querySelector('.supply-grid'), supplyHeading: section.querySelector('.supply-heading'),
      empty: section.querySelector('.service-device-empty') }];
  }));
  let hasPhotos = false;
  return (metrics, model, online) => {
    const showPhotos = model === 'Dreame L40s Pro Ultra';
    if (hasPhotos !== showPhotos) {
      for (const [name, part] of Object.entries(parts)) {
        part.photo.querySelector('img')?.remove();
        if (!showPhotos) continue;
        const image = el('img');
        image.alt = '';
        image.loading = 'lazy';
        image.decoding = 'async';
        image.referrerPolicy = 'no-referrer';
        image.addEventListener('load', () => { image.dataset.loaded = ''; }, { once: true });
        image.addEventListener('error', () => image.remove(), { once: true });
        image.src = photoBase + l40sPhotos[name];
        part.photo.prepend(image);
      }
      hasPhotos = showPhotos;
      credit.hidden = !showPhotos;
    }
    totals.replaceChildren();
    for (const part of Object.values(parts)) {
      part.primary.replaceChildren();
      part.status.replaceChildren();
      part.supplies.replaceChildren();
    }
    const seen = new Set();
    for (const row of Array.isArray(metrics) ? metrics : []) {
      if (!row || typeof row.key !== 'string' || !row.key || typeof row.label !== 'string' || !row.label.trim()
        || !(Number.isFinite(row.value) && row.value >= 0 || typeof row.value === 'string' && row.value.trim())
        || seen.has(row.key)) continue;
      seen.add(row.key);
      const total = Object.hasOwn(totalMetrics, row.key) && totalMetrics[row.key];
      if (total) {
        const [title, icon] = total;
        const tile = el('div', 'service-total');
        const name = el('dt');
        name.append(glyph(icon), el('span', '', title));
        tile.append(name, el('dd', '', reading(row)));
        totals.append(tile);
        continue;
      }
      const supply = Object.hasOwn(supplies, row.key) && supplies[row.key];
      const device = supply ? supply[0] : row.key === primaryMetrics.robot ? 'robot' : 'dock';
      const part = parts[device];
      const item = el('div');
      const title = el('dt');
      const value = el('dd', '', reading(row));
      if (supply && row.unit === '%' && Number.isFinite(row.value) && row.value <= 100) {
        title.append(glyph(supply[2]), el('span', '', supply[1]));
        const meter = el('meter');
        meter.max = 100;
        meter.value = row.value;
        meter.setAttribute('aria-hidden', 'true');
        item.className = 'supply-item';
        item.dataset.empty = String(row.value === 0);
        value.append(meter);
        part.supplies.append(item);
      } else {
        const label = row.key === primaryMetrics.dock ? 'Режим станции' : row.label;
        title.append(glyph(statusIcons[row.key] || 'dock'), el('span', '', label));
        (row.key === primaryMetrics[device] ? part.primary : part.status).append(item);
      }
      item.append(title, value);
    }
    totals.hidden = !totals.children.length;
    empty.hidden = seen.size > 0;
    empty.textContent = online ? 'Робот пока не передал показания.' : 'Показания робота недоступны.';
    for (const part of Object.values(parts)) {
      part.supplyHeading.hidden = !part.supplies.children.length;
      part.empty.hidden = !seen.size || !!(part.primary.children.length || part.status.children.length || part.supplies.children.length);
    }
  };
}
