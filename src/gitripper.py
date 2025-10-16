from __future__ import annotations

from argparse import ArgumentParser
from os import environ, walk
from pathlib import Path
from re import match
from shutil import move, rmtree
from subprocess import DEVNULL, run
from sys import exit, stderr
from tempfile import TemporaryDirectory
from typing import Optional, Tuple
from zipfile import ZipFile

from requests import RequestException, get

GITHUB_API = "https://api.github.com"


"""
FIXME:
    FAILED test_gitripper.py::test_parse_github_url_invalid[https://gitlab.com/user/repo] - Failed: DID NOT RAISE <class 'ValueError'>
    FAILED test_gitripper.py::test_parse_github_url_invalid[not a url] - Failed: DID NOT RAISE <class 'ValueError'>
    FAILED test_gitripper.py::test_parse_github_url_invalid[user/repo] - Failed: DID NOT RAISE <class 'ValueError'>
    FAILED test_gitripper.py::test_main_download_fails - Exception: Download failed
    FAILED test_gitripper.py::test_main_extract_fails - Exception: Extract failed
    FAILED test_gitripper.py::test_main_init_fails - Exception: Init failed
"""


def parse_github_url(url: str) -> Tuple[str, str]:
    """Parse GitHub URL and return (owner, repo), or ("", "") on failure."""
    return next(
        (
            (m.group(1), m.group(2))
            for m in [
                match(r, url.strip().removesuffix(".git"))
                for r in [
                    r"https?://github\.com/([^/]+)/([^/]+)(/.*)?$",
                    r"git@github\.com:([^/]+)/([^/]+)$",
                    r"ssh://git@github\.com/([^/]+)/([^/]+)$",
                ]
            ]
            if m
        ),
        ("", ""),
    )


def get_default_branch(
    owner: str | None, repo: str, token: Optional[str]
) -> str:
    """Query GitHub API for default_branch."""
    if owner is None:
        raise ValueError("Owner cannot be None")

    url = (
        f"{GITHUB_API}/repos/{owner}/{repo}"
        if owner
        else f"{GITHUB_API}/repos/{repo}"
    )

    headers = {"Authorization": f"token {token}"} if token else {}
    r = get(url, headers=headers, timeout=30)

    if r.status_code == 200:
        branch_name: str = r.json().get("default_branch", "main")
        return branch_name

    elif r.status_code == 404:
        raise FileNotFoundError(f"Repository {owner}/{repo} not found (404).")

    else:
        raise RuntimeError(
            f"Failed to get repo info: {r.status_code} {r.text}"
        )


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

            if not dest_dir.exists():
                dest_dir.mkdir(parents=True)

            source_dir = (
                top_level_dirs[0] if len(top_level_dirs) == 1 else temp_path
            )

            for item in source_dir.iterdir():
                target = dest_dir / item.name

                if target.exists():
                    rmtree(target) if target.is_dir() else target.unlink()

                move(str(item), str(target))


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
        run(
            ["git", "--version"],
            check=True,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
    except OSError as e:
        raise EnvironmentError(
            "git is not installed or not available in PATH."
        ) from e


def initialize_repo(
    dest: Path,
    author_name: Optional[str],
    author_email: Optional[str],
    remote: Optional[str],
) -> None:
    """Initialize git repo with initial commit."""
    run(["git", "init"], cwd=str(dest), check=True)

    if author_name:
        run(
            ["git", "config", "user.name", author_name],
            cwd=str(dest),
            check=True,
        )

    if author_email:
        run(
            ["git", "config", "user.email", author_email],
            cwd=str(dest),
            check=True,
        )

    run(["git", "add", "."], cwd=str(dest), check=True)
    run(["git", "commit", "-m", "Initial commit"], cwd=str(dest), check=True)

    if remote:
        run(
            ["git", "remote", "add", "origin", remote],
            cwd=str(dest),
            check=True,
        )

        print(f"Set remote origin to {remote}")


def main() -> None:
    """Main entry point."""
    p = ArgumentParser(
        description="Download a GitHub repository's contents and "
        "create a local git repo."
    )

    p.add_argument("url", help="URL of the GitHub repository")

    p.add_argument(
        "--branch",
        help="Branch/ref to fetch (default: repo default branch)",
        default=None,
    )

    p.add_argument(
        "--token", help="GitHub personal access token", default=None
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
        help="Set git remote origin after initial commit",
        default=None,
    )

    p.add_argument(
        "--force",
        help="Overwrite destination if it exists",
        action="store_true",
    )

    args = p.parse_args()
    token = args.token or environ.get("GITHUB_TOKEN")

    try:
        owner, repo = parse_github_url(args.url)

        if owner is None or repo is None:
            raise ValueError(
                f"Could not determine repository "
                f"{'owner' if not owner else 'name' if not repo else 'info'}."
            )

    except ValueError as e:
        print(f"Error: {e}", file=stderr)
        exit(2)
        owner, repo = None, None

    dest = (
        Path(args.dest)
        if args.dest
        else Path(
            f"{repo}-copy" if repo is not None else "repo-copy"
        ).resolve()
    )

    # Check if destination exists
    if dest.exists():
        if any(dest.iterdir()) and not args.force:
            print(
                f"Destination '{dest}' exists and is not empty. "
                f"Use --force to overwrite.",
                file=stderr,
            )

            exit(3)

        elif args.force:
            try:
                if dest.is_dir():
                    rmtree(dest)

                else:
                    dest.unlink()

            except OSError as e:
                print(
                    f"Failed to remove existing destination: {e}",
                    file=stderr,
                )

                exit(4)

    try:
        check_git_installed()

    except EnvironmentError as e:
        print(f"Error: {e}", file=stderr)
        exit(5)

    ref = args.branch

    if ref is None:
        try:
            ref = get_default_branch(owner, repo, token)
            print(f"Using default branch '{ref}'")

        except Exception as e:  # FIXME: specify Exception type
            print(
                f"Warning: could not determine default branch: {e}. "
                f"Using 'main'."
            )

            ref = "main"

    # Download archive and extract
    with TemporaryDirectory() as tmp_dir_str:
        tmp_dir_path = Path(tmp_dir_str)

        try:
            print(f"Downloading {owner}/{repo}@{ref} ...")
            zip_path = download_zip(owner, repo, ref, token, tmp_dir_path)
            print(f"Downloaded archive to {zip_path}")

        except Exception as e:
            print(f"Failed to download repository archive: {e}", file=stderr)

            exit(6)

        try:
            print(f"Extracting archive to {dest} ...")
            extract_zip(zip_path, dest)

        except Exception as e:
            print(f"Failed to extract archive: {e}", file=stderr)
            exit(7)

    remove_embedded_git(dest)

    try:
        print("Initializing new git repository...")
        initialize_repo(dest, args.author_name, args.author_email, args.remote)

    except Exception as e:
        print(f"Failed to initialize repository: {e}", file=stderr)
        exit(8)

    print("Done. Repository copied to:", dest)
    print("Note: this repository has no history from the original repo.")


if __name__ == "__main__":
    main()
