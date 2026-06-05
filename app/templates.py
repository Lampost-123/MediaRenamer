import re
from pathlib import Path

ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
MULTI_SPACE_RE   = re.compile(r"\s+")


def sanitize(value: str) -> str:
    value = ILLEGAL_CHARS_RE.sub("", value)
    value = MULTI_SPACE_RE.sub(" ", value)
    return value.strip(" .")


def apply_template(template: str, tokens: dict) -> str:
    result = template
    for key, value in tokens.items():
        replacement = str(value) if key == "ext" else (sanitize(str(value)) if value else "")
        result = result.replace(f"{{{key}}}", replacement)
    result = re.sub(r" - \s*-", " -", result)
    result = re.sub(r"\s+-\s*\.", ".", result)
    result = re.sub(r"\s+", " ", result)
    return result.strip(" .-")


def build_tokens(file_entry: dict, match: dict | None) -> dict:
    tokens = {
        "title": "",
        "year": "",
        "season": "",
        "episode": "",
        "episode_title": "",
        "quality": file_entry.get("quality", ""),
        "ext": file_entry.get("ext", ""),
    }
    if match:
        tokens["title"] = match.get("title", "")
        tokens["year"]  = match.get("year", "")
        tokens["episode_title"] = match.get("episode_title", "")
    else:
        tokens["title"] = file_entry.get("title_guess", "")
        tokens["year"]  = str(file_entry.get("year", ""))

    if (s := file_entry.get("season")) is not None:
        tokens["season"] = f"{int(s):02d}"
    if (e := file_entry.get("episode")) is not None:
        tokens["episode"] = f"{int(e):02d}"
    return tokens


