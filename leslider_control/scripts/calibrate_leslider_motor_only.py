#!/usr/bin/env python
"""Deprecated command name; forwards to the safe ID-7-only calibrator."""

from calibrate_slider_motor_only import main

if __name__ == "__main__":
    raise SystemExit(main())
