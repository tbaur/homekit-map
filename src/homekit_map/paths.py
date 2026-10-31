# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Locations for the live store and the local snapshot."""

from __future__ import annotations

from pathlib import Path

# Apple's HomeKit daemon keeps the live database here. Commands other than
# backup refuse to open it. Backup reads it and writes somewhere else.
LIVE_HOMEKIT_DIR = Path.home() / "Library" / "HomeKit"
DEFAULT_SOURCE = LIVE_HOMEKIT_DIR / "core.sqlite"


def repo_root() -> Path:
    """Directory that contains this checkout, independent of the working directory.

    Only a checkout with this package under src/ counts, so an install inside
    another project does not put data/ in that project. Outside a checkout,
    data/ goes in the working directory.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src" / "homekit_map").is_dir():
            return parent
    return Path.cwd()


def default_snapshot_path() -> Path:
    """Snapshot path for this checkout. The data directory is gitignored."""
    return repo_root() / "data" / "core.sqlite"


def default_export_path() -> Path:
    return repo_root() / "data" / "export.json"


def is_within(path: Path, directory: Path) -> bool:
    """True when path is the directory or a file inside it.

    Text comparison is not enough: macOS volumes are usually case-insensitive,
    so `live/` and `Live/` name the same folder. Existing parts of the path are
    also compared by device and inode.
    """
    if path == directory or directory in path.parents:
        return True
    try:
        target = directory.stat()
    except OSError:
        return False
    for candidate in (path, *path.parents):
        try:
            info = candidate.stat()
        except OSError:
            continue
        if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
            return True
    return False
