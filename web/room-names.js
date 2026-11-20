export function roomNameEditor(host, headers, refresh) {
  const form = host.querySelector('form');
  const select = form.querySelector('select');
  const input = form.querySelector('input');
  const save = form.querySelector('[type="submit"]');
  const reset = form.querySelector('[type="button"]');
  const notice = form.querySelector('[role="status"]');
  let entries = [];
  let options = '';
  let editing = null;
  let busy = false;

  function buttons() {
    select.disabled = busy;
    input.disabled = busy || !editing;
    save.disabled = input.disabled || !input.value.trim() || input.value.trim() === editing.alias;
    reset.disabled = input.disabled || editing.alias === null;
  }

  function readSelected() {
    const entry = entries.find(item => item.key === select.value) || null;
    if (!entry || entry.key !== editing?.key || input.value === (editing.alias || '')) {
      editing = entry;
      input.value = entry?.alias || '';
      input.placeholder = entry?.original || '';
    }
    buttons();
  }

  function feedback(message, error = false) {
    notice.textContent = message;
    notice.dataset.error = String(error);
    notice.hidden = !message;
  }

  select.addEventListener('change', () => { feedback(''); readSelected(); });
  input.addEventListener('input', buttons);

  async function write(name) {
    if (!editing || busy) return;
    const { key, alias } = editing;
    busy = true;
    buttons();
    try {
      const response = await fetch('./api/room-name', {
        method: 'POST', headers, signal: AbortSignal.timeout(8000),
        body: JSON.stringify({ key, expected: alias, name }),
      });
      const result = await response.json();
      if (!response.ok || result.status !== 'saved') {
        feedback(result.message || 'Не удалось сохранить название.', true);
      } else {
        feedback(name ? 'Название сохранено.' : 'Имя Dreamehome восстановлено.');
      }
    } catch {
      feedback('Ответ не получен. Проверьте название перед повтором.', true);
    } finally {
      editing = null;
      await refresh();
      busy = false;
      readSelected();
    }
  }

  form.addEventListener('submit', event => {
    event.preventDefault();
    if (!save.disabled) void write(input.value.trim());
  });
  reset.addEventListener('click', () => { if (!reset.disabled) void write(null); });

  return next => {
    host.hidden = !next.length;
    entries = next;
    const nextOptions = JSON.stringify(next.map(({ id, key, original }) => [id, key, original]));
    if (nextOptions !== options) {
      const selected = select.value;
      const placeholder = select.options[0];
      select.replaceChildren(placeholder, ...next.map(({ id, key, original }) => new Option(`${original} · №${id}`, key)));
      select.value = next.some(item => item.key === selected) ? selected : '';
      options = nextOptions;
    }
    readSelected();
  };
}
