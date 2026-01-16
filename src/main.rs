use std::{
    env::var,
    fs::{
        File, Permissions, copy, create_dir_all, hard_link, remove_dir_all,
        remove_file, rename, set_permissions,
    },
    io::{self, BufReader, Cursor, Read, Write, stdin, stdout},
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    process::{Command, Stdio, exit},
    time::{Duration, SystemTime},
};

use WalkState::Continue;
use anyhow::anyhow;
use blake3::Hasher;
use clap::Parser;
use fs_extra::dir::{CopyOptions, copy as fs_extra_copy};
use git2::{IndexAddOption, Repository, Signature};
use ignore::{DirEntry, Error, WalkBuilder, WalkState};
use memmap2::MmapOptions;
use once_cell::sync::Lazy;
use phf::{Map, phf_map};
use rayon::iter::{IntoParallelIterator, ParallelIterator};
use regex::Regex;
use reqwest::blocking::Client;
use serde_json::Value;
use tempfile::tempdir;
use zip::ZipArchive;

const DEFAULT_BRANCH: &str = "main";
const DEFAULT_COMMIT_MESSAGE: &str = "Initial commit";
const TIMEOUT_GET_REPO_SECS: u64 = 30;
const TIMEOUT_DOWNLOAD_SECS: u64 = 60;
const TIMEOUT_GET_REPO: Duration = Duration::from_secs(TIMEOUT_GET_REPO_SECS);
const TIMEOUT_DOWNLOAD: Duration = Duration::from_secs(TIMEOUT_DOWNLOAD_SECS);
const ACCEPT_HEADER: &str = "application/vnd.github+json";
const RE_GITHUB_PATTERN: &str = r"(?xi)^(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?(?:/|$)";
const ARCHIVE_PREFIX: &str = "archive-";
const GITHUB_API: &str = "https://api.github.com";
const USER_AGENT: &str = BUILD_USER_AGENT;
const ERR_INVALID_URL: i32 = 2;
const ERR_DEST_EXISTS: i32 = 3;
const ERR_CLEANUP_FAILED: i32 = 4;
const ERR_GIT_NOT_FOUND: i32 = 5;
const ERR_DOWNLOAD_FAILED: i32 = 6;
const ERR_EXTRACTION_FAILED: i32 = 7;
const ERR_INIT_FAILED: i32 = 8;

const fn max_timeout_secs(a: u64, b: u64) -> u64 {
    if a > b { a } else { b }
}
const MAX_TIMEOUT_SECS: u64 =
    max_timeout_secs(TIMEOUT_GET_REPO_SECS, TIMEOUT_DOWNLOAD_SECS);

const DEFAULT_README: &str = include_str!("../assets/DEFAULT_README.md");

const BUILD_VERSION: &str = env!("CARGO_PKG_VERSION");
const OPTIONAL_FLAG: Option<&'static str> = option_env!("MY_BUILD_FLAG");

include!(concat!(env!("OUT_DIR"), "/generated.rs"));
#[cfg(feature = "zip")]
fn zip_enabled() {
    println!("feature 'zip' is compiled in");
}

#[cfg(feature = "gix")]
fn gix_enabled() {
    println!("feature 'gix' is compiled in");
}

static MIME_BY_EXT: Map<&'static str, &'static str> = phf_map! {
    "rs" => "text/rust",
    "md" => "text/markdown",
    "json" => "application/json",
};

static HTTP_CLIENT: Lazy<Client> = Lazy::new(|| {
    Client::builder()
        .user_agent(USER_AGENT)
        .build()
        .expect("failed to build global HTTP client")
});

fn get_client() -> &'static Client {
    &HTTP_CLIENT
}

