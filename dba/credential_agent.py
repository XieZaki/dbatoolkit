"""Internal entry point for the per-user Credential Session Agent."""

from __future__ import annotations

import argparse
import sys

from dba.credential_session import serve_agent


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    args = parser.parse_args()
    if not args.serve:
        return 2
    try:
        serve_agent()
    except Exception:
        # Agent diagnostics intentionally omit exception text and request data.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
