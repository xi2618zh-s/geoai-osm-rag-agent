#!/usr/bin/env python3
"""Backward-compatible entry point for the enhanced installation verifier.

Use ``python -m unittest discover -s tests -v`` for offline unit tests and
``python verify_installation.py --help`` for optional network/E2E checks.
"""

from verify_installation import main


if __name__ == "__main__":
    raise SystemExit(main())
