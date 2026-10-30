import {receiverUrl, selectedReceiver} from './model.js';
import { ref, computed, onMounted, onUnmounted } from 'vue';

export default {
  props: { showSettings: Boolean, receiverId: {type:String,default:selectedReceiver}, sourceName: String },
  setup(props) {
    const data = ref(null), counties = ref([]), stateCode = ref(''), county = ref('');
    const selected = ref([]), tests = ref(false), manual = ref(''), error = ref('');
    const sound = ref(false), saving = ref(false), online = ref(false);
    const clock = ref(Date.now());
    let timer, closed = false, context, initialized = false;
    const heard = new Set();
    const states = computed(() => [...new Set(counties.value.map(c => c.state))].sort());
    const choices = computed(() => counties.value.filter(c => c.state === stateCode.value));
    const active = computed(() => (data.value?.active || []).filter(a => a.expires * 1000 > clock.value));
    async function request(path, body) {
      const response = await fetch(receiverUrl('/api/alerts' + path, props.receiverId), {
        cache: 'no-store', signal: AbortSignal.timeout(5000),
        ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Alert service unavailable');
      return result;
    }
    function chime() {
      if (context?.state !== 'running') return;
      for (let n = 0; n < 3; n++) {
        const oscillator = context.createOscillator(), gain = context.createGain();
        const start = context.currentTime + n * .5;
        oscillator.frequency.value = 740;
        gain.gain.setValueAtTime(0, start);
        gain.gain.linearRampToValueAtTime(.18, start + .02);
        gain.gain.linearRampToValueAtTime(0, start + .3);
        oscillator.connect(gain); gain.connect(context.destination);
        oscillator.start(start); oscillator.stop(start + .32);
        oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
      }
    }
    async function enableSound() {
      try {
        context ||= new AudioContext();
        context.onstatechange = () => { if (context.state !== 'running') sound.value = false; };
        await context.resume();
        sound.value = context.state === 'running';
        if (!sound.value) throw new Error('Browser blocked sound; try the button again.');
        chime();
      } catch (e) { error.value = e.message; }
    }
    async function poll() {
      try {
        data.value = await request(''); online.value = true;
        if (!initialized) {
          selected.value = [...data.value.settings.counties]; tests.value = data.value.settings.include_tests; initialized = true;
        }
        if (sound.value && context?.state === 'running') {
          if (active.value.some(a => !heard.has(a.id))) chime();
          active.value.forEach(a => heard.add(a.id));
        }
      } catch { online.value = false; }
      finally { clock.value = Date.now(); if (!closed) timer = setTimeout(poll, 1500); }
    }
    async function save() {
      saving.value = true; error.value = '';
      try { data.value = await request('/settings', { counties: selected.value, include_tests: tests.value }); }
      catch (e) { error.value = e.message; }
      finally { saving.value = false; }
    }
    function add(code) {
      if (!/^[0-9]{6}$/.test(code)) { error.value = 'Enter a six-digit SAME code, including its leading zero.'; return; }
      if (!selected.value.includes(code) && selected.value.length < 20) selected.value.push(code);
      error.value = ''; manual.value = '';
    }
    async function acknowledge(id) {
      try { data.value = await request('/ack', { id }); }
      catch (e) { error.value = e.message; }
    }
    const name = code => { const c = counties.value.find(c => c.code === code); return c ? `${c.name}, ${c.state} (${code})` : code; };
    const date = value => new Date(value * 1000).toLocaleString();
    onMounted(() => { poll(); request('/counties').then(c => { counties.value = c; }).catch(e => { error.value = e.message; }); });
    onUnmounted(() => { closed = true; clearTimeout(timer); context?.close(); });
    return { data, selected, states, stateCode, county, choices, tests, manual, error, sound, online,
      active, saving, save, add, acknowledge, enableSound, name, date, disableSound: () => { sound.value = false; } };
  },
  template: `
    <p v-if="!showSettings && data?.settings.counties.length" class="notice">Weather alerts: {{!online?'service disconnected':data.error?'decoder error':data.monitoring?'monitoring NOAA audio':'not monitoring — tune NOAA Weather'}}.</p>
    <section v-for="a in active" :key="a.id" class="same-banner" role="alert">
      <strong>{{sourceName ? sourceName+' · ' : ''}}{{a.test ? 'TEST · ' : ''}}{{a.title}}</strong>
      <p>{{a.locations.map(name).join(' · ')}}</p>
      <p>Issued {{date(a.issued)}} · Expires {{date(a.expires)}} · {{a.sender}}</p>
      <p>Listen to the NOAA broadcast for details and instructions.</p>
      <button @click="acknowledge(a.id)">Dismiss alert</button>
    </section>
    <p v-if="!showSettings&&data?.monitoring" class="notice">{{sourceName}} · Monitoring NOAA <button @click="sound?disableSound():enableSound()">{{sound?'Disable alert sound':'Enable alert sound & test'}}</button></p>
    <section v-if="showSettings" class="panel same-settings">
      <div class="panel-title"><span>NOAA WEATHER ALERTS</span><span>{{!online?'Alert service disconnected':data?.error?'Decoder error':data?.monitoring?'Monitoring NOAA audio':'Not monitoring — tune a NOAA channel'}}</span></div>
      <p>Keep the receiver on a NOAA channel covering your county and this page open. Playback may be muted. Browser sleep or a closed page prevents banner and sound delivery.</p>
      <p v-if="data?.error" class="error">{{data.error}}</p>
      <p v-if="error" class="error">{{error}}</p>
      <div class="same-controls">
        <label>State / territory<select v-model="stateCode" @change="county='' "><option value="">Select state…</option><option v-for="s in states" :value="s">{{s}}</option></select></label>
        <label>County<select v-model="county"><option value="">Select county…</option><option v-for="c in choices" :value="c.code">{{c.name}}</option></select></label>
        <button @click="add(county)" :disabled="!county">Add county</button>
        <label>Or SAME code<input v-model="manual" maxlength="6" inputmode="numeric" placeholder="053077"></label><button @click="add(manual)" :disabled="!manual">Add code</button>
      </div>
      <p v-if="!selected.length">Select and save a county to enable alerts.</p>
      <div class="same-controls"><button v-for="code in selected" @click="selected=selected.filter(c=>c!==code)" :aria-label="'Remove '+name(code)">{{name(code)}} ×</button></div>
      <label><input type="checkbox" v-model="tests"> Include test broadcasts (weekly / monthly / demonstration)</label>
      <div class="same-controls"><button @click="save" :disabled="saving||!online">Save alert settings</button>
      <button v-if="!sound" @click="enableSound">Enable alert sound & test</button><button v-else @click="disableSound">Alert sound enabled · Disable</button></div>
      <p class="muted">Alerts include warnings, watches, and other SAME messages for your saved areas. Sound is enabled separately in each browser session. County list: <a href="https://www.weather.gov/nwr/counties" target="_blank" rel="noopener">NOAA</a>; enter a current SAME code manually if your county is missing.</p>
      <details v-if="data?.history.length"><summary>Recent matching alerts</summary><p v-for="a in data.history" :key="a.id">{{a.title}} · {{date(a.received)}} · {{a.acknowledged?'Dismissed':a.expires*1000<Date.now()?'Expired':'Active'}}</p></details>
    </section>`
};
