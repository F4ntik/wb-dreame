import json
import os
import shutil
import socket
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import patch
from uuid import uuid4

import paho.mqtt.publish as mqtt_publish

from config import Refused
from mqtt_namespace import BASE, LEGACY_FILE, OWNER_FILE, RetainedSession, metadata, read_owner

BROKER = os.environ.get("WB_MQTT_TEST_BROKER") or shutil.which("mosquitto")


@unittest.skipUnless(BROKER, "Локальный mosquitto не найден")
class RetainedNamespaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        if os.name == "nt":
            # Production uses Linux directory fsync. Keep real MQTT I/O on Windows.
            self.enterContext(patch("mqtt_namespace.atomic", lambda p, v: p.write_text(json.dumps(v))))
        with socket.socket() as available:
            available.bind(("127.0.0.1", 0))
            self.port = available.getsockname()[1]
        broker = subprocess.Popen([BROKER, "-p", str(self.port)], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_broker, broker)
        deadline = time.monotonic() + 3
        while True:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.25):
                    break
            except OSError:
                if broker.poll() is not None or time.monotonic() > deadline:
                    self.fail("Тестовый Mosquitto не запустился")
                time.sleep(0.02)

    def stop_broker(self, broker):
        broker.terminate()
        try:
            broker.wait(timeout=3)
        except subprocess.TimeoutExpired:
            broker.kill()
            broker.wait(timeout=3)

    def session(self):
        session = RetainedSession()
        self.addCleanup(session.close)
        return session

    def publish(self, topic, payload):
        mqtt_publish.single(BASE + topic, payload, qos=1, retain=True, hostname="127.0.0.1", port=self.port)

    def snapshot(self):
        session = self.session()
        session.inspect(self.port)
        return dict(session.snapshot), session.unknown_topic

    def claim(self, title="Dreame"):
        session = self.session()
        session.claim(self.state, title, self.port)
        session.close()
        return session

    def test_foreign_partial_namespace_is_untouched(self):
        for topic in ("/controls/level_battery", "/controls/foreign/meta"):
            with self.subTest(topic=topic):
                self.publish(topic, "foreign")
                before = self.snapshot()
                with self.assertRaisesRegex(Refused, "занят"):
                    self.claim()
                self.assertFalse((self.state / OWNER_FILE).exists())
                self.assertEqual(self.snapshot(), before)
                self.publish(topic, b"")

    def test_owned_reconnect_and_cleanup_preserve_overwritten_foreign_entries(self):
        self.claim()
        owner = read_owner(self.state / OWNER_FILE)
        second = self.claim("Dreame L40s Pro Ultra")
        self.assertEqual(second.owner, owner)
        current, _ = self.snapshot()
        self.assertEqual(json.loads(current["/meta"][0])["title"]["ru"], "Dreame L40s Pro Ultra")
        self.assertTrue(all(second.owns(tag) for _, tag in current.values()))

        self.publish("/controls/fw/meta", "other driver")
        with self.assertRaisesRegex(Refused, "занят"):
            self.claim()
        self.publish("/controls/foreign/meta", "other control")
        cleaner = self.session()
        cleaner.clear(owner, self.port)
        foreign, unknown = self.snapshot()
        self.assertEqual(foreign, {"/controls/fw/meta": (b"other driver", ())})
        self.assertTrue(unknown)

    def test_live_collision_stops_publications(self):
        writer = self.session()
        writer.claim(self.state, "Dreame", self.port)
        disconnected = Event()
        writer.start_watch(disconnected.set)
        self.assertTrue(writer.ready)
        self.publish("/controls/state_robot", "foreign")
        self.assertTrue(disconnected.wait(2))
        self.assertFalse(writer.ready)
        current, _ = self.snapshot()
        self.assertEqual(current["/controls/state_robot"], (b"foreign", ()))

    def test_only_preview17_upgrade_may_adopt_complete_legacy_metadata(self):
        legacy = metadata("Dreame L40s Pro Ultra")
        for topic, payload in legacy.items():
            self.publish(topic, payload)
        self.publish("/controls/level_battery", "97")
        before = self.snapshot()
        with self.assertRaisesRegex(Refused, "занят"):
            self.claim()
        self.assertEqual(self.snapshot(), before)

        flag = self.state / LEGACY_FILE
        flag.touch()
        self.publish("/controls/fw/meta", b"")
        before = self.snapshot()
        with self.assertRaisesRegex(Refused, "занят"):
            self.claim()
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(flag.exists())
        self.assertFalse((self.state / OWNER_FILE).exists())

        self.publish("/controls/fw/meta", legacy["/controls/fw/meta"])
        interrupted = self.session()
        publish = interrupted.publish_confirmed
        sent = []

        def interrupted_publish(topic, payload):
            if sent:
                raise Refused("Имитация потери подтверждения")
            publish(topic, payload)
            sent.append(topic)

        with patch.object(interrupted, "publish_confirmed", side_effect=interrupted_publish):
            with self.assertRaisesRegex(Refused, "потери подтверждения"):
                interrupted.claim(self.state, "Dreame L40s Pro Ultra", self.port)
        interrupted.close()
        self.assertTrue(flag.exists())
        current, _ = self.snapshot()
        self.assertTrue(interrupted.owns(current[sent[0]][1]))
        self.assertTrue(any(tag == () for _, tag in current.values()))
        self.assertEqual(current["/controls/level_battery"][0], b"97")

        migrated = self.claim("Dreame L40s Pro Ultra")
        current, _ = self.snapshot()
        self.assertEqual(current["/controls/level_battery"][0], b"97")
        self.assertTrue(all(migrated.owns(tag) for _, tag in current.values()))
        self.assertFalse(flag.exists())

    def test_inspection_timeout_at_probe_never_claims_namespace(self):
        self.publish("/meta", "foreign")
        session = self.session()
        def no_echo(client, userdata, message):
            if message.topic == session.probe:
                session.deadline = time.monotonic()
                session.changed.set()
            else:
                session.on_message(client, userdata, message)

        session.client.on_message = no_echo
        with patch.object(session.client, "publish", wraps=session.client.publish) as out:
            with self.assertRaisesRegex(Refused, "не подтвердил"):
                session.claim(self.state, "Dreame", self.port)
            self.assertEqual([call.args[0] for call in out.call_args_list], [session.probe])
        self.assertFalse((self.state / OWNER_FILE).exists())
        self.assertEqual(self.snapshot(), ({"/meta": (b"foreign", ())}, False))

    def test_cleanup_uses_local_owner_even_when_device_root_is_missing(self):
        self.claim()
        owner = read_owner(self.state / OWNER_FILE)
        self.publish("/meta", b"")
        self.session().clear(uuid4().hex, self.port)
        current, _ = self.snapshot()
        self.assertTrue(current)
        self.session().clear(owner, self.port)
        self.assertEqual(self.snapshot(), ({}, False))
