use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::Mutex,
};

use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, State};

struct EngineSidecar {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl EngineSidecar {
    fn start(projects_root: &PathBuf) -> Result<Self, String> {
        std::fs::create_dir_all(projects_root)
            .map_err(|error| format!("cannot create projects root: {error}"))?;
        let mut child = Command::new(engine_command())
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

    fn request(&mut self, request: &Value, app: &AppHandle) -> Result<Value, String> {
        let expected_id = request
            .get("id")
            .cloned()
            .ok_or_else(|| "engine request must include an id".to_owned())?;
        let payload = serde_json::to_string(request)
            .map_err(|error| format!("cannot encode engine request: {error}"))?;
        writeln!(self.stdin, "{payload}")
            .map_err(|error| format!("cannot send request to local engine: {error}"))?;
        self.stdin
            .flush()
            .map_err(|error| format!("cannot flush engine request: {error}"))?;
        loop {
            let mut line = String::new();
            self.stdout
                .read_line(&mut line)
                .map_err(|error| format!("cannot read local engine response: {error}"))?;
            if line.trim().is_empty() {
                return Err("local engine closed without a response".to_owned());
            }
            let message: Value = serde_json::from_str(&line)
                .map_err(|error| format!("local engine returned invalid JSON: {error}"))?;
            if message.get("id") == Some(&expected_id) {
                return Ok(message);
            }
            if message.get("method").is_some() && message.get("id").is_none() {
                app.emit("engine-notification", &message)
                    .map_err(|error| format!("cannot emit engine notification: {error}"))?;
                continue;
            }
            return Err("local engine returned a response with an unexpected id".to_owned());
        }
    }
}

fn engine_command() -> PathBuf {
    if let Ok(executable) = std::env::current_exe() {
        if let Some(parent) = executable.parent() {
            let bundled = parent.join("ltagent-engine");
            if bundled.is_file() {
                return bundled;
            }
        }
    }
    PathBuf::from("ltagent-engine")
}

#[cfg(test)]
mod tests {
    use super::engine_command;

    #[test]
    fn engine_command_has_the_allowlisted_binary_name() {
        assert_eq!(
            engine_command().file_name().and_then(|name| name.to_str()),
            Some("ltagent-engine")
        );
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
    fn request(&self, request: Value, app: &AppHandle) -> Result<Value, String> {
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
            .request(&request, app);
        if result.is_err() {
            *sidecar = None;
        }
        result
    }
}

#[tauri::command]
fn engine_request(
    app: AppHandle,
    state: State<'_, EngineState>,
    request: Value,
) -> Result<Value, String> {
    state.request(request, &app)
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
