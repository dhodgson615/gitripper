use std::{
    fs::{File, Permissions, create_dir_all, set_permissions},
    io::{self, Cursor, Write},
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
};

use anyhow::anyhow;
use memmap2::MmapOptions;
use once_cell::sync::Lazy;
use regex::Regex;
use zip::ZipArchive;

const RE_GITHUB_PATTERN: &str = r"(?xi)^(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+)/([^/]+?)(?:\.git)?(?:/|$)";
const PARALLEL_THRESHOLD_BYTES: u64 = 10_485_760; // 10 MB

/// Represents an entry in the ZIP archive before extraction
#[derive(Debug)]
pub struct MemEntry {
    pub rel_path:   PathBuf,
    pub is_dir:     bool,
    pub _data_size: u64,
    pub unix_mode:  Option<u32>,
    pub _file_idx:  usize,
    pub data:       Vec<u8>,
}

/// Parse a GitHub URL to extract owner and repository name
pub fn parse_github_url(url: &str) -> Result<(String, String), &'static str> {
    static RE_GITHUB: Lazy<Regex> =
        Lazy::new(|| Regex::new(RE_GITHUB_PATTERN).unwrap());

    let trimmed = url.trim();
    let stripped = trimmed.strip_suffix(".git").unwrap_or(trimmed);

    if let Some(caps) = RE_GITHUB.captures(stripped) {
        let owner = caps.get(1).unwrap().as_str().to_string();
        let repo = caps.get(2).unwrap().as_str().to_string();
        Ok((owner, repo))
    } else {
        Err("Invalid GitHub URL")
    }
}

/// Write a single entry (file or directory) to disk
pub fn write_entry(entry: &MemEntry, dest_dir: &Path) -> anyhow::Result<()> {
    let outpath = dest_dir.join(&entry.rel_path);

    if entry.is_dir {
        create_dir_all(&outpath)?;
    } else {
        if let Some(parent) = outpath.parent() {
            create_dir_all(parent)?;
        }
        let mut outfile = File::create(&outpath)?;
        outfile.write_all(&entry.data)?;

        #[cfg(unix)]
        {
            if let Some(mode) = entry.unix_mode {
                let _ = set_permissions(&outpath, Permissions::from_mode(mode));
            }
        }
    }
    Ok(())
}

