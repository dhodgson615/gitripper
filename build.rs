use std::{env, fs};
use std::process::Command;

fn main() {
    // Attempt to get a short git hash; fall back to empty string
    let git_hash = Command::new("git")
        .args(&["rev-parse", "--short", "HEAD"])
        .output()
        .ok()
        .and_then(|o| {
            if o.status.success() {
                Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
            } else {
                None
            }
        })
        .unwrap_or_default();

    let out_path = env::var("CARGO_MANIFEST_DIR").unwrap() + "/src/generated.rs";
    let contents = format!("pub const GIT_HASH: &str = \"{}\";\n", git_hash);
    fs::write(out_path, contents).expect("Unable to write generated.rs");
}
