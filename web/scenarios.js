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
  const host = byId('scenario-panel'), list = byId('scene-items'), feedback = byId('scene-message');
  const apply = byId('entrance-apply'), edit = byId('entrance-edit'), save = byId('entrance-save'), note = byId('entrance-note');
  const refresh = byId('scene-refresh');
  let entrance, editor, pending = false;
  const status = byId('entrance-editor-status');

  function report(text) {
    if (editor) status.textContent = text;
    feedback.textContent = text;
    feedback.hidden = !text;
  }
  function render() {
    const disabled = !ui.online() || !ui.map() || ui.busy() || pending;
    apply.hidden = entrance?.status !== 'ready';
    apply.disabled = edit.disabled = disabled || !entrance;
    const clear = Boolean(editor?.version) && ui.zoneCount() === 0;
    save.textContent = clear ? 'Сбросить сценарий' : 'Сохранить сценарий';
    save.disabled = disabled || !editor || (!clear && !ui.zones()?.length);
    byId('entrance-cancel').disabled = pending;
    if (!host.open) return;
    refresh.disabled = disabled;
    for (const button of list.querySelectorAll('button')) {
      button.disabled = disabled || button.dataset.map !== String(ui.map()?.id);
    }
    edit.textContent = entrance?.status === 'ready' ? 'Изменить сценарий' : 'Настроить сценарий';
    note.textContent = entrance?.status === 'stale' ? 'Карта изменилась. Выберите участок заново.'
      : entrance?.status === 'ready' ? `Зон: ${entrance.zones.length} · ${ui.summary(entrance.settings)}`
      : 'Сохраните на WB зоны у входа, мощность, воду и число проходов.';

  }
  async function run(work) {
    if (pending || ui.busy() || !ui.online()) return;
    pending = true; report(''); render();
    try { await work(); }
    catch (error) { report(error instanceof TypeError || error.name === 'TimeoutError'
      ? 'Нет ответа. Обновите сценарии.' : error.message); }
    finally { pending = false; render(); }
  }
  async function load() {
    await run(async () => {
      entrance = await fetchScenario('entrance');
      list.replaceChildren();
      const {items} = await fetchScenario('shortcuts');
      for (const scene of items) {
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.map = String(scene.map_id);
        button.textContent = scene.name;
        if (!ui.writable()) button.textContent += ' · проверить';
        if (scene.map_id !== ui.map()?.id) button.textContent += scene.map_id ? ' · карта №' + scene.map_id : ' · карта неизвестна';
        button.addEventListener('click', () => run(async () => {
          const detail = await fetchScenario('shortcuts/' + scene.id);
          const map = ui.map();
          if (!map || detail.map_id !== map.id || detail.name !== scene.name) throw new Error('Карта или сценарий изменились. Обновите список.');
          const targets = [...(detail.whole ? ['Вся квартира'] : []), ...detail.rooms.map(id => map.rooms[id] || 'Комната №' + id)];
          if (detail.zones.length) targets.push('Зон: ' + detail.zones.length);
          await ui.send({report, job:{action:'start', kind:'shortcut', map_id:map.id, revision:map.revision,
            shortcut_id:scene.id, shortcut_revision:detail.revision}, confirmation:'Запустить «' + detail.name
              + '» по настройкам Dreamehome? ' + targets.join(' · ') + '. Этапов: ' + detail.steps + '.'});
        }));
        list.append(button);
      }
      if (!items.length) list.textContent = 'В Dreamehome пока нет сценариев.';
    });
  }
  host.addEventListener('toggle', () => { if (host.open) void load(); });
  refresh.addEventListener('click', load);
  async function openEntrance(editing) {
    await run(async () => {
      entrance = await fetchScenario('entrance');
      const map = ui.map(), ready = entrance.status === 'ready';
      if (!map || (ready && (map.revision !== entrance.revision || map.id !== entrance.map_id)))
        throw new Error('Карта изменилась. Обновите панель.');
      if (!editing && !ready) throw new Error('Сначала настройте сценарий.');
      if (editing) {
        editor = {version: entrance.version, id: map.id, revision: map.revision, previous: ui.draft()};
        status.textContent = 'Изменения сохраняются кнопкой «Сохранить сценарий».';
      }
      if (!ui.pick(ready ? entrance.zones : [], ready ? entrance.settings : {}, editing)) {
        if (editing) endEditor('');
        throw new Error('Зоны не совпадают с картой. Обновите панель.');
      }
    });
  }
  function endEditor(message, restore = true) {
    const previous = editor?.previous;
    editor = null;
    if (previous && restore) ui.restore(previous);
    report(message);
  }
  async function saveEditor() {
    if (!editor) return;
    await run(async () => {
      const map = ui.map(), zones = ui.zones(), current = editor;
      const clear = Boolean(current?.version) && ui.zoneCount() === 0;
      if (!map || (!clear && !zones?.length) || !current || current.id !== map.id || current.revision !== map.revision)
        throw new Error('Карта изменилась. Откройте сценарий заново.');
      if (clear && !await ui.confirm('Сбросить «Песок у входа»?', 'С контроллера будут удалены сохранённые зоны и параметры этого сценария.', 'Сбросить')) return;
      if (current !== editor || map.revision !== ui.map()?.revision) throw new Error('Карта изменилась. Обновите сценарий.');
      const controls = [byId('map'), byId('job-form')];
      controls.forEach(el => { el.inert = true; });
      try {
        entrance = await fetchScenario('entrance', {version:current.version, map_id:map.id, revision:map.revision,
          ...(clear ? {action:'clear'} : {zones, settings:ui.tuning()})});
      } finally { controls.forEach(el => { el.inert = false; }); }
      if (current !== editor) return;
      endEditor(clear ? 'Настройки сценария сброшены.' : '«Песок у входа» сохранён на контроллере.');
      edit.focus();
    });
  }
  edit.addEventListener('click', () => openEntrance(true));
  apply.addEventListener('click', () => openEntrance(false));
  save.addEventListener('click', saveEditor);
  byId('entrance-cancel').addEventListener('click', () => { endEditor('Изменения отменены.'); edit.focus(); });
  return {render, editing: () => Boolean(editor), saveEditor,
    mapChanged() { if (editor) endEditor('Карта изменилась. Откройте сценарий заново.', false); }};
}
