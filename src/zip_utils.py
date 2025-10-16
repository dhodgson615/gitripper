from pathlib import Path
from shutil import move, rmtree
from tempfile import TemporaryDirectory
from typing import Optional
from zipfile import ZipFile

from requests import get

from src.github_api import GITHUB_API


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

        if r.status_code in (301, 302):
            raise RuntimeError(f"Unexpected redirect: {r.status_code}")

        if r.status_code == 404:
            raise FileNotFoundError(
                f"Archive for {owner}/{repo}@{ref} not found (404)."
            )

        raise RuntimeError(
            f"Failed to download archive: {r.status_code} {r.text}"
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
