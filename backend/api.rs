
// Appended inside app.rs: serialized mutations use upstream command handlers.
impl Nrsc5App {
    pub fn web_init(&mut self) -> Result<(), String> {
        if let Some(error) = &self.audio_player.init_error { return Err(error.clone()); }
        if !self.config.recording_dir.as_deref().is_some_and(|p| {
            let path = std::path::Path::new(p);
            path.starts_with("/data/recordings") && !path.components().any(|c| c == std::path::Component::ParentDir)
        }) { self.config.recording_dir = Some("/data/recordings".into()); }
        self.refresh_sdr_devices();
        Ok(())
    }

    pub fn web_save(&mut self) {
        self.sync_runtime_to_config();
        save_config(&self.config);
        self.play_log.save();
    }

    pub fn web_request(&mut self, request: serde_json::Value) -> serde_json::Value {
        use serde_json::json;
        match request["op"].as_str().unwrap_or("") {
            "self_test" if std::env::var("NRSC5_SELF_TEST").as_deref() == Ok("1") => {
                self.handle_nrsc5_event(NrscEvent::StationName("CI TEST FM".into()));
                self.handle_nrsc5_event(NrscEvent::Slogan("Injected test metadata".into()));
                self.handle_nrsc5_event(NrscEvent::Metadata { program: 0, title: "CI test tone".into(),
                    artist: "Test generator".into(), album: "Smoke test".into(), genre: "Test".into() });
                let sink = self.audio_player.sink();
                std::thread::spawn(move || {
                    for block in 0..500 {
                        let samples: Vec<i16> = (0..882).flat_map(|i| {
                            let t = (block * 882 + i) as f32 / 44100.0;
                            let v = (8000.0 * (t * 440.0 * std::f32::consts::TAU).sin()) as i16;
                            [v, v]
                        }).collect();
                        sink.push(&samples);
                        std::thread::sleep(Duration::from_millis(20));
                    }
                });
                json!({"ok": true})
            },
            "state" => self.web_state(),
            "log" => json!({"entries": self.play_log.entries()}),
            "spectrum" => {
                if let Some(tap) = &self.app_state.spectrum_tap {
                    tap.snapshot_into(&mut self.app_state.spectrum_snapshot);
                }
                let s = &self.app_state.spectrum_snapshot;
                json!({"bins": s.spectrum_db, "generation": s.generation,
                    "sample_rate": s.sample_rate_sps, "center_hz": s.center_freq_hz})
            },
            "export" => match self.play_log.export_csv(std::path::Path::new("/data/play-log.csv")) {
                Ok(()) => json!({"ok": true}), Err(error) => json!({"error": error.to_string()})
            },
            "shutdown" => {
                self.stop_recording(false);
                eframe::App::on_exit(self, None);
                json!({"ok": true})
            },
            "command" => {
                let command = match serde_json::from_value::<UiCommand>(request["command"].clone()) {
                    Ok(command) => command,
                    Err(error) => return json!({"error": error.to_string()}),
                };
                match &command {
                    UiCommand::SelectProgram(p) if *p > 7 => return json!({"error": "Invalid HD program"}),
                    UiCommand::SavePreset(i) | UiCommand::RecallPreset(i) | UiCommand::ClearPreset(i)
                        if *i >= 48 => return json!({"error": "Invalid preset index"}),
                    UiCommand::SetPreset(i, p) if *i >= 48 || p.program > 7 => return json!({"error": "Invalid preset"}),
                    UiCommand::SetRecordingDir(path) if !path.as_deref().is_some_and(|p| {
                        let p = std::path::Path::new(p);
                        p.starts_with("/data/recordings") && !p.components().any(|c| c == std::path::Component::ParentDir)
                    }) => return json!({"error": "Recording folder must be beneath /data/recordings"}),
                    UiCommand::ExportLogCsv |
                    UiCommand::ShowSdrSettings | UiCommand::HideSdrSettings |
                    UiCommand::ShowAbout | UiCommand::HideAbout => return json!({"error": "Desktop-only command"}),
                    _ => {},
                }
                if self.retune_task.is_some() { return json!({"error": "Retune in progress; retry shortly"}); }
                self.handle_command(command);
                self.web_save();
                self.web_state()
            },
            _ => json!({"error": "Unknown operation"}),
        }
    }

