"""Command-line entry point. Comparison commands arrive in Phase 2."""

from __future__ import annotations

import argparse
import sys

from geodiff import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geodiff", description=__doc__)
    parser.add_argument("--version", action="version", version=f"geodiff {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
