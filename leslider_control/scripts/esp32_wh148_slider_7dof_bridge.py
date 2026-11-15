#!/usr/bin/env python
"""Preferred command name for the safe seven-pot SO-101 slider controller.

The implementation remains import-compatible with the earlier local filename,
with calibrated multi-turn travel and session-reference checks for motor ID 7.
"""

from esp32_wh148_leslider_7dof_bridge import *  # noqa: F403

if __name__ == "__main__":
    raise SystemExit(main())  # noqa: F405
