// Atelier desktop shell (Tauri v2).
//
// On startup it launches the bundled Python sidecar `atelier-launcher` (a
// PyInstaller build of desktop/run_local.py), which in turn starts the backend
// and tools services natively and writes the chosen URL to ~/.atelier/backend_url.
// We poll for that file, then point the window at the local backend (which serves
// both the UI and the API — single origin).
//
// NOTE: authored against the Tauri v2 API but NOT compiled in this environment
// (no Rust toolchain here). Build it with `cargo tauri build` — see desktop/README.md.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{path::PathBuf, time::Duration};

use tauri::{Manager, WebviewWindow};
use tauri_plugin_shell::ShellExt;

fn data_dir() -> PathBuf {
    // ~/.atelier (matches run_local's default ATELIER_DATA_DIR).
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".atelier")
}

fn read_backend_url() -> Option<String> {
    let p = data_dir().join("backend_url");
    std::fs::read_to_string(p).ok().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
}

fn navigate_when_ready(window: WebviewWindow) {
    tauri::async_runtime::spawn(async move {
        // The sidecar wins/loses races on first boot; give it up to ~60s.
        for _ in 0..240 {
            if let Some(url) = read_backend_url() {
                let _ = window.eval(&format!("window.location.replace('{}')", url));
                return;
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        let _ = window.eval(
            "document.body.innerHTML = '<p style=\"font-family:sans-serif;padding:2rem\">\
             Atelier failed to start its local services. See ~/.atelier for logs.</p>'",
        );
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Remove a stale URL so we wait for THIS launch's value.
            let _ = std::fs::remove_file(data_dir().join("backend_url"));

            // Spawn the Python launcher sidecar (it starts backend + tools and
            // tears them down on SIGTERM when this process exits).
            let sidecar = app.shell().sidecar("atelier-launcher")?;
            let (_rx, _child) = sidecar.spawn()?;

            if let Some(window) = app.get_webview_window("main") {
                navigate_when_ready(window);
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Atelier");
}
