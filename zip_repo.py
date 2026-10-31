#!/usr/bin/env python3
"""
zip_repo.py - Zip up a git repo's contents, excluding anything .gitignore
(or other git exclude rules) would ignore.

This works by asking git itself which files it tracks/would track
(`git ls-files --cached --others --exclude-standard`), so nested
.gitignore files, global gitignore, and .git/info/exclude are all
respected automatically — no manual pattern parsing needed.

Usage:
    python zip_repo.py [repo_path] [output_zip]

    repo_path   Path to the git repo (default: current directory)
    output_zip  Path for the resulting zip file (default: <repo_name>.zip,
                placed next to repo_path)
"""

import subprocess
import sys
import zipfile
from pathlib import Path


def get_included_files(repo_path: Path) -> list[str]:
    """Return every file git would include: tracked + untracked-but-not-ignored."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo_path,
            capture_output=True,
            check=True,
        )
    except FileNotFoundError:
        sys.exit("Error: git is not installed or not on PATH.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"Error running git ls-files: {e.stderr.decode().strip()}")

    files = result.stdout.decode("utf-8", errors="replace").split("\0")
    return [f for f in files if f]  # drop trailing empty entry


def zip_repo(repo_path: Path, output_zip: Path) -> None:
    files = get_included_files(repo_path)

    if not files:
        sys.exit("No files found (empty repo, or not a git repo?).")

    output_zip.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in files:
            full_path = repo_path / rel_path
            if full_path.is_file():
                zf.write(full_path, arcname=rel_path)

    print(f"Zipped {len(files)} files into {output_zip}")


def main():
    repo_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.cwd()

    if not (repo_path / ".git").exists():
        sys.exit(f"Error: {repo_path} doesn't look like a git repo (no .git folder).")

    default_name = f"{repo_path.name}.zip"
    output_zip = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) > 2
        else repo_path.parent / default_name
    )

    zip_repo(repo_path, output_zip)


if __name__ == "__main__":
    main()
