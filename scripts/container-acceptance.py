"""Run synthetic tests inside the acceptance image, without provider secrets."""
from pathlib import Path
import os
import subprocess
import sys


root = Path(__file__).resolve().parents[1]
Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(root / "backend/tests"),
                "-t", str(root / "backend"), "-p", "test_*.py", "-v"], check=True)
for suite in sorted((root / "backend/tests").glob("ui_*acceptance.py")):
    subprocess.run([sys.executable, "-u", "-m", "tests." + suite.stem], check=True)
print("Container unit, HTTP, SQLite and Chromium acceptance passed", flush=True)
