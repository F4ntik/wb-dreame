"""Read-only panel details from the device's reported properties and history cache."""
from datetime import datetime
from enum import IntEnum
from math import isfinite
from time import time

from vendor.types import CleaningStatus
from vendor.types import DreameVacuumAutoSwitchProperty as A
from vendor.types import DreameVacuumProperty as P

HISTORY_LIMIT = 30
HISTORY_STATUS = {
    CleaningStatus.INTERRUPTED: "Прервана",
    CleaningStatus.COMPLETED: "Завершена",
    CleaningStatus.MANUALLY_ENDED: "Остановлена вручную",
    CleaningStatus.FAILED: "Ошибка уборки",
}

BASE_STATES = (
    (P.SELF_WASH_BASE_STATUS, "Станция", {
        0: "Ожидание", 1: "Моет салфетки", 2: "Сушит салфетки", 3: "Робот возвращается на мойку",
        4: "Пауза", 5: "Промывка и подача воды",
        6: "Робот возвращается для слива", 7: "Робот возвращается для сушки",
    }),
    (P.AUTO_EMPTY_STATUS, "Выгрузка мусора", {
        0: "Ожидание", 1: "Выгружает мусор", 2: "Пропущена", 3: "Робот возвращается для выгрузки",
    }),
    (P.CHARGING_STATUS, "Зарядка", {
        1: "Идёт зарядка", 2: "Не заряжается", 3: "Заряжен", 5: "Возвращается на зарядку",
    }),
    (P.CLEAN_WATER_TANK_STATUS, "Бак чистой воды", {
        0: "Установлен", 1: "Не установлен", 2: "Мало воды", 3: "Проверка",
    }),
    (P.DIRTY_WATER_TANK_STATUS, "Бак грязной воды", {
        0: "Установлен", 1: "Полон или не установлен",
    }),
    (P.DUST_BAG_STATUS, "Мешок для мусора", {
        0: "Установлен", 1: "Не установлен", 2: "Требует проверки",
    }),
    (P.DETERGENT_STATUS, "Моющее средство", {
        0: "Установлено", 1: "Отключено", 2: "Мало средства",
    }),
    (P.DUST_BAG_DRYING_STATUS, "Сушка мешка", {0: "Ожидание", 1: "Сушится", 2: "На паузе"}),
    (P.STATION_DRAINAGE_STATUS, "Слив станции", {0: "Ожидание", 1: "В процессе"}),
)
FRESH_BASE_PROPERTIES = tuple(prop for prop, _, _ in BASE_STATES) + (P.DRYING_PROGRESS,)

TOTALS = (
    (P.CLEANING_COUNT, "Всего уборок", None),
    (P.TOTAL_CLEANED_AREA, "Общая площадь уборок", "м²"),
    (P.TOTAL_CLEANING_TIME, "Общее время уборок", "мин"),
)
CONSUMABLES = (
    (P.MAIN_BRUSH_LEFT, "Ресурс основной щётки"),
    (P.SIDE_BRUSH_LEFT, "Ресурс боковой щётки"),
    (P.FILTER_LEFT, "Ресурс фильтра пылесоса"),
    (P.MOP_PAD_LEFT, "Ресурс салфеток"),
    (P.SENSOR_DIRTY_LEFT, "Ресурс до очистки датчиков"),
    (P.TANK_FILTER_LEFT, "Ресурс фильтра бака"),
    (P.DETERGENT_LEFT, "Остаток моющего средства"),
    (P.SILVER_ION_LEFT, "Ресурс модуля с ионами серебра"),
    (P.WASHBOARD_CLEANING_LEFT, "Ресурс до очистки поддона"),
    (P.DIRTY_WATER_CHANNEL_DIRTY_LEFT, "Ресурс до очистки канала грязной воды"),
    (P.SCALE_INHIBITOR_LEFT, "Ресурс средства от накипи"),
)

TOGGLE = {0: "Выкл.", 1: "Вкл."}
SUCTION_LABELS = {0: "Тихая", 1: "Стандартная", 2: "Сильная", 3: "Турбо"}
SETTINGS = (
    (P.SUCTION_LEVEL, "Мощность всасывания", SUCTION_LABELS),
    (P.WATER_VOLUME, "Подача воды", {1: "Низкая", 2: "Средняя", 3: "Высокая"}),
    (A.CLEANGENIUS, "CleanGenius", {0: "Выкл.", 1: "Обычная уборка", 2: "Глубокая уборка"}),
    (A.CLEANING_ROUTE, "Маршрут уборки", {1: "Стандартный", 2: "Интенсивный", 3: "Тщательный", 4: "Быстрый"}),
    (P.MOP_WASH_LEVEL, "Промывка салфеток", {0: "Экономная", 1: "Обычная", 2: "Глубокая"}),
    (P.CARPET_BOOST, "Усиление на ковре", TOGGLE),
    (P.RESUME_CLEANING, "Возобновление после зарядки", TOGGLE),
    (P.CHILD_LOCK, "Защита от детей", TOGGLE),
    (P.AUTO_WATER_REFILLING, "Автодолив воды", TOGGLE),
)
CLEANING_MODES = {
    0: "Сухая уборка", 1: "Влажная уборка", 2: "Сухая и влажная одновременно", 3: "Сначала сухая, затем влажная",
}


