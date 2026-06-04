import asyncio
import json
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, scanner, tmdb, templates, renamer, cache as tmdb_cache, skiplist, nfo, watcher

app = FastAPI(title="MediaRenamer")

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.on_event("startup")
def startup():
    config.load()


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = FRONTEND_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def get_settings():
    return config.get()


class SettingsUpdate(BaseModel):
    tmdb_api_key: str | None = None
    tv_template: str | None = None
    movie_template: str | None = None
    last_folder: str | None = None
    generate_nfo: bool | None = None


@app.post("/api/settings")
def post_settings(body: SettingsUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    return config.save(updates)


# ── Folder browser ────────────────────────────────────────────────────────────

@app.get("/api/browse")
async def browse_folder():
    """Open a native Windows folder picker and return the chosen path."""
    import subprocess
    from fastapi.concurrency import run_in_threadpool

    def _pick():
        # Run tkinter in its own Python process so it gets a proper main thread
        # and can correctly claim foreground focus from Windows.
        picker_script = (
            "import tkinter as tk, tkinter.filedialog as fd\n"
            "r = tk.Tk()\n"
            "r.withdraw()\n"
            "r.attributes('-topmost', True)\n"
            "r.lift()\n"
            "r.focus_force()\n"
            "r.update()\n"
            "p = fd.askdirectory(title='Select a media folder to scan', mustexist=True, parent=r)\n"
            "r.destroy()\n"
            "print(p or '', end='')\n"
        )
        import sys
        r = subprocess.run(
            [sys.executable, "-c", picker_script],
            capture_output=True, text=True, timeout=120,
            creationflags=0x08000000,  # CREATE_NO_WINDOW — no console flash
        )
        return r.stdout.strip()

    path = await run_in_threadpool(_pick)
    return {"path": path}


# ── Scan ──────────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    path: str
    recursive: bool = True


@app.post("/api/scan")
def post_scan(body: ScanRequest):
    from pathlib import Path as _Path
    p = _Path(body.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.path}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a folder: {body.path}")
    files, folders = scanner.scan_folder(body.path, body.recursive)
    config.save({"last_folder": body.path})
    return {"files": files, "count": len(files), "folders": folders}


# ── TMDB Lookup ───────────────────────────────────────────────────────────────

class LookupRequest(BaseModel):
    file_id: str
    query: str
    media_type: str  # "movie" | "tv" | "unknown"
    year: int | None = None
    season: int | None = None
    episode: int | None = None


@app.post("/api/search")
async def post_search(body: LookupRequest):
    cfg = config.get()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")

    async with __import__("httpx").AsyncClient() as client:
        if body.media_type == "movie":
            candidates = await tmdb.search_movie(client, body.query, body.year, api_key)
        elif body.media_type == "tv":
            candidates = await tmdb.search_tv(client, body.query, api_key)
            if candidates and body.season and body.episode:
                ep = await tmdb.get_episode(client, candidates[0]["tmdb_id"], body.season, body.episode, api_key)
                candidates[0]["episode_title"] = ep["episode_title"]
        else:
            movie_c = await tmdb.search_movie(client, body.query, body.year, api_key)
            tv_c = await tmdb.search_tv(client, body.query, api_key)
            candidates = movie_c + tv_c

    return {"candidates": candidates}


class BulkLookupRequest(BaseModel):
    files: list[dict]


@app.post("/api/lookup/bulk")
async def post_bulk_lookup(body: BulkLookupRequest):
    cfg = config.get()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")
    results = await tmdb.lookup_bulk(body.files, api_key)
    return {"results": results}


# ── Preview name proposal ──────────────────────────────────────────────────────

class PreviewRequest(BaseModel):
    file_entry: dict
    match: dict | None = None


@app.post("/api/preview")
def post_preview(body: PreviewRequest):
    cfg = config.get()
    tv_tmpl    = cfg.get("tv_template",    config.DEFAULTS["tv_template"])
    movie_tmpl = cfg.get("movie_template", config.DEFAULTS["movie_template"])
    name   = templates.propose_name(body.file_entry, body.match, tv_tmpl, movie_tmpl)
    folder = templates.propose_folder(body.file_entry, body.match)
    return {"proposed_name": name, "proposed_folder": folder}


@app.get("/api/templates/help")
def get_template_help():
    return {"tokens": templates.TOKEN_HELP}


class CleanupRequest(BaseModel):
    root: str


@app.post("/api/cleanup")
def post_cleanup(body: CleanupRequest):
    from pathlib import Path as _Path
    if not _Path(body.root).is_dir():
        raise HTTPException(status_code=404, detail=f"Folder not found: {body.root}")
    deleted = renamer.cleanup_empty_folders(body.root)
    return {"deleted": deleted, "count": len(deleted)}


class MovieFolderProposalRequest(BaseModel):
    files: list[dict]
    matches: dict  # fileId -> match dict


@app.post("/api/movie-folder-proposals")
def post_movie_folder_proposals(body: MovieFolderProposalRequest):
    """Return folder rename proposals for movies that are alone in their folder."""
    proposals = []
    seen: set[str] = set()
    for f in body.files:
        ftype = (body.matches.get(f["id"]) or {}).get("type") or f.get("type", "unknown")
        if ftype != "movie":
            continue
        if f.get("folder_file_count", 2) != 1:
            continue
        folder_path = f["folder"]
        if folder_path in seen:
            continue
        seen.add(folder_path)
        match = body.matches.get(f["id"])
        proposed = templates.propose_movie_folder_name(f, match)
        from pathlib import Path as _P
        current_name = _P(folder_path).name
        if proposed and proposed != current_name:
            proposals.append({
                "id": folder_path,
                "path": folder_path,
                "folder": str(_P(folder_path).parent),
                "filename": current_name,
                "proposed_name": proposed,
                "op_type": "folder",
            })
    return {"proposals": proposals}


# ── Rename (SSE streaming) ─────────────────────────────────────────────────────

class RenameOperation(BaseModel):
    src: str
    dst: str


class RenameRequest(BaseModel):
    operations: list[RenameOperation]


async def rename_stream(operations: list[dict]) -> AsyncGenerator[str, None]:
    total = len(operations)
    yield f"data: {json.dumps({'type': 'start', 'total': total})}\n\n"
    await asyncio.sleep(0)

    results = []
    completed = []
    for i, op in enumerate(operations):
        # Execute without logging (log_only=False deferred)
        result = renamer.execute_one(op)
        results.append(result)
        if result["status"] == "done":
            completed.append({
                "src": result["src"],
                "dst": result["dst"],
                "op_type": op.get("op_type", "file"),
            })
        yield f"data: {json.dumps({'type': 'progress', 'index': i, 'total': total, 'result': result})}\n\n"
        await asyncio.sleep(0)

    # Write ONE batch log entry for the entire session
    if completed:
        renamer.write_batch_log(completed)

    can_undo_now = renamer.can_undo()
    yield f"data: {json.dumps({'type': 'done', 'results': results, 'can_undo': can_undo_now})}\n\n"


@app.post("/api/rename")
async def post_rename(body: RenameRequest):
    ops = [op.model_dump() for op in body.operations]
    return StreamingResponse(
        rename_stream(ops),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── History & Undo ────────────────────────────────────────────────────────────

@app.get("/api/history")
def get_history():
    return {"batches": renamer.get_history()}


class UndoRequest(BaseModel):
    batch_id: str | None = None


@app.post("/api/undo")
def post_undo(body: UndoRequest = None):
    if body and body.batch_id:
        result = renamer.undo_batch(body.batch_id)
    else:
        result = renamer.undo_last()
    return {**result, "can_undo": renamer.can_undo()}


@app.get("/api/undo/status")
def get_undo_status():
    return {"can_undo": renamer.can_undo()}


# ── TMDB Cache ─────────────────────────────────────────────────────────────────

@app.get("/api/cache/stats")
def get_cache_stats():
    return tmdb_cache.stats()


@app.post("/api/cache/clear")
def post_cache_clear():
    tmdb_cache.clear()
    return {"cleared": True}


# ── Skip list ──────────────────────────────────────────────────────────────────

@app.get("/api/skiplist")
def get_skiplist():
    return {"paths": skiplist.get_all()}


class SkipRequest(BaseModel):
    path: str


@app.post("/api/skiplist/add")
def post_skiplist_add(body: SkipRequest):
    skiplist.add(body.path)
    return {"paths": skiplist.get_all()}


@app.post("/api/skiplist/remove")
def post_skiplist_remove(body: SkipRequest):
    skiplist.remove(body.path)
    return {"paths": skiplist.get_all()}


@app.post("/api/skiplist/clear")
def post_skiplist_clear():
    skiplist.clear()
    return {"paths": []}


# ── NFO generation ─────────────────────────────────────────────────────────────

class NfoRequest(BaseModel):
    file_entry: dict
    match: dict
    proposed_path: str  # full destination path of the video file


@app.post("/api/nfo/write")
def post_nfo_write(body: NfoRequest):
    from pathlib import Path as _P
    video_path = _P(body.proposed_path)
    nfo_path   = video_path.with_suffix(".nfo")
    ftype = body.match.get("type", "movie")
    if ftype == "tv":
        content = nfo.tv_nfo(body.match, body.file_entry.get("season", 0), body.file_entry.get("episode", 0))
    else:
        content = nfo.movie_nfo(body.match)
    nfo.write_nfo(nfo_path, content)
    return {"nfo_path": str(nfo_path)}


# ── Missing episode detection ──────────────────────────────────────────────────

class MissingEpisodesRequest(BaseModel):
    tmdb_id: int
    season: int
    owned_episodes: list[int]


@app.post("/api/missing-episodes")
async def post_missing_episodes(body: MissingEpisodesRequest):
    cfg = config.get()
    api_key = cfg.get("tmdb_api_key", "")
    if not api_key:
        raise HTTPException(status_code=400, detail="TMDB API key not configured")
    async with __import__("httpx").AsyncClient() as client:
        all_eps = await tmdb.get_season_episodes(client, body.tmdb_id, body.season, api_key)
    owned = set(body.owned_episodes)
    missing = [e for e in all_eps if e["episode_number"] not in owned]
    return {"all_episodes": len(all_eps), "owned": len(owned), "missing": missing}


# ── Watched folder ─────────────────────────────────────────────────────────────

class WatchRequest(BaseModel):
    folder: str


@app.post("/api/watch/start")
def post_watch_start(body: WatchRequest):
    started = watcher.start(body.folder)
    return {"watching": watcher.watched_folders(), "started": started}


@app.post("/api/watch/stop")
def post_watch_stop(body: WatchRequest):
    stopped = watcher.stop(body.folder)
    return {"watching": watcher.watched_folders(), "stopped": stopped}


@app.get("/api/watch/poll")
def get_watch_poll():
    return {"events": watcher.drain_events(), "watching": watcher.watched_folders()}


@app.get("/api/watch/status")
def get_watch_status():
    return {"watching": watcher.watched_folders()}
