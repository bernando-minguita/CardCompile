"""Run the test suite and print a compact summary (XML based)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    report = Path(tempfile.gettempdir()) / "dpb_pytest_report.xml"
    if report.exists():
        report.unlink()
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--tb=short",
        "-q",
        f"--junitxml={report}",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    output = completed.stdout + completed.stderr
    failures = [line for line in output.splitlines() if line.startswith(("FAILED", "ERROR"))]
    if report.exists():
        root = ET.parse(report).getroot()
        suite = root if root.tag == "testsuite" else root.find("testsuite")
        if suite is not None:
            print(
                f"TESTS={suite.get('tests')} FAILURES={suite.get('failures')} "
                f"ERRORS={suite.get('errors')} SKIPPED={suite.get('skipped')} "
                f"TIME={suite.get('time')}s"
            )
    for line in failures:
        print(line)
    print("PYTEST-EXIT", completed.returncode)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