/// Extract a ZIP archive to the destination directory
pub fn extract_zip(zip_path: &Path, dest_dir: &Path) -> anyhow::Result<()> {
    let f = File::open(zip_path)?;
    let mmap = unsafe { MmapOptions::new().map(&f)? };
    let cursor = Cursor::new(&mmap[..]);
    let mut archive = ZipArchive::new(cursor)?;
    let len = archive.len();

    if len == 0 {
        return Err(anyhow!("Zip archive is empty."));
    }

    create_dir_all(dest_dir)?;

    let mut entries: Vec<MemEntry> = Vec::with_capacity(len);
    let mut root_prefix: Option<PathBuf> = None;
    let mut root_mismatch = false;
    let mut total_size: u64 = 0;

    // Single pass: detect root prefix, collect entries, and read file data
    for i in 0..len {
        let mut file = archive.by_index(i)?;
        let in_path = file
            .enclosed_name()
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|| PathBuf::from(file.name()));

        // Root prefix detection with early short-circuit
        if !root_mismatch {
            if let Some(first) = in_path.components().next() {
                let first_str = first.as_os_str().to_string_lossy();
                if first_str.is_empty() {
                    root_mismatch = true;
                } else if let Some(ref current_prefix) = root_prefix {
                    if current_prefix.as_os_str() != first.as_os_str() {
                        root_mismatch = true;
                    }
                } else {
                    root_prefix = Some(PathBuf::from(first.as_os_str()));
                }
            } else {
                root_mismatch = true;
            }
        }

        let rel_path = if !root_mismatch {
            if let Some(ref root) = root_prefix {
                match in_path.strip_prefix(root) {
                    Ok(p) => p.to_path_buf(),
                    Err(_) => {
                        root_mismatch = true;
                        in_path.clone()
                    },
                }
            } else {
                in_path.clone()
            }
        } else {
            in_path.clone()
        };

        if rel_path.as_os_str().is_empty() {
            continue;
        }

        let is_dir = file.name().ends_with('/');
        let unix_mode = file.unix_mode();

        // Read file data in single by_index call
        let (data_size, data) = if is_dir {
            (0, Vec::new())
        } else {
            let size = file.size();
            let mut buf = Vec::with_capacity(size as usize);
            io::copy(&mut file, &mut buf)?;
            (size, buf)
        };

        total_size += data_size;

        entries.push(MemEntry {
            rel_path,
            is_dir,
            _data_size: data_size,
            unix_mode,
            _file_idx: i,
            data,
        });
    }

    // Gate parallelism on total size
    if total_size > PARALLEL_THRESHOLD_BYTES {
        use rayon::iter::{IntoParallelIterator, ParallelIterator};
        entries.into_par_iter().try_for_each(
            |entry| -> anyhow::Result<()> { write_entry(&entry, dest_dir) },
        )?;
    } else {
        for entry in entries {
            write_entry(&entry, dest_dir)?;
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_github_url_https() {
        let url = "https://github.com/user/repo";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_parse_github_url_https_with_git() {
        let url = "https://github.com/user/repo.git";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_parse_github_url_ssh() {
        let url = "git@github.com:user/repo.git";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_parse_github_url_ssh_no_git() {
        let url = "git@github.com:user/repo";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_parse_github_url_ssh_protocol() {
        let url = "ssh://git@github.com/user/repo.git";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_parse_github_url_with_trailing_slash() {
        let url = "https://github.com/user/repo/";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_parse_github_url_with_whitespace() {
        let url = "  https://github.com/user/repo  ";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_parse_github_url_invalid() {
        let url = "https://example.com/user/repo";
        assert!(parse_github_url(url).is_err());
    }

    #[test]
    fn test_parse_github_url_invalid_empty() {
        let url = "";
        assert!(parse_github_url(url).is_err());
    }

    #[test]
    fn test_parse_github_url_case_insensitive() {
        let url = "HTTPS://GITHUB.COM/user/repo";
        let (owner, repo) = parse_github_url(url).unwrap();
        assert_eq!(owner, "user");
        assert_eq!(repo, "repo");
    }

    #[test]
    fn test_write_entry_file() {
        let temp_dir = tempfile::tempdir().unwrap();
        let dest = temp_dir.path();

        let entry = MemEntry {
            rel_path:   PathBuf::from("test.txt"),
            is_dir:     false,
            _data_size: 11,
            unix_mode:  Some(0o644),
            _file_idx:  0,
            data:       b"hello world".to_vec(),
        };

        write_entry(&entry, dest).unwrap();

        let file_path = dest.join("test.txt");
        assert!(file_path.exists());
        let content = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(content, "hello world");
    }

    #[test]
    fn test_write_entry_nested_file() {
        let temp_dir = tempfile::tempdir().unwrap();
        let dest = temp_dir.path();

        let entry = MemEntry {
            rel_path:   PathBuf::from("nested/dir/test.txt"),
            is_dir:     false,
            _data_size: 5,
            unix_mode:  Some(0o644),
            _file_idx:  0,
            data:       b"hello".to_vec(),
        };

        write_entry(&entry, dest).unwrap();

        let file_path = dest.join("nested/dir/test.txt");
        assert!(file_path.exists());
        let content = std::fs::read_to_string(&file_path).unwrap();
        assert_eq!(content, "hello");
    }

    #[test]
    fn test_write_entry_directory() {
        let temp_dir = tempfile::tempdir().unwrap();
        let dest = temp_dir.path();

        let entry = MemEntry {
            rel_path:   PathBuf::from("mydir"),
            is_dir:     true,
            _data_size: 0,
            unix_mode:  None,
            _file_idx:  0,
            data:       Vec::new(),
        };

        write_entry(&entry, dest).unwrap();

        let dir_path = dest.join("mydir");
        assert!(dir_path.is_dir());
    }

    #[test]
    fn test_mem_entry_debug() {
        let entry = MemEntry {
            rel_path:   PathBuf::from("test.txt"),
            is_dir:     false,
            _data_size: 5,
            unix_mode:  Some(0o644),
            _file_idx:  0,
            data:       b"hello".to_vec(),
        };

        let debug_str = format!("{:?}", entry);
        assert!(debug_str.contains("test.txt"));
        assert!(debug_str.contains("false"));
    }
}
