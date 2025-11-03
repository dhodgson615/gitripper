from __future__ import annotations

from argparse import ArgumentParser
from os import environ, walk
from pathlib import Path
from re import match
from shutil import move, rmtree
from subprocess import DEVNULL, run
from sys import exit, stderr
from tempfile import TemporaryDirectory
from typing import Any, List, Optional, Tuple
from zipfile import ZipFile

from requests import RequestException, get


def main() -> None:
    """Main entry point."""
    p = ArgumentParser(
        description="Download a GitHub repository's contents and create a "
        "local git repo."
    )

    arguments: List[Tuple[str, dict[str, Any]]] = [
        ("url", {"help": "URL of the GitHub repository"}),
        (
            "--branch",
            {"help": "Branch/ref to fetch (default: repo default branch)"},
        ),
        ("--token", {"help": "GitHub personal access token"}),
        ("--dest", {"help": "Destination directory (default: ./<repo>-copy)"}),
        (
            "--author-name",
            {"help": "Set git user.name for the initial commit"},
        ),
        (
            "--author-email",
            {"help": "Set git user.email for the initial commit"},
        ),
        ("--remote", {"help": "Set git remote origin after initial commit"}),
        (
            "--force",
            {
                "help": "Overwrite destination if it exists",
                "action": "store_true",
            },
        ),
    ]

    for arg, kwargs in arguments:
        p.add_argument(arg, **kwargs)

    args = p.parse_args()
    token = args.token or environ.get("GITHUB_TOKEN")
    owner, repo = "", ""

    try:
        owner, repo = parse_github_url(args.url)

        if owner == "" or repo == "":
            raise ValueError("Could not determine repository owner or name.")

    except ValueError as e:
        print(f"Error: {e}", file=stderr)
        exit(ERR_INVALID_URL)

    dest = (
        Path(args.dest)
        if args.dest
        else Path(f"{repo}-copy" if repo else "repo-copy").resolve()
    )

    if dest.exists():
        if any(dest.iterdir()) and not args.force:
            print(
                f"Destination '{dest}' exists and is not empty."
                f"Use --force to overwrite.",
                file=stderr,
            )

            exit(ERR_DEST_EXISTS)

        if args.force:
            try:
                rmtree(dest) if dest.is_dir() else dest.unlink()

            except OSError as e:
                print(
                    f"Failed to remove existing destination: {e}", file=stderr
                )

                exit(ERR_CLEANUP_FAILED)

    try:
        check_git_installed()

    except EnvironmentError as e:
        print(f"Error: {e}", file=stderr)
        exit(ERR_GIT_NOT_FOUND)

    ref = args.branch

    if not ref:
        try:
            ref = get_default_branch(owner, repo, token)
            print(f"Using default branch '{ref}'")

        except Exception as e:
            print(
                f"Warning: could not determine default branch: {e}. "
                f"Using 'main'."
            )

            ref = "main"

    with TemporaryDirectory() as tmp_dir_str:
        tmp_dir_path = Path(tmp_dir_str)

        try:
            print(f"Downloading {owner}/{repo}@{ref} ...")
            zip_path = download_zip(owner, repo, ref, token, tmp_dir_path)
            print(f"Downloaded archive to {zip_path}")

        except RequestException as e:
            print(f"Failed to download repository archive: {e}", file=stderr)
            exit(ERR_DOWNLOAD_FAILED)

        try:
            print(f"Extracting archive to {dest} ...")
            extract_zip(zip_path, dest)

        except OSError as e:
            print(f"Failed to extract archive: {e}", file=stderr)
            exit(ERR_EXTRACTION_FAILED)

    remove_embedded_git(dest)

    try:
        print("Initializing new git repository...")
        initialize_repo(dest, args.author_name, args.author_email, args.remote)

    except OSError as e:
        print(f"Failed to initialize repository: {e}", file=stderr)
        exit(ERR_INIT_FAILED)

    print("Done. Repository copied to:", dest)
    print("Note: this repository has no history from the original repo.")


if __name__ == "__main__":
    main()


