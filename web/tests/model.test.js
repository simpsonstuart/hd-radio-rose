import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { compile } from '@vue/compiler-dom';
import { tabs, layoutRead, groupPlays, media, recordingUrl } from '../model.js';

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
