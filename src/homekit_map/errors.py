# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Errors that stop a command without changing the live HomeKit store."""


class SnapshotError(Exception):
    """The operation did not finish. The live HomeKit database was not written."""