def get_default_branch(
    owner: Optional[str], repo: str, token: Optional[str]
) -> Any:
    """Query GitHub API for default_branch."""
    if owner is None:
        raise ValueError("Owner cannot be None")

    url = f"{GITHUB_API}/repos/{owner}/{repo}"
    headers = {"Authorization": f"token {token}"} if token else {}
    r = get(url, headers=headers, timeout=30)

    if r.status_code == 200:
        return r.json().get("default_branch", "main")

    if r.status_code == 404:
        raise FileNotFoundError(f"Repository {owner}/{repo} not found (404).")

    raise RuntimeError(f"Failed to get repo info: {r.status_code} {r.text}")


ERR_INVALID_URL = 2
ERR_DEST_EXISTS = 3
ERR_CLEANUP_FAILED = 4
ERR_GIT_NOT_FOUND = 5
ERR_DOWNLOAD_FAILED = 6
ERR_EXTRACTION_FAILED = 7
ERR_INIT_FAILED = 8


def initialize_repo(
    dest: Path,
    author_name: Optional[str],
    author_email: Optional[str],
    remote: Optional[str],
) -> None:
    """Initialize git repo with initial commit."""

    def git(*args: str) -> None:
        """Run git command in dest directory."""
        run(["git", *args], cwd=str(dest), check=True)

    git("init")

    for key, value in [
        ("user.name", author_name),
        ("user.email", author_email),
    ]:
        if value:
            git("config", key, value)

    git("add", ".")
    git("commit", "-m", "Initial commit")

    if remote:
        git("remote", "add", "origin", remote)
        print(f"Set remote origin to {remote}")


def download_zip(
    owner: str, repo: str, ref: str, token: Optional[str], dest_path: Path
) -> Path:
    """Download the zip archive for owner/repo@ref."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/zipball/{ref}"
    headers = {"Accept": "application/vnd.github+json"}

    if token:
        headers["Authorization"] = f"token {token}"

    with get(url, headers=headers, stream=True, timeout=60) as r:
        if r.status_code == 200:
            zip_file = dest_path / f"{repo}-{ref}.zip"

            with open(zip_file, "wb") as fh:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        fh.write(chunk)

            return zip_file

        raise (
            RuntimeError(f"Unexpected redirect: {r.status_code}")
            if r.status_code in (301, 302)
            else (
                FileNotFoundError(
                    f"Archive for {owner}/{repo}@{ref} not found (404)."
                )
                if r.status_code == 404
                else RuntimeError(
                    f"Failed to download archive: {r.status_code} {r.text}"
                )
            )
        )


def extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract zip archive to destination directory."""
    with ZipFile(zip_path, "r") as zf:
        if not zf.namelist():
            raise RuntimeError("Zip archive is empty.")

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            zf.extractall(path=temp_path)
            top_level_dirs = [p for p in temp_path.iterdir() if p.is_dir()]
            dest_dir.mkdir(parents=True, exist_ok=True)

            source_dir = (
                top_level_dirs[0] if len(top_level_dirs) == 1 else temp_path
            )

            for item in source_dir.iterdir():
                target = dest_dir / item.name

                if target.exists():
                    rmtree(target) if target.is_dir() else target.unlink()

                move(str(item), str(target))


def parse_github_url(url: str) -> Tuple[str, str]:
    """Parse GitHub URL and return (owner, repo), or raise ValueError
    on failure.
    """
    url = url.strip().removesuffix(".git")

    for r in [
        r"https?://github\.com/([^/]+)/([^/]+)(/.*)?$",
        r"git@github\.com:([^/]+)/([^/]+)$",
        r"ssh://git@github\.com/([^/]+)/([^/]+)$",
    ]:
        m = match(r, url)

        if m:
            return m.group(1), m.group(2)

    raise ValueError(f"Invalid GitHub URL: {url}")


GITHUB_API = "https://api.github.com"


def remove_embedded_git(dirpath: Path) -> None:
    """Recursively remove any .git directories."""
    for root, dirs, _ in walk(dirpath):
        if ".git" in dirs:
            git_dir = Path(root) / ".git"

            try:
                rmtree(git_dir)
                print(f"Removed embedded .git at {git_dir}")

            except OSError as e:
                print(
                    f"Warning: failed to remove embedded .git at "
                    f"{git_dir}: {e}",
                    file=stderr,
                )


def check_git_installed() -> None:
    """Ensure git is available in PATH."""
    try:
        run(["git", "--version"], check=True, stdout=DEVNULL, stderr=DEVNULL)

    except OSError as e:
        raise EnvironmentError(
            "git is not installed or not available in PATH."
        ) from e
