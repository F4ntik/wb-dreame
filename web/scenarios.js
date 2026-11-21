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
  const apply = byId('entrance-apply'), save = byId('entrance-save'), note = byId('entrance-note');
  const refresh = byId('scene-refresh');
  let entrance, pending = false;

  function report(text) {
    feedback.textContent = text;
    feedback.hidden = !text;
  }
  function render() {
    if (!host.open) return;
    const disabled = !ui.online() || !ui.map() || ui.busy() || pending;
    apply.disabled = disabled || !entrance;
    save.hidden = !entrance || !ui.zones();
    save.disabled = disabled;
    refresh.disabled = disabled;
    for (const button of list.querySelectorAll('button')) {
      button.disabled = disabled || button.dataset.map !== String(ui.map()?.id);
    }
    apply.textContent = entrance?.status === 'ready' ? 'Показать зоны на карте' : 'Указать участок на карте';
    note.textContent = entrance?.status === 'stale' ? 'Карта изменилась. Обведите участок у входа заново.'
      : entrance?.status === 'ready' ? 'Сохранено зон: ' + entrance.zones.length
      : 'Обведите на карте участок, где скапливается песок.';
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
        if (scene.map_id !== ui.map()?.id) button.textContent += scene.map_id ? ' · карта №' + scene.map_id : ' · карта неизвестна';
        button.addEventListener('click', () => run(async () => {
          const detail = await fetchScenario('shortcuts/' + scene.id);
          const map = ui.map();
          if (!map || detail.map_id !== map.id || detail.name !== scene.name) throw new Error('Карта или сценарий изменились. Обновите список.');
          const targets = [...(detail.whole ? ['Вся квартира'] : []), ...detail.rooms.map(id => map.rooms[id] || 'Комната №' + id)];
          if (detail.zones.length) targets.push('Зон: ' + detail.zones.length);
          await ui.send({job:{action:'start', kind:'shortcut', map_id:map.id, revision:map.revision,
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
  apply.addEventListener('click', () => run(async () => {
    entrance = await fetchScenario('entrance');
    if (entrance.status === 'ready' && (ui.map()?.revision !== entrance.revision || ui.map()?.id !== entrance.map_id))
      throw new Error('Карта изменилась. Обновите панель.');
    ui.pick(entrance.status === 'ready' ? entrance.zones : null);
  }));
  save.addEventListener('click', () => run(async () => {
    const map = ui.map(), zones = ui.zones();
    if (!map || !zones?.length || !entrance) return;
    if (entrance.version && !await ui.confirm('Заменить участок у входа?',
        'Сохранить текущее выделение для сценария «Песок у входа»?', 'Заменить')) return;
    if (ui.map()?.revision !== map.revision || JSON.stringify(ui.zones()) !== JSON.stringify(zones))
      throw new Error('Выделение изменилось. Проверьте зоны ещё раз.');
    entrance = await fetchScenario('entrance',{version:entrance.version, map_id:map.id, revision:map.revision, zones});
    report('Участок сохранён на контроллере.');
  }));
  return {render};
}