fn touch_compile_items() {
    let _ = max_timeout_secs(1u64, 2u64);
    let _ = MAX_TIMEOUT_SECS;
    let _ = DEFAULT_README;
    let _ = BUILD_VERSION;
    let _ = OPTIONAL_FLAG;
    let _ = MIME_BY_EXT.get("md");

    if cfg!(feature = "zip") {
        zip_enabled();
    } else {
        println!("feature 'zip' not enabled");
    }

    println!("BUILD_FEATURES_CSV = {}", BUILD_FEATURES_CSV);
}

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
    touch_compile_items();

    let mut args = Args::parse();
    let token = args.token.take().or_else(|| var("GITHUB_TOKEN").ok());
    let url = read_url_from_args(&args)?;
    let (owner, repo) = parse_github_url(&url).map_err(|_| ERR_INVALID_URL)?;

    if owner.is_empty() || repo.is_empty() {
        eprintln!("Error: Could not determine repository owner or name.");
        return Err(ERR_INVALID_URL);
    }

    let dest = prepare_destination(&args, &repo)?;
    check_git_installed().map_err(|_| ERR_GIT_NOT_FOUND)?;

    let client = get_client();

    let reference =
        determine_reference(&args, &client, &owner, &repo, token.as_deref());

    let tmp = tempdir().map_err(|_| ERR_DOWNLOAD_FAILED)?;

    let zip_path = download_archive(
        &client,
        &owner,
        &repo,
        &reference,
        token.as_deref(),
        tmp.path(),
    )?;

    extract_zip(&zip_path, &dest).map_err(|e| {
        eprintln!("Failed to extract archive: {}", e);
        ERR_EXTRACTION_FAILED
    })?;

    remove_embedded_git(&dest);
    println!("Initializing new git repository...");

    initialize_repo(
        &dest,
        args.author_name.as_deref(),
        args.author_email.as_deref(),
        args.remote.as_deref(),
    )
    .map_err(|e| {
        eprintln!("Failed to initialize repository: {}", e);
        ERR_INIT_FAILED
    })?;

    println!("Done. Repository copied to: {}", dest.display());
    println!("Note: this repository has no history from the original repo.");
    Ok(())
}

fn read_url_from_args(args: &Args) -> Result<String, i32> {
    if let Some(u) = args.url.clone() {
        Ok(u)
    } else {
        print!("Enter repository URL: ");
        stdout().flush().ok();
        let mut input = String::new();
        stdin().read_line(&mut input).map_err(|_| ERR_INVALID_URL)?;
        Ok(input.trim().to_string())
    }
}

fn prepare_destination(args: &Args, repo: &str) -> Result<PathBuf, i32> {
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

    Ok(dest)
}

fn determine_reference(
    args: &Args,
    client: &Client,
    owner: &str,
    repo: &str,
    token: Option<&str>,
) -> String {
    if let Some(b) = args.branch.clone() {
        return b;
    }

    match get_default_branch(client, owner, repo, token) {
        Ok(b) => {
            println!("Using default branch '{}'", b);
            b
        },
        Err(e) => {
            eprintln!(
                "Warning: could not determine default branch: {}. Using '{}'.",
                e, DEFAULT_BRANCH
            );
            DEFAULT_BRANCH.to_string()
        },
    }
}

fn download_archive(
    client: &Client,
    owner: &str,
    repo: &str,
    reference: &str,
    token: Option<&str>,
    dest_dir: &Path,
) -> Result<PathBuf, i32> {
    match download_zip(client, owner, repo, reference, token, dest_dir) {
        Ok(p) => {
            println!("Downloaded archive to {}", p.display());
            Ok(p)
        },
        Err(e) => {
            eprintln!("Failed to download repository archive: {}", e);
            Err(ERR_DOWNLOAD_FAILED)
        },
    }
}

