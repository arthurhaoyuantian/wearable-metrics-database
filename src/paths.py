"""Project paths (single place for storage / database location)."""
from pathlib import Path


def project_root() -> Path:
    """Repository root (parent of ``src``)."""
    return Path(__file__).resolve().parent.parent


def storage_dir() -> Path:
    d = project_root() / "storage"
    d.mkdir(parents=True, exist_ok=True)
    return d


def database_path() -> Path:
    """Default SQLite database file for the desktop app."""
    return storage_dir() / "ehr.sqlite"
