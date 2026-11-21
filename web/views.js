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
  cleaning_count: ['Уборок', 'clean'], total_cleaned_area: ['Убрано', 'area'], total_cleaning_time: ['В работе', 'time'],
};
const supplies = {
  main_brush_left: ['robot', 'Основная щётка', 'brush'], side_brush_left: ['robot', 'Боковая щётка', 'spin'],
  filter_left: ['robot', 'Фильтр пылесоса', 'filter'], mop_pad_left: ['robot', 'Салфетки', 'drop'],
  sensor_dirty_left: ['robot', 'До очистки датчиков', 'sensor'], tank_filter_left: ['dock', 'Фильтр бака', 'filter'],
  detergent_left: ['dock', 'Моющее средство', 'drop'], silver_ion_left: ['dock', 'Модуль с ионами серебра', 'tank'],
  washboard_cleaning_left: ['dock', 'До очистки поддона', 'dock'], dirty_water_channel_dirty_left: ['dock', 'До очистки слива', 'drop'],
  scale_inhibitor_left: ['dock', 'Средство от накипи', 'drop'],
};
const statusIcons = { charging_status: 'charge', clean_water_tank_status: 'tank', dirty_water_tank_status: 'tank',
  dust_bag_status: 'bag', auto_empty_status: 'bag', detergent_status: 'drop', drying_progress: 'time' };
const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
function el(tag, cls, value) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (value !== undefined) node.textContent = value;
  return node;
}
function reading(row) {
  if (row.key === 'total_cleaning_time' && Number.isInteger(row.value) && row.value >= 60) {
    const remainder = row.value % 60;
    return Math.floor(row.value / 60) + ' ч' + (remainder ? ' ' + remainder + ' мин' : '');
  }
  return (typeof row.value === 'number' ? numbers.format(row.value) : String(row.value)) + (row.unit ? ' ' + row.unit : '');
}

export function resourceWidget(host) {
  const totals = host.querySelector('#resource-totals');
  const parts = [...host.querySelectorAll('[data-part]')];
  let current = 'robot';
  let rows = [];
  const owner = row => supplies[row.key]?.[0] || (row.key === 'charging_status' ? 'robot' : 'dock');
  function draw() {
    const present = rows.filter(row => !Object.hasOwn(totalMetrics, row.key));
    const layout = host.querySelector('#resource-layout');
    layout.hidden = !present.length;
    if (!present.length) return;
    if (!present.some(row => owner(row) === current)) current = owner(present[0]);
    layout.dataset.part = current;
    for (const btn of parts) {
      btn.disabled = !present.some(row => owner(row) === btn.dataset.part);
      btn.setAttribute('aria-pressed', String(btn.dataset.part === current));
    }
    host.querySelector('#part-name').textContent = current === 'robot' ? 'Робот' : 'Док-станция';
    const cards = host.querySelector('#part-consumables'), status = host.querySelector('#part-status');
    cards.replaceChildren();
    status.replaceChildren();
    for (const row of present.filter(row => current === owner(row))) {
      const supply = supplies[row.key];
      if (supply && Number.isFinite(row.value) && row.unit === '%') {
        const card = el('div', 'supply-item');
        const title = el('div','supply-title'); title.append(glyph(supply[2]), el('span', '', supply[1]));
        const bar = el('progress'); bar.max = 100; bar.value = row.value; bar.setAttribute('aria-label', supply[1]);
        card.append(title, el('strong','',reading(row)), bar);
        cards.append(card);
      } else {
        const line = el('div'), title = el('dt');
        title.append(glyph(statusIcons[row.key] || 'dock'), el('span','',row.label));
        line.append(title, el('dd','',reading(row))); status.append(line);
      }
    }
  }
  parts.forEach(btn => btn.addEventListener('click', () => { current = btn.dataset.part; draw(); }));
  return metrics => {
    rows = Array.isArray(metrics) ? metrics.filter(r => r && typeof r.label === 'string' && r.value != null) : [];
    host.querySelector('#resource-empty').hidden = rows.length > 0;
    totals.replaceChildren();
    for (const row of rows.filter(r => Object.hasOwn(totalMetrics, r.key))) {
      const [title, icon] = totalMetrics[row.key], cell = el('div', 'service-total');
      cell.append(glyph(icon), el('span', '', title), el('strong', '', reading(row))); totals.append(cell);
    }
    totals.hidden = !totals.children.length;
    draw();
  };
}
