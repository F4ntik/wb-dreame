const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, 'entrance-preview.js'), 'utf8');
const fixture = {
  enabled: true, contact: 'fixture/Door', openValue: '1', mapId: 429, revision: 'a'.repeat(64),
  target: { kind: 'rooms', rooms: [81] }, minOpenings: 2, windowStart: '19:00', windowEnd: '21:00',
};
const topic = '/devices/fixture/controls/Door';

function stand(config = fixture, storage = {}, hour = 19) {
  const messages = {};
  const timers = new Map();
  const http = [];
  let healthy = true;
  let now = new Date(2030, 8, 12, hour, 10).getTime();
  let nextTimer = 0;
  const control = () => ({ getError: () => healthy ? '' : 'r' });
  vm.runInNewContext(script, {
    Date: function () { return new Date(now); },
    readConfig: () => config,
    PersistentStorage: function () { return storage; },
    getDevice: control,
    getControl: control,
    trackMqtt: (name, callback) => { messages[name] = callback; },
    setTimeout: (callback, delay) => {
      timers.set(++nextTimer, { callback, at: now + delay });
      return nextTimer;
    },
    setInterval: (callback, delay) => {
      timers.set(++nextTimer, { callback, at: now + delay, interval: delay });
      return nextTimer;
    },
    clearTimeout: (timer) => timers.delete(timer),
    spawn: (file, args, options) => {
      assert.equal(file, '/usr/bin/curl');
      assert.equal(args.at(-1), 'http://localhost/dreame/api/preview');
      assert.ok(args.includes('--unix-socket'));
      assert.equal(args[args.indexOf('--unix-socket') + 1], '/run/wb-mqtt-dreame/web.sock');
      assert.ok(args.includes('POST'));
      assert.ok(args.includes('X-Dreame-Request: 1'));
      const request = JSON.parse(options.input);
      assert.equal(storage.lastAttemptDay, request.request_id.slice(-8));
      http.push(request);
      options.exitCallback(22, '');
    },
    log: { info() {}, warning() {} },
  });
  function wait(ms = 600) {
    now += ms;
    for (const [timer, task] of timers) {
      if (task.at > now) continue;
      if (task.interval) task.at = now + task.interval;
      else timers.delete(timer);
      task.callback();
    }
  }
  function emit(value, retained = false) {
    messages[topic]({ value, retained });
  }
  function cycle() { emit(config.openValue === '1' ? '0' : '1'); wait(); emit(config.openValue); wait(); }
  return { messages, http, emit, wait, cycle, storage,
    setLocalTime: (day, hour, minute = 0) => { wait(new Date(2030, 8, day, hour, minute).getTime() - now); },
    flagError: () => { healthy = false; messages[topic + '/meta/error']({ value: 'r' }); },
    recover: () => { healthy = true; messages[topic + '/meta/error']({ value: '' }); },
  };
}

test('disabled or ambiguous binding subscribes to nothing', () => {
  for (const config of [null, { ...fixture, enabled: false }, { ...fixture, revision: '' },
    { ...fixture, target: { ...fixture.target, zone: [0, 0, 100, 100] } }]) {
    assert.deepEqual(stand(config).messages, {});
  }
});

test('retained, bounce, duplicate and errors do not count as openings', () => {
  const kit = stand();
  kit.emit('0', true);
  kit.wait();
  kit.emit('1', true);
  kit.wait();
  kit.emit('0');
  kit.wait();
  kit.emit('1');
  kit.wait(100);
  kit.emit('0');
  kit.wait();
  kit.emit('1');
  kit.flagError();
  kit.wait();
  kit.recover();
  kit.emit('1');
  kit.wait();
  kit.cycle();
  kit.emit('1');
  kit.wait();
  assert.deepEqual(kit.http, []);
  kit.cycle();
  assert.deepEqual(kit.http, [{ request_id: 'entrance-preview-20300912', action: 'start', map_id: 429,
    revision: fixture.revision, kind: 'rooms', rooms: [81] }]);
});

test('attempt persists across restart and a binding change on the same local day', () => {
  const first = stand({ ...fixture, minOpenings: 1 });
  first.cycle();
  first.cycle();
  assert.equal(first.http.length, 1);
  const rebound = { ...fixture, revision: 'b'.repeat(64), openValue: '0',
    target: { kind: 'zone', zone: [-500, -450, -250, 100] }, minOpenings: 1 };
  const second = stand(rebound, first.storage);
  second.cycle();
  assert.deepEqual(second.http, []);
  second.setLocalTime(13, 19, 10);
  second.cycle();
  assert.equal(second.http[0].request_id, 'entrance-preview-20300913');
  assert.equal(second.http[0].revision, rebound.revision);
  assert.deepEqual(second.http[0].zone, rebound.target.zone);
});

test('openings outside the local evening window do not trigger a check', () => {
  for (const hour of [18, 21]) {
    const kit = stand({ ...fixture, minOpenings: 1 }, {}, hour);
    kit.cycle();
    assert.deepEqual(kit.http, []);
    assert.equal(kit.storage.lastAttemptDay, undefined);
  }
});

test('daytime openings reach the evening threshold without another MQTT event', () => {
  const kit = stand(fixture, {}, 13);
  kit.cycle();
  kit.cycle();
  assert.equal(JSON.parse(kit.storage.activity).count, 2);
  kit.setLocalTime(12, 18, 59);
  assert.deepEqual(kit.http, []);
  kit.setLocalTime(12, 19, 0);
  assert.equal(kit.http[0].request_id, 'entrance-preview-20300912');
  kit.wait(15000);
  assert.equal(kit.http.length, 1);
});

test('restart preserves openings before the daily check', () => {
  const first = stand(fixture, {}, 8);
  first.cycle();
  const restarted = stand(fixture, first.storage, 12);
  restarted.cycle();
  restarted.setLocalTime(12, 19, 1);
  assert.deepEqual(first.http, []);
  assert.equal(restarted.http[0].request_id, 'entrance-preview-20300912');
});

test('the old day cannot satisfy a new evening threshold', () => {
  const kit = stand(fixture, {}, 13);
  kit.cycle();
  kit.cycle();
  kit.setLocalTime(13, 19, 0);
  kit.cycle();
  assert.deepEqual(kit.http, []);
  kit.cycle();
  assert.equal(kit.http[0].request_id, 'entrance-preview-20300913');
});
