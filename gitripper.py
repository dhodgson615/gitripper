"""gitripper.py

Given a GitHub repo URL, download the repository contents (zip archive),
extract into a local directory, remove any embedded .git, initialize a new
git repo, and make a single "Initial commit".

Features:
 - Accepts many forms of GitHub URLs (https, ssh, git@, with or without .git)
 - Optionally specify branch/ref; otherwise uses default branch via GitHub API
 - Support GitHub token for private repos / avoiding rate limits
 - Optionally set git author name/email
 - Optionally set remote origin
 - Safe by default: will not overwrite existing non-empty dest without --force
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, Tuple

try:
    import requests

except ImportError:
    print("Package 'requests' not found. Attempting to install...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests


GITHUB_API = "https://api.github.com"


def parse_github_url(url: str) -> Tuple[str, str]:
    """
    Parse various GitHub URL forms and return (owner, repo).
    Accepts:
      - https://github.com/owner/repo
      - https://github.com/owner/repo.git
      - git@github.com:owner/repo.git
      - ssh://git@github.com/owner/repo.git
    Raises ValueError if parsing fails.
    """
    original = url.strip()

    # Remove trailing .git
    if original.endswith(".git"):
        original = original[:-4]

    # HTTPS or HTTP
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)(/.*)?$", original)

    if m:
        return m.group(1), m.group(2)

    # SSH form: git@github.com:owner/repo
    m = re.match(r"git@github\.com:([^/]+)/([^/]+)$", original)

    if m:
        return m.group(1), m.group(2)

    # ssh://git@github.com/owner/repo
    m = re.match(r"ssh://git@github\.com/([^/]+)/([^/]+)$", original)

    if m:
        return m.group(1), m.group(2)

    raise ValueError(f"Could not parse GitHub URL: {url}")


def get_default_branch(owner: str, repo: str, token: Optional[str]) -> str:
    """Query GitHub API for default_branch."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    headers = {}

    if token:
        headers["Authorization"] = f"token {token}"

    r = requests.get(url, headers=headers, timeout=30)

    if r.status_code == 200:
        data = r.json()
        return data.get("default_branch", "main")

    elif r.status_code == 404:
        raise FileNotFoundError(f"Repository {owner}/{repo} not found (404).")

    else:
        raise RuntimeError(
            f"Failed to get repo info: {r.status_code} {r.text}"
        )


def download_zip(
    owner: str, repo: str, ref: str, token: Optional[str], dest_path: Path
) -> Path:
    """
    Download the zip archive for owner/repo@ref and save to dest_path.
    Returns path to the saved zip file.
    Uses GitHub API archive endpoint.
    """
    # Use API zipball endpoint
    url = f"{GITHUB_API}/repos/{owner}/{repo}/zipball/{ref}"
    headers = {"Accept": "application/vnd.github+json"}

    if token:
        headers["Authorization"] = f"token {token}"

    with requests.get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 200:
            zip_file = dest_path / f"{repo}-{ref}.zip"

            with open(zip_file, "wb") as fh:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)

            return zip_file

        elif r.status_code == 302 or r.status_code == 301:
            # follow redirect (requests would normally follow), but keeping fallback
            raise RuntimeError(
                f"Unexpected redirect from GitHub archive endpoint: {r.status_code}"
            )

        elif r.status_code == 404:
            raise FileNotFoundError(
                f"Archive for {owner}/{repo}@{ref} not found (404)."
            )

        else:
            raise RuntimeError(
                f"Failed to download archive: {r.status_code} {r.text}"
            )


