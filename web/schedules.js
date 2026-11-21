import { copyConflicts, recurrence } from './schedules-data.js';
import { confirmMessage } from './views.js';

const panel = document.getElementById('schedules');
const connection = document.getElementById('connection');
const find = name => panel.querySelector(`#schedules-${name}`);
const refresh = find('refresh');
const chargingForm = find('charging-form');
const chargingEdit = find('charging-edit');
const chargingSave = chargingForm.querySelector('[type="submit"]');
const charging = chargingForm.elements;
const cleaningForm = find('cleaning-form');
const cleaningList = find('cleaning-list');
const cleaningFields = find('cleaning-fields');
const cleaningSave = cleaningForm.querySelector('[type="submit"]');
const cleaningCopy = find('cleaning-copy');
const cleaningDelete = find('cleaning-delete');
const weekdays = [...cleaningForm.elements.weekday];
let chargingData;
let cleaningData;
let chargingDraft;
let cleaningDraft;
let writable = false;
let ready = false;
let busy = false;
let opened = false;

const editableHint = 'Для изменений включите управление в настройках драйвера.';
const staleHint = 'Расписание изменилось. Сверьте новые данные, затем отмените черновик и снова откройте редактор.';
const groups = [
  ['enabled', 'Включены'],
  ['disabled', 'Выключены'],
  ['invalid', 'Недействительны'],
];
const groupOf = task => task.invalid ? 'invalid' : task.enabled ? 'enabled' : 'disabled';
const taskButton = id => cleaningList.querySelector(`button[data-task="${id}"]`);
const overnight = (start, end) => Boolean(start && end && end < start);

function message(text, error = false, section = '') {
  const feedback = find(`${section}result`);
  feedback.hidden = !text;
  feedback.dataset.error = String(error);
  feedback.textContent = text;
}

function checkDraft(draft, data, kind) {
  if (!draft) return;
  const text = !data?.revision ? 'Обновите расписание после восстановления связи. Черновик сохранён в форме.'
    : draft.revision !== data.revision ? staleHint : '';
  message(text, true, `${kind}-`);
}

function interval() {
  return { enabled: charging.enabled.checked, start: charging.start.value, end: charging.end.value };
}

function cleaningInput() {
  const { time, enabled } = cleaningForm.elements;
  const fields = { time: time.value };
  if (!cleaningDraft.copy) fields.enabled = enabled.checked;
  if (!cleaningDraft.task.once) {
    const mask = Array(7).fill('0');
    for (const input of weekdays) if (input.checked) mask[input.value] = '1';
    fields.weekdays = mask.join('');
  }
  return fields;
}

function updateControls() {
  const online = connection.dataset.online === 'true';
  const allow = online && connection.dataset.writable === 'true' && ready && writable && !busy;
  refresh.disabled = busy || !online;
  chargingEdit.disabled = !allow || Boolean(chargingDraft);
  chargingForm.querySelector('fieldset').disabled = !allow;
  find('charging-cancel').disabled = busy;
  const { enabled, start, end } = interval();
  find('charging-next-day').hidden = !overnight(start, end);
  chargingSave.disabled = !allow || !chargingDraft || chargingDraft.revision !== chargingData?.revision
    || (chargingDraft.enabled === enabled && chargingDraft.start === start && chargingDraft.end === end);

  for (const button of cleaningList.querySelectorAll('button[data-task]')) button.disabled = !allow || Boolean(cleaningDraft);
  cleaningFields.disabled = !allow;
  find('cleaning-cancel').disabled = busy;
  const canSaveCleaning = allow && cleaningDraft && cleaningDraft.revision === cleaningData?.revision;
  cleaningCopy.disabled = cleaningDelete.disabled = !canSaveCleaning;
  cleaningSave.disabled = !canSaveCleaning || !cleaningDraft.task.editable;
  if (!cleaningSave.disabled && !cleaningDraft.copy) {
    const fields = cleaningInput();
    cleaningSave.disabled = Object.entries(fields).every(([key, value]) => cleaningDraft.task[key] === value);
  }
}

function showCharging(data) {
  find('charging').hidden = false;
  chargingData = data;
  const available = typeof data?.revision === 'string';
  find('charging-summary').hidden = !available;
  chargingEdit.hidden = !writable;
  const note = find('charging-note');
  note.hidden = available && writable;
  note.textContent = available ? editableHint : data?.reason || 'Робот не подтвердил интервал зарядки.';
  if (available) {
    find('charging-time').textContent = `${data.start} — ${data.end}`;
    const state = find('charging-state');
    state.className = data.enabled ? 'schedule-state-on' : 'muted';
    state.textContent = `${data.enabled ? 'Включено' : 'Выключено'}${overnight(data.start, data.end) ? ' · до следующего дня' : ''}`;
  }
  checkDraft(chargingDraft, data, 'charging');
}

