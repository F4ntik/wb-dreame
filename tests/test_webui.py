import json
import re
import os
import socket
import stat
import unittest
from http.client import HTTPConnection
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from webui import start_ui


class UnixHTTPConnection(HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


@unittest.skipUnless(os.name == "posix", "Unix socket permissions require POSIX")
class GateServiceTest(unittest.TestCase):
    def test_state_and_same_origin_preview_do_not_dispatch_jobs(self):
        bridge = Mock()
        bridge.web_state.return_value = {"online": False}
        bridge.web_job.return_value = {"status": "preview"}
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "index.html").write_text("OK", encoding="utf8")
            path = directory / "web.sock"
            with patch("webui.chown") as chown:
                server = start_ui(bridge, directory, path)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            chown.assert_called_once_with(path, group="www-data")
            self.assertTrue(path.is_socket())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o660)

            def call(method, path, body=None, headers=None):
                client = UnixHTTPConnection(directory / "web.sock")
                try:
                    client.request(method, path, body, headers or {})
                    response = client.getresponse()
                    return response.status, response.read()
                finally:
                    client.close()
            client = UnixHTTPConnection(directory / "web.sock")
            try:
                client.request("GET", "/dreame/")
                response = client.getresponse()
                self.assertEqual((response.status, response.read()), (200, b"OK"))
                self.assertEqual(response.getheader("Referrer-Policy"), "no-referrer")
                policy = response.getheader("Content-Security-Policy")
                directives = dict(part.strip().split(" ", 1) for part in policy.split(";"))
                self.assertEqual(directives["connect-src"], "'self'")
                photo_root = "https://api.ru.dreametech.com/media/catalog/product/cache/74c1057f7991b4edb2bc7bdaa94de933/s/"
                sources = set(directives["img-src"].split())
                parts = {source for source in sources if source.startswith("https://fr.dreametech.com/cdn/shop/files/")}
                self.assertEqual(len(parts), 7)
                self.assertTrue(all(re.fullmatch(r"https://fr\.dreametech\.com/cdn/shop/files/[\w.-]+_160x\.(?:png|jpg)", path) for path in parts))
                self.assertEqual(sources - parts, {
                    "'self'", "data:", photo_root + "wk3xYixrkQPQ5pr/download/04.jpg", photo_root + "EPRqR47CysdkPBc/download/05.jpg",
                })
            finally:
                client.close()
            self.assertEqual(json.loads(call("GET", "/dreame/api/state")[1]), {"online": False})
            headers = {"Content-Type": "application/json", "X-Dreame-Request": "1"}
            self.assertEqual(call("POST", "/dreame/api/job", "{}", {"Content-Type": "text/plain"})[0], 403)
            self.assertEqual(call("POST", "/dreame/api/job", "{}", {**headers, "Origin": "http://another-host"})[0], 403)
            self.assertEqual(call("GET", "/dreame/../account.json")[0], 404)
            bridge.web_history.return_value = {"timestamp": 100, "svg": "<svg/>"}
            self.assertEqual(json.loads(call("GET", "/dreame/api/history/100")[1])["timestamp"], 100)
            bridge.web_history.assert_called_once_with(100, None)
            bridge.web_history.return_value = (b"fixture", "image/jpeg")
            self.assertEqual(call("GET", "/dreame/api/history/100/obstacles/item-1/photo"), (200, b"fixture"))
            self.assertEqual(call("GET", "/dreame/api/history/100/obstacles/../../file/photo")[0], 404)
            bridge.web_history.side_effect = KeyError("fixture")
            self.assertEqual(call("GET", "/dreame/api/history/200")[0], 404)
            bridge.web_job.assert_not_called()
            self.assertEqual(call("POST", "/dreame/api/preview", "{}", {**headers, "Origin": "http://localhost"})[0], 200)
            for status, code in (("confirmed", 200), ("conflict", 409), ("unknown", 409)):
                outcome = {"status": status, "key": "volume", "preferences": [{"key": "volume", "value": 20}]}
                bridge.web_preference.return_value = outcome
                http_status, body = call("POST", "/dreame/api/preference", "{}", headers)
                self.assertEqual((http_status, json.loads(body)), (code, outcome))
            self.assertEqual(call("POST", "/dreame/api/room-name", "{}", headers)[0], 404)
            bridge.web_job.assert_called_once_with({}, preview=True)
            bridge.web_schedules.return_value = {"charging": {"enabled": True}}
            schedule_path = "/dreame/api/schedules"
            self.assertEqual(json.loads(call("GET", schedule_path)[1]), {"charging": {"enabled": True}})
            self.assertEqual(call("POST", schedule_path, "{}", {"Content-Type": "application/json"})[0], 403)
            self.assertEqual(call("POST", schedule_path, "{}", {**headers, "Origin": "http://another-host"})[0], 403)
            bridge.web_schedules.assert_called_once_with()
            payload = {"enabled": False}
            self.assertEqual(call("POST", schedule_path, json.dumps(payload), headers)[0], 200)
            bridge.web_schedules.assert_called_with(payload)

    def test_existing_socket_path_is_left_untouched(self):
        with TemporaryDirectory() as folder:
            directory = Path(folder)
            path = directory / "web.sock"
            path.write_bytes(b"fixture")
            with self.assertRaises(OSError), patch("webui.chown") as chown:
                start_ui(Mock(), directory, path)
            chown.assert_not_called()
            self.assertEqual(path.read_bytes(), b"fixture")
