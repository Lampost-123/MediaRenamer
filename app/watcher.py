"""Thread-based folder watcher. Polls every N seconds for new media files."""
import threading
from pathlib import Path

MEDIA_EXT = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv", ".flv", ".webm", ".m2ts"}

_threads: dict[str, threading.Thread] = {}
_stops:   dict[str, threading.Event]  = {}
_new_events: list[dict] = []          # consumed by /api/watch/poll
_lock = threading.Lock()


def _scan(folder: str) -> set[str]:
    root = Path(folder)
    if not root.is_dir():
        return set()
    return {str(p) for p in root.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA_EXT}


def start(folder: str, interval: float = 10.0) -> bool:
    if folder in _threads:
        return False   # already watching

    stop_evt = threading.Event()
    _stops[folder] = stop_evt

    def _poll():
        known = _scan(folder)
        while not stop_evt.wait(interval):
            current = _scan(folder)
            new = [f for f in current if f not in known]
            if new:
                with _lock:
                    _new_events.append({"folder": folder, "files": new})
            known = current

    t = threading.Thread(target=_poll, daemon=True, name=f"watcher:{folder}")
    _threads[folder] = t
    t.start()
    return True


def stop(folder: str) -> bool:
    if folder not in _stops:
        return False
    _stops[folder].set()
    _stops.pop(folder, None)
    _threads.pop(folder, None)
    return True


def stop_all() -> None:
    for folder in list(_stops):
        stop(folder)


def watched_folders() -> list[str]:
    return list(_threads.keys())


def drain_events() -> list[dict]:
    with _lock:
        events = list(_new_events)
        _new_events.clear()
    return events