def _normalise(s: str) -> str:
    """Strip non-alphanumeric chars and lowercase — used for fuzzy folder name matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def tv_show_folder_name(match: dict | None, file_entry: dict | None = None) -> str:
    """
    Canonical TV root folder name — always 'Show Title (Year)' using the
    series START year (TMDB first_air_date), e.g. 'The Last Man On Earth (2015)'.
    Falls back to just the title when no year is available.
    """
    match = match or {}
    file_entry = file_entry or {}
    title = sanitize(match.get("title") or file_entry.get("title_guess", ""))
    year  = str(match.get("year") or file_entry.get("year", "")).strip()
    return f"{title} ({year})" if year and title else title


def _folder_is_show(folder_path: str, show_title: str) -> bool:
    """
    Return True when the leaf folder already IS a cleanly named show folder.

    Accepts only:
      • Exact normalised match:    "The Shield"  →  "theshield" == "theshield"
      • Match + bare 4-digit year: "Lost (2004)" →  "lost2004", suffix "2004"

    Rejects junk/batch names like "The.Shield.S01-07.1080p" so those get
    handled by _folder_is_junk_show() instead.
    """
    if not show_title:
        return True
    folder_name = Path(folder_path).name
    nf = _normalise(folder_name)
    ns = _normalise(show_title)
    if not ns:
        return True
    if nf == ns:
        return True
    # Allow "Show (2004)" — show name + exactly 4 digit year, nothing else
    if nf.startswith(ns) and len(nf) == len(ns) + 4 and nf[len(ns):].isdigit():
        return True
    return False


def _folder_is_junk_show(folder_name: str, show_title: str) -> bool:
    """
    Return True when a folder name is a junk-encoded version of the show title,
    i.e. it starts with the show name but has extra crud after it.

    Examples that match "The Last Man On Earth":
      The.Last.Man.On.Earth.S01-S04.1080p.AMZN.WEBRip.DDP5.1.x265-SiGMA
      The.Last.Man.On.Earth.Complete.Series.BluRay

    Used to decide whether to rename the scan root folder to the clean title.
    """
    ns = _normalise(show_title)
    nf = _normalise(folder_name)
    # Need at least 4 chars in the title to avoid false positives
    if len(ns) < 4:
        return False
    if not nf.startswith(ns):
        return False
    # Must have extra content beyond the show name (otherwise it's already clean)
    suffix = nf[len(ns):]
    return len(suffix) > 0


def clean_show_folder_name(folder_name: str, show_title: str) -> str:
    """
    Return the proposed clean folder name for a junk-encoded show folder.
    Uses the TMDB show title directly.
    """
    return show_title


def propose_folder(file_entry: dict, match: dict | None) -> str:
    """
    Return the target directory for this file based on media type.

    TV organisation logic (uses scan_root as the single reliable anchor):
      • scan_root IS the show folder  →  <scan_root>/Season XX/
          e.g. user scans F:\\TV\\The Shield  →  F:\\TV\\The Shield\\Season 01
      • scan_root is a parent/container  →  <scan_root>/<ShowTitle>/Season XX/
          e.g. user scans F:\\TV  →  F:\\TV\\The Shield\\Season 01
      • Season 0 episodes always go into Specials/

    This means files buried in any depth of junk sub-folders
    (e.g. The.Shield.S01-S07.Complete\\The.Shield.S01.1080p\\episode.mkv)
    all get cleanly lifted out to <scan_root>/Season XX/ regardless.

    Movie alone in its own folder → keep path (folder renamed separately).
    Movie in a shared folder → <current_folder>/<Movie Title (Year)>/
    """
    ftype = (match or {}).get("type") or file_entry.get("type", "unknown")

    if ftype == "tv":
        scan_root  = file_entry.get("scan_root") or file_entry.get("folder", "")
        show_title = sanitize((match or {}).get("title") or file_entry.get("title_guess", ""))
        # Root folder name always carries the series start year, e.g. "Lost (2004)"
        root_name  = tv_show_folder_name(match, file_entry)
        season     = file_entry.get("season", 0)
        season_dir = "Specials" if int(season) == 0 else f"Season {int(season):02d}"
        scan_root_path = Path(scan_root)

        if not show_title:
            return str(scan_root_path / season_dir)

        # When scan_root IS the show folder — whether already clean (Lost),
        # missing the year (Lost → Lost (2004)), or junk-named
        # (The.Last.Man.On.Earth.S01-S04...) — keep files INSIDE it.
        # The scan_root folder itself is renamed to "Show (Year)" separately
        # (see /api/tv-root-proposal), and src paths are remapped through that
        # rename, so files end up in  <parent>/Show (Year)/Season XX/.
        if _folder_is_show(scan_root, show_title) or _folder_is_junk_show(scan_root_path.name, show_title):
            return str(scan_root_path / season_dir)

        # scan_root is a collection folder (e.g. F:\TV)
        # → create the show folder (with year) inside it
        return str(scan_root_path / root_name / season_dir)

    if ftype == "movie":
        title = sanitize((match or {}).get("title") or file_entry.get("title_guess", ""))
        year  = str((match or {}).get("year") or file_entry.get("year", ""))
        folder_name = f"{title} ({year})" if year else title
        if not folder_name:
            return file_entry.get("folder", "")

        # If this file is the only media file in its folder, the folder itself
        # will be renamed (handled as a folder proposal). Don't create a sub-folder.
        if file_entry.get("folder_file_count", 2) == 1:
            return file_entry.get("folder", "")

        # Multiple movies in same folder → create a dedicated sub-folder
        return str(Path(file_entry.get("folder", "")) / folder_name)

    return file_entry.get("folder", "")


def propose_name(file_entry: dict, match: dict | None, tv_template: str, movie_template: str) -> str:
    """Return the proposed filename (without folder path)."""
    tokens = build_tokens(file_entry, match)
    ftype  = (match or {}).get("type") or file_entry.get("type", "unknown")
    tmpl   = tv_template if ftype == "tv" else movie_template
    return apply_template(tmpl, tokens)


def propose_movie_folder_name(file_entry: dict, match: dict | None) -> str:
    """For a movie that is alone in its folder, return what that folder should be named."""
    title = sanitize((match or {}).get("title") or file_entry.get("title_guess", ""))
    year  = str((match or {}).get("year") or file_entry.get("year", ""))
    return f"{title} ({year})" if year else title


# ── Token reference (used by the help tooltip in the UI) ─────────────────────
TOKEN_HELP = {
    "tv": [
        ("{title}",         "Show name",              "Breaking Bad"),
        ("{season}",        "Season number (zero-padded)", "01"),
        ("{episode}",       "Episode number (zero-padded)", "05"),
        ("{episode_title}", "Episode title from TMDB",  "Gray Matter"),
        ("{quality}",       "Detected quality tag",    "1080p"),
        ("{ext}",           "File extension",          ".mkv"),
    ],
    "movie": [
        ("{title}",   "Movie title",         "The Dark Knight"),
        ("{year}",    "Release year",        "2008"),
        ("{quality}", "Detected quality tag", "BluRay"),
        ("{ext}",     "File extension",      ".mkv"),
    ],
}
