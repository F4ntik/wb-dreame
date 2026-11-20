import io
import json
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from config import Refused
from registration import embedded_panel, register, retire_overview
from panel import make_panel


class RegistrationTest(unittest.TestCase):
    def test_embedded_same_origin_view_and_required_editor_fields(self):
        panel = embedded_panel()
        frame = ElementTree.fromstring(panel["svg"]["current"]).find(".//{*}iframe")
        self.assertEqual(frame.get("src"), "/dreame/")
        self.assertFalse(panel["swipe"]["enable"])
        self.assertTrue(panel["svg_fullwidth"])

    def test_legacy_dashboard_is_removed_only_for_an_exact_template(self):
        legacy = make_panel()
        for changed in (True, False):
            saved = dict(legacy, name="Custom") if changed else legacy
            with self.subTest(changed=changed), patch("pathlib.Path.read_bytes", return_value=json.dumps({"dashboards": [saved]}).encode()), patch("registration.urlopen") as rpc:
                retire_overview([{"id": legacy["id"]}])
                if changed:
                    rpc.assert_not_called()
                else:
                    request = rpc.call_args.args[0]
                    self.assertEqual(request.get_method(), "DELETE")
                    self.assertTrue(request.full_url.endswith("/dreame-vacuum"))

    def test_existing_foreign_panel_is_not_replaced(self):
        index = json.dumps({"dashboards": [{"id": "dreame-control"}]}).encode()
        with patch("registration.urlopen", side_effect=[io.BytesIO(index), io.BytesIO(b"<svg/>")]) as rpc:
            with self.assertRaises(Refused):
                register()
        self.assertTrue(all(isinstance(call.args[0], str) for call in rpc.call_args_list))
