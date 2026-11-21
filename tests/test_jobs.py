import json
import unittest
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace
from unittest.mock import Mock

from config import Refused
from jobs import MAX_ZONES, Jobs, blocking_error


class JobsTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = directory.name
        self.jobs = Jobs(self.directory)
        self.robot = Mock()
        self.plan = {"id": 7, "revision": "a" * 64, "rooms": {"2": "Room", "3": "Hall"},
                     "bounds": [-500, -700, 500, 700], "floor": {"grid_size": 50, "runs": [[0, -250, 250]]}}
        self.snap = {"command_time": monotonic(), "state": "CHARGING_COMPLETED", "task": "COMPLETED",
                     "battery": 95, "error": 0, "command_map": True, "command_grid": 50, "map_id": self.plan["id"],
                     "rooms": self.plan["rooms"], "command_settings": {"mode": "both", "min_battery": 15,
                         "suction": 1, "water": 2, "preserve_water": True, "cleangenius": False,
                         "customized": False, "max_suction": False}}
        self.request = {"request_id": "job-1", "action": "start", "kind": "rooms", "rooms": [3, 2],
                        "map_id": self.plan["id"], "revision": self.plan["revision"]}

    def test_preview_and_disabled_do_not_reserve_or_send(self):
        preview = self.jobs.preview(self.request, self.snap, self.plan)
        self.assertEqual(preview["request"]["rooms"], [3, 2])
        self.assertEqual(preview["request"]["repeats"], 1)
        self.assertNotIn("repeats", self.request)
        with self.assertRaisesRegex(Refused, "отключено"):
            self.jobs.submit(self.request, self.robot, self.snap, self.plan)
        self.assertEqual(self.robot.mock_calls, [])
        self.assertIsNone(self.jobs.lookup(self.request))
        self.assertIsNone(self.jobs.last)

    def test_native_wetness_uses_fresh_robot_level_without_changing_global_settings(self):
        self.snap["command_settings"].update(preserve_water=False, verified_wetness=True, water=2)
        self.jobs.preview(dict(self.request, water=3), self.snap, self.plan)
        area = {k: v for k, v in self.request.items() if k != "rooms"}
        area.update(kind="zone", zone=[-200, -200, 200, 200])
        self.jobs.preview(area, self.snap, self.plan)
        with self.assertRaises(Refused):
            self.jobs.preview(dict(area, water=3), self.snap, self.plan)

    def test_replay_and_conflict_after_durable_intent(self):
        def send(request, snap, *, allow_commands):
            self.assertIs(allow_commands, True)
            self.assertEqual(self.jobs.lookup(request)["status"], "unknown")
            return {"code": 0}
        self.robot.send_job.side_effect = send
        accepted = self.jobs.submit(self.request, self.robot, self.snap, self.plan, True)
        self.assertEqual(accepted["status"], "acknowledged")
        reopened = Jobs(self.directory)
        self.assertEqual(reopened.submit(dict(self.request, repeats=1), self.robot, {}, None), accepted)
        self.assertEqual(reopened.last, accepted)
        self.assertEqual(reopened.history, [accepted])
        with self.assertRaisesRegex(Refused, "идентификатор уже использован"):
            reopened.lookup(dict(self.request, rooms=[2, 3]))
        self.robot.send_job.assert_called_once()

    def test_timeout_and_process_exit_never_replay(self):
        self.robot.send_job.side_effect = TimeoutError
        lost = self.jobs.submit(self.request, self.robot, self.snap, self.plan, True)
        self.assertEqual(lost["status"], "unknown")
        other = dict(self.request, request_id="job-2")
        self.robot.send_job.side_effect = SystemExit
        with self.assertRaises(SystemExit):
            self.jobs.submit(other, self.robot, self.snap, self.plan, True)
        reopened = Jobs(self.directory)
        self.assertEqual(reopened.submit(other, self.robot, self.snap, self.plan, True)["status"], "unknown")
        self.assertEqual(reopened.lookup(self.request), lost)
        self.assertEqual(self.robot.send_job.call_count, 2)

    def test_invalid_targets_and_stale_inputs_reject_before_send(self):
        zone = {**self.request, "kind": "zone", "zone": [-200, -500, 200, 500]}
        del zone["rooms"]
        self.jobs.preview(zone, self.snap, self.plan)
        cases = [
            (dict(zone, zone=[-200, -500, -100, 500]), self.snap, self.plan),
            (dict(zone, zone=[-200, -900, 200, 500]), self.snap, self.plan),
            (dict(self.request, rooms=[True]), self.snap, self.plan),
            (dict(self.request, rooms=[4]), self.snap, self.plan),
            (self.request, self.snap, dict(self.plan, revision="b" * 64)),
            (self.request, dict(self.snap, command_map=False), self.plan),
            (self.request, dict(self.snap, command_time=monotonic() - 20), self.plan),
            (self.request, dict(self.snap, state="SWEEPING", task="AUTO_CLEANING"), self.plan),
        ]
        for request, snap, plan in cases:
            with self.subTest(request=request, snap=snap, plan=plan), self.assertRaises(Refused):
                self.jobs.submit(request, self.robot, snap, plan, True)
        self.assertEqual(self.robot.mock_calls, [])

    def test_multiple_zones_keep_order_and_reject_the_whole_job_on_a_bad_rectangle(self):
        first, second = [-240, -200, -50, 200], [40, -200, 250, 200]
        request = {k: v for k, v in self.request.items() if k != "rooms"}
        request.update(kind="zone", zones=[first, second])
        preview = self.jobs.preview(request, self.snap, self.plan)["request"]
        self.assertEqual(preview["zones"], [first, second])
        self.assertIsNot(preview["zones"][0], first)
        self.jobs.preview(dict(request, zones=[first] * MAX_ZONES), self.snap, self.plan)
        for zones in ([first, [-100, -200, 0, 200]], [first, [260, -200, 450, 200]],
                      [first, [0, -800, 300, 200]], [first, [False, -200, 300, 200]],
                      [first, [[-100, -200, 0, 200]]], [first, [0, -200, 10**30, 200]],
                      [first] * (MAX_ZONES + 1), [], None, first):
            with self.subTest(zones=zones), self.assertRaises(Refused):
                self.jobs.submit(dict(request, zones=zones), self.robot, self.snap, self.plan, True)
        for plan in (dict(self.plan, floor=None), dict(self.plan, floor={"grid_size": 30, "runs": []})):
            with self.assertRaisesRegex(Refused, "пол"):
                self.jobs.submit(request, self.robot, self.snap, plan, True)
        with self.assertRaises(Refused):
            self.jobs.submit(dict(request, zone=first), self.robot, self.snap, self.plan, True)
        self.assertEqual(self.robot.mock_calls, [])
        self.assertIsNone(self.jobs.last)

        self.robot.send_job.return_value = {"code": 0}
        accepted = self.jobs.submit(request, self.robot, self.snap, self.plan, True)
        self.assertEqual(accepted["request"]["zones"], [first, second])
        self.assertEqual(self.jobs.submit(request, self.robot, {}, None), accepted)
        with self.assertRaisesRegex(Refused, "идентификатор уже использован"):
            self.jobs.lookup(dict(request, zones=[second, first]))
        self.robot.send_job.assert_called_once()

    def test_drying_start_requires_completed_task_charging_dock_and_no_error(self):
        snap = dict(self.snap, state="DRYING", docked_drying=True)
        self.jobs.preview(self.request, snap, self.plan)
        for override in ({"docked_drying": None}, {"docked_drying": False}, {"task": "SEGMENT_CLEANING_PAUSED"},
                         {"error": 6}, {"state": "WASHING"}):
            with self.subTest(override=override), self.assertRaises(Refused):
                self.jobs.submit(self.request, self.robot, snap | override, self.plan, True)
        self.robot.send_job.assert_not_called()

    def test_mop_handling_code_on_a_self_wash_base_requires_clear_fault_register(self):
        for faults in (0, "0", "68", "68, 0", ""):
            self.assertEqual(blocking_error(68, faults, True), 0)
        for error, faults, base in ((68, None, True), (68, "bad", True), (68, "68,80", True),
                                    (0, "80", True), (80, "0", True), (68, 0, False), (None, "0", True)):
            self.assertNotEqual(blocking_error(error, faults, base), 0)

    def test_genius_uses_robot_settings_and_only_confirmed_rooms(self):
        smart = dict(self.snap, command_settings=self.snap["command_settings"] | {
            "cleangenius": True, "cleangenius_level": 1, "mop_installed": True, "mode": "mop_after_sweep"})
        request = dict(self.request, expected_cleangenius=1)
        self.jobs.preview(request, smart, self.plan)
        for change in ({"water": 2}, {"suction": 1}, {"repeats": 2}, {"rooms": [18]}, {"expected_cleangenius": 0}):
            with self.subTest(change=change), self.assertRaises(Refused):
                self.jobs.submit(request | change, self.robot, smart, self.plan, True)
        for change in ({"mop_installed": False}, {"cleangenius": None}, {"cleangenius_level": 2}, {"water": None}):
            with self.subTest(change=change), self.assertRaises(Refused):
                self.jobs.submit(request, self.robot, smart | {"command_settings": smart["command_settings"] | change}, self.plan, True)
        zone = dict(self.request, kind="zone", zone=[-200, -200, 200, 200])
        del zone["rooms"]
        with self.assertRaises(Refused):
            self.jobs.preview(zone, smart, self.plan)
        self.robot.send_job.assert_not_called()

    def test_modes_do_not_silently_replace_options(self):
        self.jobs.preview({key: value for key, value in dict(self.request, kind="all").items() if key != "rooms"},
                          self.snap, self.plan)
        for settings in ({"mode": None}, {"mode": "mop_after_sweep"}, {"cleangenius": True},
                         {"customized": None}, {"preserve_water": False}, {"max_suction": True}):
            snap = dict(self.snap, command_settings=self.snap["command_settings"] | settings)
            with self.subTest(settings=settings), self.assertRaises(Refused):
                self.jobs.preview(dict(self.request, water=3, suction=2), snap, self.plan)
        all_with_repeat = dict(self.request, kind="all", repeats=2)
        del all_with_repeat["rooms"]
        with self.assertRaises(Refused):
            self.jobs.preview(all_with_repeat, self.snap, self.plan)

    def test_resume_requires_actual_paused_cleanup_and_stop_needs_no_map(self):
        resume = {"request_id": "resume-1", "action": "resume"}
        with self.assertRaises(Refused):
            self.jobs.preview(resume, self.snap, None)
        paused = dict(self.snap, state="PAUSED", task="SEGMENT_CLEANING_PAUSED")
        self.jobs.preview(resume, paused, None)
        self.jobs.preview({"request_id": "stop-1", "action": "stop"}, paused, None)


