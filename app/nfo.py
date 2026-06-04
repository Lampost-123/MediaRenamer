"""Generate Kodi / Plex / Jellyfin compatible .nfo XML sidecar files."""
from pathlib import Path


def _x(s) -> str:
    return (str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def tv_nfo(match: dict, season: int, episode: int) -> str:
    ep_title = match.get("episode_title", "")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<episodedetails>
  <title>{_x(ep_title)}</title>
  <showtitle>{_x(match.get("title", ""))}</showtitle>
  <season>{season}</season>
  <episode>{episode}</episode>
  <year>{_x(match.get("year", ""))}</year>
  <plot>{_x(match.get("overview", ""))}</plot>
  <uniqueid type="tmdb" default="true">{_x(match.get("tmdb_id", ""))}</uniqueid>
</episodedetails>
"""


def movie_nfo(match: dict) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>{_x(match.get("title", ""))}</title>
  <year>{_x(match.get("year", ""))}</year>
  <plot>{_x(match.get("overview", ""))}</plot>
  <uniqueid type="tmdb" default="true">{_x(match.get("tmdb_id", ""))}</uniqueid>
</movie>
"""


def write_nfo(nfo_path: Path, content: str) -> None:
    nfo_path.write_text(content, encoding="utf-8")
