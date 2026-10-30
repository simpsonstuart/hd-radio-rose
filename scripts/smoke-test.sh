#!/usr/bin/env bash
set -euo pipefail
image=${1:-dx-studio:test}
name=rose-smoke
cleanup() {
    docker logs "$name" || true
    docker rm -f "$name" >/dev/null 2>&1 || true
}
trap cleanup EXIT
test "$(docker image inspect "$image" --format '{{.Architecture}}')" = arm64
docker run -d --name "$name" -p 127.0.0.1:18080:8080 \
    -e WEB_PASSWORD=ci-smoke-password -e NRSC5_SELF_TEST=1 "$image"
ready=false
for attempt in $(seq 1 90); do
    if curl -fsS http://127.0.0.1:18080/healthz && curl -fsS -u radio:ci-smoke-password http://127.0.0.1:18080/api/receivers/default/healthz; then
        ready=true
        break
    fi
    if test "$(docker inspect "$name" --format '{{.State.Running}}')" != true; then
        exit 1
    fi
    sleep 2
done
test "$ready" = true
python3 scripts/smoke-http.py
python3 scripts/smoke-receivers.py
docker cp scripts/smoke-tv.py "$name":/tmp/smoke-tv.py
docker exec "$name" python3 /tmp/smoke-tv.py
docker cp scripts/test-same-decoder.py "$name":/tmp/test-same-decoder.py
docker cp scripts/smoke-same.py "$name":/tmp/smoke-same.py
docker exec "$name" python3 /tmp/smoke-same.py
# No desktop server or sound daemon is installed in the runtime.
docker exec "$name" sh -c 'if command -v Xvfb || command -v x11vnc || command -v pulseaudio; then exit 1; fi'
docker exec "$name" sh -ec 'SoapySDRUtil --info > /tmp/soapy.txt 2>&1; cat /tmp/soapy.txt; grep -i rtlsdr /tmp/soapy.txt; grep -i hackrf /tmp/soapy.txt'
# Inject a gated test tone into the real Rust AudioSink, then decode browser MP3.
# The self-test operation is loopback-only and disabled in normal deployments.
docker exec "$name" python3 -c 'from gateway import rpc; assert rpc({"op":"self_test"})["ok"]'
docker exec "$name" ffmpeg -hide_banner -loglevel info \
    -i http://127.0.0.1:8090/audio.mp3 -t 5 -af volumedetect -f null - 2> /tmp/rose-audio-test.log
cat /tmp/rose-audio-test.log
python3 -c 'import re; text=open("/tmp/rose-audio-test.log").read(); match=re.search(r"max_volume: (-?[\d.]+) dB", text); assert match and float(match[1]) > -50, text'
# Validate corresponding source archive exists and is readable.
docker exec "$name" sh -c 'tar -tzf /usr/share/doc/dx-studio/sources.tar.gz >/dev/null'
# Restart and ensure persisted API settings survive graceful shutdown.
docker restart -t 25 "$name"
sleep 5
for attempt in $(seq 1 45); do
    if curl -fsS http://127.0.0.1:18080/healthz && curl -fsS -u radio:ci-smoke-password http://127.0.0.1:18080/api/receivers/default/healthz; then
        docker exec "$name" python3 -c 'from gateway import rpc; s=rpc({"op":"state"}); assert s["config"]["presets"][0]["name"]=="CI preset"; assert s["config"]["show_hd5_hd8"]'
        docker exec "$name" python3 -c 'import json, urllib.request; s=json.load(urllib.request.urlopen("http://127.0.0.1:8090/api/alerts")); assert s["settings"]["counties"]==["053077"]; assert not s["active"]'
        exit 0
    fi
    sleep 2
done
exit 1
