"""Tiny logging helpers used by all phases.

Output goes to stderr so stdout stays clean for machine-parsable output
(e.g., the bundle path printed at the end of a successful capture).
"""
from __future__ import annotations

import os
import sys
import time

QUIET = os.environ.get("GB_QUIET", "0") == "1"
VERBOSE = os.environ.get("GB_VERBOSE", "0") == "1"


def _ts() -> str:
    return time.strftime("%H:%M:%S", time.gmtime())


def info(msg: str) -> None:
    if QUIET:
        return
    print(f"[{_ts()}] {msg}", file=sys.stderr)


def warn(msg: str) -> None:
    print(f"[{_ts()}] WARN: {msg}", file=sys.stderr)


def error(msg: str) -> None:
    print(f"[{_ts()}] ERROR: {msg}", file=sys.stderr)


def debug(msg: str) -> None:
    if not VERBOSE:
        return
    print(f"[{_ts()}] DEBUG: {msg}", file=sys.stderr)
