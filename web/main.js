import { createApp, ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { tabs, media, recordingUrl, db, groupPlays, layoutRead, formatFrequency, weatherChannels, fmChannel, parseFrequency, digitalLocked, hdAvailable, receiverStatus } from './model.js';
import { drawSpectrum, drawWaterfall, drawConstellation } from './charts.js';
import './style.css';
import { seekStations } from './seek.js';
import SameAlerts from './alerts.js';

createApp({
  components: { SameAlerts },
  setup() {
    const state = ref(null), connected = ref(false), error = ref(''), pending = ref(false);
    const active = ref('Listen'), order = ref(layoutRead(localStorage)), theme = ref(localStorage.getItem('rose-theme') || 'dark');
    const frequency = ref(101.1), audioOn = ref(false), audioStatus = ref('Audio off'), volume = ref(.8);
    const player = ref(null), spectrum = ref(null), waterfall = ref(null), constellation = ref(null);
    const log = ref([]), logView = ref('timeline'), query = ref(''), files = ref([]);
    const weatherIndex = ref(0), weatherPlaying = ref(true), weatherTimer = ref(null), editPreset = ref(null);
    const deviceIndex = ref(''), remoteHost = ref(''), remotePort = ref(55132), remoteExtra = ref('');
    let closed = false, stateTimer, scopeTimer, detailsTimer, previousGeneration = -1, dragTab;
    const seeking = ref(false), seekMessage = ref('');
    let seekCancelled = false;
    const tuningDisabled = computed(() => pending.value || !connected.value || !state.value?.streaming || state.value?.retuning || seeking.value);
    const isWeather = computed(() => state.value?.band==='noaa');
    const current = computed(() => state.value?.programs[state.value.speaker ?? state.value.selected] || {});
    const station = computed(() => state.value?.station || {});
    const config = computed(() => state.value?.config || {});
    const weather = computed(() => state.value?.weather?.[weatherIndex.value]);
    const logRows = computed(() => {
      const rows = logView.value === 'top' ? groupPlays(log.value) : [...log.value].reverse();
      const term = query.value.toLowerCase();
      return rows.filter(r => `${r.title} ${r.artist}`.toLowerCase().includes(term));
    });
    async function get(path, options = {}) {
      const response = await fetch(path, { ...options, signal: AbortSignal.timeout(40000), cache: 'no-store' });
      let result;
      try { result = await response.json(); } catch { throw new Error(`Server returned ${response.status}; reload to sign in.`); }
      if (!response.ok || result.error) throw new Error(result.error || `HTTP ${response.status}`);
      return result;
    }
    function accept(s) {
      const old = state.value;
      state.value = s;
      if (!old || old.frequency !== s.frequency || old.band !== s.band) frequency.value = formatFrequency(s.frequency,s.band);
      if (!old || old.config.sdr.transport !== s.config.sdr.transport) {
        remoteHost.value = s.config.sdr.remote_host || '';
        remotePort.value = s.config.sdr.remote_port || (s.config.sdr.transport === 'rtl_tcp_remote' ? 1234 : 55132);
        remoteExtra.value = s.config.sdr.remote_extra_args || '';
      }
      if (weatherPlaying.value) weatherIndex.value = Math.min(weatherIndex.value, Math.max(0, s.weather.length - 1));
    }
    async function refresh() {
      try { accept(await get('/api/state')); connected.value = true; }
      catch (e) { connected.value = false; error.value = e.message; }
      finally { if (!closed) stateTimer = setTimeout(refresh, document.hidden ? 4000 : 1000); }
    }
    async function command(type, value) {
      if (pending.value || !connected.value || seeking.value) return;
      if (['TuneMhz','TuneWeather','RecallPreset'].includes(type) && tuningDisabled.value) return;
      pending.value = true; error.value = '';
      try { const body = value === undefined ? { type } : { type, value }; accept(await get('/api/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })); }
      catch (e) { error.value = e.message; }
      finally { pending.value = false; }
    }
    async function tune() {
      if (tuningDisabled.value || isWeather.value) return;
      try { const value = parseFrequency(frequency.value); frequency.value = formatFrequency(value); await command('TuneMhz', value); }
      catch (e) { error.value = e.message; }
    }
    function step(amount) { if (tuningDisabled.value) return; frequency.value = Math.max(87.9, Math.min(107.9, Math.round((Number(frequency.value) + amount) * 10) / 10)); tune(); }
    async function seek(direction) {
      if (tuningDisabled.value) return;
      seeking.value = true; seekCancelled = false; error.value = '';
      try {
        if (!isWeather.value) accept(await get('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:'SelectProgram',value:0})}));
        const result = await seekStations({frequency:state.value.frequency, band:state.value.band||'fm', direction,
          cancelled:() => seekCancelled || closed,
          progress:mhz => { seekMessage.value = `Seeking at ${formatFrequency(mhz,state.value.band)} MHz…`; },
          wait:ms => new Promise(resolve => setTimeout(resolve, ms)),
          tune:async mhz => accept(await get('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({type:isWeather.value?'TuneWeather':'TuneMhz',value:mhz})})),
          read:async () => { const s = await get('/api/state'); accept(s); return {state:s,spectrum:await get('/api/spectrum')}; }
        });
        seekMessage.value = result.cancelled ? 'Seek cancelled.' : result.notFound ? 'No station found; returning to the original frequency.' : `Signal found at ${formatFrequency(result.frequency,state.value.band)} MHz.`;
      } catch (e) { error.value = e.message; seekMessage.value = 'Seek stopped.'; }
      finally { seeking.value = false; }
    }
    async function details() {
      try {
        if (active.value === 'Song log') log.value = (await get('/api/log')).entries;
        if (active.value === 'Recordings') files.value = (await get('/api/recordings')).files;
      } catch (e) { error.value = e.message; }
    }
    async function scopes() {
      try {
        if (connected.value && !document.hidden && ['Listen', 'Spectrum'].includes(active.value)) {
          const s = await get('/api/spectrum');
          if (s.generation !== previousGeneration) {
            drawSpectrum(spectrum.value, s.bins);
            drawWaterfall(waterfall.value, s.bins);
            previousGeneration = s.generation;
          }
          drawConstellation(constellation.value, (state.value.mer_lower + state.value.mer_upper) / 2);
        }
      } catch { /* Connection state is handled by the state poll. */ }
      finally { if (!closed) scopeTimer = setTimeout(scopes, active.value === 'Spectrum' ? 250 : 1000); }
    }
    async function toggleAudio() {
      if (audioOn.value) { stopAudio(); return; }
      const audio = player.value;
      audio.src = `/audio.mp3?t=${Date.now()}`; audio.volume = volume.value;
      audioOn.value = true; audioStatus.value = 'Buffering audio…';
      try { await audio.play(); } catch (e) { stopAudio(); error.value = e.message; }
    }
    function stopAudio() { audioOn.value = false; if (player.value) { player.value.pause(); player.value.removeAttribute('src'); player.value.load(); } audioStatus.value = 'Audio off'; }
    function audioFailed() { if (audioOn.value) { stopAudio(); error.value = 'Audio disconnected. Press Listen to reconnect.'; } }
    function destructive(type) { if (confirm('Clear this stored data? This cannot be undone.')) command(type); }
    function presetEdit(index) {
      const old = config.value.presets[index];
      editPreset.value = { index, name: old?.name || '', frequency_mhz: formatFrequency(old?.frequency_mhz || state.value.frequency), program: old?.program || 0 };
    }
    async function presetSave() {
      const p = editPreset.value;
      await command('SetPreset', [p.index, { name: p.name, frequency_mhz: Number(p.frequency_mhz), program: Number(p.program) }]);
      if (!error.value) editPreset.value = null;
    }
    function dropTab(target) {
      if (!dragTab || dragTab === target) return;
      const items = order.value.filter(t => t !== dragTab); items.splice(items.indexOf(target), 0, dragTab);
      order.value = items; localStorage.setItem('rose-layout', JSON.stringify(items));
    }
    async function selectDevice() {
      const d = state.value.devices[Number(deviceIndex.value)];
      if (d) await command('SelectSdrDevice', { driver: d.driver, device_args: d.args });
    }
    async function applyRemote() {
      await command('SetSdrRemoteHost', remoteHost.value); if (error.value) return;
      await command('SetSdrRemotePort', Number(remotePort.value)); if (error.value) return;
      await command('SetSdrRemoteExtraArgs', remoteExtra.value);
    }
    const format = (v) => v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v);
    const time = (v) => new Date(v).toLocaleString();
    watch(theme, v => { document.documentElement.dataset.theme = v; localStorage.setItem('rose-theme', v); }, { immediate: true });
    watch(volume, v => { if (player.value) player.value.volume = Number(v); });
    watch(active, async () => { previousGeneration = -1; await nextTick(); details(); });
    onMounted(() => {
      refresh(); scopes(); detailsTimer = setInterval(details, 5000);
      weatherTimer.value = setInterval(() => { const n = state.value?.weather?.length || 0; if (weatherPlaying.value && n) weatherIndex.value = (weatherIndex.value + 1) % n; }, 800);
    });
    onUnmounted(() => { closed = true; seekCancelled = true; clearTimeout(stateTimer); clearTimeout(scopeTimer); clearInterval(detailsTimer); clearInterval(weatherTimer.value); stopAudio(); });
    return { isWeather, weatherChannels, formatFrequency, seeking, seekMessage, tuningDisabled, seek, cancelSeek: () => { seekCancelled = true; }, state, connected, error, pending, active, order, theme, frequency, audioOn, audioStatus, volume,
      player, spectrum, waterfall, constellation, current, station, config, logRows, logView, query, files,
      weatherIndex, weatherPlaying, weather, editPreset, deviceIndex, remoteHost, remotePort, remoteExtra,
      command, tune, step, toggleAudio, audioFailed, destructive, presetEdit, presetSave, selectDevice, applyRemote,
      format, time, media, recordingUrl, db, digitalLocked, hdAvailable, receiverStatus, dropTab, dragStart: t => { dragTab = t; }, tabs,
      resetLayout: () => { order.value = [...tabs]; localStorage.removeItem('rose-layout'); } };
  },
  template: `
  <div class="shell">
    <aside class="sidebar">
      <a href="/" class="brand"><span class="brand-mark">◉</span><span>ROSE <b>RADIO</b><small>HD RADIO / NETWORK RECEIVER</small></span></a>
      <div class="side-label">WORKSPACE <small>drag to reorder</small></div>
      <nav aria-label="Workspace"><button v-for="(tab,i) in order" :key="tab" :class="{active:active===tab}" @click="active=tab" draggable="true" @dragstart="dragStart(tab)" @dragover.prevent @drop.prevent="dropTab(tab)"><span class="nav-number">{{String(i+1).padStart(2,'0')}}</span>{{tab}}</button></nav>
      <div class="server-card"><span :class="['dot', {online:connected}]"></span><b>{{connected ? 'Receiver online' : 'Connecting…'}}</b><p>ROSE ARM64 · Native web</p><small v-if="state">NRSC5 {{state.version}}</small></div>
      <button class="theme" @click="theme=theme==='dark'?'light':'dark'">{{theme==='dark'?'☀ Light appearance':'☾ Dark appearance'}}</button>
    </aside>
    <main>
      <SameAlerts :show-settings="isWeather || active==='Settings'" />
      <header class="page-header"><div><h1>{{active}}</h1></div><div class="header-status"><span :class="['dot',{online:digitalLocked(state)}]"></span>{{digitalLocked(state)?'HD signal locked':state?.streaming?(isWeather?'NOAA weather · NFM':'Acquiring signal'):'Receiver idle'}}</div></header>
      <div v-if="error" class="notice error" role="alert">{{error}}<button @click="error=''" aria-label="Dismiss error">×</button></div>
      <div v-if="!connected" class="notice">The receiver is unavailable. Controls will reconnect automatically.</div>
      <template v-if="state">
      <div v-if="isWeather" class="notice">NOAA weather audio · Mono narrowband FM · Manual gain. NOAA recording is not available. Changing bands stops recording and weather alert monitoring.</div>
      <div v-if="state.preview" class="notice">UI preview data — no SDR or headless engine is running in this preview.</div>
      <div v-if="station.alert" class="notice alert" role="alert"><b>Broadcast emergency alert</b> {{station.alert}}</div>
      <section class="tuner panel" aria-label="Tuner">
        <label class="band-picker">Band<select :value="state.band||'fm'" @change="command('SelectBand',$event.target.value)" :disabled="pending||!connected||seeking||state.retuning"><option value="fm">FM / HD Radio</option><option value="noaa">NOAA Weather</option></select></label>
        <label v-if="isWeather" class="weather-picker">Weather channel<select :value="state.frequency" @change="command('TuneWeather',Number($event.target.value))" :disabled="tuningDisabled"><option v-for="f in weatherChannels" :key="f" :value="f">{{f.toFixed(3)}} MHz</option></select></label>
        <form v-if="!isWeather" @submit.prevent="tune" class="frequency"><button type="button" @click="step(-.2)" :disabled="tuningDisabled" aria-label="Tune down">−</button><label><input v-model="frequency" type="text" inputmode="decimal" required maxlength="6" :disabled="tuningDisabled" aria-label="Frequency MHz" title="Enter 98.3 or 983"><span>MHz / FM</span></label><button type="button" @click="step(.2)" :disabled="tuningDisabled" aria-label="Tune up">+</button><button type="submit" :disabled="tuningDisabled">Tune</button></form>
        <div class="seek-controls"><button @click="seek(-1)" :disabled="tuningDisabled" title="Seek previous station; stops recording">◀ Seek</button><button @click="seek(1)" :disabled="tuningDisabled" title="Seek next station; stops recording">Seek ▶</button><button v-if="seeking" @click="cancelSeek">Cancel seek</button></div>
        <div v-if="!isWeather" class="hd-channels" aria-label="HD subchannels"><span :class="['hd-indicator',{lit:connected&&digitalLocked(state)}]" role="status" :aria-label="connected&&digitalLocked(state)?'HD Radio digital signal locked':'HD Radio digital signal unavailable'"><img src="/hd-radio.svg" alt="" aria-hidden="true"></span><button v-for="n in (config.show_hd5_hd8?8:4)" :key="n" :class="{selected:connected&&hdAvailable(state,n-1)&&state.selected===n-1}" :title="hdAvailable(state,n-1)?(station.programs[n-1].short_name || 'Digital channel available'):'No digital service available'" @click="command('SelectProgram',n-1)" :disabled="seeking||pending||!connected||!hdAvailable(state,n-1)">HD{{n}}</button></div>
        <button :class="['primary',{running:state.streaming}]" @click="command(state.streaming?'Stop':'Start')" :disabled="seeking||pending||!connected||state.retuning">{{state.retuning?'Retuning…':state.streaming?'■ Stop receiver':'▶ Start receiver'}}</button>
      </section>
      <p v-if="seeking" class="muted">Seeking stations; recording stops when tuning changes. Keep this page open, or cancel to stay on the current frequency.</p>
      <p v-if="seekMessage" class="receiver-status" role="status">{{seekMessage}}</p>
      <p v-if="!state.streaming&&!state.retuning" class="muted">Start the receiver to tune or seek.</p>
      <p class="receiver-status" role="status">{{receiverStatus(state)}}</p>

      <template v-if="active==='Listen'">
        <div class="listen-grid">
          <section class="panel now-playing"><div class="panel-title"><span>ON AIR</span><span>{{isWeather?'NOAA · '+formatFrequency(state.frequency,'noaa')+' MHz':digitalLocked(state)?'HD'+((state.speaker ?? state.selected)+1):state.streaming?(config.analog_fallback_mode==='digital_only'?'Waiting for HD':'FM / Analog'):'Receiver idle'}}</span></div>
            <div class="track"><div class="cover"><img v-if="current.cover||current.logo" :src="media(current.cover||current.logo)" alt="Broadcast artwork"><span v-else>♫</span></div><div class="track-copy"><p class="eyebrow">{{isWeather?'NOAA WEATHER RADIO':station.call_sign||station.rds_program_service||'Waiting for station identification'}}</p><h2>{{isWeather?(state.streaming?'Weather broadcast':'Receiver idle'):current.title||station.rds_radiotext||(state.streaming?'No program title broadcast':'Receiver idle')}}</h2><p v-if="current.artist">{{current.artist}}</p><small>{{[current.album,current.genre].filter(Boolean).join(' · ')}}</small><p v-if="state.stale||(!current.fresh&&current.title)" class="muted">Metadata may be stale.</p></div></div>
            <div class="station-message">{{station.slogan||station.message||'Station metadata appears as it arrives over the air.'}}</div>
            <div class="player-bar"><button class="primary" @click="toggleAudio">{{audioOn?'Ⅱ Pause audio':'▶ Listen'}}</button><label>Volume <input v-model="volume" type="range" min="0" max="1" step=".01" aria-label="Browser volume"></label><small>{{audioStatus}}</small><button :class="{recording:state.recording}" @click="command(state.recording?'StopRecording':'StartRecording')" :disabled="isWeather||pending||!state.streaming">{{state.recording?'■ Stop recording':'● Record'}}</button></div>
            <p v-if="state.recording" class="record-note">Recording HD{{state.recording.program+1}} · {{state.recording.seconds}} s. This channel stays fixed when you change what you listen to.</p>
          </section>
          <section class="panel signal"><div class="panel-title"><span>SIGNAL QUALITY</span><span>{{({auto:'Automatic',manual:'Manual',hardware_agc:'Hardware AGC'})[state.active_gain_mode||config.gain_mode]}}</span></div><div class="signal-big">{{state.synced?db((state.mer_lower+state.mer_upper)/2):'—'}}<small>dB MER</small></div><div class="metric"><span>Lower sideband</span><b>{{db(state.mer_lower)}} dB</b></div><meter min="0" max="20" low="8" high="12" optimum="20" :value="state.mer_lower"></meter><div class="metric"><span>Upper sideband</span><b>{{db(state.mer_upper)}} dB</b></div><meter min="0" max="20" low="8" high="12" optimum="20" :value="state.mer_upper"></meter><div class="metric"><span>Bit error rate</span><b>{{state.ber?.toExponential(2)}}</b></div><div class="metric"><span>Tuner gain (readback)</span><b>{{db(state.gains?.find(g=>g.name===state.primary_gain_element)?.value)}} dB</b></div><p class="muted">{{!state.streaming?'Receiver idle.':state.active_gain_mode==='hardware_agc'?'Gain is controlled by the radio hardware.':state.active_gain_mode==='manual'?'Manual gain is active.':state.agc?.reason||'Waiting for automatic gain measurements.'}}</p></section>
          <section v-if="!isWeather" class="panel presets"><div class="panel-title"><span>YOUR PRESETS</span><span>{{config.preset_slot_count}} slots</span></div><div class="preset-grid"><div v-for="n in config.preset_slot_count" :key="n" class="preset"><button @click="config.presets[n-1]?.frequency_mhz?command('RecallPreset',n-1):command('SavePreset',n-1)" :disabled="tuningDisabled"><small>{{String(n).padStart(2,'0')}}</small><strong>{{config.presets[n-1]?.name||'Save current station'}}</strong><span v-if="config.presets[n-1]?.frequency_mhz">{{config.presets[n-1].frequency_mhz.toFixed(1)}} · HD{{config.presets[n-1].program+1}}</span></button><button class="edit" @click="presetEdit(n-1)" :aria-label="'Edit preset '+n">✎</button></div></div></section>
          <section class="panel mini-spectrum"><div class="panel-title"><span>SPECTRUM</span><button @click="active='Spectrum'">Expand ↗</button></div><canvas ref="spectrum" width="1024" height="220" aria-label="Live radio spectrum"></canvas><div class="axis"><span>−744 kHz</span><span>{{formatFrequency(state.frequency,state.band)}} MHz</span><span>+744 kHz</span></div></section>
        </div>
      </template>

      <template v-if="active==='Spectrum'">
        <section class="panel scope"><div class="panel-title"><span>LIVE SPECTRUM & WATERFALL</span><span>1.488375 MS/s · 1024 bins</span></div><canvas ref="spectrum" width="1024" height="260" aria-label="Live FFT spectrum"></canvas><div class="axis"><span>−744 kHz</span><span>{{formatFrequency(state.frequency,state.band)}} MHz</span><span>+744 kHz</span></div><canvas ref="waterfall" width="1024" height="256" aria-label="Live waterfall"></canvas><p v-if="!state.streaming" class="muted">Start the receiver to see live I/Q measurements.</p></section>
        <div class="two-col"><section class="panel"><div class="panel-title"><span>CONSTELLATION</span><span>MER-derived illustration</span></div><canvas ref="constellation" width="440" height="240" aria-label="MER-derived QPSK illustration"></canvas><p class="muted">Like Studio's scope, this illustrates signal quality from MER; these are not measured I/Q symbols.</p></section><section class="panel"><div class="panel-title"><span>GAIN CONTROLLER</span></div><dl v-if="state.agc"><template v-for="(v,k) in state.agc"><dt>{{k.replaceAll('_',' ')}}</dt><dd>{{format(v)}}</dd></template></dl><p v-else class="empty">AGC telemetry appears when tuning.</p></section></div>
      </template>

      <template v-if="active==='Station'">
        <div class="two-col"><section class="panel"><div class="panel-title"><span>STATION INFORMATION</span><span v-if="state.stale">stale</span></div><img v-if="current.logo" class="station-logo" :src="media(current.logo)" alt="Station logo"><h2>{{station.call_sign||'Waiting for SIS'}}</h2><p>{{station.slogan}}</p><p>{{station.message}}</p><dl><dt>Country</dt><dd>{{station.country||'—'}}</dd><dt>FCC facility</dt><dd><a v-if="station.fcc_facility_id" :href="'https://www.fcc.gov/media/radio/fm-query?facid='+station.fcc_facility_id" target="_blank" rel="noopener">{{station.fcc_facility_id}} ↗</a><span v-else>—</span></dd><dt>Transmitter</dt><dd>{{station.location?station.location.latitude+', '+station.location.longitude+' · '+station.location.altitude_m+' m':'—'}}</dd><dt>Service mode</dt><dd>{{station.sync_psmi===null?'—':'MP'+station.sync_psmi}}</dd><dt>Carrier offset</dt><dd>{{format(station.sync_freq_offset_hz)}} Hz</dd><dt>RDS PS</dt><dd>{{station.rds_program_service||'—'}}</dd><dt>RadioText</dt><dd>{{station.rds_radiotext||'—'}}</dd><dt>Logo source</dt><dd>{{current.logo_source||'—'}}</dd></dl></section>
        <section class="panel"><div class="panel-title"><span>AUDIO & DATA SERVICES</span></div><div class="service" v-for="(p,i) in station.programs" :key="i"><template v-if="p"><b>HD{{i+1}} · {{p.short_name}}</b><p>{{p.program_type}} · {{p.sound_experience}} · {{p.bit_rate_kbps??'—'}} kbps</p></template></div><div v-for="d in station.data_services" class="service"><b>{{d.name}}</b><small>Service {{d.number}} · {{d.mime||'MIME pending'}}</small></div><p v-if="!station.programs?.some(Boolean)" class="empty">Broadcast services populate progressively after sync.</p></section></div>
        <section class="panel"><div class="panel-title"><span>ENGINEERING TELEMETRY</span></div><div class="two-col engineering"><div v-for="key in ['exciter','importer','local_time','leap_second','am_sync']"><h3>{{key.replaceAll('_',' ')}}</h3><dl v-if="station[key]"><template v-for="(v,k) in station[key]"><dt>{{k.replaceAll('_',' ')}}</dt><dd>{{format(v)}}</dd></template></dl><p v-else class="muted">Not broadcast yet</p></div></div><details><summary>Recent decoder payloads</summary><p v-for="p in state.payload" class="mono">{{p.time}} {{p.text}}</p></details></section>
      </template>

      <section v-if="active==='Artwork'" class="panel"><div class="panel-title"><span>ALBUM ART COLLAGE · ROLLING 8 HOURS</span><div><button @click="destructive('ClearCollage')">Clear collage</button><button @click="command('ClearArtBlocklist')" :disabled="!state.blocked_art">Unblock all ({{state.blocked_art}})</button></div></div><div class="art-grid"><article v-for="a in state.art" :key="a.hash" :style="{flexGrow:Math.min(6,a.count)}"><img :src="media(a.path)" :alt="a.album||'Broadcast album artwork'"><div><b>{{a.album||a.songs[0]?.[0]||'Broadcast artwork'}}</b><small>{{a.count}} plays · {{a.songs.map(s=>s.join(' — ')).join(' / ')}}</small><button @click="command('BlockCover',a.hash)">Block cover</button></div></article></div><p v-if="!state.art.length" class="empty">Covers appear here when a station broadcasts album art. Frequent plays receive more space.</p></section>

      <div v-if="active==='Traffic & weather'" class="two-col"><section class="panel"><div class="panel-title"><span>TRAFFIC</span><span>{{state.traffic_updated||'Waiting for broadcast'}}</span></div><img v-if="state.traffic" :src="media(state.traffic)+'&v='+encodeURIComponent(state.traffic_updated||'')" class="map" alt="Broadcast traffic map"><p v-else class="empty">Traffic tiles are assembled as they arrive. Only stations carrying traffic data will populate this view.</p></section><section class="panel"><div class="panel-title"><span>WEATHER RADAR</span><span>{{state.weather.length}} frames</span></div><img v-if="weather" :src="media(weather.path)" class="map" alt="Broadcast weather radar"><p v-else class="empty">Waiting for weather broadcasts. A complete frame can take several minutes.</p><div class="weather-controls"><button @click="weatherPlaying=!weatherPlaying">{{weatherPlaying?'Pause':'Play'}}</button><input type="range" min="0" :max="Math.max(0,state.weather.length-1)" v-model.number="weatherIndex" @input="weatherPlaying=false" aria-label="Weather frame"><span>{{weather?time(weather.time):'—'}}</span></div></section></div>

      <section v-if="active==='Song log'" class="panel"><div class="panel-title"><span>SONG HISTORY · {{config.play_log_retention_hours}} HOURS</span><div><a class="button" href="/api/log.csv">Export CSV ↓</a><button @click="destructive('ClearLog')">Clear log</button></div></div><div class="filter-bar"><input v-model="query" placeholder="Find a title or artist" aria-label="Search song history"><select v-model="logView" aria-label="Song log view"><option value="timeline">Timeline</option><option value="top">Top played</option></select></div><div class="table-wrap"><table><thead><tr><th>{{logView==='top'?'Plays':'Time'}}</th><th>Title</th><th>Artist</th><th>Station</th></tr></thead><tbody><tr v-for="(r,i) in logRows" :key="i"><td>{{logView==='top'?r.count:time(r.ts_millis)}}</td><td>{{r.title}}</td><td>{{r.artist}}</td><td>{{r.frequency_mhz.toFixed(1)}} HD{{r.program+1}}</td></tr></tbody></table></div><p v-if="!logRows.length" class="empty">No matching plays. Broadcast song metadata is logged automatically.</p></section>

      <section v-if="active==='Recordings'" class="panel"><div class="panel-title"><span>RECORDING LIBRARY</span><button :class="{recording:state.recording}" @click="command(state.recording?'StopRecording':'StartRecording')" :disabled="isWeather||pending||!state.streaming">{{state.recording?'■ Finish recording':'● Record selected HD channel'}}</button></div><div v-if="state.recording" class="notice">Recording HD{{state.recording.program+1}} · {{state.recording.seconds}} seconds · {{state.recording.path}}</div><p class="muted">Opus recordings remain on the ROSE. Downloads use your browser. Stop recording before downloading the active file.</p><div class="table-wrap"><table><thead><tr><th>File</th><th>Modified</th><th>Size</th><th></th></tr></thead><tbody><tr v-for="f in files" :key="f.name"><td>{{f.name}}</td><td>{{time(f.modified*1000)}}</td><td>{{(f.bytes/1048576).toFixed(1)}} MB</td><td><a :href="recordingUrl(f.name)" download>Download ↓</a></td></tr></tbody></table></div><p v-if="!files.length" class="empty">Your recordings will appear here.</p></section>

      <template v-if="active==='Settings'">
      <div class="settings-grid" :inert="seeking||pending||!connected">
        <section class="panel"><div class="panel-title"><span>SDR CONNECTION</span><button @click="command('RefreshSdrDevices')" :disabled="pending||state.refreshing">{{state.refreshing?'Refreshing…':'Refresh'}}</button></div>
          <label>Transport<select :value="config.sdr.transport" @change="command('SelectSdrTransport',$event.target.value)"><option value="local_soapy">Local USB / SoapySDR</option><option value="soapy_remote">SoapyRemote</option><option value="rtl_tcp_remote">rtl_tcp</option></select></label>
          <template v-if="config.sdr.transport==='local_soapy'"><label>Detected receiver<select v-model="deviceIndex" @change="selectDevice"><option value="" disabled>Select a receiver…</option><option v-for="(d,i) in state.devices" :value="String(i)">{{d.label}} ({{d.driver}})</option></select></label><p class="muted">Configured: {{config.sdr.driver}} {{config.sdr.device_args}}</p><p v-if="!state.devices.length" class="muted">No local receiver detected. Check the ROSE USB assignment and ensure another radio service isn't using it.</p></template>
          <template v-else><label>Remote host<input v-model="remoteHost" maxlength="512"></label><label>Port<input v-model.number="remotePort" type="number" min="1" max="65535"></label><label v-if="config.sdr.transport==='soapy_remote'">Extra Soapy arguments<input v-model="remoteExtra" maxlength="512"></label><button @click="applyRemote" :disabled="pending">Apply remote connection</button></template>
          <label>Frequency correction (PPM)<input type="number" min="-1000" max="1000" step=".1" :value="config.sdr.freq_correction_ppm" @change="command('SetSdrFreqCorrectionPpm',Number($event.target.value))"></label><label v-if="state.antennas.length">Antenna<select :value="state.active_antenna" @change="command('SetSdrAntenna',$event.target.value)"><option v-for="a in state.antennas" :value="a">{{a}}</option></select></label><button @click="destructive('ResetSdrConfig')">Reset SDR settings</button>
        </section>
        <section class="panel"><div class="panel-title"><span>GAIN & RECEPTION</span></div><label v-if="!isWeather">Gain control<select :value="config.gain_mode" @change="command('SetGainMode',$event.target.value)"><option value="auto">Automatic (signal search)</option><option value="manual">Manual</option><option v-if="config.sdr.driver==='rtlsdr'" value="hardware_agc">Radio hardware AGC</option></select></label><label v-if="config.gain_mode==='manual'">Tuner gain (dB)<input type="number" :min="state.primary_gain_min??-100" :max="state.primary_gain_max??100" step=".1" :value="config.manual_gain_tenths/10" @change="command('SetManualGainTenths',Math.round(Number($event.target.value)*10))"></label><label v-for="g in state.gains.filter(g=>g.name!==state.primary_gain_element)" v-show="config.gain_mode==='manual'" :key="g.name">{{g.name}} (dB)<input type="number" :min="g.min" :max="g.max" :step="g.step||.1" :value="state.streaming?g.value:(config.sdr.gains[g.name]??g.value)" @change="command('SetSdrGainElement',{element:g.name,value_db:Number($event.target.value)})"></label><label v-if="config.gain_mode==='auto'">Advanced: AGC RMS target (dBFS, blank = device default)<input type="number" min="-30" max="-10" :value="config.agc_amp_target_dbfs_override" @change="command('SetAgcAmpTargetDbfs',$event.target.value===''?null:Number($event.target.value))"></label><button v-if="config.gain_mode==='auto'" @click="destructive('ClearGainCache')">Clear learned gain cache</button><p class="muted">Changing gain mode or the automatic target restarts reception briefly and stops any recording. Manual gain changes apply immediately; values snap to the radio’s supported steps. Active mode: {{state.active_gain_mode||'idle'}}.</p></section>
        <section class="panel"><div class="panel-title"><span>AUDIO & ANALOG FALLBACK</span></div><label v-if="!isWeather">Reception mode<select :value="config.analog_fallback_mode" @change="command('SetAnalogFallbackMode',$event.target.value)"><option value="digital_only">Digital only</option><option value="automatic">Automatic HD / analog</option><option value="analog_only">Analog only</option></select></label><label v-if="!isWeather" class="check"><input type="checkbox" :checked="config.analog_fallback_stereo" @change="command('SetAnalogFallbackStereo',$event.target.checked)">Analog stereo</label><label v-if="!isWeather" class="check"><input type="checkbox" :checked="config.analog_fallback_rds_enabled" @change="command('SetAnalogFallbackRdsEnabled',$event.target.checked)">Decode RDS</label><label>Receiver volume (all listeners)<input type="range" min="0" max="1" step=".01" :value="config.volume" @change="command('SetVolume',Number($event.target.value))"></label><label class="check"><input type="checkbox" :checked="config.muted" @change="command('SetMute',$event.target.checked)">Mute receiver for all listeners</label><p class="muted">The Listen page's volume control affects only this browser. Analog settings may require a retune.</p></section>
        <section class="panel"><div class="panel-title"><span>DISPLAY & HISTORY</span></div><label class="check"><input type="checkbox" :checked="config.show_hd5_hd8" @change="command('SetShowHd5Hd8',$event.target.checked)">Show HD5–HD8</label><label>Preset slots<input type="number" min="1" max="48" :value="config.preset_slot_count" @change="command('SetPresetSlotCount',Number($event.target.value))"></label><label>Artwork tile limit<select :value="config.collage_max_tiles" @change="command('SetCollageTileCap',Number($event.target.value))"><option v-for="n in [8,16,32,64,128,256,512]" :value="n">{{n}}</option></select></label><label>Song log retention<select :value="config.play_log_retention_hours" @change="command('SetPlayLogRetention',Number($event.target.value))"><option v-for="n in [1,6,12,24,48,72,168]" :value="n">{{n}} hours</option></select></label><label class="check"><input type="checkbox" :checked="config.spectrum_smoothing_enabled" @change="command('SetSpectrumSmoothingEnabled',$event.target.checked)">Smooth spectrum</label><label>Smoothing alpha<input type="number" min=".1" max="1" step=".1" :value="config.spectrum_smoothing_alpha" @change="command('SetSpectrumSmoothingAlpha',Number($event.target.value))"></label><button @click="resetLayout">Reset workspace order</button></section>
        <section class="panel"><div class="panel-title"><span>RECORDING</span></div><label>Rotate files after (minutes)<input type="number" min="1" max="240" :value="config.recording_max_minutes" @change="command('SetRecordingMaxMinutes',Number($event.target.value))"></label><label class="check"><input type="checkbox" :checked="config.recording_subfolder_per_station" @change="command('SetRecordingSubfolderPerStation',$event.target.checked)">Group recordings into station folders</label><label>Output folder (beneath /data/recordings)<input :value="config.recording_dir||'/data/recordings'" @change="command('SetRecordingDir',$event.target.value)"></label><p class="muted">Recording stays locked to its original HD subchannel while you listen to another. Folders remain inside the persistent data mount and appear in the download library.</p></section>
        <section class="panel"><div class="panel-title"><span>ABOUT THIS RECEIVER</span></div><p>NRSC5 Studio {{state.version}} receiver engine with a native Vue web interface. Radio decoding, AGC, maps, metadata and Opus recording run on the ROSE.</p><p class="muted">One shared tuner. Other connected users can change the station. HackRF support follows upstream's experimental profile.</p><a href="/sources.tar.gz">Download corresponding source ↓</a></section>
      </div></template>
      </template>
      <div v-else class="panel empty">Connecting to your radio engine…</div>
      <footer>ROSE RADIO<span>Direct receiver API · No virtual desktop</span></footer>
    </main>
    <audio ref="player" preload="none" @playing="audioStatus='Live audio'" @waiting="audioStatus='Buffering audio…'" @error="audioFailed" @ended="audioFailed"></audio>
    <div v-if="editPreset" class="modal-backdrop" @click.self="editPreset=null"><form class="panel modal" @submit.prevent="presetSave" role="dialog" aria-modal="true" aria-labelledby="preset-title"><h2 id="preset-title">Edit preset {{editPreset.index+1}}</h2><label>Name<input v-model="editPreset.name" maxlength="128" autofocus></label><label>Frequency (MHz)<input v-model.number="editPreset.frequency_mhz" type="number" min="87.9" max="107.9" step=".2" required></label><label>Subchannel<select v-model.number="editPreset.program"><option v-for="n in 8" :value="n-1">HD{{n}}</option></select></label><div class="modal-actions"><button type="button" @click="command('ClearPreset',editPreset.index);editPreset=null">Clear</button><button type="button" @click="editPreset=null">Cancel</button><button class="primary" type="submit" :disabled="pending">Save preset</button></div></form></div>
  </div>`
}).mount('#app');
