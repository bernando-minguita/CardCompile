"""Smoke test for the packaged executable: start it, wait, verify, stop it."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = ROOT / "dist" / "CardCompile" / "CardCompile.exe"
LOG = ROOT / "dist" / "CardCompile" / "logs" / "application.log"


def main() -> int:
    if not EXE.is_file():
        print("EXE missing:", EXE)
        return 2
    print(f"EXE size: {EXE.stat().st_size / (1024 * 1024):.2f} MB")
    if LOG.exists():
        LOG.unlink()
    process = subprocess.Popen([str(EXE)], cwd=str(EXE.parent))
    time.sleep(12)
    alive = process.poll() is None
    print("RUNNING:", alive)
    if not alive:
        print("EXIT CODE:", process.returncode)
    else:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
    if LOG.is_file():
        lines = LOG.read_text(encoding="utf-8", errors="ignore").splitlines()
        print("LOG-LINES:", len(lines))
        for line in lines[:6]:
            print("  ", line)
    else:
        print("LOG missing:", LOG)
    return 0 if alive else 1


if __name__ == "__main__":
    sys.exit(main())
