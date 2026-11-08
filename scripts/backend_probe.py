#!/usr/bin/env python3
"""Record and validate that a packaging environment contains its named backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from localsr.core.backend_validation import probe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = probe(args.backend)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
