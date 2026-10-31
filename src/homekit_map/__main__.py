# Copyright (c) 2026 tbaur
#
# Licensed under the Apache License, Version 2.0
# See LICENSE file for full license text

"""Allow `python -m homekit_map`."""

from __future__ import annotations

import sys

from homekit_map.cli import main

if __name__ == "__main__":
    sys.exit(main())
