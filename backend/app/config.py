"""Configuration is anchored to the checkout, never the terminal directory."""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")  # Explicit environment variables take precedence.

class ConfigurationError(RuntimeError):
    pass

def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value or any(marker in value for marker in ("CHANGE_ME", "HOST.tsdb", "change-me-demo")):
        raise ConfigurationError(f"Set {name} in the repository .env file, then restart the server.")
    return value

def evidence_dir() -> Path:
    path = Path(os.environ.get("EVIDENCE_DIR", "evidence")).expanduser()
    # Preserve the original backend/evidence default and existing evidence locations.
    return (path if path.is_absolute() else ROOT / "backend" / path).resolve()
