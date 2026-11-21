import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from config import Refused
from entrance import Entrance
from jobs import Jobs
from robot import guard, check_writes_with
from shortcuts import read_shortcut, start_shortcut
from vendor.types import DreameVacuumAction as A, DreameVacuumProperty as P, DreameVacuumStatus
from vendor.types import DreameVacuumActionMapping as ACTION_MAPPING, DreameVacuumPropertyMapping as PROPERTY_MAPPING


class ScenariosTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.robot = Mock()
        self.robot.account = {"did": "synthetic-device"}
        self.robot.device.property_mapping = PROPERTY_MAPPING
        self.robot.device.action_mapping = ACTION_MAPPING
        self.steps = [[[2, 1, 16, 1, 2, 1]]]
        self.sent = []
        self.plan = {"id": 12, "revision": "a"*64, "rooms": {"2":"Hall"}, "bounds": [0,0,500,500],
                     "floor":{"grid_size":50, "runs":[[50,0,500]]}}

        def action(siid, aiid, args, retry_count):
            self.assertEqual(retry_count, 0)
            wire = {"did":self.robot.account["did"], "siid":siid, "aiid":aiid, "in":args}
            guard("action",wire)
            with self.assertRaises(Refused):
                guard("action",wire)
            if aiid == ACTION_MAPPING[A.SHORTCUTS]["aiid"] and siid == ACTION_MAPPING[A.SHORTCUTS]["siid"]:
                cmd = json.loads(args[0]["value"])
                rows = [{"mapId":12,"id":32}] if cmd["cmd"] == "GET_COMMANDS" else self.steps
                return [{"code":0,"out":[{"value":json.dumps(rows)}]}]
            self.sent.append(wire)
            return [{"code":0}]
        self.robot.device._protocol.action.side_effect = action

    def test_cloud_shortcut_confirmation_binds_settings_and_identity(self):
        import time
        rows = [{"id":32,"name":base64.b64encode("После еды".encode()).decode()}]
        with patch("shortcuts._read", return_value={P.SHORTCUTS:json.dumps(rows)}):
            scene = read_shortcut(self.robot,32)
            self.assertEqual((scene["rooms"],scene["name"],scene["steps"]),([2],"После еды",1))
            request = {"request_id":"native-1","action":"start","kind":"shortcut","map_id":12,
                       "revision":self.plan["revision"],"shortcut_id":32,"shortcut_revision":scene["revision"]}
            snap = {"command_time":time.monotonic(),"state":"CHARGING","task":"COMPLETED","error":0,"map_id":12,
                    "command_map":True,"command_shortcut":scene,"rooms":self.plan["rooms"],"battery":80,
                    "command_settings":{"mode":"both","min_battery":15}}
            jobs=Jobs(Path(self.dir.name))
            jobs.preview(request,snap,self.plan)
            self.assertFalse(self.sent)
            self.steps[0][0][1]=3
            snap["command_shortcut"]=read_shortcut(self.robot,32)
            with self.assertRaises(Refused):
                jobs.submit(request,self.robot,snap,self.plan,True)
            self.assertFalse(self.sent)
        with self.assertRaises(Refused):
            start_shortcut(self.robot, 32)
        with check_writes_with(lambda: False),self.assertRaises(Refused):
            start_shortcut(self.robot,32)
        with check_writes_with(lambda: True):
            self.assertEqual(start_shortcut(self.robot,32),{"code":0})
        self.assertEqual([i["value"] for i in self.sent[0]["in"]],[DreameVacuumStatus.SHORTCUT.value,"32"])

    def test_entrance_survives_restart_and_rejects_stale_edits(self):
        path=Path(self.dir.name)/"entrance.json"; entrance=Entrance(path)
        data={"version":None,"zones":[[0,0,200,200],[100,0,400,200]],"map_id":12,"revision":self.plan["revision"]}
        original=entrance.save("one",self.plan,self.plan,data)
        self.assertEqual(original["zones"],Entrance(path).view("one",self.plan)["zones"])
        self.assertEqual(entrance.view("other",self.plan)["status"],"stale")
        self.assertEqual(entrance.view("one",{**self.plan,"revision":"b"*64})["status"],"stale")
        for update in [data,{**data,"version":original["version"],"zones":[[0,0,99,90]]},
                       {**data,"version":original["version"],"zones":[[0,0,200,200]]*2}]:
            with self.assertRaises(Refused):
                entrance.save("one",self.plan,self.plan,update)
        self.assertEqual(entrance.view("one",self.plan),original)

    def test_scenario_tuning_round_trip_and_older_editor(self):
        path = Path(self.dir.name) / "entrance.json"
        store = Entrance(path)
        body = {"version": None, "zones": [[0, 0, 200, 200]], "map_id": 12, "revision": self.plan["revision"]}
        tuned = {"suction": 0, "water": 2, "repeats": 3}
        saved = store.save("one", self.plan, self.plan, {**body, "settings": tuned})
        self.assertEqual(Entrance(path).view("one", self.plan)["settings"], tuned)
        edited = {**body, "version": saved["version"], "zones": [[50, 0, 250, 200]]}
        old_client = store.save("one", self.plan, self.plan, edited)
        self.assertEqual(old_client["settings"], tuned)
        edited["version"] = old_client["version"]
        for invalid in ({"suction": True}, {"water": 0}, {"repeats": 4}, {"mode": 2}, [], None):
            with self.subTest(invalid=invalid), self.assertRaises(Refused):
                store.save("one", self.plan, self.plan, {**edited, "settings": invalid})
        inherited = store.save("one", self.plan, self.plan, {**edited, "settings": {}})
        self.assertEqual(inherited["settings"], {"repeats": 1})
        item = json.loads(path.read_text(encoding="utf8"))
        del item["settings"]
        path.write_text(json.dumps(item), encoding="utf8")
        self.assertEqual(store.view("one", self.plan)["settings"], {"repeats": 1})

        clear = {"action": "clear", "map_id": 12, "revision": self.plan["revision"], "version": saved["version"]}
        with self.assertRaises(Refused):
            store.save("one", self.plan, self.plan, clear)
        clear["version"] = store.view("one", self.plan)["version"]
        self.assertEqual(store.save("one", self.plan, self.plan, clear)["status"], "empty")
        self.assertFalse(path.exists())
