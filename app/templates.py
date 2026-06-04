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


def propose_folder(file_entry: dict, match: dict | None) -> str:
    """
    Return the target directory for this file based on media type.

    TV   → <show_root>/Season XX  or  <show_root>/Specials
    Movie alone in folder → keep in same folder (the folder itself gets renamed separately)
    Movie in shared folder → <current_folder>/<Movie Title (Year)>
    """
    ftype = (match or {}).get("type") or file_entry.get("type", "unknown")

    if ftype == "tv":
        show_root = file_entry.get("show_root") or file_entry.get("folder", "")
        season = file_entry.get("season", 0)
        sub = "Specials" if int(season) == 0 else f"Season {int(season):02d}"
        return str(Path(show_root) / sub)

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