    fn web_state(&self) -> serde_json::Value {
        use serde_json::json;
        let s = &self.app_state;
        let programs: Vec<_> = s.programs.iter().enumerate().map(|(i,p)| json!({
            "index": i, "title": p.title, "artist": p.artist, "album": p.album,
            "genre": p.genre, "cover": p.cover_art_path, "logo": s.station_logo_paths[i],
            "logo_source": s.station_logo_sources[i], "decoded": s.decoded[i],
            "fresh": crate::gui::state::AppState::is_psd_field_fresh(p.title_updated)
        })).collect();
        let devices: Vec<_> = s.sdr_devices.iter().map(|d| json!({"driver": d.driver,
            "label": d.label, "serial": d.serial, "args": d.args_after_driver()})).collect();
        let gains: Vec<_> = s.sdr_gain_elements.iter().map(|g| json!({"name": g.name,
            "min": g.min_db, "max": g.max_db, "step": g.step_db, "value": g.current_db})).collect();
        let art: Vec<_> = s.art_tiles.iter().map(|a| json!({"path": a.path,
            "hash": a.hash.to_string(), "count": a.count, "songs": a.songs, "album": a.album})).collect();
        let weather: Vec<_> = s.weather_frames.iter().map(|f| json!({"path": f.path,
            "time": f.captured_at.to_rfc3339()})).collect();
        let payload: Vec<_> = s.payload_log.iter().map(|p| json!({"time": p.hhmmss, "text": p.text})).collect();
        json!({
            "ready": true, "version": env!("CARGO_PKG_VERSION"),
            "streaming": s.is_streaming, "synced": s.currently_synced,
            "retuning": self.retune_task.is_some(), "status": s.nrsc5_status,
            "frequency": s.frequency_mhz, "selected": s.selected_program,
            "speaker": s.active_speaker, "programs": programs,
            "station": s.station_info, "stale": s.sync_data_stale(),
            "mer_lower": s.mer_lower, "mer_upper": s.mer_upper, "ber": s.ber,
            "gain": s.agc_db, "startup_seconds": s.startup_wait_s, "silence_seconds": s.silence_s,
            "config": self.config, "devices": devices, "gains": gains,
            "antennas": s.sdr_antennas, "active_antenna": s.active_antenna,
            "refreshing": s.sdr_devices_refresh_in_flight, "sdr_present": s.sdr_present,
            "active_gain_mode": s.active_gain_mode, "active_manual_gain": s.active_manual_gain_tenths,
            "agc": s.agc_snapshot.as_ref().map(|a| json!({"status": format!("{:?}", a.status),
                "phase": format!("{:?}", a.phase), "gain": a.current_tenths as f32 / 10.0,
                "best_mer": a.best_mer, "probes": a.probes_done, "cached": a.from_cache,
                "reason": a.last_reason, "seconds_since_change": a.last_change_at.elapsed().as_secs_f32()})),
            "recording": s.recording.as_ref().map(|r| json!({"program": r.program,
                "seconds": r.started_at.elapsed().as_secs(), "path": r.output_path})),
            "art": art, "blocked_art": s.art_blocklist_count,
            "traffic": s.traffic_map_path, "traffic_updated": s.traffic_map_last_updated_hhmmss,
            "weather": weather, "weather_updated": s.weather_map_last_updated_hhmmss,
            "payload": payload, "last_event": s.last_event
        })
    }
}

#[cfg(test)]
mod web_adapter_tests {
    use super::*;
    #[test]
    fn web_commands_deserialize_native_variants() {
        let c: UiCommand = serde_json::from_str(r#"{"type":"SelectProgram","value":7}"#).unwrap();
        assert!(matches!(c, UiCommand::SelectProgram(7)));
        let c: UiCommand = serde_json::from_str(r#"{"type":"SetPreset","value":[0,{"name":"FM","frequency_mhz":101.1,"program":1}]}"#).unwrap();
        assert!(matches!(c, UiCommand::SetPreset(0, _)));
    }
    #[test]
    fn station_snapshot_excludes_monotonic_clock_fields() {
        let mut s = crate::station_info::StationInfo::default();
        s.last_updated = Some(Instant::now());
        let json = serde_json::to_value(s).unwrap();
        assert!(json.get("last_updated").is_none());
        assert_eq!(json["programs"].as_array().unwrap().len(), 8);
    }
}