def extract_zip(zip_path: Path, dest_dir: Path):
    """Extract zip_path into dest_dir. Assumes single top-level folder in zip."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.namelist()

        if not members:
            raise RuntimeError("Zip archive is empty.")

        # Extract all to temp directory so we can flatten top-level dir
        with tempfile.TemporaryDirectory() as tmpd:
            tmpd = Path(tmpd)
            zf.extractall(tmpd)

            # The archive usually contains a single top-level folder like owner-repo-<hash>/
            top_level_dirs = [p for p in tmpd.iterdir() if p.is_dir()]

            if len(top_level_dirs) == 1:
                top = top_level_dirs[0]

                # Move contents of top into dest_dir
                if not dest_dir.exists():
                    dest_dir.mkdir(parents=True)

                for item in top.iterdir():
                    target = dest_dir / item.name

                    if target.exists():
                        # If target exists, remove (we're copying into a fresh dir typically)
                        if target.is_dir():
                            shutil.rmtree(target)

                        else:
                            target.unlink()

                    if item.is_dir():
                        shutil.move(str(item), str(target))

                    else:
                        shutil.move(str(item), str(target))

            else:
                # If zip has multiple top-level entries, copy all
                if not dest_dir.exists():
                    dest_dir.mkdir(parents=True)

                for entry in tmpd.iterdir():
                    tgt = dest_dir / entry.name

                    if tgt.exists():
                        if tgt.is_dir():
                            shutil.rmtree(tgt)

                        else:
                            tgt.unlink()

                    shutil.move(str(entry), str(tgt))


def remove_embedded_git(dirpath: Path):
    """Recursively remove any .git directories that might be in the extracted content."""
    for root, dirs, files in os.walk(dirpath):
        if ".git" in dirs:
            git_dir = Path(root) / ".git"

            try:
                shutil.rmtree(git_dir)
                print(f"Removed embedded .git at {git_dir}")

            except Exception as e:
                print(
                    f"Warning: failed to remove embedded .git at {git_dir}: {e}",
                    file=sys.stderr,
                )


def check_git_installed():
    """Ensure git is available in PATH."""
    try:
        subprocess.run(
            ["git", "--version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    except Exception as e:
        raise EnvironmentError(
            "git is not installed or not available in PATH."
        ) from e


def initialize_repo(
    dest: Path,
    author_name: Optional[str],
    author_email: Optional[str],
    remote: Optional[str],
):
    """Initialize git repo, add files, and create initial commit."""
    # init
    subprocess.run(["git", "init"], cwd=str(dest), check=True)

    # optionally set local user.name/email if provided
    if author_name:
        subprocess.run(
            ["git", "config", "user.name", author_name],
            cwd=str(dest),
            check=True,
        )

    if author_email:
        subprocess.run(
            ["git", "config", "user.email", author_email],
            cwd=str(dest),
            check=True,
        )

    # git add .
    # Use env to avoid pager and to ensure no interactive prompts
    subprocess.run(["git", "add", "."], cwd=str(dest), check=True)

    # commit
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=str(dest), check=True
    )

    # optionally set remote
    if remote:
        subprocess.run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(dest),
            check=True,
        )
        print(f"Set remote origin to {remote}")


def main():
    p = argparse.ArgumentParser(
        description="Download a GitHub repository's contents (no history) and create a local git repo with a single Initial commit."
    )
    p.add_argument(
        "url",
        help="URL of the GitHub repository (https://github.com/owner/repo, git@..., etc.)",
    )
    p.add_argument(
        "--branch",
        help="Branch/ref to fetch (default: repo default branch)",
        default=None,
    )
    p.add_argument(
        "--token",
        help="GitHub personal access token (also read from GITHUB_TOKEN env var)",
        default=None,
    )
    p.add_argument(
        "--dest",
        help="Destination directory (default: ./<repo>-copy)",
        default=None,
    )
    p.add_argument(
        "--author-name",
        help="Set git user.name for the initial commit",
        default=None,
    )
    p.add_argument(
        "--author-email",
        help="Set git user.email for the initial commit",
        default=None,
    )
    p.add_argument(
        "--remote",
        help="Set git remote origin (e.g. a new repo URL) after initial commit",
        default=None,
    )
    p.add_argument(
        "--force",
        help="Overwrite destination if it exists",
        action="store_true",
    )
    args = p.parse_args()
    token = args.token or os.environ.get("GITHUB_TOKEN")

    try:
        owner, repo = parse_github_url(args.url)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    dest = Path(args.dest) if args.dest else Path(f"{repo}-copy").resolve()

    # If dest exists and non-empty and not force -> error
    if dest.exists():
        if any(dest.iterdir()) and not args.force:
            print(
                f"Destination '{dest}' exists and is not empty. Use --force to overwrite.",
                file=sys.stderr,
            )
            sys.exit(3)

        elif args.force:
            # remove and recreate
            try:
                if dest.is_dir():
                    shutil.rmtree(dest)

                else:
                    dest.unlink()

            except Exception as e:
                print(
                    f"Failed to remove existing destination: {e}",
                    file=sys.stderr,
                )
                sys.exit(4)

    try:
        check_git_installed()

    except EnvironmentError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(5)

    # Determine ref
    ref = args.branch

    if ref is None:
        try:
            ref = get_default_branch(owner, repo, token)
            print(f"Using default branch '{ref}'")

        except Exception as e:
            print(
                f"Warning: could not determine default branch via API: {e}. Falling back to 'main'."
            )
            ref = "main"

    # Download archive and extract
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        try:
            print(f"Downloading {owner}/{repo}@{ref} ...")
            zip_path = download_zip(owner, repo, ref, token, tmp)
            print(f"Downloaded archive to {zip_path}")

        except Exception as e:
            print(
                f"Failed to download repository archive: {e}", file=sys.stderr
            )
            sys.exit(6)

        try:
            print(f"Extracting archive to {dest} ...")
            extract_zip(zip_path, dest)

        except Exception as e:
            print(f"Failed to extract archive: {e}", file=sys.stderr)
            sys.exit(7)

    # Remove any embedded .git
    remove_embedded_git(dest)

    # Initialize git repo and make initial commit
    try:
        print("Initializing new git repository and committing files...")
        initialize_repo(dest, args.author_name, args.author_email, args.remote)

    except subprocess.CalledProcessError as e:
        print(f"Git command failed: {e}", file=sys.stderr)
        sys.exit(8)

    except Exception as e:
        print(f"Failed to initialize repository: {e}", file=sys.stderr)
        sys.exit(9)

    print("Done. Repository copied to:", dest)
    print(
        "Note: this repository has no history from the original repo. Check license of original before redistribution."
    )


if __name__ == "__main__":
    main()
