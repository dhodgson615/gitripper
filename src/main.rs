use std::{
    env,
    fs::{
        File, Permissions, create_dir_all, remove_dir_all, remove_file,
        set_permissions, {self},
    },
    io::{
        Write, stdin, stdout, {self},
    },
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    process::{
        Command, Stdio, {self},
    },
    time::{Duration, SystemTime},
};

use anyhow::anyhow;
use clap::Parser;
use env::var;
use fs::{copy, rename};
use once_cell::sync::Lazy;
use process::exit;
use regex::Regex;
use reqwest::blocking::Client;
use serde_json::{
    Value, {self},
};
use tempfile::tempdir;
use walkdir::WalkDir;
use zip::ZipArchive;

const GITHUB_API: &str = "https://api.github.com";
const ERR_INVALID_URL: i32 = 2;
const ERR_DEST_EXISTS: i32 = 3;
const ERR_CLEANUP_FAILED: i32 = 4;
const ERR_GIT_NOT_FOUND: i32 = 5;
const ERR_DOWNLOAD_FAILED: i32 = 6;
const ERR_EXTRACTION_FAILED: i32 = 7;
const ERR_INIT_FAILED: i32 = 8;

#[derive(Parser, Debug)]
#[command(
    author,
    version,
    about = "Download a GitHub repository's contents and create a local git repo."
)]
struct Args {
    url: Option<String>,

    #[arg(long)]
    branch: Option<String>,

    #[arg(long)]
    token: Option<String>,

    #[arg(long)]
    dest: Option<PathBuf>,

    #[arg(long)]
    author_name: Option<String>,

    #[arg(long)]
    author_email: Option<String>,

    #[arg(long)]
    remote: Option<String>,

    #[arg(long)]
    force: bool,
}

fn main() {
    if let Err(code) = run() {
        exit(code);
    }
}

fn run() -> Result<(), i32> {
    let mut args = Args::parse();
    let token = args.token.take().or_else(|| var("GITHUB_TOKEN").ok());
    let url = read_url_from_args(&args)?;
    let (owner, repo) = parse_github_url(&url).map_err(|_| ERR_INVALID_URL)?;

    if owner.is_empty() || repo.is_empty() {
        eprintln!("Error: Could not determine repository owner or name.");
        return Err(ERR_INVALID_URL);
    }

    let dest = args
        .dest
        .clone()
        .unwrap_or_else(|| PathBuf::from(format!("{}-copy", repo)));
    if dest.exists() {
        let not_empty =
            dest.read_dir().map(|mut rd| rd.next().is_some()).unwrap_or(false);

        if not_empty && !args.force {
            eprintln!(
                "Destination '{}' exists and is not empty. Use --force to overwrite.",
                dest.display()
            );
            return Err(ERR_DEST_EXISTS);
        }

        if args.force {
            remove_dir_all(&dest).map_err(|_| ERR_CLEANUP_FAILED)?;
        }
    }

    check_git_installed().map_err(|_| ERR_GIT_NOT_FOUND)?;

    let client = Client::builder()
        .user_agent("gitripper/0.1")
        .build()
        .map_err(|_| ERR_DOWNLOAD_FAILED)?;

    let mut reference = args.branch.clone();

    if reference.is_none() {
        match get_default_branch(&client, &owner, &repo, token.as_deref()) {
            Ok(b) => {
                reference = Some(b);
                println!(
                    "Using default branch '{}'",
                    reference.as_ref().unwrap()
                );
            },

            Err(e) => {
                eprintln!(
                    "Warning: could not determine default branch: {}. Using 'main'.",
                    e
                );
                reference = Some("main".to_string());
            },
        }
    }

    let reference = reference.unwrap();
    let tmp = tempdir().map_err(|_| ERR_DOWNLOAD_FAILED)?;

    let zip_path = match download_zip(
        &client,
        &owner,
        &repo,
        &reference,
        token.as_deref(),
        tmp.path(),
    ) {
        Ok(p) => {
            println!("Downloaded archive to {}", p.display());
            p
        },
        Err(e) => {
            eprintln!("Failed to download repository archive: {}", e);
            return Err(ERR_DOWNLOAD_FAILED);
        },
    };

    if let Err(e) = extract_zip(&zip_path, &dest) {
        eprintln!("Failed to extract archive: {}", e);
        return Err(ERR_EXTRACTION_FAILED);
    }

    remove_embedded_git(&dest);
    println!("Initializing new git repository...");

    if let Err(e) = initialize_repo(
        &dest,
        args.author_name.as_deref(),
        args.author_email.as_deref(),
        args.remote.as_deref(),
    ) {
        eprintln!("Failed to initialize repository: {}", e);
        return Err(ERR_INIT_FAILED);
    }

    println!("Done. Repository copied to: {}", dest.display());
    println!("Note: this repository has no history from the original repo.");
    Ok(())
}

