"""Guard the Dreame device's retained MQTT publications on the local Mosquitto broker."""
import json
import os
from contextlib import contextmanager
from threading import Event
from time import monotonic
from uuid import UUID, uuid4

import paho.mqtt.client as mqtt

from config import MODELS, Refused, atomic, read

BASE = "/devices/dreame_l40_1"
OWNER = "wb-mqtt-dreame-owner"
OWNER_FILE = "mqtt-owner.json"
LEGACY_FILE = "mqtt-legacy-preview17"
COLLISION = f"MQTT: {BASE} занят или принадлежность записей не подтверждена"
UNAVAILABLE = "Локальный MQTT не подтвердил проверку устройства"
CHANNELS = {
    "state_connection": ("Связь", "Connection", "text"),
    "state_robot": ("Состояние пылесоса", "Vacuum State", "text"),
    "level_battery": ("Заряд аккумулятора", "Battery Level", "value"),
    "fw": ("Версия прошивки", "Firmware Version", "text"),
    "map_id": ("ID сохраненной карты", "Saved Map ID", "text"),
    "rooms": ("Комнаты карты", "Map Rooms", "text"),
    "map_frame": ("Изображение карты", "Map frame", "text"),
}
TOPICS = frozenset(("/meta", "/meta/error")) | {
    "/controls/" + name + suffix for name in CHANNELS for suffix in ("", "/meta", "/meta/error")
}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def metadata(title):
    topics = {"/meta": encode({"driver": "wb-mqtt-dreame", "title": {"ru": title, "en": title}})}
    for order, (name, (ru, en, kind)) in enumerate(CHANNELS.items(), 1):
        meta = {"type": kind, "readonly": True, "order": order, "title": {"ru": ru, "en": en}}
        if name == "level_battery":
            meta.update(units="%", min=0, max=100)
        if name == "map_frame":
            meta["hidden"] = True
        topics["/controls/" + name + "/meta"] = encode(meta)
    return topics


@contextmanager
def lock_namespace(state):
    import fcntl

    fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("Другой процесс wb-mqtt-dreame работает с MQTT") from None
        yield
    finally:
        os.close(fd)


def read_owner(path):
    try:
        owner = read(path)
        if not isinstance(owner, str) or UUID(owner).hex != owner:
            raise ValueError
        return owner
    except FileNotFoundError:
        return None
    except ValueError:
        raise Refused("Повреждён локальный идентификатор владельца MQTT") from None


