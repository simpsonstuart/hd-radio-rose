use std::path::PathBuf;

pub fn data(name: &str) -> PathBuf {
    PathBuf::from(std::env::var("ROSE_DATA_DIR").unwrap_or_else(|_| "/data".into())).join(name)
}
pub fn run(name: &str) -> PathBuf {
    PathBuf::from(std::env::var("ROSE_RUN_DIR").unwrap_or_else(|_| "/run/rose".into())).join(name)
}
pub fn address(key: &str, fallback: u16) -> String {
    let port = std::env::var(key).ok().and_then(|v| v.parse::<u16>().ok()).unwrap_or(fallback);
    format!("127.0.0.1:{port}")
}
