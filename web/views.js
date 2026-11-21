const strokes = {
  device: 'M21 12a9 9 0 1 1-18 0 9 9 0 1 1 18 0 M14.2 8.5a2.2 2.2 0 1 1-4.4 0 2.2 2.2 0 1 1 4.4 0 M8 16.8c2.3 1.2 5.7 1.2 8 0',
  scenarios: 'M5 4h13a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z M7 20h11 M10 8l4 2.5-4 2.5z',
  entrance: 'M4 20h16 M6 20V4h12v16 M9 19V7l6-1v13 M13 12h.1',
  layers: 'M3 8l9-5 9 5-9 5z M3 12l9 5 9-5 M3 16l9 5 9-5',
  refresh: 'M20 10a8 8 0 0 0-14-4L3 9m0-5v5h5 M4 14a8 8 0 0 0 14 4l3-3m0 5v-5h-5',
  edit: 'M14 5l5 5 M4 20l4-1 12-12a2 2 0 0 0-4-4L4 15z',
  forward: 'M9 5l7 7-7 7',
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

export function confirmMessage(title, message, accept = 'Подтвердить', destructive = false) {
  const modal = document.getElementById('confirmation');
  if (modal.open) return Promise.resolve(false);
  document.getElementById('confirmation-title').textContent = title;
  document.getElementById('confirmation-message').textContent = message;
  document.getElementById('confirmation-accept').textContent = accept;
  modal.dataset.destructive = String(destructive);
  modal.returnValue = 'cancel';
  return new Promise(resolve => {
    modal.addEventListener('close', () => resolve(modal.returnValue === 'send'), { once: true });
    modal.showModal();
  });
}

export function panelNavigation(shell) {
  const tablist = shell.querySelector('[role=tablist]');
  const embedded = window.self !== window.top;
  const fitsRail = matchMedia('(min-width: 1000px)');
  const wide = matchMedia('(min-width: 1160px)');
  let preference = 'auto';
  const apply = () => {
    const left = preference === 'left' ? fitsRail.matches : preference === 'auto' && !embedded && wide.matches;
    shell.dataset.navigation = left ? 'left' : 'top';
    tablist.setAttribute('aria-orientation', left ? 'vertical' : 'horizontal');
  };
  fitsRail.addEventListener('change', apply);
  wide.addEventListener('change', apply);
  apply();
  return value => {
    if (value === undefined) return;
    const next = value === 'left' || value === 'top' ? value : 'auto';
    if (preference !== next) { preference = next; apply(); }
  };
}

export function nextTab(key, index, count, vertical, columns) {
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  if (key === (vertical ? 'ArrowDown' : 'ArrowRight')) return (index + 1) % count;
  if (key === (vertical ? 'ArrowUp' : 'ArrowLeft')) return (index + count - 1) % count;
  if (!vertical && columns > 0) {
    if (key === 'ArrowDown' && index + columns < count) return index + columns;
    if (key === 'ArrowUp' && index >= columns) return index - columns;
  }
}

export function initViews() {
  for (const node of document.querySelectorAll('[data-icon]')) node.append(glyph(node.dataset.icon));
  const tabs = [...document.querySelectorAll('.view-tabs [role=tab]')];
  const select = (tab, remember = true) => {
    for (const item of tabs) {
      const active = item === tab;
      const panel = document.getElementById(item.getAttribute('aria-controls'));
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
      panel.hidden = !active;
    }
    if (remember) history.replaceState(null, '', '#' + tab.dataset.glyph);
    document.dispatchEvent(new CustomEvent('dreame:view', { detail: tab.dataset.glyph }));
  };
  const fromHash = () => select(tabs.find(item => item.dataset.glyph === location.hash.slice(1)) || tabs[0], false);
  tabs.forEach((tab, index) => {
    tab.prepend(glyph(tab.dataset.glyph));
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', event => {
      const vertical = tab.parentElement.getAttribute('aria-orientation') === 'vertical';
      const checkRows = !vertical && (event.key === 'ArrowUp' || event.key === 'ArrowDown');
      const columns = checkRows ? tabs.findIndex(item => item.offsetTop > tabs[0].offsetTop) : 0;
      const next = nextTab(event.key, index, tabs.length, vertical, columns);
      if (next === undefined) return;
      event.preventDefault();
      select(tabs[next]);
      tabs[next].focus();
    });
  });
  window.addEventListener('hashchange', fromHash);
  fromHash();
}
