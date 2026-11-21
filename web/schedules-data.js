const dayNames = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
const week = [1, 2, 3, 4, 5, 6, 0];

export function recurrence(task) {
  if (task.once) return 'Один раз';
  if (task.weekdays === '1111111') return 'Каждый день';
  if (task.weekdays === '0111110') return 'Пн – Пт';
  if (task.weekdays === '1000001') return 'Сб и Вс';
  return week.filter(day => task.weekdays?.[day] === '1')
    .map(day => dayNames[day]).join(', ') || 'Дни уточните в Dreamehome';
}

export function copyConflicts(tasks, { time, weekdays }) {
  return tasks.some(task => task.time === time && (task.once || !task.weekdays?.includes('1')
    || week.some(day => weekdays[day] === '1' && task.weekdays[day] === '1')));
}
