import {test} from 'node:test';
import assert from 'node:assert/strict';
import {constellationPoints, drawConstellation} from '../charts.js';

test('measured coordinates survive unchanged; invalid or unavailable samples do not plot', () => {
  const frame = {available:true, modulation:4, lower:[[1.23,-.83], [6,-4], [NaN,1], [1,Infinity], ['1',1], [1]], upper:[[-1,1]]};
  assert.deepEqual(constellationPoints(frame,'lower'), [[1.23,-.83],[6,-4]]);
  assert.deepEqual(constellationPoints(frame,'upper'), [[-1,1]]);
  assert.deepEqual(constellationPoints({...frame,available:false},'lower'), []);
  assert.deepEqual(constellationPoints({...frame,modulation:16},'lower'), []);
  assert.deepEqual(constellationPoints(null,'lower'), []);
  assert.equal(constellationPoints({...frame,lower:Array(2000).fill([1,1])},'lower').length,1024);
});

function canvas() {
  const fills = [], labels = [];
  const context = {fillRect:(...args)=>fills.push(args), fillText:t=>labels.push(t),
    beginPath(){}, moveTo(){}, lineTo(){}, stroke(){}, strokeRect(){}};
  return {width:360,height:360,getContext:()=>context,fills,labels};
}
test('plot uses equal I/Q scale, measured coordinates, and clears on signal loss', () => {
  const c = canvas();
  drawConstellation(c,{available:true,modulation:4,lower:[[1.25,-.75],[9,9]]});
  assert.deepEqual(c.fills[1], [255,224.6,2,2]);
  assert.ok(c.labels.includes('1 points outside axes'));
  const count = c.fills.length;
  drawConstellation(c,null);
  assert.equal(c.fills.length,count+1); // background only; no manufactured dots
  assert.ok(c.labels.includes('No measured symbols'));
});
test('AM primary QAM uses its own fixed amplitude scale', () => {
  const c = canvas();
  drawConstellation(c,{available:true,modulation:64,upper:[[0,0],[4,0]]},'upper');
  assert.deepEqual(c.fills[1],[179,179,2,2]);
  assert.ok(c.labels.includes('4.5'));
  assert.equal(c.fills.length,3);
});
