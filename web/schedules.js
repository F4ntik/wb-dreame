const panel = document.getElementById('schedules');
const find = name => panel.querySelector(`#schedules-${name}`);
const refresh = find('refresh');
const feedback = find('result');
const form = find('charging-form');
const fieldset = form.querySelector('fieldset');
const save = form.querySelector('button[type="submit"]');
const { enabled, start, end } = form.elements;
let current = null;
let writable = false;
let busy = false;
let opened = false;

function updateControls() {
  refresh.disabled = busy;
  fieldset.disabled = busy || !writable;
  save.disabled = !current || (current.enabled === enabled.checked && current.start === start.value && current.end === end.value);
}

function showCharging(data) {
  find('charging').hidden = false;
  current = typeof data?.revision === 'string' ? data : null;
  form.hidden = !current;
  find('charging-note').textContent = current
    ? (writable ? 'Интервал может переходить через полночь.' : 'Для сохранения включите управление в настройках драйвера.')
    : data?.reason || 'Робот не подтвердил интервал.';
  if (current) {
    enabled.checked = current.enabled;
    start.value = current.start;
    end.value = current.end;
  }
  updateControls();
}

function recurrence(task) {
  if (task.once) return 'Один раз';
  if (task.days === 7) return 'Каждый день';
  if (!task.days) return 'С повтором';
  const day = ['день', 'дня', 'дня', 'дня', 'дней', 'дней'][task.days - 1];
  return `${task.days} ${day} в неделю`;
}

function showCleaning(data) {
  find('cleaning').hidden = false;
  const list = find('cleaning-list');
  list.replaceChildren();
  list.hidden = !data?.tasks?.length;
  const edit = 'Создать, изменить или удалить расписание можно в Dreamehome.';
  find('cleaning-note').textContent = data?.reason || (list.hidden ? `Сохранённых уборок по расписанию нет. ${edit}` : edit);
  for (const task of data?.tasks || []) {
    const item = document.createElement('li');
    const state = task.invalid ? 'Недействительно' : task.enabled ? 'Включено' : 'Выключено';
    item.textContent = `${task.time} · ${recurrence(task)} · ${state} (№ ${task.id})`;
    list.append(item);
  }
}

function message(text, error = false) {
  feedback.hidden = !text;
  feedback.dataset.error = String(error);
  feedback.textContent = text;
}

async function request(options) {
  let response;
  let data;
  try {
    response = await fetch('./api/schedules', {
      cache: 'no-store', signal: AbortSignal.timeout(25000), ...options,
    });
    data = await response.json();
  } catch {
    throw new Error('Ответ не получен. Прочитайте расписания ещё раз.');
  }
  if (!response.ok) throw new Error(data.message || 'Робот не подтвердил расписание.');
  return data;
}

async function load() {
  if (busy) return;
  busy = true;
  writable = false;
  updateControls();
  message('Читаем расписания пылесоса…');
  try {
    const data = await request();
    writable = data.allow_commands === true;
    showCharging(data.charging);
    showCleaning(data.cleaning);
    message('');
  } catch (error) {
    find('charging').hidden = true;
    find('cleaning').hidden = true;
    message(error.message, true);
  } finally {
    busy = false;
    updateControls();
  }
}

refresh.addEventListener('click', load);
panel.addEventListener('toggle', () => {
  if (panel.open && !opened) {
    opened = true;
    load();
  }
});
form.addEventListener('input', updateControls);
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!current || !writable || busy || save.disabled) return;
  if (start.value === end.value) {
    message('Укажите разное время начала и конца.', true);
    return;
  }
  busy = true;
  updateControls();
  message('Сохраняем интервал зарядки…');
  try {
    const data = await request({
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dreame-Request': '1' },
      body: JSON.stringify({ revision: current.revision, enabled: enabled.checked, start: start.value, end: end.value }),
    });
    showCharging(data.charging);
    message(data.message);
  } catch (error) {
    writable = false;
    message(error.message, true);
  } finally {
    busy = false;
    updateControls();
  }
});
