"""Simple disk-backed TTL cache for TMDB API results."""
import json
import time
from pathlib import Path
from app import config

CACHE_FILE = config.CONFIG_DIR / "tmdb_cache.json"
CACHE_TTL  = 86_400  # 24 hours

_mem: dict = {}


def _load() -> None:
    global _mem
    if CACHE_FILE.exists():
        try:
            _mem = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _mem = {}


def _save() -> None:
    CACHE_FILE.write_text(json.dumps(_mem, indent=2), encoding="utf-8")


def get(key: str) -> list | None:
    if not _mem:
        _load()
    entry = _mem.get(key)
    if entry and time.time() - entry["ts"] < CACHE_TTL:
        return entry["data"]
    return None


def put(key: str, data: list) -> None:
    if not _mem:
        _load()
    _mem[key] = {"ts": time.time(), "data": data}
    _save()


def clear() -> None:
    global _mem
    _mem = {}
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()


def stats() -> dict:
    if not _mem:
        _load()
    now = time.time()
    valid = sum(1 for v in _mem.values() if now - v["ts"] < CACHE_TTL)
    return {"total": len(_mem), "valid": valid, "ttl_hours": CACHE_TTL // 3600}
