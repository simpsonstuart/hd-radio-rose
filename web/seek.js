import {weatherChannels} from './model.js';
// Analog seek is a spectrum heuristic, not an audio-quality guarantee.
export function signalCandidate(state, spectrum, mhz) {
  if (!state.streaming || state.retuning || Math.abs(state.frequency - mhz) > .01) return false;
  const weather = state.band === 'noaa';
  if (!weather && state.synced && state.config?.analog_fallback_mode !== 'analog_only') return true;
  if (!weather && state.config?.analog_fallback_mode === 'digital_only') return false;
  if (!weather && (state.station?.rds_program_service || state.station?.rds_radiotext)) return true;
  const {bins, sample_rate: rate, center_hz: center} = spectrum || {};
  if (!bins?.length || !rate || Math.abs(center - mhz * 1e6) > 1000) return false;
  const channel = [], floor = [];
  bins.forEach((db, i) => {
    if (!Number.isFinite(db)) return;
    const hz = Math.abs((i / bins.length - .5) * rate);
    if (hz >= (weather?1000:15000) && hz <= (weather?7000:85000)) channel.push(db); // exclude DC spike
    if (hz >= (weather?20000:280000) && hz <= (weather?70000:650000)) floor.push(db);
  });
  const median = values => values.sort((a,b)=>a-b)[Math.floor(values.length / 2)];
  return channel.length >= (weather?4:10) && floor.length > 10 && median(channel) - median(floor) >= 8;
}

export async function seekStations({frequency, band='fm', direction, tune, read, wait, cancelled, progress}) {
  const channels = band==='noaa'?weatherChannels:Array.from({length:101},(_,i)=>(879+i*2)/10);
  const original = channels.findIndex(f=>Math.abs(f-frequency)<.01);
  if (original<0) throw new Error('Invalid starting frequency');
  for (let n = 1; n < channels.length; n++) {
    if (cancelled()) return {cancelled:true};
    const mhz = channels[((original + direction*n)%channels.length + channels.length)%channels.length];
    progress(mhz);
    await tune(mhz);
    let settled = 0, hits = 0, generation;
    for (let poll = 0; poll < 80; poll++) {
      if (cancelled()) return {cancelled:true};
      await wait(250);
      if (cancelled()) return {cancelled:true};
      const {state, spectrum} = await read();
      if (cancelled()) return {cancelled:true};
      if ((state.band||'fm')!==band) throw new Error('Seek stopped: band changed by another client.');
      if (Math.abs(state.frequency - mhz) > .01) throw new Error('Seek stopped: receiver tuned by another client.');
      if (state.retuning) {
        if (poll === 79) throw new Error('Seek stopped: retune timed out.');
        settled = 0; continue;
      }
      if (!state.streaming) throw new Error(state.status || 'Receiver stopped during seek.');
      settled++;
      if (settled >= 6 && spectrum.generation !== generation) {
        generation = spectrum.generation;
        hits = signalCandidate(state, spectrum, mhz) ? hits + 1 : 0;
        if (hits >= 3) return {frequency:mhz};
      }
      if (settled >= 24) break;
      if (poll === 79) throw new Error('Seek stopped: retune timed out.');
    }
  }
  if (cancelled()) return {cancelled:true};
  await tune(channels[original]);
  return {notFound:true};
}
