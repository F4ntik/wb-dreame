import json
import unittest
from datetime import datetime
from time import time
from types import SimpleNamespace
from unittest.mock import Mock

from details import FRESH_BASE_PROPERTIES, HISTORY_LIMIT, history_records, read_details
from robot import Robot
from vendor.types import CleaningHistory
from vendor.types import DreameVacuumCleaningMode as Mode
from vendor.types import DreameVacuumProperty as P
from vendor.types import DreameVacuumPropertyMapping as MAPPING


def cleaning(timestamp, **values):
    fields = {P.CLEANING_START_TIME: timestamp}
    fields.update({P[name.upper()]: value for name, value in values.items()})
    return CleaningHistory([{**MAPPING[prop], "value": value} for prop, value in fields.items()], MAPPING)


def fixture(properties, history=None, mode=None):
    return SimpleNamespace(
        get_property=properties.get, status=SimpleNamespace(_cleaning_history=history, cleaning_mode=mode)
    )


class DetailsTest(unittest.TestCase):
    def test_history_has_real_units_and_filters_invalid_or_duplicate_events(self):
        newest = int(time()) - 240
        history = [
            cleaning(newest - 120, cleaned_area=18, clean_log_status=0),
            cleaning(newest, cleaning_time=31, cleaned_area=27, clean_log_status=1),
            cleaning(newest, cleaning_time=31),
            cleaning(newest - 40),
            cleaning(0, cleaning_time=1),
            cleaning(newest + 600, cleaning_time=1),
            SimpleNamespace(date="bad"),
        ]
        data = read_details(fixture({}, history))
        self.assertTrue(data["history_available"])
        self.assertEqual(data["history"], [
            {"timestamp": newest, "duration_min": 31, "area_m2": 27, "status": "Завершена", "completed": True, "map_available": False},
            {"timestamp": newest - 120, "area_m2": 18, "status": "Прервана", "completed": False, "map_available": False},
        ])
        self.assertEqual(json.loads(json.dumps(data, allow_nan=False)), data)

    def test_archive_is_bound_by_event_time_and_conflicting_references_stay_unavailable(self):
        stamp = int(time()) - 90
        event = cleaning(stamp, clean_log_file_name="one,private-key", status=18, suction_level=2)
        duplicate = cleaning(stamp, clean_log_file_name="two", cleaning_time=10)
        row, ref = history_records([event])[stamp]
        self.assertEqual(ref, ("one", "private-key"))
        self.assertTrue(row["map_available"])
        self.assertEqual((row["task_label"], row["suction_label"]), ("По комнатам", "Сильная"))
        self.assertNotIn("private-key", json.dumps(row))
        row, ref = history_records([event, duplicate, event])[stamp]
        self.assertFalse(row["map_available"])
        self.assertIsNone(ref)

    def test_archives_resolve_after_reordering_and_drop_cache_access_when_record_disappears(self):
        stamp = int(time()) - 600
        robot = Robot({}, ".")
        device = robot.device = Mock()
        robot.identity = Mock()
        event = cleaning(stamp, clean_log_file_name="archive,key")
        device.status._cleaning_history = [event]
        archive = SimpleNamespace(history_map=True)
        device._map_manager.get_history_map.return_value = archive
        self.assertIs(robot.archived_map(stamp), archive)
        device.status._cleaning_history = [cleaning(stamp + 1, clean_log_file_name="other"), event]
        self.assertIs(robot.archived_map(stamp), archive)
        device._map_manager.get_history_map.assert_called_once_with("archive", "key")
        device.status._cleaning_history = []
        with self.assertRaises(KeyError):
            robot.archived_map(stamp)

    def test_history_is_limited_and_empty_is_distinguished_from_unavailable(self):
        now = int(time())
        history = [cleaning(now - offset, cleaning_time=1) for offset in range(1, HISTORY_LIMIT + 5)]
        result = read_details(fixture({}, history))
        self.assertEqual(len(result["history"]), HISTORY_LIMIT)
        self.assertEqual(result["history"][-1]["timestamp"], now - HISTORY_LIMIT)
        self.assertTrue(read_details(fixture({}, []))["history_available"])
        self.assertTrue(read_details(fixture({P.CLEANING_COUNT: 0}))["history_available"])
        self.assertFalse(read_details(fixture({}, [SimpleNamespace(date=datetime.min)]))["history_available"])
        self.assertFalse(read_details(fixture({}))["history_available"])

    def test_only_reported_valid_metrics_and_settings_are_displayed(self):
        properties = {
            P.CLEANING_COUNT: 24, P.TOTAL_CLEANING_TIME: 220, P.TOTAL_CLEANED_AREA: 108,
            P.MAIN_BRUSH_LEFT: 0, P.SIDE_BRUSH_LEFT: 90, P.FILTER_LEFT: 101,
            P.MOP_PAD_LEFT: float("nan"), P.DETERGENT_LEFT: -1, P.SELF_WASH_BASE_STATUS: 2,
            P.DRYING_PROGRESS: 0, P.DUST_BAG_STATUS: 2, P.WATER_VOLUME: 2, P.SUCTION_LEVEL: 0,
            P.CLEANING_MODE: 768, P.CHILD_LOCK: False, P.MOP_WASH_LEVEL: -1,
        }
        data = read_details(fixture(properties, [None], Mode.MOPPING_AFTER_SWEEPING))
        self.assertFalse(data["history_available"])
        self.assertEqual({row["label"]: (row["value"], row.get("unit")) for row in data["metrics"]}, {
            "Станция": ("Сушит салфетки", None),
            "Мешок для мусора": ("Требует проверки", None),
            "Прогресс сушки салфеток": (0, "%"),
            "Всего уборок": (24, None),
            "Общая площадь уборок": (108, "м²"),
            "Общее время уборок": (220, "мин"),
            "Ресурс основной щётки": (0, "%"),
            "Ресурс боковой щётки": (90, "%"),
        })
        self.assertEqual({row["label"]: row["value"] for row in data["settings"]}, {
            "Режим уборки": "Сначала сухая, затем влажная",
            "Мощность всасывания": "Тихая",
            "Подача воды": "Средняя",
            "Защита от детей": "Выкл.",
        })

    def test_consumable_time_uses_vendor_units_and_calendar_estimates_need_observed_usage(self):
        now = int(time())
        # The old boundary row covers ten full days. Only newer cleaning time counts.
        history = [cleaning(now - 36 * 3600 - day * 86400, cleaning_time=120) for day in (0, 2, 4, 6, 8)]
        history.append(cleaning(now - 10 * 86400 - 3600, cleaning_time=90))
        readings = {P.MAIN_BRUSH_LEFT: 25, P.MAIN_BRUSH_TIME_LEFT: 75,
                    P.FILTER_TIME_LEFT: 0, P.SIDE_BRUSH_LEFT: 86,
                    P.SCALE_INHIBITOR_LEFT: 90, P.SCALE_INHIBITOR_TIME_LEFT: 980,
                    P.WASHBOARD_CLEANING_TIME_LEFT: 0, P.DETERGENT_TIME_LEFT: -1,
                    P.MOP_PAD_TIME_LEFT: 7, P.TANK_FILTER_TIME_LEFT: False}
        def metrics(events):
            return {r["key"]: r for r in read_details(fixture(readings, events))["metrics"]}
        rows = metrics(history)
        brush = rows["main_brush_left"]
        self.assertEqual((brush["value"], brush["unit"], brush["hours"], brush["estimated_days"]), (25, "%", 75, 75))
        self.assertEqual((rows["filter_left"]["unit"], rows["filter_left"]["estimated_days"]), ("ч", 0))
        self.assertNotIn("hours", rows["side_brush_left"])
        self.assertEqual(rows["scale_inhibitor_left"]["days"], 980)
        self.assertNotIn("estimated_days", rows["scale_inhibitor_left"])
        self.assertEqual((rows["washboard_cleaning_left"]["days"], rows["washboard_cleaning_left"]["unit"]), (0, "дн."))
        self.assertNotIn("estimated_days", rows["mop_pad_left"])
        self.assertNotIn("tank_filter_left", rows)
        self.assertNotIn("detergent_left", rows)
        missing_duration = [*history, cleaning(now - 3600, cleaned_area=4)]
        for incomplete in (None, history[:2], missing_duration, history[-2:]):
            self.assertNotIn("estimated_days", metrics(incomplete)["main_brush_left"])

    def test_observe_refreshes_known_station_properties_in_the_same_read(self):
        properties = {P.DUST_BAG_STATUS: 1, P.SELF_WASH_BASE_STATUS: 2, P.DRYING_PROGRESS: 30}
        device = fixture(properties)
        device.data = {prop.value: value for prop, value in properties.items()}
        device.info = SimpleNamespace(model="dreame.vacuum.r9419a", firmware_version="test")
        device.status.current_map = device.status.selected_map = None
        device.property_mapping = MAPPING
        device._protocol = Mock()
        device._protocol.cloud = SimpleNamespace(device_id="fixture", auth_key=None)
        observed = {P.STATE: 6, P.TASK_STATUS: 0, P.BATTERY_LEVEL: 84, P.ERROR: 0, P.SELF_WASH_BASE_STATUS: 0}
        device._protocol.get_properties.return_value = [
            {**MAPPING[prop], "code": 0, "value": value} for prop, value in observed.items()
        ]

        robot = Robot({"model": device.info.model, "did": "fixture"}, ".")
        robot.device = device
        result = robot.observe()
        self.assertEqual(result["battery"], 84)
        self.assertEqual(result["details"]["metrics"], [{"key": "self_wash_base_status", "label": "Станция", "value": "Ожидание"}])
        device._protocol.get_properties.assert_called_once()
        args, kwargs = device._protocol.get_properties.call_args
        self.assertEqual(kwargs, {"retry_count": 0, "timeout": 6})
        self.assertEqual({int(item["did"]) for item in args[0]}, {prop.value for prop in (*observed, *properties)})
        self.assertLessEqual(len(FRESH_BASE_PROPERTIES) + 4, 15)

        device.capability = SimpleNamespace(cleangenius=True, auto_switch_settings=True)
        device._protocol.get_properties.return_value.append(
            {**MAPPING[P.AUTO_SWITCH_SETTINGS], "code": 0, "value": '[{"k":"SmartHost","v":1}]'})
        smart = robot.observe()
        self.assertEqual(smart["cleangenius"], 1)
        self.assertIn({"label": "CleanGenius", "value": "Обычная уборка"}, smart["details"]["settings"])
