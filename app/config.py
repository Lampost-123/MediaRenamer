import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".mediarenamer"
CONFIG_FILE = CONFIG_DIR / "config.json"
UNDO_LOG_FILE = CONFIG_DIR / "undo_log.json"

DEFAULTS = {
    "tmdb_api_key": "",
    "tv_template": "{title} - S{season}E{episode} - {episode_title}{ext}",
    "movie_template": "{title} ({year}){ext}",
    "last_folder": "",
    "generate_nfo": False,
    "watched_folders": [],
}

_config: dict = {}


def load() -> dict:
    global _config
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            _config = {**DEFAULTS, **stored}
            # Never allow blank templates — fall back to defaults
            for key in ("tv_template", "movie_template"):
                if not _config.get(key, "").strip():
                    _config[key] = DEFAULTS[key]
        except Exception:
            _config = dict(DEFAULTS)
    else:
        _config = dict(DEFAULTS)
    return _config


def get() -> dict:
    if not _config:
        load()
    return _config


def save(updates: dict) -> dict:
    current = get()
    for k, v in updates.items():
        # Don't overwrite templates with blank strings
        if k in ("tv_template", "movie_template") and not str(v).strip():
            continue
        current[k] = v
    CONFIG_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def read_undo_log() -> list:
    if UNDO_LOG_FILE.exists():
        try:
            return json.loads(UNDO_LOG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def write_undo_log(log: list) -> None:
    UNDO_LOG_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")
