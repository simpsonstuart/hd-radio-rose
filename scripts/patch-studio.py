"""Adapt the pinned application model to a headless API without drawing a frame.

Reuse upstream command/event handlers, recording, maps and caches. Exact anchors
fail on upstream changes instead of silently dropping receiver functionality.
"""
from pathlib import Path
import re
import sys
import tomllib


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError(f"Upstream changed; expected one anchor: {before[:80]}")
    return text.replace(before, after, 1)


def patch(root, adapter=None):
    root = Path(root)
    adapter = Path(adapter or Path(__file__).resolve().parents[1] / "backend")
    app = (root / "src/app.rs").read_text(encoding="utf-8")
    app = replace_once(app, "pub fn new(cc: &eframe::CreationContext<'_>) -> Self {",
                       "pub fn new(ctx: &egui::Context) -> Self {")
    app = app.replace("&cc.egui_ctx", "ctx")
    # Docker enables eframe/glow, which changes the App shutdown signature.
    # The headless runner has no GL context; retain upstream cleanup unchanged.
    app = replace_once(app, "fn on_exit(&mut self) {",
                       "fn on_exit(&mut self, _gl: Option<&eframe::glow::Context>) {")
    begin = app.index("        let dock_state = cc")
    end = app.index("\n\n        let (nrsc5,", begin)
    app = app[:begin] + "        let dock_state = default_dock_state();" + app[end:]
    begin = app.index("        self.update_runtime_metrics();", app.index("impl eframe::App"))
    end = app.index("        // --- Hidden debug keyboard shortcut", begin)
    tick = app[begin:end].replace("ui.ctx()", "ctx")
    app += "\nimpl Nrsc5App {\n    pub fn web_tick(&mut self, ctx: &egui::Context) {\n" + tick + """
        self.poll_sdr_presence(ctx);
        self.maybe_prune_art_history();
        self.maybe_prune_aas_scratch();
        self.play_log.prune();
    }
}
"""
    app += (adapter / "api.rs").read_text(encoding="utf-8")
    (root / "src/app.rs").write_text(app, encoding="utf-8")
    audio_path = root / "src/audio/mod.rs"
    audio = audio_path.read_text(encoding="utf-8")
    audio = replace_once(audio, "use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};", "")
    audio = replace_once(audio, "_stream: Option<cpal::Stream>", "_stream: Option<()>")
    begin = audio.index("fn open_stream(")
    end = audio.index("// ---------------------------------------------------------------------------", begin)
    audio = audio[:begin] + (adapter / "pcm.rs").read_text() + "\n\n" + audio[end:]
    audio_path.write_text(audio, encoding="utf-8")
    dock_path = root / "src/gui/dock.rs"
    dock = dock_path.read_text(encoding="utf-8")
    dock = replace_once(dock, "#[derive(Debug, Clone)]\npub enum UiCommand {",
                        '#[derive(Debug, Clone, serde::Deserialize)]\n#[serde(tag = "type", content = "value")]\npub enum UiCommand {')
    dock_path.write_text(dock, encoding="utf-8")
    info_path = root / "src/station_info.rs"
    info = info_path.read_text(encoding="utf-8")
    info = re.sub(r"#\[derive\(([^\n]+)\)\]", r"#[derive(\1, serde::Serialize)]", info)
    info = re.sub(r"(    pub \w+: (?:Option<)?Instant>?[,]?)", r"    #[serde(skip)]\n\1", info)
    info_path.write_text(info, encoding="utf-8")
    lock_path = root / "Cargo.lock"
    lock = lock_path.read_text()
    version = next(p["version"] for p in tomllib.loads(lock)["package"] if p["name"] == "serde_json")
    cargo = (root / "Cargo.toml").read_text()
    cargo = replace_once(cargo, "[dependencies]", f'[dependencies]\nserde_json = "={version}"')
    (root / "Cargo.toml").write_text(cargo)
    begin = lock.index('name = "nrsc5-studio"')
    end = lock.index("\n[[package]]", begin)
    block = replace_once(lock[begin:end], ' "serde",', ' "serde",\n "serde_json",')
    lock_path.write_text(lock[:begin] + block + lock[end:])
    (root / "src/main.rs").write_text((adapter / "main.rs").read_text(), encoding="utf-8")
    soapy_path = root / "src/sdr/soapy.rs"
    soapy = soapy_path.read_text(encoding="utf-8")
    begin = soapy.index("        // Sample rate first")
    end = soapy.index("        self.device\n            .set_sample_rate", begin)
    soapy = soapy[:begin] + '''        // Capture at a hardware-safe rate, then resample before DSP.
        let (device_rate, resample) = capture_rate_plan(&self.driver, cfg.sample_rate_sps as f64);
        eprintln!("[sdr] driver={} capture={} sps decoder={} sps resample={}",
            self.driver, device_rate, cfg.sample_rate_sps, resample.is_some());
''' + soapy[end:]
    anchor = "        // Stash the resample plan so `run_stream` can pick it up."
    soapy = replace_once(soapy, anchor, '''        if self.driver == "hackrf" {
            self.device.set_bandwidth(RX, CH, 1_750_000.0)
                .map_err(|e| SdrError::SoapyCall {
                    func: "set_bandwidth", detail: format!("HackRF 1.75 MHz: {e}"),
                })?;
        }
''' + anchor)
    soapy += "\n" + (adapter / "soapy_rates.rs").read_text(encoding="utf-8")
    soapy_path.write_text(soapy, encoding="utf-8")


if __name__ == "__main__":
    patch(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
