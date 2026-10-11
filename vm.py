"""Command-line harness: python vm.py <command> ...   (python vm.py --help lists the commands)."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

if __name__ == "__main__":
    # Relative paths on the command line are resolved against the directory the user ran this from.
    env = {**os.environ, "VM_CWD": os.getcwd()}
    python = PYTHON if PYTHON.exists() else Path(sys.executable)
    sys.exit(subprocess.run([str(python), "-m", "app.cli", *sys.argv[1:]], cwd=ROOT / "backend", env=env).returncode)
