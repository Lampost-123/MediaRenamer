import asyncio
import httpx
from app import config, cache as _cache

BASE = "https://api.themoviedb.org/3"
IMG_BASE = "https://image.tmdb.org/t/p/w185"
TIMEOUT = 10.0


def _auth(api_key: str) -> tuple[dict, dict]:
    """Return (headers, extra_params) depending on key type.
    v3 API keys are 32 hex chars → use ?api_key= query param.
    v4 Read Access Tokens are long JWTs → use Authorization: Bearer header."""
    if len(api_key) <= 40:
        return {"accept": "application/json"}, {"api_key": api_key}
    return {"Authorization": f"Bearer {api_key}", "accept": "application/json"}, {}


async def search_movie(client: httpx.AsyncClient, query: str, year: int | None, api_key: str) -> list[dict]:
    cache_key = f"movie:{query}:{year}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    headers, auth_params = _auth(api_key)
    params = {"query": query, "include_adult": "false", "language": "en-US", "page": 1, **auth_params}
    if year:
        params["year"] = year
    try:
        r = await client.get(f"{BASE}/search/movie", params=params, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        candidates = []
        for item in results[:5]:
            release_year = ""
            if item.get("release_date"):
                release_year = item["release_date"][:4]
            candidates.append({
                "tmdb_id": item["id"],
                "title": item.get("title", ""),
                "original_title": item.get("original_title", ""),
                "year": release_year,
                "overview": item.get("overview", "")[:200],
                "poster": IMG_BASE + item["poster_path"] if item.get("poster_path") else None,
                "type": "movie",
            })
        _cache.put(cache_key, candidates)
        return candidates
    except Exception:
        return []


async def search_tv(client: httpx.AsyncClient, query: str, api_key: str) -> list[dict]:
    cache_key = f"tv:{query}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    headers, auth_params = _auth(api_key)
    params = {"query": query, "include_adult": "false", "language": "en-US", "page": 1, **auth_params}
    try:
        r = await client.get(f"{BASE}/search/tv", params=params, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        candidates = []
        for item in results[:5]:
            first_air = item.get("first_air_date", "")
            year = first_air[:4] if first_air else ""
            candidates.append({
                "tmdb_id": item["id"],
                "title": item.get("name", ""),
                "original_title": item.get("original_name", ""),
                "year": year,
                "overview": item.get("overview", "")[:200],
                "poster": IMG_BASE + item["poster_path"] if item.get("poster_path") else None,
                "type": "tv",
            })
        _cache.put(cache_key, candidates)
        return candidates
    except Exception:
        return []


async def get_season_episodes(client: httpx.AsyncClient, tmdb_id: int, season: int, api_key: str) -> list[dict]:
    """Fetch all episodes in a season — used for missing episode detection."""
    cache_key = f"season:{tmdb_id}:{season}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    headers, auth_params = _auth(api_key)
    try:
        r = await client.get(
            f"{BASE}/tv/{tmdb_id}/season/{season}",
            params=auth_params or None,
            headers=headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        episodes = [
            {"episode_number": e["episode_number"], "name": e.get("name", ""), "air_date": e.get("air_date", "")}
            for e in data.get("episodes", [])
        ]
        _cache.put(cache_key, episodes)
        return episodes
    except Exception:
        return []


async def get_episode(client: httpx.AsyncClient, tmdb_id: int, season: int, episode: int, api_key: str) -> dict:
    cache_key = f"ep:{tmdb_id}:{season}:{episode}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached[0]  # stored as single-item list

    headers, auth_params = _auth(api_key)
    try:
        r = await client.get(
            f"{BASE}/tv/{tmdb_id}/season/{season}/episode/{episode}",
            params=auth_params or None,
            headers=headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        result = {
            "episode_title": data.get("name", ""),
            "air_date": data.get("air_date", ""),
            "overview": data.get("overview", "")[:200],
        }
        _cache.put(cache_key, [result])   # list wrapper for uniform cache storage
        return result
    except Exception:
        return {"episode_title": "", "air_date": "", "overview": ""}


async def lookup_file(file_entry: dict, api_key: str) -> dict:
    async with httpx.AsyncClient() as client:
        ftype = file_entry.get("type", "unknown")
        query = file_entry.get("title_guess", "")
        if not query:
            return {"candidates": [], "error": "no title guess"}

        if ftype == "movie":
            candidates = await search_movie(client, query, file_entry.get("year"), api_key)
        elif ftype == "tv":
            candidates = await search_tv(client, query, api_key)
            if candidates:
                ep = await get_episode(
                    client,
                    candidates[0]["tmdb_id"],
                    file_entry.get("season", 1),
                    file_entry.get("episode", 1),
                    api_key,
                )
                candidates[0]["episode_title"] = ep["episode_title"]
        else:
            # Try both, merge
            movie_cands = await search_movie(client, query, file_entry.get("year"), api_key)
            tv_cands = await search_tv(client, query, api_key)
            candidates = movie_cands + tv_cands

        return {"candidates": candidates}


async def lookup_bulk(file_entries: list[dict], api_key: str) -> list[dict]:
    tasks = [lookup_file(f, api_key) for f in file_entries]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    output = []
    for i, res in enumerate(results):
        entry = dict(file_entries[i])
        if isinstance(res, Exception):
            entry["candidates"] = []
            entry["error"] = str(res)
        else:
            entry.update(res)
        output.append(entry)
    return output
