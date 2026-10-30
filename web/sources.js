import {ref, onMounted, onUnmounted} from 'vue';
import {selectedReceiver} from './model.js';
import SameAlerts from './alerts.js';

export default {
  components: {SameAlerts},
  props: {showAlertSettings:Boolean},
  emits: ['selected-source'],
  setup(_props,{emit}) {
    const registry = ref(null), error = ref(''), busy = ref(false), expanded = ref(false);
    const empty = () => ({id:'',name:'',kind:'sdr',location:'',antenna:'',purpose:'',connection:{transport:'local_soapy',driver:'rtlsdr',serial:'',host:'',port:1234,device_id:'',tuner:0}});
    const form = ref(empty());
    let timer, closed = false;
    async function request(path, body) {
      const response = await fetch(path, {cache:'no-store', signal:AbortSignal.timeout(40000),
        ...(body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Source manager unavailable');
      return result;
    }
    async function poll() {
      try { registry.value = await request('/api/sources'); const s=registry.value.sources.find(s=>receiver(s).id===selectedReceiver); if(s) emit('selected-source',s); }
      catch (e) { error.value = e.message; }
      finally { if (!closed) timer = setTimeout(poll,5000); }
    }
    function select(id) { location.assign('/?receiver=' + encodeURIComponent(id)); }
    function edit(source) {
      form.value = {...empty(),...source,connection:{...empty().connection,...source.connection}};
      delete form.value.capabilities;
      if (form.value.connection.transport === 'legacy') form.value.connection.transport = 'local_soapy';
      expanded.value = true;
    }
    async function save() {
      busy.value = true; error.value = '';
      try { registry.value = await request('/api/sources',form.value); form.value = empty(); }
      catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    async function lifecycle(receiver, action) {
      if (action === 'stop' && !confirm('Stop this receiver session? Its reception, recording and NOAA monitoring will stop.')) return;
      busy.value = true; error.value = '';
      try { registry.value = await request(`/api/receivers/${receiver.id}/lifecycle`,{action}); }
      catch (e) { error.value = e.message; }
      finally { busy.value = false; }
    }
    function receiver(source) { return registry.value.receivers.find(r=>r.source_id===source.id); }
    onMounted(poll); onUnmounted(()=>{closed=true;clearTimeout(timer);});
    function changeKind() { form.value.connection.transport = form.value.kind==='hdhomerun'?'http':'local_soapy'; }
    return {changeKind,registry,error,busy,expanded,form,selectedReceiver,select,edit,save,lifecycle,receiver,
      add:()=>{form.value=empty();expanded.value=true;}};
  },
  template: `
  <section class="panel source-panel" aria-label="Sources and receiver sessions">
    <div class="panel-title"><span>SOURCES & RECEIVERS</span><button @click="expanded=!expanded">{{expanded?'Hide sources':'Manage sources'}}</button></div>
    <template v-if="registry">
      <label>Listening workspace <select :value="selectedReceiver" @change="select($event.target.value)"><option v-for="s in registry.sources" :value="receiver(s).id" :key="s.id">{{s.name}} · {{receiver(s).status}}</option></select></label>
      <p class="muted">Changing workspaces changes browser playback. Other receivers keep receiving and recording.</p>
      <div v-if="expanded">
        <article v-for="s in registry.sources" :key="s.id" class="source-row">
          <strong>{{s.name}}</strong> · {{receiver(s).status}}<span v-if="s.location"> · {{s.location}}</span><span v-if="s.purpose"> · {{s.purpose}}</span>
          <p>{{s.kind==='hdhomerun'?'HDHomeRun · '+s.connection.host+' · tuner '+s.connection.tuner:s.connection.transport==='legacy'?'Existing receiver settings — bind a serial before adding another source':s.connection.transport+' · '+(s.connection.host||'USB')+' · '+(s.connection.serial||'rtl_tcp')}}</p>
          <p v-if="receiver(s).error" class="notice error">{{receiver(s).error}}</p>
          <button @click="select(receiver(s).id)">Open</button>
          <button :disabled="busy||['starting','stopping'].includes(receiver(s).status)" @click="lifecycle(receiver(s),receiver(s).status==='running'?'stop':'start')">{{receiver(s).status==='running'?'Stop session':'Start session'}}</button>
          <button :disabled="busy||['starting','running','stopping'].includes(receiver(s).status)" @click="edit(s)">Edit source</button>
        </article>
        <button @click="add" :disabled="busy||registry.sources.length>=8">Add source</button>
        <form @submit.prevent="save" class="source-form">
          <h3>{{form.id||'New source'}}</h3>
          <label>Source ID<input v-model="form.id" required pattern="[a-z][a-z0-9-]{0,31}" maxlength="32" placeholder="roof-noaa"></label>
          <label>Name<input v-model="form.name" required maxlength="160" placeholder="Roof NOAA receiver"></label>
          <label>Source type<select v-model="form.kind" @change="changeKind"><option value="sdr">SDR receiver</option><option value="hdhomerun">HDHomeRun TV tuner</option></select></label>
          <label v-if="form.kind==='sdr'">Connection<select v-model="form.connection.transport" @change="form.connection.port=form.connection.transport==='soapy_remote'?55132:1234"><option value="local_soapy">Local USB SDR</option><option value="rtl_tcp_remote">Remote rtl_tcp</option><option value="soapy_remote">Remote SoapySDR</option></select></label>
          <template v-if="form.kind==='sdr'&&form.connection.transport!=='rtl_tcp_remote'"><label>Radio<select v-model="form.connection.driver"><option value="rtlsdr">RTL-SDR</option><option value="hackrf">HackRF</option></select></label><label>Device serial<input v-model="form.connection.serial" required placeholder="Unique hardware serial"></label></template>
          <template v-if="form.connection.transport!=='local_soapy'"><label>Remote hostname / IPv4<input v-model="form.connection.host" required placeholder="192.168.1.50"></label><label v-if="form.kind==='sdr'">Port<input type="number" v-model.number="form.connection.port" min="1" max="65535" required></label></template>
          <template v-if="form.kind==='hdhomerun'"><label>Device ID<input v-model="form.connection.device_id" required pattern="[0-9A-Fa-f]{8}" maxlength="8" placeholder="Eight hexadecimal digits"></label><label>Tuner number (starts at 0)<input type="number" v-model.number="form.connection.tuner" min="0" max="15" required></label></template>
          <label>Location<input v-model="form.location" maxlength="160"></label><label>Antenna description<input v-model="form.antenna" maxlength="160"></label><label>Purpose<input v-model="form.purpose" maxlength="160" placeholder="Continuous NOAA monitoring"></label>
          <button type="submit" :disabled="busy">Save source</button>
        </form>
        <p class="muted">Start the session here, then open its workspace and start reception. Stop a session before editing its source. Each SDR needs a unique serial. Remote endpoints must identify distinct hardware; aliases cannot be detected automatically. HDHomeRun sources bind one numbered tuner each; add a source for each tuner you want to use.</p>
      </div>
    </template>
    <p v-if="error" class="notice error">{{error}}</p>
  </section>
  <template v-if="registry"><template v-for="s in registry.sources" :key="s.id"><SameAlerts v-if="s.kind==='sdr'||!s.kind" :receiver-id="receiver(s).id" :source-name="s.name" :show-settings="showAlertSettings&&receiver(s).id===selectedReceiver" /></template></template>`
};
