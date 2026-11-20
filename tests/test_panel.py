import json
import unittest
from base64 import b64decode
from types import SimpleNamespace
from unittest.mock import Mock, patch
from xml.etree import ElementTree

import numpy as np

from bridge import Bridge
from panel import interactive_map, make_panel, map_snapshot, render_map_frame
from vendor.types import MapData, MapFrameType, MapImageDimensions, Path, PathType, Point, Segment


def sample_map(map_id=42):
    saved = MapData()
    saved.map_id = map_id
    saved.version = 3
    saved.dimensions = MapImageDimensions(-20, -30, 3, 5, 10)
    saved.pixel_type = np.array([[1, 1, 1, 0, 0], [1, 0, 102, 2, 255], [1, 201, 2, 0, 254]], dtype=np.uint8).T
    saved.segments = {1: Segment(1, 0, 0, 4, 4, custom_name="Кухня & <холл>")}
    saved.charger_position = Point(5, 5)
    return saved


class PanelTest(unittest.TestCase):
    @patch("overlays.time", return_value=1_800_000_000)
    def test_view_requests_live_data_and_drops_stale_room_with_position(self, now):
        bridge = Bridge()
        saved = sample_map()
        saved.saved_map = True
        frame = MapData()
        frame.saved_map_id, frame.version, frame.dimensions = saved.map_id, saved.version, saved.dimensions
        frame.saved_map_status, frame.frame_type, frame.frame_id = 2, MapFrameType.I.value, 113
        frame.timestamp_ms = int(now.return_value * 1000)
        frame.robot_segment = 1
        frame.robot_position = Point(saved.dimensions.left + 5, saved.dimensions.top + 5)
        bridge.cloud = Mock(device=SimpleNamespace(status=SimpleNamespace(current_map=frame)))
        sample = {"state": "SWEEPING_AND_MOPPING", "task": "SEGMENT_CLEANING", "fw": "test",
                  "battery": 85, "map_id": saved.map_id, "saved_map": saved, "rooms": {"1": "Кухня"}}
        with patch.object(bridge, "pub"):
            bridge.snapshot(sample)
            view = bridge.web_state()
            self.assertNotIn("_live_plan", view)
            self.assertNotIn("_footprint_bits", view["map"])
            self.assertEqual((view["live_room_id"], view["live_frame_at_ms"]), (1, frame.timestamp_ms))
            self.assertEqual([f["kind"] for f in view["overlays"]], ["robot"])
            frame.docked = True
            x, y = frame.robot_position.x, frame.robot_position.y
            frame.path = [Path(x, y, PathType.MOP), Path(x + 2, y, PathType.LINE)]
            bridge.snapshot({**sample, "state": "WASHING"})
            wash = bridge.web_state()
            self.assertEqual([f["kind"] for f in wash["overlays"]], ["path"])
            self.assertEqual((wash["live_room_id"], wash["live_frame_at_ms"]), (None, frame.timestamp_ms))
            self.assertEqual(bridge.cloud.note_live_map_demand.call_count, 1)
            bridge.snapshot(sample)
            frame.timestamp_ms -= 125_000
            stale = bridge.web_state()
            self.assertEqual((stale["overlays"], stale["live_room_id"], stale["live_frame_at_ms"]), ([], None, None))
            bridge.snapshot({**sample, "saved_map": None})
            self.assertTrue(bridge.web_state()["tracking_active"])
            self.assertEqual(bridge.cloud.note_live_map_demand.call_count, 3)
            bridge.snapshot({**sample, "state": "IDLE", "task": "COMPLETED"})
            idle = bridge.web_state()
            self.assertFalse(idle["tracking_active"])
            self.assertEqual(idle["overlays"], [])
            self.assertEqual(bridge.cloud.note_live_map_demand.call_count, 3)

    def test_template_binds_live_metrics_and_local_map(self):
        panel = make_panel()
        self.assertEqual(panel["id"], "dreame-vacuum")
        self.assertEqual(panel["name"], "Пылесос")
        self.assertTrue(panel["isSvg"])
        self.assertIs(panel["swipe"]["enable"], False)
        svg = ElementTree.fromstring(panel["svg"]["current"])
        params = panel["svg"]["params"]
        self.assertEqual({item["id"] for item in params},
                         {node.get("data-svg-param-id") for node in svg.iter() if "data-svg-param-id" in node.attrib})
        self.assertEqual({item["read"]["channel"] for item in params if item["read"]["enable"]},
                         {f"dreame_l40_1/{channel}" for channel in ("state_connection", "state_robot", "level_battery")})
        self.assertTrue(all(not item["write"]["enable"] for item in params))
        self.assertEqual(params[-1]["style"]["channel"], "dreame_l40_1/map_frame")
        self.assertIn("</div>", panel["svg"]["current"])

    def test_saved_map_draws_real_cells_and_station(self):
        svg = ElementTree.fromstring(b64decode(render_map_frame(map_snapshot(sample_map()))))
        self.assertEqual(svg.find("{*}title").text, "Сохранённая карта №42")
        labels = [node.text for node in svg.findall(".//{*}text")]
        self.assertIn("Кухня & <холл>", labels)
        self.assertIn("Зарядная станция", labels)
        self.assertIn("translate(321.60 113.24)", [node.get("transform") for node in svg.findall(".//{*}g")])
        paths = {node.attrib["d"] for node in svg.findall(".//{*}path")}
        self.assertIn("M0 0h1v1h-1zM0 1h1v1h-1zM0 2h3v1h-3z", paths)
        self.assertIn("M1 0h1v1h-1zM4 1h1v1h-1z", paths)
        self.assertIn("M2 0h1v1h-1zM2 1h2v1h-2z", paths)

    def test_mqtt_replaces_map_and_room_list_when_floorplan_changes(self):
        bridge = Bridge()
        saved = sample_map()
        status = {"state": "IDLE", "fw": "test", "battery": 75}
        with patch.object(bridge, "pub") as publish:
            def send(saved, rooms):
                publish.reset_mock()
                bridge.snapshot({**status, "map_id": saved.map_id if saved else None, "saved_map": saved, "rooms": rooms})
                changes = {topic: value for topic, value in (call.args for call in publish.call_args_list)}
                self.assertEqual(json.loads(changes["/controls/rooms"]), rooms)
                frame = changes.get("/controls/map_frame")
                return b64decode(frame).decode("utf-8") if frame else None

            first = send(saved, {"1": "Кухня", "2": "Холл"})
            self.assertIsNone(send(saved, {"1": "Кухня", "2": "Холл"}))
            saved.pixel_type[saved.pixel_type == 2] = 1
            saved.pixel_type[saved.pixel_type == 102] = 1
            merged = send(saved, {"1": "Кухня"})
            self.assertNotEqual(first, merged)
            self.assertIn("Сохранённая карта №42", merged)
            saved.segments[1].name = "Гостиная"
            self.assertIn("Гостиная", send(saved, {"1": "Гостиная"}))
            saved.custom_name = "Второй этаж"
            self.assertIn("Второй этаж", send(saved, {"1": "Гостиная"}))
            saved.segments[1].visibility = False
            self.assertNotIn("Гостиная", send(saved, {}))
            self.assertIn("Сохранённая карта №77", send(sample_map(77), {"1": "Кухня", "2": "Холл"}))
            missing = send(None, {})
            self.assertIn("Карта пока недоступна", missing)
            self.assertNotIn("Сохранённая карта №77", missing)
            self.assertIsNone(send(None, {}))

    def test_reconnect_republishes_frame_even_when_map_did_not_change(self):
        bridge = Bridge()
        saved = sample_map()
        with patch.object(bridge, "pub") as publish, patch("bridge.RetainedSession"):
            bridge.publish_map(saved)
            previous = bridge.map_frame
            bridge.connect_mqtt()
            publish.reset_mock()
            bridge.publish_map(saved)
            publish.assert_any_call("/controls/map_frame", previous)

    def test_interactive_geometry_keeps_robot_coordinates_and_invalidates_room_edits(self):
        saved = sample_map()
        plan = interactive_map(saved)
        x0, y0, x1, y1 = plan["bounds"]
        svg = ElementTree.fromstring(plan["svg"])
        self.assertEqual(svg.get("viewBox"), f"{x0} {-y1} {x1-x0} {y1-y0}")
        self.assertEqual(svg.find("{*}g").get("transform"), f"translate({x0} {-y1}) scale(10)")
        self.assertEqual(svg.find("{*}circle").get("cy"), "-5")
        self.assertEqual(plan["units_per_metre"], 1000)
        self.assertAlmostEqual(plan["room_area_m2"]["1"], 0.0005)
        self.assertEqual(sum(end - start for _, start, end in plan["floor"]["runs"]), 90)
        self.assertEqual([p.get("data-room") for p in svg.findall(".//{*}path") if p.get("data-room")], ["1"])
        self.assertIs(interactive_map(saved, plan), plan)
        saved.pixel_type[1, 1] = 1
        self.assertNotEqual(interactive_map(saved, plan)["revision"], plan["revision"])
