use std::{
    env,
    fs::write,
    process::Command,
    time::{SystemTime, UNIX_EPOCH},
};

use env::var;

fn run_cmd(args: &[&str]) -> Option<String> {
    Command::new(args[0]).args(&args[1..]).output().ok().and_then(|o| {
        if o.status.success() {
            Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
        } else {
            None
        }
    })
}

fn main() {
    println!("cargo:rerun-if-changed=.git/HEAD");
    println!("cargo:rerun-if-changed=.git/refs/heads");
    println!("cargo:rerun-if-env-changed=MY_BUILD_FLAG");

    let git_short =
        run_cmd(&["git", "rev-parse", "--short", "HEAD"]).unwrap_or_default();

    let git_long = run_cmd(&["git", "rev-parse", "HEAD"]).unwrap_or_default();

    let git_branch = run_cmd(&["git", "rev-parse", "--abbrev-ref", "HEAD"])
        .unwrap_or_default();

    let git_count =
        run_cmd(&["git", "rev-list", "--count", "HEAD"]).unwrap_or_default();

    let git_date =
        run_cmd(&["git", "log", "-1", "--format=%cI"]).unwrap_or_default();

    let git_author =
        run_cmd(&["git", "log", "-1", "--format=%an"]).unwrap_or_default();

    let git_remote = run_cmd(&["git", "config", "--get", "remote.origin.url"])
        .unwrap_or_default();

    let git_describe =
        run_cmd(&["git", "describe", "--tags", "--dirty", "--always"])
            .unwrap_or_default();

    let rustc_version = run_cmd(&["rustc", "--version"]).unwrap_or_default();
    let pkg_version = var("CARGO_PKG_VERSION").unwrap_or_default();
    let pkg_name = var("CARGO_PKG_NAME").unwrap_or_default();
    let profile = var("PROFILE").unwrap_or_default();
    let target = var("TARGET").unwrap_or_default();

    let mut features: Vec<String> = env::vars()
        .filter_map(|(k, _)| {
            const PREFIX: &str = "CARGO_FEATURE_";
            if k.starts_with(PREFIX) {
                Some(k[PREFIX.len()..].to_lowercase())
            } else {
                None
            }
        })
        .collect();
    features.sort();

    let features_csv = features.join(",");

    let build_user =
        var("USER").or_else(|_| var("USERNAME")).unwrap_or_default();

    let build_host = run_cmd(&["hostname"]).unwrap_or_default();

    let build_ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or_default();

    let build_user_agent = if git_short.is_empty() {
        format!("{}/{}", pkg_name, pkg_version)
    } else {
        format!("{}/{}+{}", pkg_name, pkg_version, git_short)
    };

    let zip_api_prefix = "https://api.github.com/repos/";
    let out_path = var("CARGO_MANIFEST_DIR").unwrap() + "/src/generated.rs";

    let contents = format!(
        "pub const GIT_HASH_SHORT: &str = {:?};\n\
         pub const GIT_HASH_LONG: &str = {:?};\n\
         pub const GIT_BRANCH: &str = {:?};\n\
         pub const GIT_COMMIT_COUNT: &str = {:?};\n\
         pub const GIT_COMMIT_DATE: &str = {:?};\n\
         pub const GIT_COMMIT_AUTHOR: &str = {:?};\n\
         pub const GIT_REMOTE_URL: &str = {:?};\n\
         pub const GIT_DESCRIBE: &str = {:?};\n\
         pub const RUSTC_VERSION: &str = {:?};\n\
         pub const BUILD_PKG_NAME: &str = {:?};\n\
         pub const BUILD_PKG_VERSION: &str = {:?};\n\
         pub const BUILD_PROFILE: &str = {:?};\n\
         pub const BUILD_TARGET: &str = {:?};\n\
         pub const BUILD_FEATURES_CSV: &str = {:?};\n\
         pub const BUILD_USER: &str = {:?};\n\
         pub const BUILD_HOST: &str = {:?};\n\
         pub const BUILD_USER_AGENT: &str = {:?};\n\
         pub const ZIP_API_PREFIX: &str = {:?};\n\
         pub const BUILD_TIMESTAMP_SECS: u64 = {};\n",
        git_short,
        git_long,
        git_branch,
        git_count,
        git_date,
        git_author,
        git_remote,
        git_describe,
        rustc_version,
        pkg_name,
        pkg_version,
        profile,
        target,
        features_csv,
        build_user,
        build_host,
        build_user_agent,
        zip_api_prefix,
        build_ts
    );

    write(out_path, contents).expect("Unable to write `src/generated.rs`");
}