class RetainedSession:
    def __init__(self):
        # A new connection starts with no queued QoS 1 writes or Will.
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5,
                                  reconnect_on_failure=False)
        self.client.connect_timeout = 2
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_message = self.on_message
        self.client.on_publish = self.on_publish
        self.snapshot = {}
        self.unknown_topic = False
        self.probe = BASE + "/_wb_dreame_probe/" + uuid4().hex
        self.echo = None
        self.connected = None
        self.subscribed = None
        self.acks = {}
        self.changed = Event()
        self.wake = None
        self.owner = None
        self.properties = None
        self.watching = False

    @property
    def ready(self):
        return self.watching and self.connected is True

    def on_connect(self, client, userdata, flags, reason, properties):
        self.connected = not reason.is_failure
        self.changed.set()

    def on_disconnect(self, *_):
        self.connected = False
        self.changed.set()
        if self.wake:
            self.wake()

    def on_subscribe(self, client, userdata, mid, reasons, properties):
        self.subscribed = len(reasons) == 1 and not reasons[0].is_failure
        self.changed.set()

    def on_publish(self, client, userdata, mid, reason, properties):
        if not self.watching:
            self.acks[mid] = not reason.is_failure
            self.changed.set()
        elif reason.is_failure:
            self.connected = False
            client.disconnect()

    def on_message(self, client, userdata, message):
        if message.topic == self.probe and not message.retain:
            self.echo = message.payload
            self.changed.set()
        if not message.retain:
            return
        topic = message.topic[len(BASE):]
        props = message.properties
        tag = tuple(getattr(props, "UserProperty", ()))
        if self.watching:
            if topic not in TOPICS or not self.owns(tag):
                self.connected = False
                client.disconnect()
        elif topic not in TOPICS:
            self.unknown_topic |= bool(message.payload)
        elif message.payload:
            self.snapshot[topic] = (message.payload, tag or (() if props.isEmpty() else None))
        else:
            self.snapshot.pop(topic, None)

    def wait_for(self, condition):
        while True:
            self.changed.clear()
            if self.connected is False:
                raise Refused(UNAVAILABLE)
            if condition():
                return
            if not self.changed.wait(self.deadline - monotonic()):
                raise Refused(UNAVAILABLE)

    def inspect(self, port):
        self.deadline = monotonic() + 5
        self.client.connect("127.0.0.1", port, keepalive=10)
        self.client.loop_start()
        self.wait_for(lambda: self.connected)
        rc, _ = self.client.subscribe(BASE + "/#", options=mqtt.SubscribeOptions(qos=1, retainAsPublished=True))
        if rc != mqtt.MQTT_ERR_SUCCESS:
            raise Refused(UNAVAILABLE)
        self.wait_for(lambda: self.subscribed is not None)
        if not self.subscribed:
            raise Refused(UNAVAILABLE)
        self.fence()

    def fence(self):
        # Mosquitto queues retained deliveries before processing the following publish.
        nonce = uuid4().bytes
        if self.client.publish(self.probe, nonce, qos=1).rc != mqtt.MQTT_ERR_SUCCESS:
            raise Refused(UNAVAILABLE)
        self.wait_for(lambda: self.echo == nonce)

    def close(self):
        self.connected = False
        self.client.disconnect()
        self.client.loop_stop()

    def set_owner(self, owner):
        self.owner = owner
        self.properties = mqtt.Properties(mqtt.PacketTypes.PUBLISH)
        self.properties.UserProperty = (OWNER, owner)

    def owns(self, tag):
        return self.owner is not None and tag == ((OWNER, self.owner),)

    def publish(self, topic, payload):
        return self.client.publish(BASE + topic, payload, qos=1, retain=True, properties=self.properties)

    def publish_confirmed(self, topic, payload):
        info = self.publish(topic, payload)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise Refused(UNAVAILABLE)
        self.wait_for(lambda: info.mid in self.acks)
        if not self.acks.pop(info.mid):
            raise Refused(UNAVAILABLE)

    def claim(self, state, title, port=1883):
        self.inspect(port)
        path = state / OWNER_FILE
        legacy_flag = state / LEGACY_FILE
        owner = read_owner(path)
        if owner is not None:
            self.set_owner(owner)
        legacy = {topic: (payload, tag) for topic, (payload, tag) in self.snapshot.copy().items() if not self.owns(tag)}
        if self.unknown_topic or (legacy and not (legacy_flag.is_file() and self.matches_preview(legacy))):
            raise Refused(COLLISION)
        if owner is None:
            owner = uuid4().hex
            atomic(path, owner)
            self.set_owner(owner)
        for topic, (payload, tag) in legacy.items():
            if self.unknown_topic or self.snapshot.get(topic) != (payload, tag):
                raise Refused(COLLISION)
            self.publish_confirmed(topic, payload)
        for topic, payload in metadata(title).items():
            if self.unknown_topic or any(not self.owns(tag) and legacy.get(name) != (value, tag)
                                         for name, (value, tag) in self.snapshot.copy().items()):
                raise Refused(COLLISION)
            self.publish_confirmed(topic, payload)
        self.fence()
        self.watching = True
        if self.unknown_topic or any(not self.owns(tag) for _, tag in self.snapshot.copy().values()):
            raise Refused(COLLISION)
        legacy_flag.unlink(missing_ok=True)

    def matches_preview(self, legacy):
        if any(tag != () for _, tag in legacy.values()):
            return False
        # Only a dpkg upgrade from preview17 may adopt its complete metadata fingerprint.
        return any(all(self.snapshot.get(topic, (None,))[0] == payload.encode()
                       for topic, payload in metadata(title).items())
                   for title in {*MODELS.values(), "Dreame"})

    def start_watch(self, wake):
        self.wake = wake
        if not self.ready:
            raise Refused(UNAVAILABLE)

    def clear(self, owner, port=1883):
        self.inspect(port)
        self.set_owner(owner)
        for topic in sorted(self.snapshot.copy(), key=lambda item: (item == "/meta", item)):
            if self.owns(self.snapshot.get(topic, (None, None))[1]):
                self.publish_confirmed(topic, b"")
