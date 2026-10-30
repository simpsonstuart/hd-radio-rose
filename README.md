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
| Constellation | MER-derived QPSK illustration, explicitly labeled; upstream also synthesizes this display |
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
that status. One shared receiver session is controlled by all signed-in users.

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
Internal API/PCM services bind loopback only. Health does not indicate RF lock or
USB access. Optional restart-on-failure is configured in RouterOS, independently
of start-on-boot. Give the container at least 15 seconds to stop so active Opus
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
