import { test } from 'node:test';
import assert from 'node:assert/strict';
import { resourceReadings } from '../web/resources.js';

const row = (key, value, unit = '', extra = {}) => ({ key, label: key, value, unit, ...extra });
const text = value => value?.replaceAll('\u00a0', ' ');

test('исчерпанные расходники и явные проблемы станции предшествуют малому остатку', () => {
  const { items, equipment } = resourceReadings([
    row('main_brush_left', 76, '%'), row('sensor_dirty_left', 0, '%'), row('mop_pad_left', 15, '%'),
    row('filter_left', 16, '%'), row('dust_bag_status', 'Не установлен'),
    row('dirty_water_tank_status', 'Полон или не установлен'), row('clean_water_tank_status', 'Установлен'),
  ]);
  assert.deepEqual(items.map(i => i.band), ['due', 'due', 'due', 'low', 'normal', 'normal']);
  const byKey = new Map(items.map(item => [item.key, item]));
  assert.equal(byKey.get('sensor_dirty_left').action, 'Очистить');
  assert.equal(byKey.get('dust_bag_status').action, 'Установить мешок');
  assert.equal(byKey.get('dirty_water_tank_status').value, 'Полон или не установлен');
  assert.deepEqual(equipment.dock.status.map(i => [i.key, i.attention]), [
    ['clean_water_tank_status', false], ['dirty_water_tank_status', true],
  ]);
});

test('проценты и время сохраняют единицы, прогноз не подменяет дни Dreame', () => {
  const { items } = resourceReadings([
    row('main_brush_left', 35, '%', { hours: 100, estimated_days: 40 }),
    row('filter_left', 52, '%', { hours: 86 }),
    row('mop_pad_left', 3, '%', { days: 1, estimated_days: 9 }),
    row('sensor_dirty_left', 0, 'ч', { hours: 0 }),
    row('scale_inhibitor_left', 20, 'дн.', { days: 20 }),
    row('side_brush_left', 18, 'ч', { hours: 18, estimated_days: 8 }),
    row('detergent_left', 87, '%', { days: null }), row('tank_filter_left', 22, '%', { estimated_days: -5 }),
  ]);
  const get = key => items.find(i => i.key === key);
  assert.equal(text(get('main_brush_left').duration), '≈ 40 дн.');
  assert.equal(text(get('main_brush_left').note[0]), '100 ч работы');
  assert.equal(text(get('filter_left').duration), '86 ч работы');
  assert.equal(text(get('mop_pad_left').duration), '1 дн.');
  assert.equal(get('mop_pad_left').forecast, false);
  assert.equal(text(get('sensor_dirty_left').value), '0 ч работы');
  assert.equal(get('sensor_dirty_left').percent, null);
  assert.equal(get('sensor_dirty_left').band, 'due');
  assert.equal(text(get('scale_inhibitor_left').value), '20 дн.');
  assert.equal(get('scale_inhibitor_left').duration, undefined);
  assert.equal(text(get('side_brush_left').value), '18 ч работы');
  assert.equal(text(get('side_brush_left').duration), '≈ 8 дн.');
  assert.equal(get('detergent_left').duration, undefined);
  assert.equal(get('tank_filter_left').duration, undefined);
});

test('нулевой прогноз не поднимает деталь в раздел обслуживания, нулевой срок Dreame поднимает', () => {
  const { items } = resourceReadings([
    row('main_brush_left', 20, '%', { estimated_days: 0, hours: 2 }),
    row('scale_inhibitor_left', 20, '%', { days: 0 }),
  ]);
  assert.deepEqual(items.map(i => [i.key, i.band, text(i.duration)]), [
    ['scale_inhibitor_left', 'due', '0 дн.'], ['main_brush_left', 'normal', '≈ 0 дн.'],
  ]);
});

test('статусы мешка и средства видны без процента, статус и остаток средства объединяются', () => {
  const { items } = resourceReadings([row('dust_bag_status', 'Установлен'), row('detergent_status', 'Мало средства')]);
  assert.deepEqual(items.map(i => [i.key, i.band, i.value, i.percent]), [
    ['detergent_left', 'due', 'Мало средства', null], ['dust_bag_status', 'normal', 'Установлен', null],
  ]);
  const { items: combined } = resourceReadings([row('detergent_status', 'Мало средства'), row('detergent_left', 12, '%')]);
  assert.equal(combined.length, 1);
  assert.equal(combined[0].band, 'due');
  assert.equal(combined[0].percent, 12);
  assert.deepEqual(combined[0].note, ['Мало средства']);
});

test('неизвестное показание, итоги и режимы станции остаются видны, некорректные строки пропускаются', () => {
  const { items, equipment, statistics } = resourceReadings([row('filter_left', 110, '%'), row('mop_pad_left', '', '%'),
    row('total_cleaning_time', 123, 'мин'), row('charging_status', 'Заряжен'), row('self_wash_base_status', 'Ожидание'),
    row('dust_bag_drying_status', 'Сушится'), row('dust_bag_drying_status', 'На паузе'), null]);
  assert.deepEqual(items, []);
  assert.equal(text(statistics[0].value), '2 ч 3 мин');
  assert.equal(equipment.robot.primary, 'Заряжен');
  assert.equal(equipment.dock.primary, 'Ожидание');
  assert.equal(equipment.dock.status[0].value, 'Сушится');
  assert.deepEqual(resourceReadings(null).items, []);
});