function cleaningRow(task, canDelete) {
  const item = document.createElement('li');
  item.className = 'schedule-item';
  const row = document.createElement('div');
  row.className = 'schedule-row';
  const time = document.createElement('time');
  time.className = 'schedule-time';
  time.dateTime = time.textContent = task.time;
  const days = document.createElement('span');
  days.className = 'schedule-recurrence';
  days.textContent = recurrence(task);
  row.append(time, days);
  if (writable && (task.editable || canDelete)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.task = task.id;
    button.textContent = task.editable ? 'Изменить' : 'Управлять';
    button.setAttribute('aria-label', `${button.textContent} задание № ${task.id} на ${task.time}`);
    button.addEventListener('click', () => openCleaning(task, item));
    row.append(button);
  }
  item.append(row);
  return item;
}

function showCleaning(data) {
  const section = find('cleaning');
  section.hidden = false;
  cleaningData = data;
  section.append(cleaningForm);
  cleaningList.replaceChildren();
  const tasks = data?.tasks || [];
  find('cleaning-note').textContent = data?.reason || (!tasks.length
    ? 'Заданий пока нет. Первое задание можно создать в Dreamehome.'
    : !data.can_edit ? 'Изменения доступны в Dreamehome.'
      : !writable ? editableHint : 'Комнаты и режим уборки настраиваются в Dreamehome.');
  for (const [group, label] of groups) {
    const entries = tasks.filter(task => groupOf(task) === group);
    if (!entries.length) continue;
    const heading = document.createElement('h4');
    heading.className = 'schedule-group';
    heading.dataset.state = group;
    heading.textContent = label;
    const list = document.createElement('ul');
    list.className = 'schedule-list';
    list.setAttribute('aria-label', label);
    for (const task of entries) {
      const item = cleaningRow(task, data.can_delete);
      if (task.id === cleaningDraft?.task.id) item.append(cleaningForm);
      list.append(item);
    }
    cleaningList.append(heading, list);
  }
  checkDraft(cleaningDraft, data, 'cleaning');
}

function openCleaning(task, item) {
  cleaningDraft = { task, revision: cleaningData.revision, copy: false };
  cleaningForm.elements.time.value = task.time;
  cleaningForm.elements.enabled.checked = task.enabled;
  for (const input of weekdays) input.checked = task.weekdays?.[input.value] === '1';
  item.append(cleaningForm);
  cleaningForm.hidden = false;
  showCleaningEditor();
}

function showCleaningEditor() {
  const { task, copy } = cleaningDraft;
  find('cleaning-title').textContent = `${copy ? 'Копия задания' : 'Задание'} № ${task.id}`;
  find('cleaning-enable').hidden = copy;
  find('cleaning-days').hidden = task.once;
  cleaningFields.hidden = !task.editable;
  cleaningCopy.hidden = copy || !task.copyable;
  cleaningDelete.hidden = copy || !cleaningData.can_delete;
  cleaningSave.hidden = !task.editable;
  cleaningSave.textContent = copy ? 'Создать копию' : 'Сохранить';
  const note = find('cleaning-edit-note');
  note.hidden = task.editable && !copy;
  note.textContent = copy ? 'Копия сохранит комнаты и режим уборки и будет выключена. Выберите другое время или дни.'
    : 'Параметры этого задания можно изменить в Dreamehome.';
  message('', false, 'cleaning-');
  updateControls();
  (task.editable ? cleaningForm.elements.time : find('cleaning-cancel')).focus();
}

function closeCleaning() {
  cleaningDraft = null;
  cleaningForm.hidden = true;
}

function closeCharging() {
  chargingDraft = null;
  chargingForm.hidden = true;
}

async function request(payload) {
  let response;
  let data;
  try {
    response = await fetch('./api/schedules', {
      cache: 'no-store', signal: AbortSignal.timeout(30000),
      ...(payload && { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Dreame-Request': '1' },
        body: JSON.stringify(payload) }),
    });
    data = await response.json();
  } catch {
    throw new Error('Ответ не получен.');
  }
  if (!response.ok) throw new Error(data.message || 'Робот не подтвердил расписание.');
  return data;
}

async function load() {
  if (busy) return;
  busy = true;
  updateControls();
  message('Читаем расписание пылесоса…');
  try {
    const data = await request();
    writable = data.allow_commands === true;
    showCharging(data.charging);
    showCleaning(data.cleaning);
    ready = true;
    message('');
  } catch (error) {
    ready = false;
    message(`${error.message} Нажмите «Обновить», чтобы прочитать расписание ещё раз.`, true);
  } finally {
    busy = false;
    updateControls();
  }
}

