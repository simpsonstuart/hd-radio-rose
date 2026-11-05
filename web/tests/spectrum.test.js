import test from 'node:test';
import assert from 'node:assert/strict';
import {matchesTunedSpectrum} from '../charts.js';

test('only live FFT frames for the selected tuning can be plotted', () => {
  for (const [band, frequency] of [['fm', 101.1], ['am', 1], ['noaa', 162.55],
    ['gmrs', 462.5625], ['dmr', 438.612501]]) {
    const state = {band, frequency, streaming:true};
    const frame = {bins:[-65], center_hz:frequency * 1e6};
    assert.ok(matchesTunedSpectrum(state, frame));
    assert.equal(matchesTunedSpectrum(state, {...frame, center_hz:99.1e6}), false);
    assert.equal(matchesTunedSpectrum(state, {...frame, bins:[]}), false);
    assert.equal(matchesTunedSpectrum({...state, retuning:true}, frame), false);
    assert.equal(matchesTunedSpectrum({...state, streaming:false}, frame), false);
  }
});
