import {test} from 'node:test';
import assert from 'node:assert/strict';
import {parseFrequency, formatFrequency, digitalLocked} from '../model.js';
import {signalCandidate, seekStations} from '../seek.js';

test('frequency shorthand keeps normal decimals and rejects invalid channels', () => {
  for (const [input, expected] of [['983',98.3],['1079',107.9],['879',87.9],['98.3',98.3]]) assert.equal(parseFrequency(input),expected);
  for (const input of ['982','1080','foo','98.35','']) assert.throws(()=>parseFrequency(input));
});
const state = {frequency:98.3, streaming:true, config:{analog_fallback_mode:'automatic'}};
function fft(strong=false) {
  return {sample_rate:1488375,center_hz:98300000,generation:1,bins:Array.from({length:1024},(_,i)=>strong&&Math.abs(i-512)<60?-60:-95)};
}
test('analog detection rejects noise, DC spike, stale frequency and stopped streams', () => {
  assert.equal(signalCandidate(state,fft(),98.3),false);
  const spike=fft(); spike.bins[512]=0;
  assert.equal(signalCandidate(state,spike,98.3),false);
  assert.equal(signalCandidate(state,fft(true),98.3),true);
  assert.equal(signalCandidate(state,fft(true),99.1),false);
  assert.equal(signalCandidate({...state,streaming:false},fft(true),98.3),false);
  assert.equal(signalCandidate({...state,config:{analog_fallback_mode:'digital_only'}},fft(true),98.3),false);
});
function harness(extra={}) {
  let mhz=107.9, generation=0; const tuned=[];
  return {tuned, options:{frequency:mhz,direction:1,progress:()=>{},wait:async()=>{},cancelled:()=>false,
    tune:async f=>{mhz=f;tuned.push(f);},
    read:async()=>({state:{...state,frequency:mhz,synced:true},spectrum:{generation:++generation}}),...extra}};
}
test('seek wraps upward and stops after sustained fresh detection', async()=>{
  const h=harness(); assert.deepEqual(await seekStations(h.options),{frequency:87.9});
  assert.deepEqual(h.tuned,[87.9]);
});
test('seek cancellation never tunes another channel', async()=>{
  const h=harness({cancelled:()=>true}); assert.deepEqual(await seekStations(h.options),{cancelled:true});
  assert.equal(h.tuned.length,0);
});
test('no station wraps once and restores starting channel', async()=>{
  const h=harness(); let mhz;
  h.options.tune=async f=>{mhz=f;h.tuned.push(f);};
  h.options.read=async()=>({state:{...state,frequency:mhz},spectrum:fft()});
  assert.deepEqual(await seekStations(h.options),{notFound:true});
  assert.equal(new Set(h.tuned.slice(0,-1)).size,100);
  assert.equal(h.tuned.at(-1),107.9);
});
test('retune stall terminates without advancing',async()=>{
  const h=harness({read:async()=>({state:{...state,frequency:87.9,retuning:true},spectrum:{}})});
  await assert.rejects(seekStations(h.options),/retune timed out/);
  assert.equal(h.tuned.length,1);
});
test('NOAA seek wraps through weather channels and detects a narrow carrier',async()=>{
  let mhz=162.4, generation=0; const tuned=[];
  const result = await seekStations({frequency:162.4,band:'noaa',direction:-1,
    cancelled:()=>false,progress:()=>{},wait:async()=>{},
    tune:async f=>{mhz=f;tuned.push(f);},
    read:async()=>({state:{...state,band:'noaa',frequency:mhz}, spectrum:{...fft(),center_hz:mhz*1e6,generation:++generation,
      bins:Array.from({length:1024},(_,i)=>Math.abs(i-512)<5?-55:-95)}})});
  assert.deepEqual(result,{frequency:162.55}); assert.deepEqual(tuned,[162.55]);
  assert.equal(formatFrequency(162.4,'noaa'),'162.400');
  assert.equal(digitalLocked({...state,band:'noaa',synced:true}),false);
});
test('NOAA seek exhausts only six alternatives then restores original',async()=>{
  let mhz=162.4; const tuned=[];
  const result=await seekStations({frequency:mhz,band:'noaa',direction:1,
    cancelled:()=>false,progress:()=>{},wait:async()=>{},tune:async f=>{mhz=f;tuned.push(f);},
    read:async()=>({state:{...state,band:'noaa',frequency:mhz},spectrum:fft()})});
  assert.deepEqual(result,{notFound:true}); assert.equal(tuned.length,7); assert.equal(tuned.at(-1),162.4);
});
