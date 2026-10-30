import {ref,computed,watch,onUnmounted,nextTick} from 'vue';
import Hls from 'hls.js';
import AtscDx from './atsc-dx.js';
import {receiverUrl} from './model.js';

export default {
  components: {AtscDx},
  props: {state:Object, connected:Boolean},
  emits: ['update-state'],
  setup(props,{emit}) {
    const video=ref(null), selected=ref(''), query=ref(''), busy=ref(false), error=ref(''), watching=ref(false);
    const channels=computed(()=>props.state?.channels||[]);
    const filtered=computed(()=>channels.value.filter(c=>(c.number+' '+c.name).toLowerCase().includes(query.value.toLowerCase())));
    let hls, attached=null, closed=false;
    function detach() {
      hls?.destroy(); hls=null; attached=null;
      if (video.value) { video.value.pause(); video.value.removeAttribute('src'); video.value.load(); }
    }
    async function attach() {
      const playback=props.state?.playback;
      if (closed||!watching.value||!playback?.ready||attached===playback.generation) return;
      await nextTick();
      if (closed||!video.value||!watching.value) return;
      detach(); attached=playback.generation;
      const url=receiverUrl(playback.url);
      const play=()=>video.value?.play().catch(()=>{error.value='Press Play in the video controls to enable playback.';});
      if (video.value.canPlayType('application/vnd.apple.mpegurl')) {
        video.value.src=url; play();
      } else if (Hls.isSupported()) {
        hls=new Hls({maxBufferLength:12,backBufferLength:10,liveSyncDurationCount:3});
        hls.on(Hls.Events.MANIFEST_PARSED,play);
        hls.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal) {error.value='Video playback failed: '+data.details+'. Check the receiver status and retry Watch channel.';detach();}});
        hls.loadSource(url); hls.attachMedia(video.value);
      } else { error.value='This browser does not support HLS video playback.'; watching.value=false; }
    }
    async function command(action,body={}) {
      busy.value=true; error.value='';
      try {
        const response=await fetch(receiverUrl('/api/tv/'+action),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(40000)});
        const value=await response.json();
        if(!response.ok) throw new Error(value.error||'TV request failed');
        if (closed) return;
        emit('update-state',value);
        if(action==='play') {watching.value=true;await nextTick();attach();}
        if(action==='stop') {watching.value=false;detach();}
      } catch(e) {error.value=e.message;}
      finally {busy.value=false;}
    }
    watch(channels,rows=>{if(!selected.value) selected.value=props.state?.channel||rows.find(c=>!c.unavailable_reason)?.number||'';},{immediate:true});
    watch(()=>[props.state?.playback?.generation,props.state?.playback?.ready],()=>{
      if(!props.state?.playback?.running) detach();
      else attach();
    });
    onUnmounted(()=>{closed=true;detach();});
    return {video,selected,query,busy,error,watching,channels,filtered,command,
      closePlayback:()=>{watching.value=false;detach();}};
  },
  template: `
  <section class="panel television">
    <div class="panel-title"><span>LIVE TELEVISION</span><span>{{state?.device?.FriendlyName||'HDHomeRun'}} · tuner {{state?.tuner??'—'}}</span></div>
    <p v-if="!connected" class="notice">Start this TV session in Sources to load its channel lineup.</p>
    <p v-if="state?.device_error" class="notice error">{{state.device_error}}. Cached channels may be stale.</p>
    <p v-if="state?.playback_error" class="notice error">{{state.playback_error}}</p><p v-if="error" class="notice error">{{error}}</p>
    <div class="tv-controls">
      <label>Find a channel<input v-model="query" placeholder="Channel number or station name"></label>
      <label>Channel<select v-model="selected"><option value="" disabled>Choose a channel</option><option v-for="c in filtered" :key="c.number" :value="c.number" :disabled="!!c.unavailable_reason">{{c.number}} · {{c.name}}{{c.unavailable_reason?' · Unavailable':''}}</option></select></label>
      <button class="primary" :disabled="busy||!connected||!selected" @click="command('play',{channel:selected})">Watch channel</button>
      <button :disabled="busy||!connected" @click="command('refresh')">Refresh lineup</button>
      <button v-if="watching" @click="closePlayback">Close my playback</button>
      <button :disabled="busy||!state?.playback?.running" @click="command('stop')">Stop stream for everyone</button>
    </div>
    <p v-if="state?.playback?.running" role="status">Channel {{state.channel}} · {{state.playback.ready?'Live stream ready':'Preparing video… allow several seconds for buffering.'}}</p>
    <video ref="video" controls playsinline preload="none" aria-label="Live HDHomeRun television" @error="error='Browser video error. Check stream status and retry Watch channel.'"></video>
    <p class="muted">This tuner has one shared channel. Changing it affects all viewers of this source. Closing your playback leaves other viewers connected; the stream releases the tuner after 60 seconds without video requests. SDR receivers keep running independently.</p>
    <details><summary>Channels and device information</summary>
      <p>{{state?.device?.ModelNumber}} · Device {{state?.device?.DeviceID}} · Firmware {{state?.device?.FirmwareVersion}} · {{state?.device?.TunerCount}} tuners</p>
      <p>Lineup refreshed: {{state?.lineup_updated?new Date(state.lineup_updated*1000).toLocaleString():'Not loaded'}}</p>
      <div class="table-wrap"><table><thead><tr><th>Channel</th><th>Station</th><th>Video</th><th>Audio</th><th>Availability</th></tr></thead><tbody><tr v-for="c in filtered" :key="c.number"><td>{{c.number}}</td><td>{{c.name}}</td><td>{{c.video_codec||'Not reported'}}</td><td>{{c.audio_codec||'Not reported'}}</td><td>{{c.unavailable_reason||'Try playback'}}</td></tr></tbody></table></div>
    </details>
    <details v-if="state?.diagnostics?.length"><summary>Playback diagnostics</summary><pre>{{state.diagnostics.join('\n')}}</pre></details>
    <p class="muted">Browser playback transcodes to H.264/AAC, up to 720p30, using server CPU. Protected channels and AC-4 audio are not supported. Lineup data is device-reported; RF diagnostics and scans are available below.</p>
  </section>
  <AtscDx :dx="state?.dx" :connected="connected" :busy="busy" :playing="!!state?.playback?.running" @command="command" />`
};
