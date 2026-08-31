"""Minimal external-script smoke for the embedded standalone interpreter."""

from __future__ import annotations

import argparse
import json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--message", required=True)
    args = parser.parse_args()
    print(json.dumps({"message": args.message}, separators=(",", ":")))


if __name__ == "__main__":
    main()