def _number(value, maximum=None):
    if type(value) not in (int, float) or (type(value) is float and not isfinite(value)) or value < 0:
        return None
    return value if maximum is None or value <= maximum else None


def _choice(value, labels):
    if type(value) is int or isinstance(value, IntEnum):
        return labels.get(value)
    return None


def history_records(cache):
    """Pair public rows with their server-only archive references, indexed by start time."""
    if not isinstance(cache, (list, tuple)):
        return {}

    latest_allowed = time() + 300
    records = {}
    references = {}
    for item in tuple(cache):
        date = getattr(item, "date", None)
        if not isinstance(date, datetime):
            continue
        try:
            # CleaningHistory uses datetime.fromtimestamp, so convert back in the same local timezone.
            timestamp = int(date.timestamp())
        except (ValueError, OverflowError, OSError):
            continue
        if not 0 < timestamp <= latest_allowed:
            continue

        record = {"timestamp": timestamp}
        # The vendor parser gives absent measurements the same zero as a sub-minute run.
        for prop, field in (("cleaning_time", "duration_min"), ("cleaned_area", "area_m2")):
            value = _number(getattr(item, prop, None))
            if value:
                record[field] = value
        cleaning_status = getattr(item, "cleaning_status", None)
        status = _choice(cleaning_status, HISTORY_STATUS)
        completed = getattr(item, "completed", None)
        if status is not None:
            record["status"] = status
            record["completed"] = cleaning_status == CleaningStatus.COMPLETED
        elif completed is True:
            record["status"] = HISTORY_STATUS[CleaningStatus.COMPLETED]
            record["completed"] = True
        for prop, field, labels in (
            ("status", "task_label", {2: "Вся квартира", 18: "По комнатам", 19: "По зоне", 20: "Вокруг точки"}),
            ("cleanup_method", "method_label", {0: "Обычная", 1: "По настройкам комнат", 2: "CleanGenius", 3: "Удаление пятен"}),
            ("suction_level", "suction_label", SUCTION_LABELS),
        ):
            label = _choice(getattr(item, prop, None), labels)
            if label:
                record[field] = label
        name, key = getattr(item, "object_name", None), getattr(item, "key", None)
        has_reference = isinstance(name, str) and bool(name) and (key is None or isinstance(key, str))
        if len(record) == 1 and not has_reference:
            continue
        if has_reference:
            references.setdefault(timestamp, set()).add((name, key))
        if timestamp not in records or len(record) > len(records[timestamp]):
            records[timestamp] = record
    result = {}
    for stamp in sorted(records, reverse=True)[:HISTORY_LIMIT]:
        refs = references.get(stamp, ())
        ref = next(iter(refs)) if len(refs) == 1 else None
        result[stamp] = ({**records[stamp], "map_available": ref is not None}, ref)
    return result


def _history(cache, cleaning_count):
    records = history_records(cache)
    available = bool(records) or (isinstance(cache, (list, tuple)) and not cache) or (cache is None and cleaning_count == 0)
    return [row for row, _ in records.values()], available


def _add(rows, label, value, unit=None, *, key=None):
    if value is not None:
        row = {"label": label, "value": value}
        if key is not None:
            row["key"] = key.name.lower()
        if unit is not None:
            row["unit"] = unit
        rows.append(row)


def read_details(device, *, current=None):
    """Return panel details without any network requests. Current base reads override cache."""
    cached_property = getattr(device, "get_property", None)

    def get(prop):
        if current is not None and prop in current:
            return current[prop]
        if cached_property is not None:
            try:
                return cached_property(prop)
            except (TypeError, ValueError):
                return None
        return None

    metrics = []
    for prop, label, labels in BASE_STATES:
        _add(metrics, label, _choice(get(prop), labels), key=prop)
    if get(P.SELF_WASH_BASE_STATUS) == 2:
        _add(metrics, "Прогресс сушки салфеток", _number(get(P.DRYING_PROGRESS), 100), "%", key=P.DRYING_PROGRESS)
    for prop, label, unit in TOTALS:
        _add(metrics, label, _number(get(prop)), unit, key=prop)
    for prop, label in CONSUMABLES:
        _add(metrics, label, _number(get(prop), 100), "%", key=prop)

    settings = []
    if type(get(P.CLEANING_MODE)) is int:
        _add(settings, "Режим уборки", _choice(device.status.cleaning_mode, CLEANING_MODES))
    for prop, label, labels in SETTINGS:
        value = get(prop)
        if labels is TOGGLE and type(value) is bool:
            value = int(value)
        _add(settings, label, _choice(value, labels))

    history, available = _history(getattr(device.status, "_cleaning_history", None), _number(get(P.CLEANING_COUNT)))
    return {"history": history, "history_available": available, "metrics": metrics, "settings": settings}
