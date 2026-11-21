const panel = document.getElementById('schedules');
const find = name => panel.querySelector(`#schedules-${name}`);
const refresh = find('refresh');
const feedback = find('result');
const form = find('charging-form');
const fieldset = form.querySelector('fieldset');
const save = form.querySelector('button[type="submit"]');
const { enabled, start, end } = form.elements;
const cleaningForm = find('cleaning-form');
const cleaningList = find('cleaning-list');
const cleaningFields = find('cleaning-fields');
const cleaningDays = find('cleaning-days');
const cleaningSave = cleaningForm.querySelector('button[type="submit"]');
const cleaningDelete = find('cleaning-delete');
const weekdays = [...cleaningForm.elements.weekday];
const dayNames = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
let current = null;
let cleaning = null;
let editing = null;
let editOpener = null;
let writable = false;
let busy = false;
let opened = false;

function updateControls() {
  refresh.disabled = busy;
  fieldset.disabled = busy || !writable;
  save.disabled = !current || (current.enabled === enabled.checked && current.start === start.value && current.end === end.value);
  cleaningForm.querySelector('fieldset').disabled = busy || !writable;
  for (const button of cleaningList.querySelectorAll('button')) button.disabled = busy || !writable;
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
  return weekdays.filter(input => task.weekdays?.[input.value] === '1')
    .map(input => dayNames[input.value]).join(', ') || 'Дни уточните в Dreamehome';
}

function showCleaning(data) {
  find('cleaning').hidden = false;
  cleaning = data;
  cleaningForm.hidden = true;
  editing = null;
  editOpener = null;
  cleaningList.replaceChildren();
  cleaningList.hidden = !data?.tasks?.length;
  const create = 'Новые задания, комнаты и режим уборки настраиваются в Dreamehome.';
  const hint = !data?.can_edit ? 'Изменения доступны в Dreamehome.'
    : !writable ? 'Для изменений включите управление в настройках драйвера.' : create;
  find('cleaning-note').textContent = data?.reason || (cleaningList.hidden ? `Заданий пока нет. ${create}` : hint);
  for (const task of data?.tasks || []) {
    const item = document.createElement('li');
    const state = task.invalid ? 'Недействительно' : task.enabled ? 'Включено' : 'Выключено';
    const description = `${task.time} · ${recurrence(task)} · ${state} (№ ${task.id})`;
    item.textContent = description;
    if (writable && (task.editable || data.can_delete)) {
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.task = task.id;
      button.textContent = task.editable ? 'Изменить' : 'Управлять';
      button.setAttribute('aria-label', `${button.textContent} задание № ${task.id} на ${task.time}`);
      button.addEventListener('click', () => editCleaning(task, button));
      item.append(' ', button);
    }
    cleaningList.append(item);
  }
}

function editCleaning(task, button) {
  editing = task;
  editOpener = button;
  find('cleaning-title').textContent = `Задание № ${task.id} на ${task.time}`;
  cleaningFields.hidden = !task.editable;
  find('cleaning-edit-note').hidden = task.editable;
  cleaningSave.hidden = !task.editable;
  cleaningDelete.hidden = !cleaning.can_delete;
  cleaningDays.hidden = task.once;
  cleaningForm.elements.time.value = task.time;
  cleaningForm.elements.enabled.checked = task.enabled;
  for (const input of weekdays) input.checked = task.weekdays?.[input.value] === '1';
  cleaningForm.hidden = false;
  (task.editable ? cleaningForm.elements.time : cleaningDelete).focus();
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
    const data = await saveSchedule({ revision: current.revision, enabled: enabled.checked, start: start.value, end: end.value });
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

function saveSchedule(payload) {
  return request({
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dreame-Request': '1' },
    body: JSON.stringify(payload),
  });
}

async function saveCleaning(operation, fields = {}) {
  const taskId = editing.id;
  busy = true;
  updateControls();
  message(operation === 'delete' ? 'Удаляем задание…' : 'Сохраняем задание уборки…');
  try {
    const data = await saveSchedule({ kind: 'cleaning', operation, id: taskId, revision: cleaning.revision, ...fields });
    showCleaning(data.cleaning);
    message(data.message);
  } catch (error) {
    writable = false;
    message(error.message, true);
  } finally {
    busy = false;
    updateControls();
    (cleaningList.querySelector(`button[data-task="${taskId}"]:not(:disabled)`) || refresh).focus();
  }
}

cleaningForm.addEventListener('submit', event => {
  event.preventDefault();
  if (!editing?.editable || !writable || busy) return;
  const fields = { enabled: cleaningForm.elements.enabled.checked, time: cleaningForm.elements.time.value };
  if (!editing.once) {
    const mask = Array(7).fill('0');
    for (const input of weekdays) if (input.checked) mask[input.value] = '1';
    fields.weekdays = mask.join('');
    if (!fields.weekdays.includes('1')) {
      message('Выберите хотя бы один день недели.', true);
      weekdays[0].focus();
      return;
    }
  }
  saveCleaning('update', fields);
});

cleaningDelete.addEventListener('click', () => {
  if (!editing || !writable || busy || !cleaning.can_delete) return;
  if (window.confirm(`Удалить задание № ${editing.id} на ${editing.time}?`)) saveCleaning('delete');
});

find('cleaning-cancel').addEventListener('click', () => {
  cleaningForm.hidden = true;
  editing = null;
  editOpener?.focus();
  editOpener = null;
});