fn parse_github_url(url: &str) -> Result<(String, String), &'static str> {
    static RE_GITHUB: Lazy<Regex> = Lazy::new(|| {
        Regex::new(
            r"(?xi)^(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?(?:/|$)"
        ).unwrap()
    });

    let mut s = url.trim().to_string();

    if let Some(stripped) = s.strip_suffix(".git") {
        s = stripped.to_string();
    }

    if let Some(caps) = RE_GITHUB.captures(&s) {
        let owner = caps.get(1).unwrap().as_str().to_string();
        let repo = caps.get(2).unwrap().as_str().to_string();
        Ok((owner, repo))
    } else {
        Err("Invalid GitHub URL")
    }
}

fn get_default_branch(
    client: &Client,
    owner: &str,
    repo: &str,
    token: Option<&str>,
) -> anyhow::Result<String> {
    let url = format!("{}/repos/{}/{}", GITHUB_API, owner, repo);
    let mut req = client.get(&url);

    if let Some(t) = token {
        req = req.header("Authorization", format!("token {}", t));
    }

    let res = req.timeout(Duration::from_secs(30)).send()?;

    match res.status().as_u16() {
        200 => {
            let v: Value = res.json()?;

            Ok(v.get("default_branch")
                .and_then(|b| b.as_str())
                .unwrap_or("main")
                .to_string())
        },

        404 => Err(anyhow!("Repository {}/{} not found (404).", owner, repo)),
        s => {
            let txt = res.text().unwrap_or_default();
            Err(anyhow!("Failed to get repo info: {} {}", s, txt))
        },
    }
}

fn download_zip(
    // TODO: this function might be broken, do we need `NamedTempFile`?
    client: &Client,
    owner: &str,
    repo: &str,
    reference: &str,
    token: Option<&str>,
    dest_dir: &Path,
) -> anyhow::Result<PathBuf> {
    let url = format!(
        "{}/repos/{}/{}/zipball/{}",
        GITHUB_API, owner, repo, reference
    );

    let mut req =
        client.get(&url).header("Accept", "application/vnd.github+json");

    if let Some(t) = token {
        req = req.header("Authorization", format!("token {}", t));
    }

    let mut resp = req.timeout(Duration::from_secs(60)).send()?;
    let status = resp.status();

    if !status.is_success() {
        return if status.as_u16() == 404 {
            Err(anyhow!(
                "Archive for {}/{}@{} not found (404).",
                owner,
                repo,
                reference
            ))
        } else if status.is_redirection() {
            Err(anyhow!("Unexpected redirect: {}", status))
        } else {
            let txt = resp.text().unwrap_or_default();
            Err(anyhow!("Failed to download archive: {} {}", status, txt))
        };
    }

    let ts = SystemTime::now().duration_since(SystemTime::UNIX_EPOCH)?;
    let filename = format!("archive-{}.zip", ts.as_nanos());
    let path = dest_dir.join(filename);

    {
        let mut outfile = File::create(&path)?;
        io::copy(&mut resp, &mut outfile)?;
    }

    Ok(path)
}

