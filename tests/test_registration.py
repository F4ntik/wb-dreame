import io
import json
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from config import Refused
from registration import embedded_panel, register


class RegistrationTest(unittest.TestCase):
    def test_embedded_same_origin_view_and_required_editor_fields(self):
        panel = embedded_panel()
        frame = ElementTree.fromstring(panel["svg"]["current"]).find(".//{*}iframe")
        self.assertEqual(frame.get("src"), "/dreame/")
        self.assertFalse(panel["swipe"]["enable"])
        self.assertTrue(panel["svg_fullwidth"])

    def test_existing_foreign_panel_is_not_replaced(self):
        index = json.dumps({"dashboards": [{"id": "dreame-control"}]}).encode()
        with patch("registration.urlopen", side_effect=[io.BytesIO(index), io.BytesIO(b"<svg/>")]) as rpc:
            with self.assertRaises(Refused):
                register()
        self.assertTrue(all(isinstance(call.args[0], str) for call in rpc.call_args_list))
