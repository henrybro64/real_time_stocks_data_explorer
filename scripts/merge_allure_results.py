#!/usr/bin/env python3

import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: merge_allure_results.py <output-dir> <input-dir> [<input-dir> ...]",
            file=sys.stderr,
        )
        return 1

    output_dir = Path(sys.argv[1]).resolve()
    input_dirs = [Path(arg).resolve() for arg in sys.argv[2:]]

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    copied = 0

    for input_dir in input_dirs:
        if not input_dir.exists():
            continue
        for source in input_dir.rglob("*"):
            if not source.is_file() or source.name.startswith("."):
                continue

            relative_path = source.relative_to(input_dir)
            target = output_dir / relative_path

            if target.exists():
                target = output_dir / f"{source.stem}-{copied}{source.suffix}"

            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied += 1

    print(f"Merged {copied} files into {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