fn extract_zip(zip_path: &Path, dest_dir: &Path) -> anyhow::Result<()> {
    let f = File::open(zip_path)?;
    let mut archive = ZipArchive::new(f)?;
    let len = archive.len();

    if len == 0 {
        return Err(anyhow!("Zip archive is empty."));
    }

    let mut in_paths: Vec<PathBuf> = Vec::with_capacity(len);
    for i in 0..len {
        let file = archive.by_index(i)?;

        let p = file
            .enclosed_name()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from(file.name()));

        in_paths.push(p);
    }

    let mut candidate: Option<String> = None;
    let mut all_same = true;

    for p in &in_paths {
        if let Some(first) = p.components().next() {
            let s = first.as_os_str().to_string_lossy().into_owned();
            if s.is_empty() {
                all_same = false;
                break;
            }
            if let Some(ref c) = candidate {
                if c != &s {
                    all_same = false;
                    break;
                }
            } else {
                candidate = Some(s);
            }
        } else {
            all_same = false;
            break;
        }
    }

    let root_prefix: Option<PathBuf> = if let Some(ref cand) = candidate {
        if all_same { Some(PathBuf::from(cand)) } else { None }
    } else {
        None
    };

    create_dir_all(dest_dir)?;

    for i in 0..len {
        let mut file = archive.by_index(i)?;

        let in_path = in_paths
            .get(i)
            .cloned()
            .unwrap_or_else(|| PathBuf::from(file.name()));

        let rel_path = if let Some(ref root) = root_prefix {
            match in_path.strip_prefix(root) {
                Ok(p) => p.to_path_buf(),
                Err(_) => in_path.clone(),
            }
        } else {
            in_path.clone()
        };

        if rel_path.as_os_str().is_empty() {
            continue;
        }

        let outpath = dest_dir.join(&rel_path);

        if file.name().ends_with('/') {
            create_dir_all(&outpath)?;
        } else {
            if let Some(parent) = outpath.parent() {
                create_dir_all(parent)?;
            }

            let mut outfile = File::create(&outpath)?;
            io::copy(&mut file, &mut outfile)?;
        }

        #[cfg(unix)]
        {
            if let Some(mode) = file.unix_mode() {
                let _ = set_permissions(&outpath, Permissions::from_mode(mode));
            }
        }
    }

    Ok(())
}

fn _move_items_to_dest(
    items: Vec<PathBuf>,
    dest_dir: &Path,
) -> anyhow::Result<()> {
    for src in items {
        let name =
            src.file_name().ok_or_else(|| anyhow!("Invalid source name"))?;

        let target = dest_dir.join(name);

        if target.exists() {
            if target.is_dir() {
                remove_dir_all(&target)?;
            } else {
                remove_file(&target)?;
            }
        }

        match rename(&src, &target) {
            Ok(_) => continue,
            Err(_) => {
                if src.is_dir() {
                    _copy_dir_recursive(&src, &target)?;
                    remove_dir_all(&src)?;
                } else {
                    if let Some(parent) = target.parent() {
                        create_dir_all(parent)?;
                    }
                    copy(&src, &target)?;
                    let _ = remove_file(&src);
                }
            },
        }
    }
    Ok(())
}

fn _copy_dir_recursive(src: &Path, dst: &Path) -> anyhow::Result<()> {
    create_dir_all(dst)?;
    for entry in WalkDir::new(src) {
        let entry = entry?;
        let rel = entry.path().strip_prefix(src)?;
        let dest_path = dst.join(rel);

        if entry.file_type().is_dir() {
            create_dir_all(&dest_path)?;
        } else {
            if let Some(parent) = dest_path.parent() {
                create_dir_all(parent)?;
            }

            copy(entry.path(), &dest_path)?;
        }
    }

    Ok(())
}

fn remove_embedded_git(dirpath: &Path) {
    for entry in
        WalkDir::new(dirpath).min_depth(1).into_iter().filter_map(Result::ok)
    {
        if entry.file_type().is_dir() && entry.file_name() == ".git" {
            let git_dir = entry.into_path();
            if let Err(e) = remove_dir_all(&git_dir) {
                eprintln!(
                    "Warning: failed to remove embedded .git at {}: {}",
                    git_dir.display(),
                    e
                );
            } else {
                println!("Removed embedded .git at {}", git_dir.display());
            }
        }
    }
}

fn check_git_installed() -> Result<(), ()> {
    match Command::new("git")
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
    {
        Ok(s) if s.success() => Ok(()),
        _ => Err(()),
    }
}

fn initialize_repo(
    dest: &Path,
    author_name: Option<&str>,
    author_email: Option<&str>,
    remote: Option<&str>,
) -> anyhow::Result<()> {
    let run_git = |args: &[&str]| -> anyhow::Result<()> {
        let status =
            Command::new("git").args(args).current_dir(dest).status()?;

        if status.success() {
            Ok(())
        } else {
            Err(anyhow!("git {:?} failed with status {}", args, status))
        }
    };

    run_git(&["init"])?;

    if let Some(name) = author_name {
        run_git(&["config", "user.name", name])?;
    }

    if let Some(email) = author_email {
        run_git(&["config", "user.email", email])?;
    }

    run_git(&["add", "."])?;
    run_git(&["commit", "-m", "Initial commit"])?;

    if let Some(r) = remote {
        run_git(&["remote", "add", "origin", r])?;
        println!("Set remote origin to {}", r);
    }

    Ok(())
}
