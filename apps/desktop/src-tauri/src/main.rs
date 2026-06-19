use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::Mutex,
};

use serde_json::Value;
use tauri::{Manager, State};

struct EngineSidecar {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl EngineSidecar {
    fn start(projects_root: &PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(projects_root)
            .map_err(|error| format!("cannot create projects root: {error}"))?;
        let mut child = Command::new("ltagent-engine")
            .arg("--projects-root")
            .arg(projects_root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|error| format!("cannot start local engine: {error}"))?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "local engine did not provide stdin".to_owned())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "local engine did not provide stdout".to_owned())?;
        Ok(Self {
            child,
            stdin,
            stdout: BufReader::new(stdout),
        })
    }

    fn request(&mut self, request: &Value) -> Result<Value, String> {
        let payload = serde_json::to_string(request)
            .map_err(|error| format!("cannot encode engine request: {error}"))?;
        writeln!(self.stdin, "{payload}")
            .map_err(|error| format!("cannot send request to local engine: {error}"))?;
        self.stdin
            .flush()
            .map_err(|error| format!("cannot flush engine request: {error}"))?;
        let mut response = String::new();
        self.stdout
            .read_line(&mut response)
            .map_err(|error| format!("cannot read local engine response: {error}"))?;
        if response.trim().is_empty() {
            return Err("local engine closed without a response".to_owned());
        }
        serde_json::from_str(&response)
            .map_err(|error| format!("local engine returned invalid JSON: {error}"))
    }
}

impl Drop for EngineSidecar {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

struct EngineState {
    projects_root: PathBuf,
    sidecar: Mutex<Option<EngineSidecar>>,
}

impl EngineState {
    fn request(&self, request: Value) -> Result<Value, String> {
        if !request.is_object() {
            return Err("engine request must be a JSON object".to_owned());
        }
        let mut sidecar = self
            .sidecar
            .lock()
            .map_err(|_| "local engine lock was poisoned".to_owned())?;
        if sidecar.is_none() {
            *sidecar = Some(EngineSidecar::start(&self.projects_root)?);
        }
        let result = sidecar
            .as_mut()
            .expect("sidecar was initialised above")
            .request(&request);
        if result.is_err() {
            *sidecar = None;
        }
        result
    }
}

#[tauri::command]
fn engine_request(state: State<'_, EngineState>, request: Value) -> Result<Value, String> {
    state.request(request)
}

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            let projects_root = app.path().app_data_dir()?.join("projects");
            app.manage(EngineState {
                projects_root,
                sidecar: Mutex::new(None),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_request])
        .run(tauri::generate_context!())
        .expect("error while running Hardware Design Workbench");
}
