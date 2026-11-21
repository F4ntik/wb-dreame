import { glyph } from './views.js';

const byId = id => document.getElementById(id);

async function fetchScenario(path, body) {
  const options = {cache: 'no-store', signal: AbortSignal.timeout(20000)};
  if (body) Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Dreame-Request': '1'},
    body: JSON.stringify(body)});
  const response = await fetch('./api/' + path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || 'Проверьте связь с WB.');
  return result;
}

export function scenarios(ui) {
  const host = byId('view-scenarios'), list = byId('scene-items'), feedback = byId('scene-message');
  const apply = byId('entrance-apply'), edit = byId('entrance-edit'), save = byId('entrance-save'), note = byId('entrance-note');
  const refresh = byId('scene-refresh');
  const startHint = byId('scene-start-hint'), defaultStartHint = startHint.textContent;
  let entrance, editor, loadedFor, pending = false;
  const status = byId('entrance-editor-status');
  const values = byId('entrance-values');

  function report(text, error = false) {
    if (editor) status.textContent = text;
    feedback.textContent = text;
    feedback.dataset.error = String(error);
    feedback.hidden = !text;
  }
  function render() {
    const online = ui.online(), map = ui.map();
    if (!online) loadedFor = null;
    const disabled = !online || !map || ui.busy() || pending;
    apply.hidden = entrance?.status !== 'ready';
    apply.disabled = disabled || !entrance || Boolean(editor);
    edit.disabled = disabled || !entrance;
    const clear = Boolean(editor?.version) && ui.zoneCount() === 0;
    save.textContent = clear ? 'Сбросить сценарий' : 'Сохранить сценарий';
    save.disabled = disabled || !editor || (!clear && !ui.zones()?.length);
    byId('entrance-cancel').disabled = pending;
    if (host.hidden) return;
    refresh.disabled = !online || ui.busy() || pending;
    const startBlock = ui.startBlock();
    const startText = startBlock || defaultStartHint;
    if (startHint.textContent !== startText) startHint.textContent = startText;
    for (const button of list.querySelectorAll('button')) {
      const sameMap = button.dataset.map === String(map?.id);
      button.disabled = disabled || Boolean(startBlock) || !sameMap;
      const hint = button.querySelector('small');
      hint.textContent = sameMap ? (!startBlock && !ui.writable() ? 'Доступна проверка' : '')
        : Number(button.dataset.map) ? 'Карта № ' + button.dataset.map : 'Карта неизвестна';
      hint.hidden = !hint.textContent;
    }
    edit.textContent = editor ? 'Вернуться к настройке' : entrance?.status === 'ready' ? 'Изменить' : 'Настроить';
    edit.classList.toggle('primary', !entrance || entrance.status !== 'ready');
    note.textContent = !online ? 'Для настройки и запуска нужна связь с роботом.'
      : !map ? 'Робот ещё не передал карту помещений.'
      : entrance?.status === 'stale' ? 'Карта изменилась. Выберите участок заново.'
      : entrance?.status === 'ready' ? `Зоны на карте — ${entrance.zones.length}`
      : !entrance ? (pending ? 'Загружаем настройки…' : 'Обновите сценарии, чтобы прочитать настройки.')
      : 'Выберите на карте участок у двери и сохраните параметры уборки.';
    values.hidden = !online || !map || entrance?.status !== 'ready';
    if (!values.hidden) {
      const readings = ui.tuningValues(entrance.settings);
      values.querySelectorAll('dd').forEach((field, index) => { field.textContent = readings[index]; });
    }
    if (!disabled && loadedFor !== map.revision) void load(false);
  }
  async function run(work, clearMessage = true) {
    if (pending || ui.busy() || !ui.online()) return;
    pending = true;
    if (clearMessage) report('');
    render();
    try { await work(); }
    catch (error) { report(error instanceof TypeError || error.name === 'TimeoutError'
      ? 'Нет ответа. Обновите сценарии.' : error.message, true); }
    finally { pending = false; render(); }
  }
  async function load(clearMessage = true) {
    if (pending || ui.busy() || !ui.online()) return;
    loadedFor = ui.map()?.revision;
    await run(async () => {
      list.textContent = 'Загружаем сценарии…';
      try { entrance = await fetchScenario('entrance'); }
      catch { entrance = null; report('Не удалось прочитать сценарий контроллера. Нажмите «Обновить».', true); }
      let items;
      try { ({items} = await fetchScenario('shortcuts')); }
      catch { list.textContent = 'Не удалось прочитать список. Нажмите «Обновить».'; return; }
      list.replaceChildren();
      for (const scene of items) {
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.map = String(scene.map_id);
        const title = document.createElement('span');
        title.textContent = scene.name;
        const hint = document.createElement('small');
        title.append(hint);
        button.append(glyph('scenarios'), title, glyph('forward'));
        button.addEventListener('click', () => run(async () => {
          const detail = await fetchScenario('shortcuts/' + scene.id);
          const map = ui.map();
          if (!map || detail.map_id !== map.id || detail.name !== scene.name) throw new Error('Карта или сценарий изменились. Обновите список.');
          const targets = [...(detail.whole ? ['Вся квартира'] : []), ...detail.rooms.map(id => map.rooms[id] || 'Комната № ' + id)];
          if (detail.zones.length) targets.push('Зон — ' + detail.zones.length);
          await ui.send({report, job:{action:'start', kind:'shortcut', map_id:map.id, revision:map.revision,
            shortcut_id:scene.id, shortcut_revision:detail.revision}, confirmation:'«' + detail.name + '» · Dreamehome\n' +
              [...targets, 'Этапов — ' + detail.steps].join(' · ')});
        }));
        list.append(button);
      }
      if (!items.length) list.textContent = 'В Dreamehome пока нет сценариев.';
    }, clearMessage);
  }
  document.addEventListener('dreame:view', render);
  refresh.addEventListener('click', () => load());
  async function openEntrance(editing) {
    if (editor) { ui.openMap(); return; }
    await run(async () => {
      entrance = await fetchScenario('entrance');
      const map = ui.map(), ready = entrance.status === 'ready';
      if (!map || (ready && (map.revision !== entrance.revision || map.id !== entrance.map_id)))
        throw new Error('Карта изменилась. Обновите панель.');
      if (!editing && !ready) throw new Error('Сначала настройте сценарий.');
      if (editing) {
        editor = {version: entrance.version, id: map.id, revision: map.revision, previous: ui.draft()};
        status.textContent = 'Сохранение обновит сценарий на контроллере. Уборку можно запустить отдельно.';
      }
      if (!ui.pick(ready ? entrance.zones : [], ready ? entrance.settings : {}, editing)) {
        if (editing) endEditor('', true);
        throw new Error('Зоны не совпадают с картой. Обновите панель.');
      }
      ui.openMap();
    });
  }
  function endEditor(message, sameMap = true) {
    const previous = editor?.previous;
    editor = null;
    if (previous) ui.restore(previous, sameMap);
    const returnHere = !byId('view-map').hidden || !host.hidden;
    if (returnHere) byId('tab-scenarios').click();
    report(message, !sameMap);
    if (returnHere) requestAnimationFrame(() => (edit.disabled ? byId('tab-scenarios') : edit).focus({preventScroll:true}));
  }
  async function saveEditor() {
    if (!editor) return;
    await run(async () => {
      const map = ui.map(), zones = ui.zones(), current = editor;
      const clear = Boolean(current?.version) && ui.zoneCount() === 0;
      if (!map || (!clear && !zones?.length) || !current || current.id !== map.id || current.revision !== map.revision)
        throw new Error('Карта изменилась. Откройте сценарий заново.');
      if (clear && !await ui.confirm('Сбросить «Песок у входа»?', 'С контроллера будут удалены сохранённые зоны и параметры этого сценария.', 'Сбросить', true)) return;
      if (current !== editor || map.revision !== ui.map()?.revision) throw new Error('Карта изменилась. Обновите сценарий.');
      const controls = [byId('map'), byId('job-form')];
      controls.forEach(el => { el.inert = true; });
      try {
        entrance = await fetchScenario('entrance', {version:current.version, map_id:map.id, revision:map.revision,
          ...(clear ? {action:'clear'} : {zones, settings:ui.tuning()})});
      } finally { controls.forEach(el => { el.inert = false; }); }
      if (current !== editor) return;
      endEditor(clear ? 'Настройки сценария сброшены.' : '«Песок у входа» сохранён на контроллере.');
    });
  }
  edit.addEventListener('click', () => openEntrance(true));
  apply.addEventListener('click', () => openEntrance(false));
  save.addEventListener('click', saveEditor);
  byId('entrance-cancel').addEventListener('click', () => endEditor('Изменения отменены.'));
  return {render, editing: () => Boolean(editor), saveEditor,
    mapChanged() { if (editor) endEditor('Карта изменилась. Откройте сценарий заново.', false); }};
}
