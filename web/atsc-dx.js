import {ref,watch,computed} from 'vue';
import {receiverUrl} from './model.js';

export default {
  props:{dx:Object,connected:Boolean,busy:Boolean,playing:Boolean},
  emits:['command'],
  setup(props) {
    const first=ref(2),last=ref(36),dwell=ref(4),notes=ref(''),entries=ref([]),error=ref(''),loading=ref(false);
    const sample=computed(()=>props.dx?.measurement);
    const scan=computed(()=>props.dx?.scan);
    async function load(older=false) {
      if(loading.value) return;
      loading.value=true;error.value='';
      try {
        const cursor=older?entries.value.at(-1)?.id:null;
        const response=await fetch(receiverUrl('/api/tv/receptions'+(cursor?'?before='+cursor:'')),{signal:AbortSignal.timeout(10000)});
        const result=await response.json();
        if(!response.ok) throw new Error(result.error||'Could not load reception log');
        entries.value=older?[...entries.value,...result.entries]:result.entries;
      } catch(e) {error.value=e.message;} finally {loading.value=false;}
    }
    watch(()=>props.connected,value=>{if(value) load();},{immediate:true});
    watch(()=>props.dx?.last_logged,()=>{if(props.connected) load();});
    return {first,last,dwell,notes,entries,error,loading,load,sample,scan,receiverUrl,
      time:value=>value?new Date(value).toLocaleString():'Not measured',
      percent:value=>value==null?'Not reported':value+'%'};
  },
  template:`
  <section class="panel">
    <div class="panel-title"><span>ATSC RECEPTION / DX</span><span>{{dx?.context?.antenna||'Antenna not set'}} · {{dx?.context?.location||'Location not set'}}</span></div>
    <p class="muted">Set antenna and location in Sources while this session is stopped. Each observation keeps those labels permanently.</p>
    <p v-if="dx?.error" class="notice error">{{dx.error}}</p>
    <div class="tv-controls">
      <label v-for="metric in [['strength','Signal strength'],['quality','Signal quality'],['symbol_quality','Symbol quality']]" :key="metric[0]">
        {{metric[1]}}: {{percent(sample?.[metric[0]])}}
        <meter v-if="sample?.[metric[0]]!=null" min="0" max="100" :value="sample[metric[0]]" :aria-label="metric[1]"></meter>
      </label>
    </div>
    <p>RF {{sample?.rf_channel??'unknown'}} · {{sample?.frequency_hz?sample.frequency_hz/1000000+' MHz':'Frequency not reported'}} · Lock: {{sample?.lock||'unknown'}} · {{sample?.bits_per_second??'—'}} bit/s</p>
    <p class="muted">Measured: {{time(sample?.observed_at)}}. Vendor percentages, not dBm or SNR in dB. RF mapping uses the US broadcast plan; virtual channel numbers are shown below only when reported by the tuner.</p>
    <pre v-if="sample?.streaminfo">{{sample.streaminfo}}</pre>
    <details><summary>Scan physical RF channels</summary>
      <p>US/Canada broadcast RF 2–36. Stop this source’s TV stream first. Scans reserve its tuner, leave the saved channel lineup unchanged, and release the tuner afterward. Other receivers remain available.</p>
      <div class="tv-controls">
        <label>First RF<input type="number" min="2" max="36" v-model.number="first"></label>
        <label>Last RF<input type="number" min="2" max="36" v-model.number="last"></label>
        <label>Dwell seconds<input type="number" min="2" max="15" v-model.number="dwell"></label>
        <button :disabled="busy||!connected||playing||scan?.running||dx?.cleanup_pending" @click="$emit('command','scan',{first,last,dwell})">Start RF scan</button>
        <button :disabled="busy||!scan?.running" @click="$emit('command','scan-cancel',{})">Cancel scan</button>
        <button v-if="dx?.cleanup_pending" :disabled="busy||!connected" @click="$emit('command','scan-release',{})">Retry tuner release</button>
      </div>
      <p v-if="playing" class="notice">Stop stream for everyone above to enable scanning.</p>
      <p role="status">{{scan?.status||'idle'}} · {{scan?.completed||0}} / {{scan?.total||0}} measured<span v-if="scan?.running"> · RF {{scan.current_rf??'preparing'}}</span></p>
      <progress v-if="scan?.running" :max="scan.total||1" :value="scan.completed||0"></progress>
      <p v-if="scan?.error" class="notice error">{{scan.error}}</p>
      <p class="muted">Each completed dwell is logged, including no-lock attempts. A lock confirms reception at that instant, not station identity or sustained watchability. Increase dwell for weak signals or slow service discovery. ATSC 3.0 reporting depends on the device firmware.</p>
    </details>
    <div class="tv-controls">
      <label>Reception notes<input v-model="notes" maxlength="1000" placeholder="Antenna direction, propagation, conditions"></label>
      <button :disabled="busy||!connected||scan?.running||!sample?.locked" @click="$emit('command','log',{notes})">Log current reception</button>
      <button :disabled="loading||!connected" @click="load()">Refresh log</button>
      <a :href="receiverUrl('/api/tv/receptions.json')" download="receptions.json">Export retained history (JSON)</a>
    </div>
    <p v-if="error" class="notice error">{{error}}</p>
    <div class="table-wrap"><table><thead><tr><th>UTC observation</th><th>Source / antenna / location</th><th>RF / lock</th><th>Strength / quality / symbol</th><th>Services / notes</th></tr></thead>
      <tbody><tr v-for="entry in entries" :key="entry.id">
        <td>{{entry.measurement.observed_at}}<br>{{entry.reason}}{{entry.measurement.locked?' · reception':' · no confirmed lock'}}</td>
        <td>{{entry.source.name||entry.source.id}}<br>{{entry.source.antenna||'Not set'}}<br>{{entry.source.location||'Not set'}}<br>{{entry.device_id}} / tuner {{entry.tuner}}</td>
        <td>{{entry.measurement.rf_channel??entry.requested_rf??'unknown'}} / {{entry.measurement.lock}}</td>
        <td>{{percent(entry.measurement.strength)}} / {{percent(entry.measurement.quality)}} / {{percent(entry.measurement.symbol_quality)}}</td>
        <td><pre>{{entry.measurement.streaminfo}}</pre>{{entry.notes}}</td>
      </tr></tbody></table></div>
    <p v-if="!entries.length">No observations loaded. Log a current reception or run an RF scan.</p>
    <button v-if="entries.length" :disabled="loading||!connected" @click="load(true)">Load older entries</button>
    <p class="muted">The newest 10,000 observations are retained per receiver across restarts. Export to keep a longer archive.</p>
  </section>`
};
