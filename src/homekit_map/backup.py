# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Copy the live HomeKit database without writing it.

The live file is opened read-only, and only when its -wal file already
exists. Opening a WAL database that has no -wal file makes SQLite create
that file, so that case is a byte copy instead. The snapshot is written
outside the live folder. A later backup may replace the snapshot. It
cannot replace the live database.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from homekit_map.errors import SnapshotError
from homekit_map.paths import is_within

COPY_CHUNK = 1024 * 1024
BACKUP_PAGES = 32
BACKUP_SLEEP_SECONDS = 0.05
SQLITE_MAGIC = b"SQLite format 3\x00"


def sqlite_readonly_uri(path: Path) -> str:
    """Build a file URI that asks SQLite to open this path read-only."""
    quoted = quote(str(path), safe="/")
    return f"file://{quoted}?mode=ro"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(COPY_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def assert_regular_database(source: Path) -> None:
    info = source.lstat()
    if stat.S_ISLNK(info.st_mode):
        raise SnapshotError(f"Refusing to follow a symlink: {source}")
    if not stat.S_ISREG(info.st_mode):
        raise SnapshotError(f"Source is not a regular file: {source}")
    if info.st_size < len(SQLITE_MAGIC):
        raise SnapshotError(f"Source is too small to be a SQLite database: {source}")
    with source.open("rb") as handle:
        magic = handle.read(len(SQLITE_MAGIC))
    if magic != SQLITE_MAGIC:
        raise SnapshotError(f"Source is not a SQLite database: {source}")


def assert_destination_allowed(source: Path, dest: Path) -> None:
    """The snapshot must stay outside the folder that holds the source."""
    if dest.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {dest}")
    parent = dest.parent
    if not parent.is_dir():
        raise SnapshotError(f"Destination directory does not exist: {parent}")
    if parent.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {parent}")
    if dest.exists() and not dest.is_file():
        raise SnapshotError(f"Snapshot destination is not a file: {dest}")

    live_dir = source.resolve().parent
    dest_resolved = parent.resolve() / dest.name
    source_resolved = source.resolve()
    if dest_resolved == source_resolved:
        raise SnapshotError("Refusing to write the snapshot onto the source database.")
    protected = {
        source_resolved,
        live_dir / f"{source.name}-wal",
        live_dir / f"{source.name}-shm",
    }
    if dest_resolved in protected or is_within(dest_resolved, live_dir):
        raise SnapshotError(f"Refusing to write inside the live HomeKit folder: {dest_resolved}")


def unlink_snapshot(path: Path, source: Path) -> None:
    """Delete a file this tool created. Refuse if it is in the live folder."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        raise SnapshotError(f"Refusing to delete a symlink: {path}")
    resolved = path.resolve()
    live_dir = source.resolve().parent
    if is_within(resolved, live_dir):
        raise SnapshotError(f"Refusing to delete a live HomeKit file: {resolved}")
    path.unlink()


def write_all(fd: int, chunk: bytes) -> None:
    view = memoryview(chunk)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise SnapshotError("Could not write the snapshot.")
        view = view[written:]


def copy_bytes(source: Path, dest: Path) -> None:
    """Read source once and write dest. Both opens refuse to follow a symlink."""
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(source_fd)
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotError(f"Source is not a regular file: {source}")
        dest_fd = os.open(dest, os.O_WRONLY | os.O_NOFOLLOW)
        try:
            while True:
                chunk = os.read(source_fd, COPY_CHUNK)
                if not chunk:
                    break
                write_all(dest_fd, chunk)
        finally:
            os.close(dest_fd)
    finally:
        os.close(source_fd)
    os.chmod(dest, 0o600)


def seal_snapshot(dest: Path, source: Path) -> None:
    """Make the snapshot one file and check it. dest is the copy, not the live store."""
    assert_destination_allowed(source, dest)
    connection = sqlite3.connect(dest)
    try:
        journal = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
        if journal is None or str(journal[0]).lower() != "delete":
            raise SnapshotError(f"Could not seal the snapshot into one file: {dest}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            detail = integrity[0] if integrity else "no result"
            raise SnapshotError(f"Snapshot failed its integrity check: {detail}")
    finally:
        connection.close()
    os.chmod(dest, 0o600)


def _require_query_only(connection: sqlite3.Connection) -> None:
    query_only = connection.execute("PRAGMA query_only").fetchone()
    if query_only is None or query_only[0] != 1:
        raise SnapshotError("SQLite did not keep the live database read-only. Stopped.")


def backup_with_sqlite(source: Path, partial: Path) -> None:
    """Read a consistent snapshot from a live WAL database into partial."""
    assert_destination_allowed(source, partial)
    src = sqlite3.connect(sqlite_readonly_uri(source), uri=True)
    try:
        src.execute("PRAGMA query_only=ON")
        _require_query_only(src)
        dst = sqlite3.connect(partial)
        try:
            src.backup(dst, pages=BACKUP_PAGES, sleep=BACKUP_SLEEP_SECONDS)
            _require_query_only(src)
        finally:
            dst.close()
    finally:
        src.close()


def create_partial(partial: Path) -> None:
    """Create an empty snapshot file. O_EXCL refuses to clobber or follow a symlink."""
    try:
        fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise SnapshotError(f"Refusing to overwrite {partial}") from exc
    os.close(fd)


def remove_partial(partial: Path, source: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(f"{partial}{suffix}") if suffix else partial
        unlink_snapshot(candidate, source)


def snapshot(source: Path, dest: Path) -> tuple[str, Path | None]:
    """Copy source to dest. Returns the mode and a path to the archived copy.

    Raises SnapshotError without deleting anything in the live folder.
    An existing different snapshot is kept under versions/ and then replaced.
    The first identical backup keeps the current copy so a later diff has a start.
    Later identical backups do not add another version.
    """
    source = source.expanduser()
    dest = dest.expanduser()
    if source.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {source}")
    source = source.resolve(strict=True)
    assert_regular_database(source)
    assert_destination_allowed(source, dest)

    partial = dest.parent / f"{dest.name}.partial"
    assert_destination_allowed(source, partial)
    # A crashed run can leave this file. It is not the live database.
    unlink_snapshot(partial, source)
    wal = source.parent / f"{source.name}-wal"
    finished = False
    try:
        create_partial(partial)
        if wal.exists():
            mode = "backup"
            backup_with_sqlite(source, partial)
        else:
            mode = "bytes"
            before = sha256_file(source)
            copy_bytes(source, partial)
            if wal.exists():
                raise SnapshotError(
                    "HomeKit created a -wal file while the bytes were being copied. "
                    "The partial snapshot was discarded. Run this again."
                )
            after = sha256_file(source)
            if before != after:
                raise SnapshotError(
                    "The live database changed while its bytes were being copied. "
                    "The partial snapshot was discarded. Run this again."
                )
        seal_snapshot(partial, source)
        for suffix in ("-wal", "-shm"):
            unlink_snapshot(Path(f"{partial}{suffix}"), source)
        archived = None
        if dest.is_file():
            if sha256_file(dest) == sha256_file(partial):
                unlink_snapshot(partial, source)
                finished = True
                return "same", _keep_baseline(dest, source)
            archived = _archive_snapshot(dest, source)
        os.replace(partial, dest)
        os.chmod(dest, 0o600)
        finished = True
    finally:
        if not finished:
            remove_partial(partial, source)
    return mode, archived


def _keep_baseline(dest: Path, source: Path) -> Path | None:
    """Keep one copy of an unchanged snapshot when nothing older is saved."""
    versions = dest.parent / "versions"
    if versions.is_dir() and any(path.is_file() for path in versions.glob("*.sqlite")):
        return None
    return _archive_snapshot(dest, source)


def _archive_snapshot(dest: Path, source: Path) -> Path:
    """Copy the current snapshot aside before it is replaced."""
    versions = dest.parent / "versions"
    stamp = datetime.fromtimestamp(dest.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
    archive = _unique_version(versions, stamp)
    live_dir = source.resolve().parent
    if archive == source.resolve() or is_within(archive, live_dir):
        raise SnapshotError(f"Refusing to write inside the live HomeKit folder: {archive}")
    versions.mkdir(parents=True, exist_ok=True)
    assert_destination_allowed(source, archive)
    create_partial(archive)
    try:
        copy_bytes(dest, archive)
    except Exception:
        unlink_snapshot(archive, source)
        raise
    return archive


def _unique_version(folder: Path, stamp: str) -> Path:
    candidate = folder / f"{stamp}.sqlite"
    number = 2
    while candidate.exists():
        candidate = folder / f"{stamp}-{number}.sqlite"
        number += 1
    return candidate


def report_live_files(source: Path, before: dict[str, str | None]) -> None:
    """Say whether the live database files changed. The lock index may change."""
    wal = source.parent / f"{source.name}-wal"
    shm = source.parent / f"{source.name}-shm"
    after = {
        "database": sha256_file(source),
        "wal": sha256_file(wal) if wal.exists() else None,
        "shm": sha256_file(shm) if shm.exists() else None,
    }
    database_same = before["database"] == after["database"]
    wal_same = before["wal"] == after["wal"]
    if database_same and wal_same:
        print(f"Live {source.name} and its -wal file are unchanged.", file=sys.stderr)
    else:
        print(
            "HomeKit wrote the live database while the snapshot was taken. "
            "The snapshot is still one consistent moment. Run this again for a later one.",
            file=sys.stderr,
        )
    if before["shm"] != after["shm"]:
        print(
            "The lock file core.sqlite-shm changed. SQLite uses it to register a reader. "
            "It is not the database.",
            file=sys.stderr,
        )


def resolved_destination(dest: Path) -> Path:
    """Resolve dest even when the last directory in the path does not exist yet."""
    dest = dest.expanduser()
    parent = dest.parent
    if parent.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {parent}")
    if parent.exists():
        return parent.resolve() / dest.name
    cursor = parent
    tail: list[str] = []
    while not cursor.exists():
        tail.append(cursor.name)
        if cursor == cursor.parent:
            raise SnapshotError(f"Destination directory does not exist: {parent}")
        cursor = cursor.parent
    if cursor.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {cursor}")
    resolved_parent = cursor.resolve()
    for name in reversed(tail):
        resolved_parent = resolved_parent / name
    return resolved_parent / dest.name


def run_backup(source: Path, dest: Path) -> int:
    """Copy source to dest and print what happened. Returns a process status."""
    source = source.expanduser()
    dest = dest.expanduser()
    print(f"Source: {source}", file=sys.stderr)
    print(f"Snapshot: {dest}", file=sys.stderr)
    try:
        if source.is_symlink():
            raise SnapshotError(f"Refusing to follow a symlink: {source}")
        resolved = source.resolve(strict=True)
        assert_regular_database(resolved)
        landed = resolved_destination(dest)
        if landed == resolved or is_within(landed, resolved.parent):
            raise SnapshotError(f"Refusing to write inside the live HomeKit folder: {landed}")
        if not dest.parent.is_dir():
            dest.parent.mkdir(parents=True, exist_ok=True)
        wal = resolved.parent / f"{resolved.name}-wal"
        shm = resolved.parent / f"{resolved.name}-shm"
        before = {
            "database": sha256_file(resolved),
            "wal": sha256_file(wal) if wal.exists() else None,
            "shm": sha256_file(shm) if shm.exists() else None,
        }
        mode, archived = snapshot(source, dest)
    except FileNotFoundError:
        print(f"Database not found: {source}", file=sys.stderr)
        return 1
    except PermissionError:
        print(
            "Cannot read the HomeKit database. Grant Full Disk Access to the app running this command, then try again.",
            file=sys.stderr,
        )
        return 2
    except sqlite3.OperationalError as exc:
        print(f"Could not read the database: {exc}", file=sys.stderr)
        if "unable to open" in str(exc).lower():
            print(
                "Grant Full Disk Access to the app running this command, then try again.",
                file=sys.stderr,
            )
            return 2
        return 1
    except SnapshotError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if mode == "same" and archived is None:
        print("Snapshot matches the current copy. No new version.", file=sys.stderr)
    elif mode == "same":
        print("Snapshot matches the current copy.", file=sys.stderr)
    elif mode == "backup":
        print("Copied with SQLite's backup API because a -wal file is present.", file=sys.stderr)
    else:
        print("Copied the database bytes because no -wal file is present.", file=sys.stderr)
    if archived is not None:
        print(f"Kept the previous snapshot as versions/{archived.name}", file=sys.stderr)
    report_live_files(resolved, before)
    print(f"Wrote {dest.resolve()}", file=sys.stderr)
    return 0
