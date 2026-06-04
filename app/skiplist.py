"""Persistent skip list — files in this set are ignored during scans."""
import json
from app import config

SKIP_FILE = config.CONFIG_DIR / "skip_list.json"
_skip: set[str] = set()


def _load() -> None:
    global _skip
    if SKIP_FILE.exists():
        try:
            _skip = set(json.loads(SKIP_FILE.read_text(encoding="utf-8")))
        except Exception:
            _skip = set()


def _save() -> None:
    SKIP_FILE.write_text(json.dumps(sorted(_skip), indent=2), encoding="utf-8")


def get_all() -> list[str]:
    if not _skip:
        _load()
    return sorted(_skip)


def is_skipped(path: str) -> bool:
    if not _skip:
        _load()
    return path in _skip


def add(path: str) -> None:
    _load()
    _skip.add(path)
    _save()


def remove(path: str) -> None:
    _load()
    _skip.discard(path)
    _save()


def clear() -> None:
    global _skip
    _skip = set()
    if SKIP_FILE.exists():
        SKIP_FILE.unlink()
