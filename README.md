# ROSE Radio — native web HD Radio receiver

A headless adaptation of [NRSC5 Studio](https://github.com/LTCAshraven/nrsc5-studio)
for MikroTik ROSE ARM64, with a new Vue interface. Target deployment: **RouterOS
7.25**, RTL-SDR or HackRF assigned through the container's USB device settings.

The browser sends API commands to the receiver and renders metadata, artwork,
charts and maps itself. The container runs **no X server, VNC server, desktop,
GPU renderer, ALSA playback device or PulseAudio daemon**. Decoded PCM goes directly
from the Rust receiver to FFmpeg, then to the browser as a live MP3 stream.

This should remove desktop-rendering and screen-streaming overhead. It does not
reduce the underlying SDR/DSP work; actual CPU, RAM and latency improvements must
be measured on the ROSE. MP3 playback still has browser-dependent buffering.

HackRF capture uses **8 MS/s** with a 1.75 MHz baseband filter, then software
resampling to the decoder's exact **1.488375 MS/s**. RTL-SDR still captures at the
decoder rate directly. The HackRF path therefore requires more USB bandwidth and
CPU. This follows the [HackRF sampling guidance](https://hackrf.readthedocs.io/en/stable/sampling_rate.html);
requesting the decoder rate directly from HackRF is below its supported range.
The startup log reports the capture and decoder rates. If HackRF opens but
`rx_stream.read` times out, first confirm the rebuilt image reports
`driver=hackrf capture=8000000`; then check USB cable, power, passthrough and
whether another service owns the radio. Device discovery alone does not confirm
working sample transfers. Hardware validation of this resampling path is pending.

## Feature coverage

The adapter preserves Studio's existing command handlers and event processing,
rather than reimplementing the decoder. The frontend exposes these capabilities:

| Feature | Native web experience |
| --- | --- |
| FM tuning, HD1–HD8, active speaker | Persistent tuner, channel buttons, shared receiver state |
| Presets | Save, recall, rename, retarget, clear; 1–48 slots |
| Title, artist, album, genre, art and logos | Now Playing with stale-metadata indicators |
| Analog fallback | Digital / automatic / analog; stereo and RDS controls |
| Station/SIS information | Call sign, slogans, messages, RDS, FCC link, location, program/data services, alerts |
| Engineering information | Service mode, carrier offset, exciter/importer, broadcaster time, leap seconds, AM indicators, payload log |
| Signal and AGC | MER per sideband, BER, gain, controller phase/reason/cache state; auto/manual/hardware gain |
| Device settings | Discovery, device selection, per-element gains, antenna, PPM, gain-cache reset and RMS target |
| Remote SDRs | SoapyRemote and rtl_tcp configuration |
| Spectrum / waterfall | Browser canvas from the real upstream FFT tap; smoothing controls |
| Constellation | Measured post-equalization symbols, separate sidebands: FM QPSK and AM primary 64-QAM |
| Artwork collage | Rolling eight-hour cache, play weighting, tile limit, cover blocking/unblocking |
| Traffic and weather | Existing tile/composite processors, weather animation, pause and timeline scrub |
| Song history | Persistent timeline, top-played grouping, search, retention, CSV download |
| Opus recording | Lock to one HD channel while listening to another, file rotation, station subfolders, browser downloads |
| Appearance / workspace | Responsive light/dark interface; reorder sections, preferences stored per browser |

Desktop window docking/floating panes are replaced with responsive web sections,
not emulated. Native save dialogs are replaced by browser downloads. Recording
folders are confined to `/data/recordings` (subfolders are configurable) so every
recording stays in persistent storage and the download library.

**Coverage is implemented, not hardware-certified.** The full ARM64 compile and
radio-specific regression tests still need the first Actions run; physical RTL-SDR
and HackRF reception, maps and recording need validation on your ROSE. Upstream
describes HackRF as profile-only/not bench-validated. This project does not change
that status. Each receiver session is shared by signed-in users; separate sources have independent sessions.

## GitHub Actions: build on push

1. Push this repository to GitHub. No fork or edits to the upstream repo are needed.
2. Open **Actions → Build and publish ROSE ARM64 image**. Every push, PR and manual
   dispatch builds on `ubuntu-24.04-arm`. Your account must support that runner.
3. Successful push builds publish **`ghcr.io/OWNER/REPOSITORY:latest`** on the
   default branch. Other branches and Git tags get their own tags; every published
   build gets `sha-FULL_COMMIT_SHA`. PRs test without publishing.
4. Make the package public in GitHub Packages if the ROSE should pull anonymously.
   Packages may initially be private. The workflow uses `GITHUB_TOKEN` with
   `packages: write`; no extra registry secret is required unless organization
   policies prevent package publishing.
5. A Docker-save **`hd-radio-rose-arm64.tar.gz`** is also uploaded as a workflow
   artifact, retained for 14 days. Unzip the artifact download before importing the
   contained `.tar.gz` into RouterOS. GHCR images remain until removed.

The image is a single `linux/arm64` manifest without attached provenance/SBOM
manifests, for straightforward RouterOS import. Before publishing, CI builds the
Vue bundle, runs Python/JS tests and upstream Rust binary unit tests, and tests:
headless readiness, API settings/presets, authentication, invalid commands,
cross-origin rejection, file-path confinement, CSV export, installed SDR modules,
direct PCM-to-MP3 audio using an injected tone, and restart/persistence. No SDR is
attached to the CI runner, so this does not test RF reception.

## RouterOS 7.25 setup in WinBox / WebFig

Reuse your working OpenWebRX container network and USB assignment approach.
The [MikroTik container manual](https://manual.mikrotik.com/docs/containers/)
documents the network, mounts, environment and device settings; its current online
edition may describe a newer RouterOS version, so use the fields offered by 7.25.

1. Confirm the container package and container mode are enabled (already true if
   OpenWebRX is working).
2. Create a dedicated **veth** with an unused IP and gateway on your container
   network. Add it to the appropriate bridge. Allow your trusted LAN/VPN to reach
   the container's IP on TCP **8080**, using your existing routing/NAT arrangement.
3. Use a fresh disk-backed root directory, e.g. `disk1/containers/hd-radio-rose`,
   and a separate persistent folder, e.g. `disk1/appdata/hd-radio-rose`. Substitute
   your actual disk name. Allow several GB for extraction and source archives.
4. In **Containers → Mounts**, map the persistent folder to **`/data`**, then attach
   that mount/list to the container. Keep it separate from the disposable root.
5. Create and attach an environment list:

   | Key | Value |
   | --- | --- |
   | `WEB_USER` | `radio` |
   | `WEB_PASSWORD` | Your own long password |

6. Set the registry to **`https://ghcr.io`**, with its extraction temporary directory
   on disk. This setting is global; note the previous registry if other images use it.
7. Add **`OWNER/REPOSITORY:latest`** (lowercase) as the remote image. Choose the veth,
   root directory, data mount and environment list. Enable logging/start-on-boot.
   Leave the image user unchanged, or set **`0:0`**: root is used for USB access
   without requiring host udev/group configuration.
8. In **Devices**, select the physical USB receiver using the same GUI process
   that worked with OpenWebRX. Use the actual device entry offered by your ROSE;
   a USB path from another machine is not transferable. Stop any other service
   using that SDR. One USB receiver cannot be shared by two running decoders.
9. Wait for extraction, start the container, and open **`http://CONTAINER_IP:8080`**.
   Log in with the configured username/password.

For offline import, upload the workflow's `.tar.gz` to the ROSE disk and select it
as the container image file instead. For private GHCR packages, configure registry
read credentials in RouterOS or use the archive.

No `DISPLAY`, screen-size variables, shared-memory override, `/dev/dri`, or sound
device mapping is needed. Start with about 1–2 GB RAM available and adjust after
measurement; this is a starting allocation, not a tested minimum.

## First tune

Open **Settings**, choose local USB transport, refresh devices, and select your
RTL-SDR or HackRF. In the tuner, enter a known HD Radio FM frequency, choose HD1
or another channel, and press **Start receiver**. Allow time for signal lock.
Press **Listen** to enable audio; browsers require this gesture. Browser volume is
per listener; Settings → Receiver volume/mute affects everyone.

Recording follows the selected HD program when you start it, even after changing
the listening channel. Use **Recordings** to download completed `.opus` files.
Traffic/weather and artwork appear only when the tuned station broadcasts them.
CSV export downloads directly from **Song log**. Data updates progressively;
absence of a particular broadcast service is not an API failure.

## Migration from the virtual-desktop image

Stop the old container, back up `/data`, replace the image, and reuse that mount.
Studio's config, presets, song log and artwork cache retain their existing formats.
Old native window layout data is left alone but is not used. Desktop recording
paths outside `/data/recordings` are reset to that folder. Move any older recording
files there if you want them listed in the web library. Obsolete screen variables
can be removed. The public port and login variables remain unchanged.

## Security and API

`WEB_PASSWORD` is required unless you deliberately set `ALLOW_ANONYMOUS=true`.
Basic auth protects the UI, API, media, recordings and audio; use a trusted LAN/VPN
or an HTTPS reverse proxy. No public Internet exposure is configured. An HTTPS
proxy should preserve the Host header and avoid buffering `/audio.mp3`.

| Endpoint | Purpose |
| --- | --- |
| `GET /healthz` | Unauthenticated boolean; recent engine heartbeat + audio encoder output |
| `GET /api/state` | Receiver, station, program, device, artwork, map and config snapshot |
| `GET /api/spectrum` | FFT bins, generation, sample rate, center frequency |
| `GET /api/constellation` | Latest measured sideband I/Q pairs, modulation, generation, sample age and availability |
| `GET /api/log`, `/api/log.csv` | History JSON / CSV download |
| `GET /api/recordings` | Downloadable recording listing |
| `GET /api/recording?name=…` | Confined `.opus` download |
| `GET /api/media?path=…` | Confined broadcast image access |
| `POST /api/command` | Validated JSON command; e.g. `{"type":"TuneMhz","value":101.1}` |
| `GET /audio.mp3` | Shared 160 kbps stereo MP3 encoder; up to 16 listeners |
| `GET /sources.tar.gz` | Corresponding source archive bundled with this image |

All control requests are serialized through the Rust model. Cross-origin browser
commands and oversized/invalid inputs are rejected. Browser clients poll state at
1 Hz and spectrum at up to 4 Hz while visible; polling slows in background tabs.
Internal API/PCM services bind loopback only. Platform health indicates that the receiver manager is available. Per-receiver
health additionally checks engine heartbeat and audio output; neither indicates RF lock or
USB access. Optional restart-on-failure is configured in RouterOS, independently
of start-on-boot. Give the container at least 25 seconds to stop so active Opus
files and persistent state can be finalized.

## Troubleshooting

In the RouterOS container shell:

```sh
lsusb
ls -l /dev/bus/usb
SoapySDRUtil --info
SoapySDRUtil --find
SoapySDRUtil --probe="driver=rtlsdr"
# Or, with the HackRF assigned:
SoapySDRUtil --probe="driver=hackrf"
```

Stop reception before probing a device. Missing receivers usually mean USB
assignment, power/cabling, or another service holding the device. Refresh the ROSE
device assignment after replugging if its identity changes. Linux udev rules inside
the container cannot repair missing RouterOS passthrough. The image includes
SoapyRTLSDR, SoapyHackRF and SoapyRemote; proprietary SDRplay API software is not
included. Debian Bookworm's RTL-SDR driver predates some newer dongles such as
Blog V4; those require a newer driver build, independently of the web interface.

If audio lags, pause and resume Listen to join the live edge. Browsers may suspend
audio in background tabs or on locked phones. Check receiver volume/mute and RF
lock. Encoder/engine failures are logged and stop the container. For rollback,
retain an immutable `sha-…` image tag and a data backup.

## Development and verification

```sh
python3 -m unittest discover -s tests -v
cd web
npm ci --ignore-scripts
npm test
npm run build
cd ..
python3 scripts/preview.py
```

The preview at `http://127.0.0.1:8090` is explicitly labeled **fixture-only**. It has
no receiver engine and rejects radio commands; it is never started by the image.
For an actual Linux ARM64 Docker host:

```sh
cp .env.example .env  # edit your password
docker compose up --build -d
# Or run the complete hardware-independent container smoke test:
docker buildx build --platform linux/arm64 --load -t hd-radio-rose:test .
bash scripts/smoke-test.sh hd-radio-rose:test
```

Compose maps the USB bus with character-device permissions without privileged
mode. An x86 host needs ARM64 emulation. Windows Docker Desktop USB passthrough
is not set up here. The `NRSC5_SELF_TEST=1` environment variable is used only by
CI to enable a loopback-only test-tone/metadata injection operation; leave it unset
for ordinary deployments.

The local development checks cover Python tests, Vue template/model tests,
production frontend compilation, generated Rust syntax, and browser navigation
and narrow-screen layout. **Full Rust/ARM64 compilation, upstream unit tests and
container smoke tests have not run locally:** this workspace is Windows without
Docker/WSL. The first Actions run must pass before treating the image as validated.
No physical SDR or ROSE was available for end-to-end tests.

## Source and maintenance

Studio 0.6.5 is pinned to `61064d27fcb4e7a7a231b9f6aa546e59bee00084`; nrsc5 v3.2.0
to `a5c09723b135327aeef157770caae9aebf597547`. Rust is 1.95.0; Vue/Vite dependencies
are locked in `web/package-lock.json`. Base image tags and Debian packages may move.

`scripts/patch-studio.py` builds a headless entry point, extracts Studio's non-drawing
tick, exposes commands/snapshots, and replaces audio playback with direct PCM.
Upstream application/model code still depends on egui types at compile time; an
inert context receives repaint notifications, but **no UI frame is rendered**.
This conservative adapter keeps map/art/recording behavior intact while allowing
the frontend to evolve separately. Upstream changes require reviewing exact
patch anchors and rerunning CI. Decoder libraries use system FFTW/USB/RTL-SDR
instead of upstream's x86-specific bundled build, plus HDC-patched FAAD2.

Integration code is MIT. Studio source is MIT, but its libnrsc5-linked binary is
GPL-3.0; the image includes upstream notices, licenses, patched source, vendored
Rust dependencies and build/integration source. Preserve the bundled source
archive when redistributing images. Frontend dependencies can be restored from
the lockfile; their bundled license notices remain in the generated code.
System package copyright/source information is under `/usr/share/doc` and
[Debian Sources](https://sources.debian.org/).

### Tuning and seek

Start the receiver before entering a frequency, stepping, recalling a preset, or
seeking. Frequency entry accepts MHz (`98.3`) or shorthand (`983`, `1079`).
The API continues to accept MHz only and rejects tuning while stopped.

Seek scans 0.2 MHz channels in either direction, wraps once, and returns to the
starting frequency if none is detected. It selects HD1 and stops on HD lock,
RDS, or three fresh spectrum measurements indicating a centered FM signal.
Analog detection uses an 8 dB channel-to-background threshold and excludes the
DC spike; interference can cause false stops and weak stations may be skipped.
Digital-only mode seeks HD lock only. Real-radio seek sensitivity is unverified.
Each candidate gets up to six seconds after retuning; a full scan can take
several minutes. Cancel stops further tuning (an already submitted retune may
finish). Seeking runs in the browser, so keep the page open. Tuning stops any
active recording, as with ordinary station changes.

### NOAA Weather band

Select **NOAA Weather** in the tuner’s Band menu. Start the receiver, choose a
channel, then press Listen. The seven channel centers are 162.400, 162.425,
162.450, 162.475, 162.500, 162.525 and 162.550 MHz
([National Weather Service](https://www.weather.gov/iln/nwr_faq)).
Frequencies are labeled explicitly because weather channel numbering varies
between radio manufacturers.

The NOAA path uses the selected SoapySDR or rtl_tcp device, two-stage channel
filtering, a 5 kHz-deviation mono FM discriminator, 3 kHz voice filtering and
44.1 kHz stereo PCM (identical left/right). It bypasses the HD decoder.
HackRF retains its 8 MS/s capture/resampling path. NOAA uses manual RF gain;
the FM gain mode is preserved when switching back. Band and weather frequency
are stored in `/data/web-band.json`, independently of the FM frequency/presets.
Switching bands while running restarts reception and stops FM recording.

Seek scans only the seven weather channels using narrowband spectrum detection.
No NOAA recording, HD metadata or RDS is implemented.
The Traffic & weather tab remains the cached FM HD data
view; it is separate from NOAA voice broadcasts. Hardware reception and seek
sensitivity still need validation on the ROSE; CI includes a synthetic NFM
voice-tone demodulation test and band/API checks.

### County weather alerts (SAME)

In **NOAA Weather** or **Settings**, select a state and county, click **Add county**,
then **Save alert settings**. You can select up to 20 areas or enter a six-digit
SAME code manually. County codes are bundled from the
[NOAA county list](https://www.weather.gov/source/nwr/SameCode.txt) retrieved
2026-10-30; use manual entry for updated codes absent from that list.

Tune a NOAA channel that covers your county and leave the receiver running.
The panel shows whether NOAA audio is reaching the decoder; this status does
not prove that the station's signal is strong enough to decode SAME.
Changing to FM, stopping reception, or seeking interrupts weather monitoring.
One SDR cannot monitor NOAA while receiving an FM station.

Matching warnings, watches and other SAME messages produce a banner on every
workspace tab. Select **Enable alert sound & test** to permit a three-note chime
in that browser session. The page must stay open and the browser/device awake;
closed pages receive no notifications. Sound permission resets on reload.
Playback volume, playback mute and pausing the browser audio do not disable
decoding or the separate alert chime. Test broadcasts are excluded unless
**Include test broadcasts** is saved. Settings and dismissals are shared by
clients and saved in `/data/same-alerts.json`.

The pinned multimon-ng EAS decoder requires two matching headers out of three.
The service validates timestamps, matches county/subdivision/statewide codes,
ignores repeated alerts, and retains a bounded history. An end-of-message tone
ends the spoken message, not the alert's expiration time. SAME supplies an event
code and affected areas, not full warning text: listen to the broadcast for
details. Unknown event codes are displayed rather than silently discarded.

The decoder uses a bounded, pre-volume 22.05 kHz mono PCM tap. Retuning or audio
loss opens a fresh decoder session to avoid combining partial messages from
different stations. ARM64 builds test synthetic SAME audio for consensus,
duplicate rejection and end-of-message detection. Container smoke tests exercise
PCM delivery through the live alert API. Live reception remains unverified on
the ROSE; keep an established weather-alert source available during validation.
Decoder source is included in the image's corresponding-source archive.


### Broadcast AM and Ham It Up

Select **Broadcast AM / HD Radio** in the Band menu. This first AM band covers
**530–1700 kHz in 10 kHz steps** (North American channel spacing). Enter `1000`
for 1000 kHz; the API command is `{"type":"TuneAm","value":1000}` in kHz.
FM and NOAA commands continue using MHz.

With reception stopped, select **Ham It Up (+125 MHz)** (the default for AM),
power the converter, connect your MF/HF antenna to its input and the SDR to its
output, and put its physical switch in upconvert mode. The application tunes the
SDR to the station frequency plus 125 MHz: 1000 kHz becomes 126.000 MHz. The dial,
spectrum center and decoder metadata retain the original station frequency.
The offset follows [Nooelec’s Ham It Up documentation](https://support.nooelec.com/hc/en-us/articles/360005812474-Ham-It-Up-Upconverter).
Select **Native low-frequency tuning** only for hardware/drivers that can tune
these frequencies without a converter; this does not enable RTL direct sampling.
For FM or NOAA, put the converter in bypass or remove it from the antenna path.

Start reception and press **Listen**. In Settings, choose Automatic (analog/HD
handoff), Analog only, or Digital only. The receiver explicitly selects libnrsc5’s
AM mode for digital reception; analog AM uses a filtered mono envelope detector
with DC removal and carrier normalization, delivered as 44.1 kHz stereo PCM.
AM uses manual RF gain; adjust it in Settings. FM’s gain-mode preference survives
band changes. HD channels and metadata use the existing digital pipeline, and
Opus recording remains a digital-program feature. Analog AM recording is not
implemented. An all-digital AM station requires digital reception and lock.

AM frequency, FM frequency, band and converter selection are saved in
`/data/web-band.json`. Changing band stops recording and interrupts NOAA SAME
monitoring. AM seek and presets are not included yet; FM presets remain separate.
The spectrum still spans 1.488375 MHz with 1024 bins, so it is a broad overview,
not a high-resolution AM channel display. 9 kHz channel plans, shortwave, SSB/CW,
and multiple independent receivers are future work.

AM is implemented but **not hardware-validated**. Local checks cover input/API
validation, frontend compilation and generated Rust syntax. CI includes an AM
modulated-tone/adjacent-carrier DSP test and band/converter API smoke checks;
the full ARM64 build and reception of analog, hybrid HD and all-digital AM must
still be verified with the SDR and ROSE. The converter extends frequency access;
SDR hardware still determines dynamic range, usable bandwidth and simultaneous
receiver capability.


### Sources and independent receivers

The **Sources & receivers** panel manages up to eight independent source sessions (SDR or HDHomeRun tuner).
Each has its own engine process, audio encoder/stream, FM/AM/NOAA band settings,
presets, gain and converter settings, artwork, song history, recordings and SAME
alert decoder. A failed worker is marked failed and its companion service is
stopped; the manager and other receivers continue. Recovery is explicit through
**Start session**, rather than an unlimited restart loop.

Existing installations become the **Default receiver**, retaining the existing
`/data` layout and unprefixed API/audio URLs. No presets or recordings are moved.
Before adding a second source:

1. In Sources, stop the Default session. This finishes its recording and stops
   its reception/NOAA monitoring.
2. Edit Default and bind it to a unique device serial or remote endpoint.
3. Save it and start its session again.
4. Add another source with its own ID, name, connection, and optional location,
   antenna description and purpose. Start its session and open its workspace.
5. Choose FM, AM or NOAA and use **Start receiver** and **Listen** as before.

**Start session** launches the engine and audio services; **Start receiver** opens
hardware and begins RF reception. Enabled sessions return after a container
restart, with saved settings, but RF reception still requires Start receiver.
Changing the browser workspace stops that browser's playback and loads the other
receiver. It does not stop either receiver's reception, recording or SAME decoder.
Multiple browser tabs can listen independently. NOAA banners are polled across
registered sources and labeled by source; alert settings apply to the selected
receiver. Enable sound for each monitored source in each browser session. Reloads
and workspace navigation reset browser sound permission, as before.

Local RTL-SDR/HackRF sources require serial numbers, not enumeration indexes.
Give RTL-SDRs distinct hardware serials before registering them. Duplicate
canonical connections and duplicate assignments are rejected. The registry cannot
prove that different remote host aliases or protocols ultimately expose the same
physical radio: register that hardware once, and avoid other applications claiming
it. Driver/transport/endpoint changes in the receiver Settings panel are locked
once the source is managed; edit the source while its session is stopped instead.
PPM, gain and antenna-input controls remain receiver settings. The source's antenna
text is descriptive and does not select a hardware input.

Remote connections use the existing `rtl_tcp` and SoapyRemote backends. Supply a
hostname or IPv4 address and port; SoapyRemote additionally requires the downstream
radio driver and serial. Use the same hostname spelling for a given endpoint.
Remote servers and their network access must already be configured. This does not
install servers, configure VPNs, or implement TV reception.

The registry is versioned at `/data/sources.json`. Source records contain `kind`,
connection identity and descriptive metadata; receiver records separately contain
`source_id`, runtime slot and enabled state. The implemented providers are `sdr` and `hdhomerun`; each source owns one receiver session. Capabilities describe supported decoder
features, not a guarantee that a particular antenna/radio covers every band.
Additional TV adapters can add provider-specific connection validation,
capabilities and workers while retaining source identity and receiver routing.
Unknown provider/schema versions fail validation rather than being opened as SDRs.

Additional receiver storage lives under `/data/receivers/RECEIVER_ID/`. All Rust
paths, PCM/RPC ports, heartbeat files, audio encoders and SAME ports are assigned
per worker. HTTP routing keeps media and downloads inside the selected receiver's
storage. The default receiver cannot read additional receivers' media through its
legacy media endpoint. Internal services remain loopback-only.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/sources` or `/api/receivers` | Registry, capabilities and session process status |
| `POST /api/sources` | Create/update a stopped source; new receiver starts disabled |
| `POST /api/receivers/ID/lifecycle` | `{"action":"start"}` or `{"action":"stop"}` |
| `/api/receivers/ID/api/state` | Selected receiver state (same schema as legacy state) |
| `/api/receivers/ID/api/command` | Selected receiver commands |
| `/api/receivers/ID/audio.mp3` | Selected receiver audio |
| `/api/receivers/ID/api/…` | Spectrum, media, recordings, CSV and SAME APIs |
| `/api/receivers/ID/healthz` | Engine heartbeat + encoder health for that receiver |

All new routes inherit the existing nginx authentication; mutations enforce the
same origin and request size limits. The legacy `/api/*` and `/audio.mp3` routes
continue addressing Default. Source removal, receiver reassignment, auto-start RF,
resource scheduling and other TV adapters are not included in this foundation.
Back up the whole `/data` directory, including the registry and receiver subfolders.
Allow at least 25 seconds for container shutdown so recordings can finish.

Local validation covers registry migration/persistence, identity rejection,
separate ports and directories, failure isolation, real HTTP proxy routing,
concurrent requests, source/receiver templates and the frontend build. The ARM64
smoke suite now starts two actual receiver engines, verifies band/settings/SAME
isolation and restart persistence, without opening SDR hardware. Full ARM64 build
and smoke execution remain pending in this Windows workspace. Concurrent RTL-SDR,
HackRF and remote RF reception still need hardware validation; the eight-session
limit is an application bound, not a measured ROSE capacity guarantee.


### HDHomeRun live television

In **Sources & receivers**, choose **Add source → HDHomeRun TV tuner**. Enter a
source ID/name, the device hostname or IPv4 address, its eight-character Device ID
(from the device label or web interface), and a tuner index starting at **0**.
Add one source per tuner you want to use; for example, the same four-tuner device
can supply separate `tv-living-room` (tuner 0) and `tv-dx` (tuner 1) sources.
The device ID plus tuner index is exclusive within the registry, including when
hosts are entered under different aliases. Default remains an SDR source.
Adding TV does not require binding an otherwise single legacy Default SDR.

Start the TV **session**, open its workspace, choose a channel and click
**Watch channel**. The device must already have a scanned channel lineup.
**Refresh lineup** retrieves the current list; it does not initiate a disruptive
hardware scan. Hosts must be reachable from the ROSE on HTTP port 80 and stream
port 5004. Prefer a stable LAN IP or resolvable hostname; this implementation does
not perform LAN broadcast discovery or configure remote network connectivity.

The provider verifies `/discover.json` against the registered device ID and tuner
count before tuning, fetches `/lineup.json`, and opens the named tuner’s stream.
It never silently selects another available tuner. A tuner already used by another
application returns an error. Device-supplied stream URLs and authentication tokens
are not passed to the browser. These interfaces follow the
[SiliconDust HTTP development guide](https://info.hdhomerun.com/info/http_api).

One FFmpeg process per active TV source converts the MPEG transport stream to
**H.264 video and AAC stereo, up to 1280×720 at 30 fps**, with deinterlacing.
Output uses a short rolling HLS segment buffer under that receiver’s runtime
directory; it is not a TV recording or DVR. Browsers use native HLS where available
or bundled, version-locked [hls.js](https://github.com/video-dev/hls.js).
The player dependency is loaded only for TV workspaces. All playlists and segments
stay behind the existing authenticated, same-origin receiver routes.

TV transcoding uses CPU on the server and adds several seconds of buffering.
Two codec threads are requested per stream, but total CPU usage and practical
concurrency on ROSE are not yet measured. Starting many TV streams alongside
HackRF/RTL-SDR decoding can exceed available resources. Hardware transcoding,
original-quality passthrough, EXTEND transcoding profiles, TV recordings, captions,
program-guide integration and audio-track selection are not implemented.

Each TV source has **one shared channel**. Other browsers can join the same stream;
changing channel or **Stop stream for everyone** affects all its viewers.
**Close my playback** detaches only that browser. A stream automatically stops
and releases its tuner after 60 seconds without playlist/segment requests; this
also handles closed tabs. Failed/stalled encoders report diagnostics and release
the input. Stopping the source session also stops its stream. Other source sessions
continue, and source-labeled NOAA alerts remain visible in the TV workspace.
Session restart loads device/lineup data but does not automatically start video.

Protected channels are marked unavailable; there is no DRM playback/decryption
integration. Lineups reporting AC-4 audio are also marked unavailable because the
container’s FFmpeg path does not support it. Unknown or additional codec/receiver
limitations can still fail during playback, with diagnostics shown in the TV
workspace. This is not a claim of complete ATSC 3.0 playback support.

The TV state separates device identity/model/firmware/tuner count, channel lineup,
reported codecs and availability, lineup refresh time, and playback diagnostics.
The ATSC reception panel adds live RF measurements, cancellable scans, and a
persistent reception history. Configure **antenna** and **location** in Sources
before starting the session; those labels are copied into every observation.

### ATSC DX workflow

1. Start an HDHomeRun source and watch a channel. The DX panel polls the assigned
   tuner about every five seconds (plus device response time), showing strength,
   signal quality, symbol quality, lock/modulation, physical RF/frequency, bitrate,
   and tuner-reported service/transport information. Unknown values remain unknown;
   the timestamp identifies the last measurement. Percentages are the device's
   `ss`, `snq`, and `seq`, not calibrated dBm or SNR in dB.
2. Use **Log current reception** to take a fresh locked measurement with notes,
   such as antenna heading or propagation conditions. Manual logging requires a
   reported lock; a lineup entry alone is not evidence of reception.
3. For a scan, stop this source's TV stream, expand **Scan physical RF channels**,
   select an inclusive RF range within **2–36**, and choose **2–15 seconds** dwell.
   This version uses the US/Canada broadcast frequency plan and requires the device
   to report `us-bcast`. It does not change the device's persistent channel map or
   lineup. Tuning uses automatic modulation detection; ATSC 3.0 diagnostics and
   service discovery remain dependent on hardware/firmware, with no promise of
   complete ATSC 3.0 support.
4. The scan acquires an authenticated lock on this source's tuner, refuses an
   external stream/lock, reports progress, and can be cancelled. Playback is blocked
   until scanning and tuner release finish. Other source sessions keep running.
   Each completed dwell is stored, including explicitly marked **no confirmed lock**
   attempts. Cancelled, incomplete dwells are not represented as observations.
5. Browse history or **Export retained history (JSON)**. Each entry contains UTC
   time, source ID/name, antenna/location, device ID/tuner, RF/frequency, all three
   metrics, raw tuner status/service information, scan ID or manual origin, and
   notes. The newest **10,000 observations per receiver** survive restarts in
   `receptions.sqlite3` in that receiver's data directory. Existing observations keep
   their original labels when a source is edited. Export periodically for longer
   archives. RF is physical; reported virtual channels are separate service data.

A scan ends by clearing its tuned channel and releasing only its own lock; it does
not automatically resume TV. If release fails, **Retry tuner release** is available
and new playback/scans remain blocked. A small `receptions.lock.json` ownership
journal permits retry after a worker restart; it contains no device authentication
secret. If the source's device/tuner was changed while recovery is pending, restore
that binding first. The platform never force-unlocks another application's tuner.
Normal container shutdown allows time for scan cancellation and release.

Control uses the distribution's `hdhomerun-config` command against the configured
host and tuner, with bounded command timeouts. The server needs access to the
HDHomeRun's control service (TCP 65001) as well as its existing HTTP services.
See [SiliconDust's control documentation](https://info.hdhomerun.com/info/hdhomerun_config).
A momentary lock is not proof of sustained watchability, station identity, or a DX
record; review service information and your notes. Slow service discovery can
require a longer dwell or another scan.

| Receiver-scoped endpoint | Purpose |
| --- | --- |
| `GET /api/receivers/ID/api/state` | TV-specific state with `kind: "hdhomerun"` |
| `POST /api/receivers/ID/api/tv/refresh` | Refresh device/lineup, body `{}` |
| `POST /api/receivers/ID/api/tv/play` | Start/join channel, e.g. `{"channel":"5.1"}` |
| `POST /api/receivers/ID/api/tv/stop` | Stop the shared stream, body `{}` |
| `POST /api/receivers/ID/api/tv/scan` | Start RF scan, e.g. `{"first":2,"last":36,"dwell":4}` |
| `POST /api/receivers/ID/api/tv/scan-cancel` | Cancel current scan, body `{}` |
| `POST /api/receivers/ID/api/tv/scan-release` | Retry failed tuner release, body `{}` |
| `POST /api/receivers/ID/api/tv/log` | Fresh reception, e.g. `{"notes":"Yagi north"}` |
| `GET /api/receivers/ID/api/tv/receptions?before=ID` | Newest-first history, 100 per page; cursor optional |
| `GET /api/receivers/ID/api/tv/receptions.json` | Export retained history with original source/antenna labels |
| `GET /api/receivers/ID/tv/hls/TOKEN/index.m3u8` | Current stream playlist; use the state’s playback URL |

TV uses a separate Python provider worker, never the NRSC5/NOAA engine. The
existing process supervisor, source editing rules and HTTP routing are reused.
Tests cover mixed SDR/TV registries, tuner identity conflicts, discovery identity
checks, protected/unsupported lineup entries, explicit tuner URLs, buffer-path
confinement, idle cleanup, provider launch selection and browser template builds.
A container smoke test converts synthetic MPEG-2/AC-3 TV to H.264/AAC HLS and
checks the generated codecs. **Actual HDHomeRun reception, browser video playback,
ARM64 transcoding and the synthetic media smoke test have not run locally**:
this Windows workspace has no HDHomeRun or FFmpeg installation. Validate the
container build and one TV stream on your ROSE before increasing concurrency.

DX tests additionally cover RF frequency boundaries, unknown metrics, source context,
exclusive tuning, cancellation, lock conflicts, durable logs and release recovery.
Live signal readings, RF scans, and tuner locking still require validation on a
physical HDHomeRun; no such hardware is attached to this development workspace.
Rebuild the container to install the new control dependency before using DX features.

### Measured constellation display

The Spectrum tab plots decoder samples after equalization and before symbol
slicing, rather than synthesizing dots from MER. This implements the idea in
[Studio issue 2](https://github.com/LTCAshraven/nrsc5-studio/issues/2) using a
ROSE-local libnrsc5 extension; it is not the proposed upstream API from
[nrsc5 issue 507](https://github.com/theori-io/nrsc5/issues/507).

- FM: QPSK data carriers from all active partitions, with reference carriers
  excluded. Four OFDM symbols are sampled from every fourth synchronized block.
- AM: primary lower/upper 64-QAM data carriers, excluding training symbols.
  Secondary, tertiary, and PIDS constellations are not included.
- Each sideband has fixed, equal I/Q axes. Crosses show ideal points; measured
  samples retain their amplitude, including PAPR-related displacement. Points
  outside the plot are counted rather than clamped to its edges.
- The private C event (`0x524f5301`) carries at most 1024 pairs per sideband.
  The Rust callback copies them into a single latest-frame buffer using
  `try_lock`; it skips a frame when busy instead of queuing telemetry.
- The receiver-scoped API exposes only fresh samples while synchronized and
  streaming. Samples expire after two seconds. Retunes, stops, lost sync,
  connection failures, and NOAA mode clear or suppress the display. The browser
  polls this endpoint only while the Spectrum tab is visible.

Rebuild the container to install the patched decoder and matching Rust binding;
replacing only the web assets cannot enable measured samples on an older engine.
The Docker build runs a known-symbol C capture test and Rust snapshot tests.
Actual RF accuracy and audio continuity still require testing with a live HD
station or a known IQ recording.