async function saveSchedule(kind, payload, pending, onSaved) {
  busy = true;
  updateControls();
  message('');
  message(pending, false, `${kind}-`);
  let focus;
  try {
    const data = await request(payload);
    if (typeof data[kind]?.revision !== 'string') throw new Error('Робот не подтвердил расписание.');
    focus = onSaved(data[kind]) || refresh;
    message(payload.operation === 'delete' ? 'Задание удалено. Робот подтвердил изменение.' : data.message);
  } catch (error) {
    ready = false;
    message(`${error.message} Перед новой попыткой нажмите «Обновить».`, true, `${kind}-`);
  } finally {
    busy = false;
    updateControls();
  }
  focus?.focus();
}

function openTab() {
  if (panel.parentElement.hidden || opened || connection.dataset.online !== 'true') return;
  opened = true;
  void load();
}

document.addEventListener('dreame:connection', () => {
  opened = false;
  ready = false;
  if (connection.dataset.online !== 'true' && (chargingData || cleaningData)) {
    message('Показано последнее прочитанное расписание. Изменения будут доступны после восстановления связи.');
  }
  updateControls();
  openTab();
});
refresh.addEventListener('click', load);
new MutationObserver(openTab).observe(panel.parentElement, { attributes: true, attributeFilter: ['hidden'] });
openTab();

chargingEdit.addEventListener('click', () => {
  chargingDraft = chargingData;
  charging.enabled.checked = chargingData.enabled;
  charging.start.value = chargingData.start;
  charging.end.value = chargingData.end;
  message('', false, 'charging-');
  chargingForm.hidden = false;
  updateControls();
  charging.start.focus();
});

find('charging-cancel').addEventListener('click', () => {
  closeCharging();
  updateControls();
  (chargingEdit.disabled || chargingEdit.hidden || find('charging-summary').hidden ? refresh : chargingEdit).focus();
});

chargingForm.addEventListener('input', () => {
  if (ready && chargingDraft?.revision === chargingData?.revision) message('', false, 'charging-');
  updateControls();
});

chargingForm.addEventListener('submit', event => {
  event.preventDefault();
  if (chargingSave.disabled) return;
  const fields = interval();
  if (fields.start === fields.end) {
    message('Укажите разное время начала и конца.', true, 'charging-');
    charging.end.focus();
    return;
  }
  void saveSchedule('charging', { revision: chargingDraft.revision, ...fields }, 'Сохраняем интервал зарядки…', data => {
    closeCharging();
    showCharging(data);
    return chargingEdit;
  });
});

cleaningCopy.addEventListener('click', () => {
  cleaningDraft.copy = true;
  showCleaningEditor();
});

find('cleaning-cancel').addEventListener('click', () => {
  const button = taskButton(cleaningDraft.task.id);
  closeCleaning();
  updateControls();
  (button && !button.disabled ? button : refresh).focus();
});

cleaningForm.addEventListener('input', () => {
  if (ready && cleaningDraft?.revision === cleaningData?.revision) message('', false, 'cleaning-');
  updateControls();
});

function saveCleaning(operation, fields = {}) {
  const { task, revision } = cleaningDraft;
  const before = cleaningData.tasks;
  const pending = operation === 'delete' ? 'Удаляем задание…' : 'Сохраняем задание уборки…';
  void saveSchedule('cleaning', { kind: 'cleaning', operation, id: task.id, revision, ...fields }, pending, data => {
    closeCleaning();
    showCleaning(data);
    const added = operation === 'clone' ? data.tasks.find(entry => !before.some(old => old.id === entry.id)) : null;
    return taskButton(added?.id ?? task.id) || cleaningList.querySelector('button[data-task]');
  });
}

cleaningForm.addEventListener('submit', event => {
  event.preventDefault();
  if (cleaningSave.disabled) return;
  const fields = cleaningInput();
  if (fields.weekdays !== undefined && !fields.weekdays.includes('1')) {
    message('Выберите хотя бы один день недели.', true, 'cleaning-');
    weekdays[0].focus();
    return;
  }
  if (cleaningDraft.copy && copyConflicts(cleaningData.tasks, fields)) {
    message('На это время уже есть задание. Выберите другое время или дни.', true, 'cleaning-');
    return;
  }
  saveCleaning(cleaningDraft.copy ? 'clone' : 'update', fields);
});

cleaningDelete.addEventListener('click', async () => {
  if (cleaningDelete.disabled || cleaningDelete.hidden) return;
  const current = cleaningDraft;
  const { id, time } = current.task;
  const confirmed = await confirmMessage('Удалить задание из расписания?',
    `Задание № ${id} · ${time} · ${recurrence(current.task)}`, 'Удалить', true);
  if (confirmed && current === cleaningDraft && !cleaningDelete.disabled) saveCleaning('delete');
});
