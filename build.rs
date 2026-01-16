use std::{env, fs, process::Command};

use env::var;
use fs::write;

fn main() {
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

    let out_path = var("CARGO_MANIFEST_DIR").unwrap() + "/src/generated.rs";
    let contents = format!("pub const GIT_HASH: &str = \"{}\";\n", git_hash);
    write(out_path, contents).expect("Unable to write generated.rs");
}
