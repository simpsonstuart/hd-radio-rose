#![allow(dead_code)]
mod app;
mod art_cache;
mod audio;
mod collage;
mod config;
mod dsp;
mod ffi;
mod gui;
mod icon;
mod maps;
mod paths;
mod play_log;
mod recorder;
mod sdr;
mod sdr_detect;
mod station_info;
mod weather;

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::sync::mpsc;
use std::time::{Duration, Instant};
use serde_json::{json, Value};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Inert context for upstream repaint notifications. No eframe runner,
    // native window, renderer, display server, or drawing loop is created.
    let ctx = egui::Context::default();
    let mut app = app::Nrsc5App::new(&ctx);
    app.web_init()?;
    let (tx, rx) = mpsc::sync_channel::<(Value, mpsc::SyncSender<Value>)>(32);
    let listener = TcpListener::bind("127.0.0.1:8091")?;
    std::thread::spawn(move || {
        for client in listener.incoming() {
            let Ok(mut client) = client else { continue };
            let _ = client.set_read_timeout(Some(Duration::from_secs(3)));
            let _ = client.set_write_timeout(Some(Duration::from_secs(3)));
            let mut line = String::new();
            let result = BufReader::new((&mut client).take(16385)).read_line(&mut line);
            let response = if result.is_err() || line.len() > 16384 || !line.ends_with('\n') {
                json!({"error": "Invalid or oversized request"})
            } else if let Ok(request) = serde_json::from_str::<Value>(&line) {
                let (reply_tx, reply_rx) = mpsc::sync_channel(1);
                if tx.try_send((request, reply_tx)).is_err() {
                    json!({"error": "Receiver busy"})
                } else {
                    reply_rx.recv_timeout(Duration::from_secs(30))
                        .unwrap_or_else(|_| json!({"error": "Receiver request timed out"}))
                }
            } else { json!({"error": "Invalid JSON"}) };
            let _ = writeln!(client, "{response}");
        }
    });
    let mut last_save = Instant::now();
    let mut last_heartbeat = Instant::now() - Duration::from_secs(2);
    // Keep diagnostic reporting independent of the thread calling USB drivers.
    // Do not pretend the receiver is healthy by updating its heartbeat here.
    let progress = std::sync::Arc::new(std::sync::Mutex::new((Instant::now(), String::from("startup"))));
    let observed = progress.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_secs(10));
        let (since, stage) = observed.lock().unwrap().clone();
        if since.elapsed() >= Duration::from_secs(10) {
            eprintln!("[receiver] stalled for {:.0}s in {stage}", since.elapsed().as_secs_f32());
        }
    });
    loop {
        *progress.lock().unwrap() = (Instant::now(), "event loop / runtime metrics".into());
        app.web_tick(&ctx);
        if last_heartbeat.elapsed() >= Duration::from_secs(1) {
            std::fs::write("/run/rose/heartbeat", b"alive")?;
            last_heartbeat = Instant::now();
        }
        if let Ok((request, reply)) = rx.recv_timeout(Duration::from_millis(50)) {
            let shutdown = request["op"] == "shutdown";
            let stage = format!("op={} command={}", request["op"], request["command"]["type"]);
            *progress.lock().unwrap() = (Instant::now(), stage.clone());
            let command = request["op"] == "command";
            if command { eprintln!("[receiver] begin {stage}"); }
            let response = app.web_request(request);
            if command { eprintln!("[receiver] end {stage}: status={} error={}", response["status"], response["error"]); }
            let _ = reply.send(response);
            if shutdown { break; }
        }
        if last_save.elapsed() >= Duration::from_secs(30) {
            *progress.lock().unwrap() = (Instant::now(), "save receiver state".into());
            app.web_save();
            last_save = Instant::now();
        }
    }
    Ok(())
}
