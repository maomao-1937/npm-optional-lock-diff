"""Check byte-for-byte output and exit codes for the README examples."""

import json
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    cases = json.loads((root / "checks.json").read_text(encoding="utf-8"))
    failed = False
    for case in cases:
        result = subprocess.run(
            [sys.executable, "-I", *case["args"]], cwd=root, capture_output=True, timeout=10,
        )
        expected = json.dumps(case["report"], separators=(",", ":")).encode("ascii") + b"\n"
        name = case["args"][-1]
        if result.returncode != case["exit"] or result.stdout != expected or result.stderr:
            print(f"FAIL {name}: exit={result.returncode}, stdout={result.stdout!r}, stderr={result.stderr!r}",
                  file=sys.stderr)
            failed = True
        else:
            print(f"OK {name} (exit {result.returncode}) {result.stdout.decode('ascii').rstrip()}")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
