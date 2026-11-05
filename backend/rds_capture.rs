use std::io::{self, Write};
use std::path::{Path, PathBuf};

const MPX_MAGIC: &[u8; 8] = b"ROSEMPX1";
const MPX_LIMIT: usize = SDR_SAMPLE_RATE_HZ as usize * 4 / CHANNEL_DECIM;

struct MpxCapture {
    path: PathBuf,
    samples: Vec<f32>,
}

impl MpxCapture {
    fn armed() -> Option<Self> {
        let run = PathBuf::from(std::env::var_os("ROSE_RUN_DIR")?);
        let path = PathBuf::from(std::env::var_os("ROSE_DATA_DIR")?).join("rds-capture.mpx");
        if !run.join("rds-capture.arm").is_file() { return None; }
        if path.exists() { return None; }
        std::fs::remove_file(run.join("rds-capture.arm")).ok()?;
        Some(Self { path, samples: Vec::with_capacity(MPX_LIMIT) })
    }

    fn observe(slot: &mut Option<Self>, sample: f32) {
        let Some(active) = slot.as_mut() else { return };
        active.samples.push(sample);
        if active.samples.len() == MPX_LIMIT {
            let capture = slot.take().unwrap();
            let result = std::thread::Builder::new().name("mpx-writer".into()).spawn(move || {
                if let Err(e) = write_mpx(&capture.path, &capture.samples) {
                    eprintln!("RDS capture {}: {e}", capture.path.display());
                }
            });
            if let Err(e) = result { eprintln!("RDS capture worker: {e}"); }
        }
    }
}

fn write_mpx(path: &Path, samples: &[f32]) -> io::Result<()> {
    let file = std::fs::File::create_new(path)?;
    let mut out = io::BufWriter::new(file);
    out.write_all(MPX_MAGIC)?;
    for word in [SDR_SAMPLE_RATE_HZ, CHANNEL_DECIM as u32, samples.len() as u32] {
        out.write_all(&word.to_le_bytes())?;
    }
    for sample in samples { out.write_all(&sample.to_le_bytes())?; }
    out.flush()
}
