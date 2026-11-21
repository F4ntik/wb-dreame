import { glyph } from './views.js';
const day = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' });
const recordDay = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
const recordTime = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const PAGE_SIZE = 6;
const numbers = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const recordPlural = new Intl.PluralRules('ru-RU');
const forms = { one: 'запись', few: 'записи', many: 'записей', other: 'записей' };
const replayModes = { S: 'Подметание', M: 'Мытьё', W: 'Подметание и мытьё', L: 'Точка маршрута' };
const obstacleIcons = {
  wire: ['Провод', 'M4 5v3a4 4 0 0 0 4 4h6a4 4 0 1 1 0 8h-2 M2 5h4 M2 2v3 M6 2v3'],
  shoes: ['Обувь', 'M3 10v5l3 3h13a2 2 0 0 0 0-4h-4l-4-5H8v3H5z M8 16h12'],
  sock: ['Носок', 'M10 3h8v11l-5 6a4 4 0 0 1-5-1l-2-2 4-5z M10 7h8'],
  toy: ['Игрушка', 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z M3 12h18 M12 3c-6 5-6 13 0 18 M12 3c6 5 6 13 0 18'],
  fabric: ['Ткань', 'M3 6h12l6 6v6H3z M15 6v6h6 M7 15h10'],
  leg: ['Ножка мебели', 'M3 6h18v3H3z M6 9v11 M18 9v11 M6 13h12'],
  food_bowl: ['Миска', 'M4 11h16l-2 7H6z M3 18h18 M10 7h4'],
  other: ['Препятствие', 'M12 3l9 16H3z M12 9v4 M12 16h.01'],
};

function text(value, limit = 150) {
  return typeof value === 'string' ? value.trim().slice(0, limit) : '';
}

function measure(value) {
  return Number.isFinite(value) && value >= 0 ? value : null;
}

export function historyRows(value) {
  if (!Array.isArray(value)) return [];
  const unique = new Map();
  for (const item of value.slice(0, 60)) {
    const timestamp = item?.timestamp;
    if (!Number.isSafeInteger(timestamp) || timestamp <= 0 || timestamp > 8_640_000_000_000 || unique.has(timestamp)) continue;
    unique.set(timestamp, { timestamp, status: text(item.status), duration: measure(item.duration_min), area: measure(item.area_m2),
      mapAvailable: item.map_available === true,
      details: [text(item.task_label), text(item.method_label), text(item.suction_label)] });
  }
  return [...unique.values()].sort((a, b) => b.timestamp - a.timestamp).slice(0, 30);
}

export function historyTotals(rows) {
  let minutes = 0, area = 0, durationCount = 0, areaCount = 0;
  for (const row of rows) {
    if (row.duration !== null) { minutes += row.duration; durationCount++; }
    if (row.area !== null) { area += row.area; areaCount++; }
  }
  return { minutes, area, durationCount, areaCount };
}

export function archiveMapSource(payload, timestamp, hideCoverage = false) {
  const svg = payload?.svg;
  if (payload?.timestamp !== timestamp || typeof svg !== 'string' || svg.length > 2_000_000 ||
      !/^\s*(?:<\?xml\b[^?]*\?>\s*)?<svg(?:\s|>)/.test(svg) || /<!\s*(?:doctype|entity)\b/i.test(svg)) return null;
  try {
    let source = svg;
    if (hideCoverage) {
      const root = new DOMParser().parseFromString(svg, 'image/svg+xml').documentElement;
      const coverage = root.querySelector('g#cleaning-coverage');
      if (root.localName !== 'svg' || !coverage) return null;
      coverage.remove();
      source = new XMLSerializer().serializeToString(root);
    }
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
  }
  catch { return null; }
}

export function archiveFrame(width, height, imageWidth, imageHeight, zoom) {
  if (Math.min(width, height, imageWidth, imageHeight) <= 0) return null;
  const scale = Math.min(width / imageWidth, height / imageHeight) * zoom / 100;
  return [imageWidth * scale, imageHeight * scale];
}

function percentPoint(p) {
  return Array.isArray(p) && p.length === 2 && p.every(n => Number.isFinite(n) && n >= 0 && n <= 100) ? p : null;
}

export function archiveObstacles(list) {
  if (!Array.isArray(list)) return [];
  const seen = new Set();
  return list.slice(0, 120).flatMap(item => {
    const id = typeof item?.id === 'string' || Number.isSafeInteger(item?.id) ? String(item.id) : '';
    if (!/^[a-z0-9][a-z0-9_.-]{0,79}$/i.test(id) || seen.has(id)) return [];
    seen.add(id);
    const kind = Object.hasOwn(obstacleIcons, item.kind) ? item.kind : 'other';
    return [{ id, kind, label: text(item.label) || obstacleIcons[kind][0], position: percentPoint(item.position),
      photoAvailable: item.photo_available === true }];
  });
}

export function archiveRoute(sections) {
  if (!Array.isArray(sections)) return [];
  const frames = [];
  for (const section of sections.slice(0, 1024)) {
    if (!section || !Object.hasOwn(replayModes, section.mode) || !Array.isArray(section.points)) continue;
    for (const point of section.points.slice(0, 20000)) {
      const position = percentPoint(point);
      if (position) frames.push({ position, mode: section.mode });
      if (frames.length === 20000) return frames;
    }
  }
  return frames;
}

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value !== undefined) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function obstacleIcon(kind) {
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  const path = document.createElementNS(ns, 'path');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  path.setAttribute('d', obstacleIcons[kind][1]);
  svg.append(path);
  return svg;
}

