#!/usr/bin/env python3
"""Backward-compatible entry point. Prefer ``python -m olh``."""

from olh.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
