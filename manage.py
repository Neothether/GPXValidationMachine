"""Portable development commands. Run with Python 3.12+ from any directory."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import venv

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def npm():
    executable = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not executable:
        raise RuntimeError("Install Node.js 20+ (including npm), then reopen your terminal.")
    return executable


def run(args, cwd=ROOT):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def bootstrap():
    if not PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    run([PYTHON, "-m", "pip", "install", "-r", ROOT / "backend/requirements.txt"])
    run([npm(), "ci"], ROOT / "frontend")
    env = ROOT / ".env"
    if not env.exists():
        with env.open("x", encoding="utf-8") as f:
            f.write((ROOT / ".env.example").read_text(encoding="utf-8"))
    print("Dependencies installed. Fill in .env privately, then run python manage.py check.")


def check():
    from app import config
    import psycopg
    url = config.required("DATABASE_URL")
    config.required("GEMINI_API_KEYS")
    # Never display connection strings, passwords, or database exception messages.
    try:
        with psycopg.connect(url, connect_timeout=10) as conn:
            rows = conn.execute("select id, password_hash = crypt(%s, password_hash) from users where is_human order by id",
                                (os.environ.get("DEMO_PASSWORD", ""),)).fetchall()
    except psycopg.errors.UndefinedTable:
        raise RuntimeError("Database is reachable but has no schema. For a new database run python manage.py init-db.") from None
    except psycopg.Error:
        raise RuntimeError("Database check failed. Check DATABASE_URL, network access, and required database extensions.") from None
    if not rows:
        raise RuntimeError("No human accounts found. Ask the database owner to initialize demo identities.")
    print(f"Database connected; {len(rows)} human accounts available.")
    if all(match for _, match in rows):
        print("The local DEMO_PASSWORD matches all existing human accounts.")
    else:
        print("The local DEMO_PASSWORD does not match every account. Use the password supplied by the database owner; do not reset shared accounts.")
    print("Gemini key is configured (not tested against the provider).")


def dev():
    check()
    for port in (8000, 5173):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError(f"Port {port} is already in use. Stop the existing server before starting another copy.") from None
    children = []
    try:
        # On Windows uvicorn's reloader restarts by sending Ctrl+C to the whole console, which also stops
        # the frontend and this script. There, restart `dev` by hand after editing backend code.
        reload = [] if os.name == "nt" else ["--reload"]
        children.append(subprocess.Popen([str(PYTHON), "-m", "uvicorn", "app.main:app", *reload,
                                         "--host", "127.0.0.1", "--port", "8000"], cwd=ROOT / "backend", start_new_session=os.name != "nt"))
        children.append(subprocess.Popen([npm(), "run", "dev", "--", "--host", "127.0.0.1", "--strictPort"], cwd=ROOT / "frontend", start_new_session=os.name != "nt"))
        print("Open http://127.0.0.1:5173 . Press Ctrl+C to stop both servers.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("A development server exited. Review its output above.")
    finally:
        for child in children:
            if os.name == "nt":
                if child.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    child.kill()
                else:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("bootstrap", "check", "init-db", "dev"))
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required.")
    if args.command == "bootstrap":
        bootstrap()
        return
    if not PYTHON.exists():
        raise RuntimeError("Run python manage.py bootstrap first.")
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        result = subprocess.run([str(PYTHON), str(ROOT / "manage.py"), args.command], cwd=ROOT)
        raise SystemExit(result.returncode)
    sys.path.insert(0, str(ROOT / "backend"))
    if args.command == "check":
        check()
    elif args.command == "init-db":
        from app.setup import main as initialize
        initialize()
    else:
        dev()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        # Only our own errors are safe to display; driver/tool errors can contain secrets.
        print(str(exc) if isinstance(exc, RuntimeError) else
              f"Command failed ({type(exc).__name__}). Check configuration and dependency installation.", file=sys.stderr)
        sys.exit(1)
