impl Nrsc5App {
    fn start_weather(&mut self) -> Result<(), String> {
        self.wx_session.take();
        self.audio_player.sink().clear();
        let hz = crate::weather::channel_hz(self.wx_frequency).ok_or("Choose a NOAA channel")?;
        self.wx_session = Some(crate::weather::WeatherReceiver::start(&self.config, hz,
            self.audio_player.sink(), self.app_state.spectrum_tap.clone())?);
        Ok(())
    }
    fn weather_command(&mut self, request: &serde_json::Value) -> Option<serde_json::Value> {
        use serde_json::json;
        if request["op"] != "command" { return None; }
        let c = &request["command"];
        let kind = c["type"].as_str().unwrap_or("");
        if kind == "SelectBand" {
            let band = c["value"].as_str().unwrap_or("");
            if !["fm", "noaa", "am"].contains(&band) { return Some(json!({"error":"Unknown band"})); }
            if self.retune_task.is_some() { return Some(json!({"error":"Wait for retuning to finish"})); }
            let was_running = self.app_state.is_streaming || self.wx_session.as_ref().is_some_and(|w| w.running());
            if !self.wx_band && !self.am_band { self.fm_frequency = self.app_state.frequency_mhz; }
            self.wx_session.take();
            if self.app_state.is_streaming { self.handle_command(UiCommand::Stop); }
            self.audio_player.sink().clear();
            self.wx_band = band == "noaa";
            self.am_band = band == "am";
            self.app_state.frequency_mhz = if self.am_band { self.am_frequency as f32 } else { self.fm_frequency };
            self.config.frequency_mhz = self.app_state.frequency_mhz;
            self.app_state.selected_program = 0;
            if was_running {
                if self.wx_band {
                    if let Err(e) = self.start_weather() { self.web_save(); return Some(json!({"error":e})); }
                } else { self.handle_command(UiCommand::Start); }
            }
            self.web_save();
            return Some(self.web_state());
        }
        if !self.wx_band {
            return if kind == "TuneWeather" { Some(json!({"error":"Select NOAA Weather first"})) } else { None };
        }
        let running = self.wx_session.as_ref().is_some_and(|w| w.running());
        let result: Result<(), String> = match kind {
            "Start" => if running { Ok(()) } else { self.start_weather() },
            "Stop" => { self.wx_session.take(); self.audio_player.sink().clear(); Ok(()) },
            "TuneWeather" => {
                if !running { Err("Start the receiver before tuning".into()) }
                else if let Some(hz) = c["value"].as_f64().and_then(crate::weather::channel_hz) {
                    self.wx_frequency = hz as f64 / 1e6;
                    self.start_weather()
                } else { Err("Invalid NOAA channel".into()) }
            },
            "SetManualGainTenths" => {
                if let Some(value) = c["value"].as_i64().filter(|v| (-1000..=1000).contains(v)) {
                    let result = self.wx_session.as_ref().map(|w| w.set_gain(value as i32)).unwrap_or(Ok(()));
                    if result.is_ok() { self.config.manual_gain_tenths = value as i32; }
                    result
                } else { Err("Invalid manual gain".into()) }
            },
            "SetSdrGainElement" => {
                if let (Some(name),Some(value)) = (c["value"]["element"].as_str(),c["value"]["value_db"].as_f64()) {
                    let result = self.wx_session.as_ref().map(|w| w.sdr.set_gain_element(name,value).map_err(|e|e.to_string())).unwrap_or(Ok(()));
                    if result.is_ok() { self.config.sdr.gains.insert(name.into(),value); }
                    result
                } else { Err("Invalid gain".into()) }
            },
            "TuneMhz" | "RecallPreset" | "SavePreset" | "SelectProgram" | "StartRecording" => Err("This control is for FM / HD Radio; select that band first".into()),
            "SetGainMode" | "SetAgcAmpTargetDbfs" | "SetAnalogFallbackMode" => Err("NOAA uses narrowband FM with manual gain".into()),
            "SetVolume" | "SetMute" => return None,
            _ if running => Err("Stop NOAA reception before changing receiver settings".into()),
            _ => return None,
        };
        self.web_save();
        Some(match result { Ok(()) => self.web_state(), Err(e) => json!({"error":e}) })
    }
    fn weather_state(&self, state: &mut serde_json::Value) {
        use serde_json::json;
        state["band"] = json!(if self.wx_band {"noaa"} else {"fm"});
        if !self.wx_band { return; }
        let running = self.wx_session.as_ref().is_some_and(|w| w.running());
        state["frequency"] = json!(self.wx_frequency);
        state["streaming"] = json!(running);
        state["synced"] = json!(false);
        state["retuning"] = json!(false);
        state["station"] = json!({});
        state["programs"] = json!([{}, {}, {}, {}, {}, {}, {}, {}]);
        state["selected"] = json!(0); state["speaker"] = json!(null);
        state["status"] = json!(self.wx_session.as_ref().map(|w| w.status.lock().unwrap().clone()).unwrap_or("NOAA receiver idle".into()));
        state["recording"] = json!(null); state["agc"] = json!(null);
        state["mer_lower"] = json!(0); state["mer_upper"] = json!(0); state["ber"] = json!(null);
        state["config"]["gain_mode"] = json!("manual");
        state["active_gain_mode"] = json!(if running {Some("manual")} else {None});
        state["config"]["analog_fallback_mode"] = json!("analog_only");
        if let Some(w) = &self.wx_session {
            state["gains"] = json!(w.sdr.gain_elements().iter().map(|g|json!({"name":g.name,"min":g.min_db,"max":g.max_db,"step":g.step_db,"value":g.current_db})).collect::<Vec<_>>());
        }
    }
}
