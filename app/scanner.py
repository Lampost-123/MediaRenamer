import re
from pathlib import Path
from collections import Counter
from app import skiplist

MEDIA_EXTENSIONS    = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".wmv", ".flv", ".webm", ".m2ts"}
SUBTITLE_EXTENSIONS = {".srt", ".sub", ".ass", ".ssa", ".vtt", ".idx"}

QUALITY_RE = re.compile(
    r"\b(2160p|4k|1080p|720p|480p|HDR10?\+?|DV|BluRay|Blu-Ray|BDRip|BRRip|WEB-DL|WEBRip|HDTV|DVDRip|REMUX|IMAX)\b",
    re.IGNORECASE,
)
TV_SE_RE   = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,2})")
TV_SE_ALT  = re.compile(r"\b(\d{1,2})x(\d{2})\b")
YEAR_RE    = re.compile(r"[\(\.\s](\d{4})[\)\.\s]")
JUNK_RE    = re.compile(r"[\._\-]")
SEASON_FOLDER_RE = re.compile(r"^(?:season|series|s)\s*(\d+)$", re.IGNORECASE)


def _clean_title(raw: str) -> str:
    title = JUNK_RE.sub(" ", raw).strip()
    return re.sub(r"\s+", " ", title).strip()


def _is_season_folder(name: str) -> bool:
    return bool(SEASON_FOLDER_RE.match(name.strip()))


def _show_root(file_path: Path) -> str:
    """For a TV file, return the show's root folder (parent of season folder, or the folder itself)."""
    parent = file_path.parent
    if _is_season_folder(parent.name):
        return str(parent.parent)
    return str(parent)


def parse_filename(path: Path) -> dict:
    stem = path.stem
    ext  = path.suffix.lower()

    q_match  = QUALITY_RE.search(stem)
    quality  = q_match.group(0) if q_match else ""
    stem_clean = stem[:q_match.start()] if q_match else stem

    se = TV_SE_RE.search(stem_clean)
    if se:
        return {
            "type": "tv",
            "title_guess": _clean_title(stem_clean[:se.start()]),
            "season": int(se.group(1)),
            "episode": int(se.group(2)),
            "quality": quality,
            "ext": ext,
        }

    alt = TV_SE_ALT.search(stem_clean)
    if alt:
        return {
            "type": "tv",
            "title_guess": _clean_title(stem_clean[:alt.start()]),
            "season": int(alt.group(1)),
            "episode": int(alt.group(2)),
            "quality": quality,
            "ext": ext,
        }

    yr = YEAR_RE.search(stem_clean)
    if yr:
        return {
            "type": "movie",
            "title_guess": _clean_title(stem_clean[:yr.start()]),
            "year": int(yr.group(1)),
            "quality": quality,
            "ext": ext,
        }

    return {"type": "unknown", "title_guess": _clean_title(stem_clean), "quality": quality, "ext": ext}


def _proposed_season_name(folder_name: str) -> str | None:
    m = SEASON_FOLDER_RE.match(folder_name.strip())
    if m:
        canonical = f"Season {int(m.group(1)):02d}"
        return canonical if canonical != folder_name else None
    return None


def detect_folder_proposals(files: list[dict]) -> list[dict]:
    """Detect folders whose names need fixing (Season 1 → Season 01) from the file list."""
    seen: set[str] = set()
    proposals = []
    for f in files:
        folder_path = f["folder"]
        if folder_path in seen:
            continue
        seen.add(folder_path)
        p = Path(folder_path)
        proposed = _proposed_season_name(p.name)
        if proposed:
            proposals.append({
                "id": folder_path,
                "path": folder_path,
                "folder": str(p.parent),
                "filename": p.name,
                "proposed_name": proposed,
                "op_type": "folder",
            })
    return proposals


def _find_subtitles(video_path: Path, all_subs: dict[str, list[Path]]) -> list[dict]:
    """Return subtitle files that belong to this video."""
    stem = video_path.stem.lower()
    folder = str(video_path.parent)
    result = []
    for sub_path in all_subs.get(folder, []):
        sub_stem = sub_path.stem.lower()
        # Match: exact stem, or stem.lang (e.g. video.en.srt)
        if sub_stem == stem or sub_stem.startswith(stem + "."):
            lang = sub_stem[len(stem)+1:] if sub_stem.startswith(stem + ".") else ""
            result.append({
                "path": str(sub_path),
                "filename": sub_path.name,
                "ext": sub_path.suffix.lower(),
                "lang": lang,
            })
    return result


def scan_folder(folder: str, recursive: bool = True) -> tuple[list[dict], list[dict]]:
    """Returns (files, folder_proposals). Skips files in the skip list."""
    root = Path(folder)
    if not root.is_dir():
        return [], []

    pattern = "**/*" if recursive else "*"

    # Collect all subtitle files indexed by folder
    all_subs: dict[str, list[Path]] = {}
    raw_video: list[Path] = []
    for p in sorted(root.glob(pattern)):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in MEDIA_EXTENSIONS:
            raw_video.append(p)
        elif ext in SUBTITLE_EXTENSIONS:
            all_subs.setdefault(str(p.parent), []).append(p)

    # Drop sample/preview clips and skip-listed files
    raw_video = [
        p for p in raw_video
        if "sample" not in p.stem.lower() and not skiplist.is_skipped(str(p))
    ]

    folder_counts = Counter(str(p.parent) for p in raw_video)

    files = []
    for p in raw_video:
        parsed = parse_filename(p)
        subs = _find_subtitles(p, all_subs)
        entry = {
            "id": str(p),
            "path": str(p),
            "folder": str(p.parent),
            "filename": p.name,
            "folder_file_count": folder_counts[str(p.parent)],
            "subtitles": subs,
            # scan_root is the folder the user pointed at — used as the
            # definitive base for folder organisation (avoids deep-nesting issues)
            "scan_root": str(root),
            **parsed,
        }
        if parsed["type"] == "tv":
            entry["show_root"] = _show_root(p)
        files.append(entry)

    # Season-folder renames (Season 1 → Season 01) are NOT proposed here:
    # file moves place episodes directly into the correct "Season XX" folder
    # and the cleanup pass removes the old empty folders afterwards. This avoids
    # multi-level folder-rename ordering conflicts. Movie-folder and TV-root
    # renames are requested separately by the frontend after metadata lookup.
    return files, []