class RobotJobsTest(unittest.TestCase):
    def test_zone_and_rooms_send_one_retry_free_action_with_explicit_options(self):
        from robot import Robot, guard
        from vendor.device import DreameVacuumDevice
        from vendor.types import DreameVacuumProperty as P, DreameVacuumStatus as DS

        def action(siid, aiid, params, *, retry_count):
            guard("action", {"siid": siid, "aiid": aiid})
            self.assertEqual(retry_count, 0)
            return [{"code": 0}]

        robot = Robot({"model": "dreame.vacuum.r9419a", "did": "fixture"}, ".")
        cloud = SimpleNamespace(device_id="fixture")
        robot.device = SimpleNamespace(info=SimpleNamespace(model=robot.account["model"]),
                                       _protocol=SimpleNamespace(cloud=cloud, action=Mock(side_effect=action)),
                                       property_mapping=DreameVacuumDevice.property_mapping,
                                       action_mapping=DreameVacuumDevice.action_mapping,
                                       capability=SimpleNamespace(customized_cleaning=True))
        robot.saved_map = Mock()
        robot._command_map_key = Mock(return_value="active-map")
        snap = {"command_time": monotonic(), "command_map_key": "active-map",
                "command_settings": {"suction": 1, "water": 2}}
        zone = {"action": "start", "kind": "zone", "zone": [-150, 10, 200, 300], "repeats": 2, "suction": 0}
        with self.assertRaises(Refused):
            robot.send_job(zone, snap)
        robot.device._protocol.action.assert_not_called()
        for request, status, payload in (
            (zone, DS.ZONE_CLEANING, {"areas": [[-150, 10, 200, 300, 2, 0, 2]]}),
            ({"action": "start", "kind": "zone", "zones": [[-150, 10, 200, 300], [205, 30, 460, 400]],
              "repeats": 3, "suction": 2, "water": 1}, DS.ZONE_CLEANING,
             {"areas": [[-150, 10, 200, 300, 3, 2, 1], [205, 30, 460, 400, 3, 2, 1]]}),
            ({"action": "start", "kind": "rooms", "rooms": [3, 2], "repeats": 3, "water": 1}, DS.SEGMENT_CLEANING,
             {"selects": [[3, 3, 1, 1, 1], [2, 3, 1, 1, 1]]}),
        ):
            with self.subTest(request=request):
                robot.device._protocol.action.reset_mock()
                self.assertEqual(robot.send_job(request, snap, allow_commands=True), {"code": 0})
                robot.device._protocol.action.assert_called_once()
                siid, aiid, params = robot.device._protocol.action.call_args.args
                self.assertEqual((siid, aiid), (4, 1))
                self.assertEqual(params[0], {"piid": robot.device.property_mapping[P.STATUS]["piid"], "value": status})
                self.assertEqual(json.loads(params[1]["value"]), payload)
        with self.assertRaises(Refused):
            guard("action", {"siid": 4, "aiid": 1})
        robot._command_map_key.return_value = "changed-map"
        with self.assertRaisesRegex(Refused, "Карта изменилась"):
            robot.send_job(zone, snap, allow_commands=True)
