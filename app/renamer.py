import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from app import config


def execute_one(op: dict) -> dict:
    """Move/rename a single file or folder. Does NOT write to the undo log.

    Uses shutil.move() so it works correctly even when the destination is a
    new directory that needs to be created, or when crossing drive boundaries.
    """
    src = Path(op["src"])
    dst = Path(op["dst"])
    try:
        if not src.exists():
            return {**op, "status": "error", "error": "Source not found"}
        if dst.exists() and dst.resolve() != src.resolve():
            return {**op, "status": "error", "error": "Destination already exists"}
        # Create destination directory tree (e.g. Season 01/) if it doesn't exist
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return {**op, "status": "done", "error": ""}
    except Exception as e:
        return {**op, "status": "error", "error": str(e)}


def write_batch_log(completed: list[dict]) -> None:
    """Append one batch entry to the undo log covering all completed operations."""
    log = config.read_undo_log()
    log.append({
        "id": datetime.now(timezone.utc).isoformat(),
        "status": "done",
        "operations": completed,
    })
    config.write_undo_log(log)


def execute_renames(operations: list[dict]) -> list[dict]:
    results = []
    completed = []

    for op in operations:
        src = Path(op["src"])
        dst = Path(op["dst"])
        try:
            if not src.exists():
                results.append({**op, "status": "error", "error": "Source file not found"})
                continue
            if dst.exists() and dst != src:
                results.append({**op, "status": "error", "error": "Destination already exists"})
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)
            results.append({**op, "status": "done", "error": ""})
            completed.append({
                "src": str(src),
                "dst": str(dst),
                "op_type": op.get("op_type", "file"),
            })
        except Exception as e:
            results.append({**op, "status": "error", "error": str(e)})

    if completed:
        log = config.read_undo_log()
        log.append({
            "id": datetime.now(timezone.utc).isoformat(),
            "status": "done",
            "operations": completed,
        })
        config.write_undo_log(log)

    return results


def _reverse_batch(batch: dict) -> dict:
    reversed_ops = []
    errors = []
    # Reverse in reverse order (folders last, since files were renamed into them first)
    for op in reversed(batch["operations"]):
        src = Path(op["dst"])
        dst = Path(op["src"])
        try:
            if not src.exists():
                errors.append(f"Not found: {src}")
                continue
            os.rename(src, dst)
            reversed_ops.append({"src": str(src), "dst": str(dst)})
        except Exception as e:
            errors.append(str(e))
    return {"reversed": len(reversed_ops), "errors": errors, "operations": reversed_ops}


def undo_batch(batch_id: str) -> dict:
    """Roll back a specific batch by its id."""
    log = config.read_undo_log()
    for i, batch in enumerate(log):
        if batch["id"] == batch_id:
            if batch["status"] != "done":
                return {"reversed": 0, "errors": ["Batch already rolled back"], "operations": []}
            result = _reverse_batch(batch)
            log[i]["status"] = "undone"
            config.write_undo_log(log)
            return result
    return {"reversed": 0, "errors": ["Batch not found"], "operations": []}


def undo_last() -> dict:
    """Roll back the most recent done batch."""
    log = config.read_undo_log()
    for i in range(len(log) - 1, -1, -1):
        if log[i]["status"] == "done":
            result = _reverse_batch(log[i])
            log[i]["status"] = "undone"
            config.write_undo_log(log)
            return result
    return {"reversed": 0, "errors": ["No undoable batch found"], "operations": []}


def can_undo() -> bool:
    log = config.read_undo_log()
    return any(b["status"] == "done" for b in log)


def cleanup_empty_folders(root: str) -> list[str]:
    """Delete empty directories inside root (bottom-up). Returns paths of deleted folders."""
    root_path = Path(root)
    deleted = []
    # Sort deepest first so parent directories are deleted after their children
    dirs = sorted(
        (p for p in root_path.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for d in dirs:
        try:
            if d != root_path and not any(d.iterdir()):
                d.rmdir()
                deleted.append(str(d))
        except Exception:
            pass
    return deleted


def get_history() -> list[dict]:
    """Return all batches newest-first, enriched with human-readable info."""
    log = config.read_undo_log()
    out = []
    for batch in reversed(log):
        ops = batch.get("operations", [])
        # Derive a summary folder from the first operation
        first_dst = ops[0]["dst"] if ops else ""
        folder = str(Path(first_dst).parent) if first_dst else ""
        out.append({
            "id": batch["id"],
            "status": batch["status"],
            "timestamp": batch["id"],  # ISO string
            "count": len(ops),
            "folder": folder,
            "operations": ops,
        })
    return out
