#!/usr/bin/env python3
from __future__ import annotations

import sys

sys.path.insert(0, "/tests")

from libstruct_bench.cli.grade_library_v0 import main

if __name__ == "__main__":
    raise SystemExit(main())