function metric(value, unit) {
  return value === null ? '—' : `${numbers.format(value)}${unit}`;
}

async function responseMessage(response, fallback) {
  const body = await response.json().catch(() => null);
  return text(body?.message, 420) || fallback;
}

function requestOptions(controller) {
  return { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(20000)]), cache: 'no-store' };
}

function photoSource(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

export function historyView(coverageSwitches) {
  const $ = id => document.getElementById(id);
  const dialog = $('history-dialog');
  const close = $('history-close');
  const filter = $('history-filter');
  const list = $('history-list');
  const viewport = $('history-map');
  const mapLayer = $('history-map-layer');
  const mapMessage = $('history-map-message');
  const retry = $('history-map-retry');
  const previous = $('history-prev');
  const next = $('history-next');
  const zoomIn = $('history-zoom-in');
  const zoomOut = $('history-zoom-out');
  const obstacleList = $('history-obstacle-list');
  const pinSwitch = $('history-show-pins');
  const coverageSwitch = $('history-coverage-switch');
  pinSwitch.addEventListener('change', () => {
    const pins = mapLayer.querySelector('.history-pins');
    if (pins) pins.hidden = !pinSwitch.checked;
  });
  const photo = $('history-photo');
  const replay = $('history-replay');
  const play = $('history-play');
  const scrub = $('history-scrub');
  const replayNote = $('history-replay-note');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let records = [];
  let shown = [];
  let page = 0;
  let available = false;
  let signature = '';
  let active = null;
  let opener = null;
  let request = null;
  let photoRequest = null;
  let zoom = 100;
  let frames = [];
  let frame = 0;
  let timer = null;
  let routeMarker = null;
  let archiveImage = null;

  function applyArchiveCoverage() {
    if (!archiveImage) return;
    const { image, full, plain, source, hasPath } = archiveImage;
    const showCoverage = source && (!plain || coverageSwitch.checked);
    const imageSource = showCoverage || !plain ? full : plain;
    if (image.src !== imageSource) image.src = imageSource;
    const layers = [hasPath ? 'Маршрут' : 'Сохранённый план'];
    if (showCoverage) {
      layers.push(source === 'route' ? 'Убранный пол по влажным проходам'
        : 'Покрытие по данным Dreame. Зелёный — обработано, оранжевый — отмечено как грязное');
    } else if (!source) layers.push('Нет данных о покрытии');
    $('history-map-layers').textContent = layers.join(' · ');
  }

  for (const input of coverageSwitches) input.addEventListener('change', applyArchiveCoverage);

  function updateNavigation() {
    if (!dialog.open) return;
    const index = shown.findIndex(row => row.timestamp === active?.timestamp);
    previous.disabled = index < 0 || index === shown.length - 1;
    next.disabled = index <= 0;
    $('history-nav-count').textContent = index < 0 ? '' : `${index + 1} из ${shown.length}`;
    if ([previous, next].some(button => button === document.activeElement && button.disabled)) close.focus();
  }

  function renderList() {
    const buttonFocused = list.contains(document.activeElement) ? document.activeElement.dataset.timestamp : null;
    shown = records.filter(row => filter.value === 'all' || row.status === filter.value);
    $('history-toolbar').hidden = !records.length;
    const count = Math.ceil(shown.length / PAGE_SIZE);
    page = Math.max(0, Math.min(page, count - 1));
    $('history-pages').hidden = count < 2;
    $('journal-page').textContent = count ? `${page + 1} из ${count}` : '';
    $('journal-newer').disabled = page === 0;
    $('journal-older').disabled = page + 1 >= count;
    const totals = historyTotals(shown);
    const total = [`${shown.length} ${forms[recordPlural.select(shown.length)]}`];
    if (totals.durationCount) total.push(metric(totals.minutes, ' мин') + (totals.durationCount < shown.length ? ` по ${totals.durationCount} зап.` : ''));
    if (totals.areaCount) total.push(metric(totals.area, ' м²') + (totals.areaCount < shown.length ? ` по ${totals.areaCount} зап.` : ''));
    $('history-total').textContent = total.join(' · ');
    if (!shown.length) {
      const message = records.length ? 'Записей с таким результатом нет.' : available ? 'Записей об уборках пока нет.' : 'История пока недоступна.';
      list.replaceChildren(element('p', message, 'muted'));
    } else {
      const fragment = document.createDocumentFragment();
      let dateGroup;
      let group;
      for (const row of shown.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)) {
        const date = new Date(row.timestamp * 1000);
        const dateLabel = recordDay.format(date);
        if (dateGroup !== dateLabel) {
          dateGroup = dateLabel;
          group = element('ul', undefined, 'journal-group');
          fragment.append(element('h3', dateLabel, 'journal-date'), group);
        }
        const button = element('button', undefined, 'history-link journal-row');
        button.type = 'button';
        button.dataset.timestamp = row.timestamp;
        button.setAttribute('aria-label', 'Открыть уборку за ' + day.format(date));
        const taskIcon = element('span', undefined, 'journal-mark');
        taskIcon.append(glyph(row.details[0] === 'По зоне' ? 'area' : row.details[0] === 'По комнатам' ? 'map' : 'clean'));
        const title = element('span', undefined, 'journal-title');
        title.append(element('strong', recordTime.format(date)), element('small', row.details[0] || row.details[1] || 'Уборка'));
        const measures = element('span', undefined, 'journal-measures');
        for (const [icon, value, unit] of [['area', row.area, ' м²'], ['time', row.duration, ' мин']]) {
          if (value === null) continue;
          const item = element('span'); item.append(glyph(icon), document.createTextNode(metric(value, unit))); measures.append(item);
        }
        const result = element('span', row.status || 'Без результата', 'journal-result');
        if (row.status === 'Завершена') result.dataset.complete = 'true';
        const arrow = element('span', '›', 'journal-arrow'); arrow.setAttribute('aria-hidden', 'true');
        button.append(taskIcon, title, measures, result, arrow);
        button.addEventListener('click', () => {
          opener = button;
          dialog.showModal();
          showRecord(row);
        });
        const li = element('li'); li.append(button); group.append(li);
      }
      list.replaceChildren(fragment);
    }
    if (buttonFocused) restoreFocus(buttonFocused);
    updateNavigation();
  }

  function restoreFocus(timestamp) {
    if (!timestamp || !Number.isSafeInteger(Number(timestamp))) return;
    (list.querySelector(`button[data-timestamp="${Number(timestamp)}"]`) || $('tab-history'))?.focus({ preventScroll: true });
  }

  function setZoom(value) {
    zoom = Math.min(250, Math.max(100, value));
    viewport.dataset.zoomed = String(zoom > 100);
    $('history-zoom-value').textContent = `${zoom} %`;
    zoomOut.disabled = zoom <= 100;
    zoomIn.disabled = zoom >= 250;
    fitMap();
    if (zoom === 100) viewport.scrollTo(0, 0);
  }

  function fitMap() {
    const image = mapLayer.firstElementChild;
    if (viewport.hidden || !image?.naturalWidth) return;
    const css = getComputedStyle(viewport);
    const space = (size, start, end) => size - parseFloat(css[`padding${start}`]) - parseFloat(css[`padding${end}`])
      - parseFloat(css[`border${start}Width`]) - parseFloat(css[`border${end}Width`]);
    const box = viewport.getBoundingClientRect();
    const width = space(box.width, 'Left', 'Right');
    const height = space(box.height, 'Top', 'Bottom');
    const size = archiveFrame(width, height, image.naturalWidth, image.naturalHeight, zoom);
    if (!size) return;
    // The image and percentage-based markers share the same aspect-ratio box, with space for edge markers.
    mapLayer.style.width = `${size[0]}px`;
    mapLayer.style.height = `${size[1]}px`;
    mapLayer.style.marginTop = `${Math.max(0, (height - size[1]) / 2)}px`;
  }

  new ResizeObserver(fitMap).observe(viewport);

  function mapInfo(message, allowRetry = false) {
    mapMessage.hidden = !message;
    mapMessage.textContent = message;
    retry.hidden = !allowRetry;
  }

  function abortMap() {
    const abort = request;
    request = null;
    abort?.abort();
  }

  function abortPhoto() {
    const abort = photoRequest;
    photoRequest = null;
    abort?.abort();
  }

  function stopReplay() {
    if (timer !== null) clearInterval(timer);
    timer = null;
    play.textContent = 'Воспроизвести';
  }

  function drawFrame(index) {
    if (!frames[index]) return;
    frame = index;
    const { position, mode } = frames[frame];
    routeMarker.style.left = `${position[0]}%`;
    routeMarker.style.top = `${position[1]}%`;
    scrub.value = String(frame);
    scrub.setAttribute('aria-valuetext', `${frame + 1} из ${frames.length}. ${replayModes[mode]}`);
    $('history-replay-step').textContent = `${frame + 1} из ${frames.length}`;
    replayNote.textContent = reducedMotion.matches
      ? `${replayModes[mode]}. Выберите точку ползунком.`
      : `${replayModes[mode]}. Показаны записанные точки. Темп условный.`;
  }

  function showReplay(sections) {
    frames = archiveRoute(sections);
    if (frames.length < 2) return;
    routeMarker = element('span', undefined, 'history-route-marker');
    routeMarker.setAttribute('aria-hidden', 'true');
    mapLayer.append(routeMarker);
    scrub.max = String(frames.length - 1);
    play.disabled = reducedMotion.matches;
    drawFrame(0);
    replay.hidden = false;
  }

  play.addEventListener('click', () => {
    if (timer !== null) { stopReplay(); return; }
    if (!dialog.open || frames.length < 2 || reducedMotion.matches) return;
    if (frame === frames.length - 1) drawFrame(0);
    play.textContent = 'Пауза';
    timer = setInterval(() => {
      if (!dialog.open || frame >= frames.length - 1) { stopReplay(); return; }
      drawFrame(frame + 1);
      if (frame === frames.length - 1) stopReplay();
    }, 150);
  });
  scrub.addEventListener('input', () => { stopReplay(); drawFrame(scrub.valueAsNumber); });
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches) stopReplay();
    play.disabled = reducedMotion.matches;
    if (!replay.hidden) drawFrame(frame);
  });

  function clearRecord() {
    abortMap();
    abortPhoto();
    stopReplay();
    replay.hidden = true;
    routeMarker = null;
    archiveImage = null;
    frames = [];
    viewport.hidden = true;
    delete dialog.dataset.map;
    $('history-map-tools').hidden = true;
    $('history-map-layers').textContent = '';
    $('history-pins-choice').hidden = true;
    $('history-coverage-choice').hidden = true;
    $('history-obstacles').hidden = true;
    $('history-photo-panel').hidden = true;
    mapLayer.replaceChildren();
    obstacleList.replaceChildren();
    photo.replaceChildren();
    viewport.scrollTo(0, 0);
    setZoom(100);
  }

  function photoInfo(message) {
    $('history-photo-message').hidden = !message;
    $('history-photo-message').textContent = message;
  }

  async function showObstacle(obstacle) {
    if (!dialog.open || !active) return;
    abortPhoto();
    photo.replaceChildren();
    $('history-photo-panel').hidden = false;
    $('history-photo-title').textContent = obstacle.label;
    for (const button of dialog.querySelectorAll('button[data-obstacle]')) {
      button.setAttribute('aria-pressed', String(button.dataset.obstacle === obstacle.id));
    }
    if (!obstacle.photoAvailable) {
      photoInfo('Фото этого препятствия недоступно.');
      return;
    }
    photoInfo('Загружаем фото…');
    const controller = new AbortController();
    photoRequest = controller;
    const current = () => photoRequest === controller && dialog.open;
    try {
      const response = await fetch(`./api/history/${active.timestamp}/obstacles/${encodeURIComponent(obstacle.id)}/photo`, requestOptions(controller));
      if (!response.ok) {
        const message = await responseMessage(response, 'Фото пока недоступно.');
        if (current()) photoInfo(message);
        return;
      }
      const blob = await response.blob();
      if (!['image/jpeg', 'image/png'].includes(blob.type) || !blob.size || blob.size > 8_388_608) throw new Error('invalid photo');
      const src = await photoSource(blob);
      if (!current()) return;
      const image = element('img');
      image.alt = `Фото: ${obstacle.label}`;
      image.addEventListener('error', () => {
        if (photo.firstElementChild === image && current()) {
          photo.replaceChildren();
          photoInfo('Фото не открылось.');
        }
      });
      photo.append(image);
      image.src = src;
      photoInfo('');
    } catch {
      if (current()) photoInfo('Не удалось открыть фото. Можно выбрать препятствие ещё раз.');
    }
  }

  function showObstacles(obstacles) {
    $('history-obstacles').hidden = !obstacles.length;
    if (!obstacles.length) return;
    const pins = element('div', undefined, 'history-pins');
    for (const [index, obstacle] of obstacles.entries()) {
      const item = element('li');
      const title = `${index + 1}. ${obstacle.label}`;
      const button = element('button');
      button.append(obstacleIcon(obstacle.kind), element('span', title));
      button.type = 'button';
      button.dataset.obstacle = obstacle.id;
      button.setAttribute('aria-controls', 'history-photo-panel');
      button.setAttribute('aria-pressed', 'false');
      button.addEventListener('click', () => { void showObstacle(obstacle); });
      item.append(button);
      obstacleList.append(item);
      if (!obstacle.position) continue;
      const pin = element('button', undefined, 'history-pin');
      pin.append(obstacleIcon(obstacle.kind));
      pin.type = 'button';
      pin.dataset.obstacle = obstacle.id;
      pin.setAttribute('aria-label', title);
      pin.setAttribute('aria-controls', 'history-photo-panel');
      pin.setAttribute('aria-pressed', 'false');
      pin.style.left = `${obstacle.position[0]}%`;
      pin.style.top = `${obstacle.position[1]}%`;
      pin.addEventListener('click', () => {
        void showObstacle(obstacle);
        $('history-photo-panel').scrollIntoView({ block: 'nearest' });
      });
      pins.append(pin);
    }
    mapLayer.append(pins);
    if (pins.childElementCount) {
      $('history-pins-label').textContent = `Метки (${pins.childElementCount})`;
      $('history-pins-choice').hidden = false;
      pins.hidden = !pinSwitch.checked;
    }
  }

  async function loadMap(row) {
    if (!row.mapAvailable) {
      mapInfo('К этой записи нет карты.');
      return;
    }
    mapInfo('Загружаем карту уборки…');
    const controller = new AbortController();
    request = controller;
    const current = () => request === controller && dialog.open;
    try {
      const response = await fetch(`./api/history/${row.timestamp}`, requestOptions(controller));
      if (!response.ok) {
        const fallback = [404, 410].includes(response.status) ? 'Карта этой уборки недоступна.' : 'Карту пока не удалось загрузить.';
        const message = await responseMessage(response, fallback);
        if (current()) mapInfo(message, response.status >= 500);
        return;
      }
      const payload = await response.json();
      if (!current()) return;
      const src = archiveMapSource(payload, row.timestamp);
      if (!src) throw new Error('invalid archive map');
      const explicit = payload.coverage_source;
      const source = ['frame', 'route'].includes(explicit) ? explicit
        : explicit === undefined && payload.has_coverage === true ? 'frame' : null;
      const image = element('img');
      image.alt = 'Сохранённая карта уборки';
      archiveImage = { image, full: src, plain: source && archiveMapSource(payload, row.timestamp, true),
        source, hasPath: payload.has_path === true };
      image.addEventListener('load', () => {
        if (!current()) return;
        viewport.hidden = false;
        dialog.dataset.map = 'true';
        $('history-map-tools').hidden = false;
        $('history-coverage-choice').hidden = !archiveImage.plain;
        mapInfo('');
        showReplay(payload.route_sections);
        showObstacles(archiveObstacles(payload.obstacles));
      }, { once: true });
      image.addEventListener('load', () => { if (current()) fitMap(); });
      image.addEventListener('error', () => { if (current()) mapInfo('Не удалось отобразить карту уборки.', true); });
      mapLayer.append(image);
      applyArchiveCoverage();
    } catch {
      if (current()) mapInfo('Не удалось загрузить карту уборки.', true);
    }
  }

  function showRecord(row) {
    clearRecord();
    active = row;
    const date = day.format(row.timestamp * 1000);
    $('history-time').textContent = date;
    $('history-area').textContent = metric(row.area, ' м²');
    $('history-duration').textContent = metric(row.duration, ' мин');
    $('history-status').textContent = row.status || '—';
    const info = $('history-run-details');
    info.textContent = row.details.filter(Boolean).join(' · ');
    info.hidden = !info.textContent;
    updateNavigation();
    void loadMap(row);
  }

  function neighbour(offset) {
    const index = shown.findIndex(row => row.timestamp === active?.timestamp);
    if (index >= 0 && shown[index + offset]) showRecord(shown[index + offset]);
  }

  function dismiss() {
    clearRecord();
    active = null;
    dialog.close();
  }

  close.addEventListener('click', dismiss);
  dialog.addEventListener('cancel', event => {
    event.preventDefault();
    dismiss();
  });
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    if (active) clearRecord();
    active = null;
    restoreFocus(opener?.dataset.timestamp);
    opener = null;
  });
  filter.addEventListener('change', () => { page = 0; renderList(); });
  for (const [id, step] of [['journal-newer', -1], ['journal-older', 1]]) {
    $(id).addEventListener('click', () => {
      page += step;
      renderList();
      list.querySelector('.history-link')?.focus();
    });
  }
  retry.addEventListener('click', () => { if (active) showRecord(active); });
  previous.addEventListener('click', () => neighbour(1));
  next.addEventListener('click', () => neighbour(-1));
  zoomIn.addEventListener('click', () => setZoom(zoom + 50));
  zoomOut.addEventListener('click', () => setZoom(zoom - 50));

  return details => {
    const currentAvailable = details.history_available === true;
    const fresh = currentAvailable ? historyRows(details.history) : [];
    const key = JSON.stringify([currentAvailable, fresh]);
    if (key === signature) return;
    signature = key;
    available = currentAvailable;
    records = fresh;
    const selected = filter.value;
    filter.replaceChildren(new Option('Все результаты', 'all'));
    for (const status of new Set(records.map(row => row.status))) filter.add(new Option(status || 'Без результата', status));
    if ([...filter.options].some(option => option.value === selected)) filter.value = selected;
    renderList();
  };
}
