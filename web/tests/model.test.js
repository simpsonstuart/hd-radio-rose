import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compile } from '@vue/compiler-dom';
import { tabs, layoutRead, groupPlays, media, recordingUrl, formatFrequency, fmChannel, digitalLocked, hdAvailable, receiverStatus } from '../model.js';

test('FM input accepts channel grid and formats receiver float noise', () => {
  assert.equal(formatFrequency(Math.fround(99.1)), '99.1');
  for (const value of [87.9, 99.1, 107.9]) assert.equal(fmChannel(value), value);
  for (const value of ['', 99, 99.2, 99.15, 87.7, 108.1, NaN]) assert.throws(() => fmChannel(value));
});
test('HD controls follow lock and advertised services, not selected program', () => {
  const state = { streaming: true, synced: false, selected: 0, config: {}, station: { programs: [{}, {}, null] }, status: 'tuned to 99.1 MHz (HD1)' };
  assert.equal(hdAvailable(state, 0), false);
  assert.equal(receiverStatus(state), 'tuned to 99.1 MHz');
  state.synced = true;
  assert.equal(digitalLocked(state), true);
  assert.equal(hdAvailable(state, 0), true);
  assert.equal(hdAvailable(state, 1), true);
  assert.equal(hdAvailable(state, 2), false);
  for (const change of [{streaming:false}, {retuning:true}, {synced:false}, {config:{analog_fallback_mode:'analog_only'}}])
    assert.equal(hdAvailable({...state,...change}, 0), false);
});

test('native interface template compiles', () => {
  const source = readFileSync(new URL('../main.js', import.meta.url), 'utf8');
  const template = source.slice(source.indexOf('template: `') + 11, source.lastIndexOf('`\n}'));
  const errors = [];
  compile(template, { onError: e => errors.push(e.message) });
  assert.deepEqual(errors, []);
});
test('layout tolerates corrupt, duplicate or missing sections', () => {
  for (const value of ['bad json', 'null', '["Listen","Listen"]', '[]']) {
    assert.deepEqual(layoutRead({ getItem: () => value }), tabs);
  }
  assert.deepEqual(layoutRead({ getItem: () => JSON.stringify([...tabs].reverse()) }), [...tabs].reverse());
});
test('history groups by title and artist and orders by play count', () => {
  const rows = groupPlays([{title:'A',artist:'X',ts_millis:1},{title:'A',artist:'X',ts_millis:3},{title:'A',artist:'Y',ts_millis:2}]);
  assert.equal(rows.length, 2); assert.equal(rows[0].count, 2); assert.equal(rows[0].ts_millis, 3);
});
test('broadcast paths are encoded rather than interpreted as URLs', () => {
  assert.equal(media('a&b.png'), '/api/media?path=a%26b.png');
  assert.equal(media(null), '');
  assert.equal(recordingUrl('Station/A.opus'), '/api/recording?name=Station%2FA.opus');
});
