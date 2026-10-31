# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Write a JSON catalog of accessories and where they are used.

The catalog is for search. It does not include pairing material, and it
does not record the path of the database it was read from.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from homekit_map.backup import resolved_destination
from homekit_map.errors import SnapshotError
from homekit_map.paths import LIVE_HOMEKIT_DIR, is_within
from homekit_map.query import Accessory, Use, all_accessories
from homekit_map.schema import open_readonly


def extract_catalog(path: Path) -> dict[str, object]:
    """Read a snapshot and return the catalog."""
    connection = open_readonly(path)
    try:
        accessories = all_accessories(connection)
    finally:
        connection.close()
    return {
        "accessoryCount": len(accessories),
        "accessories": [_accessory(item) for item in accessories],
    }


def write_catalog(source: Path, output: Path) -> dict[str, object]:
    """Write the catalog as JSON. Returns the same document."""
    output = output.expanduser()
    if output.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {output}")
    landed = resolved_destination(output)
    live = LIVE_HOMEKIT_DIR.expanduser().resolve()
    if is_within(landed, live):
        raise SnapshotError("Refusing to write the export into the live HomeKit folder.")
    if output.exists() and not output.is_file():
        raise SnapshotError(f"Export destination is not a file: {output}")
    document = extract_catalog(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_private(output, (json.dumps(document, indent=2) + "\n").encode("utf-8"))
    return document


def _write_private(output: Path, payload: bytes) -> None:
    """Write a 0600 file next to output, then swap it in. No reader sees a partial or world-readable export."""
    partial = output.with_name(f"{output.name}.partial")
    if partial.is_symlink():
        raise SnapshotError(f"Refusing to follow a symlink: {partial}")
    partial.unlink(missing_ok=True)
    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(partial, output)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _accessory(item: Accessory) -> dict[str, object]:
    return {
        "configuredName": item.configured_name,
        "providedName": item.provided_name,
        "room": item.room,
        "manufacturer": item.manufacturer,
        "model": item.model,
        "services": list(item.services),
        "automations": [_use(use) for use in item.automations],
        "scenes": [_use(use) for use in item.scenes],
        "triggers": [_use(use) for use in item.triggers],
        "notifications": list(item.notifications),
    }


def _use(use: Use) -> dict[str, object]:
    body: dict[str, object] = {"name": use.name, "role": use.role}
    if use.enabled is not None:
        body["enabled"] = use.enabled
    if use.service:
        body["service"] = use.service
    if use.characteristic:
        body["characteristic"] = use.characteristic
    if use.target:
        body["target"] = use.target
    return body
