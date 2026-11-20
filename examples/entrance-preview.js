(function () {
  "use strict";

  var config;
  try {
    config = readConfig("/etc/wb-rules/dreame-entrance-preview.json");
  } catch (err) {
    log.warning("Dreame: проверка у двери отключена, нет доступной локальной привязки.");
    return;
  }
  if (!config || config.enabled !== true) return;

  function integer(value, min, max) {
    return typeof value === "number" && Math.floor(value) === value && min <= value && value <= max;
  }

  function minutes(value) {
    return typeof value === "string" && /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value)
      ? Number(value.slice(0, 2)) * 60 + Number(value.slice(3)) : -1;
  }

  var start = minutes(config.windowStart);
  var end = minutes(config.windowEnd);
  var target = config.target || {};
  var rooms = target.kind === "rooms" && !Object.prototype.hasOwnProperty.call(target, "zone") &&
    Array.isArray(target.rooms) && target.rooms.length > 0 && target.rooms.length <= 32 &&
    target.rooms.every(function (room, index) { return integer(room, 1, 255) && target.rooms.indexOf(room) === index; });
  var zone = target.kind === "zone" && !Object.prototype.hasOwnProperty.call(target, "rooms") &&
    Array.isArray(target.zone) && target.zone.length === 4 &&
    target.zone.every(function (coord) { return integer(coord, -999999, 999999); }) &&
    target.zone[0] < target.zone[2] && target.zone[1] < target.zone[3];
  if (typeof config.contact !== "string" || !/^[^/#+\r\n\0]+\/[^/#+\r\n\0]+$/.test(config.contact) ||
      (config.openValue !== "0" && config.openValue !== "1") ||
      !integer(config.mapId, 1, 2147483647) || typeof config.revision !== "string" ||
      !/^[a-f0-9]{64}$/.test(config.revision) || !(rooms || zone) ||
      !integer(config.minOpenings, 1, 1000) || start < 0 || end <= start) {
    log.warning("Dreame: проверка у двери отключена, проверьте привязку и вечернее окно.");
    return;
  }

  var parts = config.contact.split("/");
  var topic = "/devices/" + parts[0] + "/controls/" + parts[1];
  var storage = new PersistentStorage("wb-mqtt-dreame-entrance-preview", {global: true});
  var contactBinding = config.contact + "=" + config.openValue;
  var stable = null;
  var pending = null;
  var timer = null;

  function pad(value) { return ("0" + value).slice(-2); }

  function localDay(now) {
    return String(now.getFullYear()) + pad(now.getMonth() + 1) + pad(now.getDate());
  }

  function countFor(today) {
    var activity;
    try { activity = JSON.parse(storage.activity); } catch (err) { return 0; }
    return activity && activity.day === today && activity.contact === contactBinding &&
      integer(activity.count, 0, 86400) ? activity.count : 0;
  }

  function opening(now) {
    var today = localDay(now);
    var count = countFor(today);
    if (count < 86400) {
      storage.activity = JSON.stringify({day: today, contact: contactBinding, count: count + 1});
    }
    check(now);
  }

  function check(now) {
    var minute = now.getHours() * 60 + now.getMinutes();
    if (minute < start || minute >= end) return;
    var today = localDay(now);
    var last = storage.lastAttemptDay;
    if (last !== undefined && (typeof last !== "string" || !/^[0-9]{8}$/.test(last) || last >= today)) return;
    if (countFor(today) < config.minOpenings) return;
    storage.lastAttemptDay = today;
    if (storage.lastAttemptDay !== today) return;

    var request = {request_id: "entrance-preview-" + today, action: "start", map_id: config.mapId,
      revision: config.revision, kind: target.kind};
    request[target.kind] = target[target.kind];
    spawn("/usr/bin/curl", ["--disable", "--silent", "--fail", "--noproxy", "*", "--proto", "=http",
      "--unix-socket", "/run/wb-mqtt-dreame/web.sock",
      "--connect-timeout", "2", "--max-time", "9", "--request", "POST", "--header", "Content-Type: application/json",
      "--header", "X-Dreame-Request: 1", "--data-binary", "@-", "http://localhost/dreame/api/preview"], {
      input: JSON.stringify(request),
      captureOutput: true,
      captureErrorOutput: true,
      exitCallback: function (code, output) {
        if (code === 0) {
          try {
            var reply = JSON.parse(output);
            if (reply.status === "preview" && reply.request_id === request.request_id) {
              log.info("Dreame: проверка задания у двери пройдена.");
              return;
            }
          } catch (err) { /* Report only the outcome, without local data. */ }
        }
        log.warning("Dreame: проверка у двери не подтверждена. Сверьте карту и доступность драйвера.");
      }
    });
  }

  function reset() {
    if (timer !== null) clearTimeout(timer);
    timer = pending = stable = null;
  }

  function healthy() {
    try {
      var device = getDevice(parts[0]);
      var control = getControl(config.contact);
      return !!device && !!control && device.getError() === "" && control.getError() === "";
    } catch (err) { return false; }
  }

  function sensorError(message) {
    if (message.value !== "") reset();
  }

  function contact(message) {
    var retained = message.retained;
    if (retained === true) reset();
    if ((retained !== true && retained !== false) ||
        (message.value !== "0" && message.value !== "1") || !healthy()) {
      reset();
      return;
    }
    var open = message.value === config.openValue;
    if (pending === open || (pending === null && stable === open)) return;
    if (timer !== null) clearTimeout(timer);
    pending = open;
    timer = setTimeout(function () {
      if (!healthy()) { reset(); return; }
      timer = pending = null;
      var previous = stable;
      stable = open;
      if (retained === false && previous === false && open) opening(new Date());
    }, 500);
  }

  trackMqtt(topic, contact);
  trackMqtt(topic + "/meta/error", sensorError);
  trackMqtt("/devices/" + parts[0] + "/meta/error", sensorError);
  setInterval(function () { check(new Date()); }, 15000);
})();