fn parse_github_url(url: &str) -> Result<(String, String), &'static str> {
    static RE_GITHUB: Lazy<Regex> =
        Lazy::new(|| Regex::new(RE_GITHUB_PATTERN).unwrap());

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

    let res = req.timeout(TIMEOUT_GET_REPO).send()?;

    match res.status().as_u16() {
        200 => {
            let v: Value = res.json()?;

            Ok(v.get("default_branch")
                .and_then(|b| b.as_str())
                .unwrap_or(DEFAULT_BRANCH)
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
        "https://api.github.com/repos/{}/{}/zipball/{}",
        owner, repo, reference
    );

    let mut req = client.get(&url).header("Accept", ACCEPT_HEADER);

    if let Some(t) = token {
        req = req.header("Authorization", format!("token {}", t));
    }

    let mut resp = req.timeout(TIMEOUT_DOWNLOAD).send()?;
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
    let filename = format!("{}{}.zip", ARCHIVE_PREFIX, ts.as_nanos());
    let path = dest_dir.join(filename);

    {
        let mut outfile = File::create(&path)?;
        io::copy(&mut resp, &mut outfile)?;
    }

    Ok(path)
}

fn extract_zip(zip_path: &Path, dest_dir: &Path) -> anyhow::Result<()> {
    let f = File::open(zip_path)?;
    let mmap = unsafe { MmapOptions::new().map(&f)? };
    let cursor = Cursor::new(&mmap[..]);
    let mut archive = ZipArchive::new(cursor)?;
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

    #[derive(Debug)]
    struct MemEntry {
        rel_path:  PathBuf,
        is_dir:    bool,
        data:      Option<Vec<u8>>,
        unix_mode: Option<u32>,
    }

    let mut entries: Vec<MemEntry> = Vec::with_capacity(len);
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

        if file.name().ends_with('/') {
            entries.push(MemEntry {
                rel_path,
                is_dir: true,
                data: None,
                unix_mode: file.unix_mode(),
            });
        } else {
            let mut buf: Vec<u8> = Vec::with_capacity(file.size() as usize);
            file.read_to_end(&mut buf)?;
            entries.push(MemEntry {
                rel_path,
                is_dir: false,
                data: Some(buf),
                unix_mode: file.unix_mode(),
            });
        }
    }

    entries.into_par_iter().try_for_each(|entry| -> anyhow::Result<()> {
        let outpath = dest_dir.join(&entry.rel_path);

        if entry.is_dir {
            create_dir_all(&outpath)?;
        } else {
            if let Some(parent) = outpath.parent() {
                create_dir_all(parent)?;
            }
            let mut outfile = File::create(&outpath)?;
            if let Some(ref data) = entry.data {
                outfile.write_all(data)?;
            }
            #[cfg(unix)]
            {
                if let Some(mode) = entry.unix_mode {
                    let _ =
                        set_permissions(&outpath, Permissions::from_mode(mode));
                }
            }
        }
        Ok(())
    })?;

    Ok(())
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> anyhow::Result<()> {
    create_dir_all(dst)?;

    let mut options = CopyOptions::new();
    options.copy_inside = true;
    options.overwrite = true;
    options.skip_exist = false;
    fs_extra_copy(src, dst, &options).map(|_bytes| ()).map_err(|e| anyhow!(e))
}

fn compute_blake3_hex(path: &Path) -> anyhow::Result<String> {
    // Try to use a memory map for large/regular files which is typically faster
    // than copying via a buffered reader. If mmap fails for any reason, fall
    // back to the buffered reader method for robustness.
    let f = File::open(path)?;
    let metadata = f.metadata()?;
    if metadata.len() > 0 {
        // Safety: mapping a file that is not concurrently truncated is okay for
        // our use.
        match unsafe { MmapOptions::new().map(&f) } {
            Ok(mmap) => {
                let mut hasher = Hasher::new();
                hasher.update(&mmap[..]);
                return Ok(hasher.finalize().to_hex().to_string());
            },
            Err(_) => {
                // fallthrough to buffered read on mmap failure
            },
        }
    }

    // Fallback: buffered read (keeps original behavior for empty files or mmap
    // failures)
    let mut reader = BufReader::with_capacity(8192, f);
    let mut hasher = Hasher::new();
    let mut buf = [0u8; 8192];
    loop {
        let n = reader.read(&mut buf)?;
        if n == 0 {
            break;
        }
        hasher.update(&buf[..n]);
    }
    Ok(hasher.finalize().to_hex().to_string())
}

fn move_items_to_dest(
    items: Vec<PathBuf>,
    dest_dir: &Path,
) -> anyhow::Result<()> {
    items.into_par_iter().try_for_each(|src| -> anyhow::Result<()> {
        let name =
            src.file_name().ok_or_else(|| anyhow!("Invalid source name"))?;

        let target = dest_dir.join(name);

        if target.exists() {
            if target.is_dir() {
                remove_dir_all(&target)?;
            } else {
                match (compute_blake3_hex(&src), compute_blake3_hex(&target)) {
                    (Ok(src_hash), Ok(tgt_hash)) if src_hash == tgt_hash => {
                        let _ = remove_file(&src);
                        return Ok(());
                    },
                    _ => remove_file(&target)?,
                }
            }
        }

        match rename(&src, &target) {
            Ok(_) => Ok(()),
            Err(_) => {
                if src.is_dir() {
                    copy_dir_recursive(&src, &target)?;
                    remove_dir_all(&src)?;
                    Ok(())
                } else {
                    if let Some(parent) = target.parent() {
                        create_dir_all(parent)?;
                    }

                    match hard_link(&src, &target) {
                        Ok(_) => {
                            let _ = remove_file(&src);
                            Ok(())
                        },
                        Err(_) => {
                            copy(&src, &target)?;
                            let _ = remove_file(&src);
                            Ok(())
                        },
                    }
                }
            },
        }
    })?;

    Ok(())
}

fn remove_embedded_git(dirpath: &Path) {
    let mut builder = WalkBuilder::new(dirpath);
    builder.standard_filters(false).hidden(false);

    builder.build_parallel().run(|| {
        Box::new(|res: Result<DirEntry, Error>| {
            match res {
                Ok(entry) => {
                    if entry.file_type().map(|ft| ft.is_dir()).unwrap_or(false)
                        && entry.file_name() == ".git"
                    {
                        let git_dir = entry.path().to_path_buf();
                        match remove_dir_all(&git_dir) {
                            Ok(_) => {
                                println!("Removed embedded .git at {}", git_dir.display());
                            }
                            Err(e) => {
                                eprintln!(
                                    "Warning: failed to remove embedded .git at {}: {}",
                                    git_dir.display(),
                                    e
                                );
                            }
                        }
                    }
                }
                Err(e) => {
                    eprintln!("Warning: walker error: {}", e);
                }
            }
            Continue
        })
    });
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
    let repo = Repository::init(dest)?;

    if let Some(name) = author_name {
        let mut cfg = repo.config()?;
        cfg.set_str("user.name", name)?;
    }
    if let Some(email) = author_email {
        let mut cfg = repo.config()?;
        cfg.set_str("user.email", email)?;
    }

    let mut index = repo.index()?;
    index.add_all(["*"].iter(), IndexAddOption::DEFAULT, None)?;
    index.write()?;

    let tree_id = index.write_tree()?;
    let tree = repo.find_tree(tree_id)?;

    let sig_name = author_name.unwrap_or("gitripper");
    let sig_email = author_email.unwrap_or("gitripper@localhost");
    let signature = Signature::now(sig_name, sig_email)?;

    repo.commit(
        Some("HEAD"),
        &signature,
        &signature,
        DEFAULT_COMMIT_MESSAGE,
        &tree,
        &[],
    )?;

    if let Some(r) = remote {
        repo.remote("origin", r)?;
        println!("Set remote origin to {}", r);
    }

    Ok(())
}

/* TODO: Potential optimizations / alternative crates to consider
      tokio + reqwest (async) — overlap network + disk work and parallelize downloads/IO.
      isahc or curl — libcurl-based clients that can be faster and more featureful for many concurrent connections.
      async-compression — for async decompression pipelines if you move to async extraction.
*/
