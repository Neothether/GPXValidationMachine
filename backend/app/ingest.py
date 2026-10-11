"""Read a target repository into a line-numbered text bundle the agents can cite as file:line."""
from pathlib import Path

from .db import sha256

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".pytest_cache", "evidence",
             ".next", "target", "renv"}
TEXT_EXT = {".py", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml", ".env.example", ".js", ".ts",
            ".tsx", ".jsx", ".r", ".R", ".Rmd", ".sql", ".cs", ".java", ".go", ".html", ".css", ".feature", ".csv"}
MAX_FILE_BYTES = 60_000
MAX_TOTAL_CHARS = 500_000


def repo_files(repo_path: str) -> list[Path]:
    root = Path(repo_path)
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if p.suffix in TEXT_EXT or p.name in {"Dockerfile", "Makefile", "DESCRIPTION", "requirements.txt"}:
            if p.stat().st_size <= MAX_FILE_BYTES:
                files.append(p)
    return files


def bundle(repo_path: str) -> str:
    root = Path(repo_path)
    parts, total = [], 0
    for p in repo_files(repo_path):
        text = p.read_text(encoding="utf-8", errors="replace")
        numbered = "\n".join(f"{i:>4}| {line}" for i, line in enumerate(text.splitlines(), 1))
        block = f"\n===== FILE: {p.relative_to(root).as_posix()} =====\n{numbered}\n"
        if total + len(block) > MAX_TOTAL_CHARS:
            parts.append("\n[bundle truncated: repository exceeds the context budget]\n")
            break
        parts.append(block)
        total += len(block)
    return "".join(parts)


def fingerprint(repo_path: str) -> str:
    """Content hash of the target, recorded on every execution as the software version under test.
    Covers every file, including ones too large or of a type the agents do not read."""
    root = Path(repo_path)
    files = [p for p in sorted(root.rglob("*"))
             if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts)]
    return sha256("\n".join(f"{p.relative_to(root).as_posix()}:{sha256(p.read_bytes())}" for p in files))


def refresh_fingerprint(system_id, actor: str = "system") -> str | None:
    """Record the fingerprint of the target as it is on disk now. A change of version lands in the audit trail."""
    from . import db
    with db.read() as conn:
        s = conn.execute("select repo_path, current_fingerprint from systems where id = %s", (system_id,)).fetchone()
    if not s:
        return None
    if not Path(s["repo_path"]).is_dir():       # registered on another machine: keep the last known version
        return s["current_fingerprint"]
    current = fingerprint(s["repo_path"])
    if current != s["current_fingerprint"]:
        with db.tx(actor, db.APP) as conn:
            conn.execute("update systems set current_fingerprint = %s where id = %s", (current, system_id))
    return current
