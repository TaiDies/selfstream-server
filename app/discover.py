"""Weekly Discover: songs you have not played yet, picked from what you actually listen to.

How it works: the tracks and artists you spent the most time on lately (recent listening counts more, quick skips
count against) become "seeds". YouTube Music's radio for each seed supplies candidates. A candidate scores higher the
more seeds point to it, the earlier it appears, and the more its genres match yours. Songs you already know are dropped,
and no artist fills the list. A few "new from artists you love" picks are mixed in.
"""
import asyncio
import datetime
import random
from typing import Awaitable, Callable

import store as store_mod
import subsonic

Radio = Callable[[str], Awaitable[list[dict]]]
Resolve = Callable[[str, str], Awaitable[str | None]]
Latest = Callable[[str], Awaitable[list[dict]]]


def week_id(now: float) -> str:
    y, w, _ = datetime.date.fromtimestamp(now).isocalendar()
    return f"{y}-W{w:02d}"


def pick_seeds(taste: dict, rng: random.Random, count: int = 6) -> list[tuple[str, dict, float]]:
    """(track key, song, weight) of the tracks to base recommendations on, sampled from your top ones."""
    ranked = sorted(((w, k) for k, w in taste["tracks"].items() if w > 0), reverse=True)[:14]
    if not ranked:
        return []
    top = ranked[0][0]
    pool = list(ranked)
    chosen = []
    while pool and len(chosen) < count:
        k = rng.choices(range(len(pool)), weights=[w for w, _ in pool])[0]
        w, key = pool.pop(k)
        chosen.append((key, taste["songs"][key], w / top))
    return chosen


def score_candidates(seeds_hits: list[tuple[float, list[dict]]], known: set[str], taste: dict,
                     artist_genres: Callable[[str], list[str] | None]) -> list[dict]:
    """Merge the radio lists of all seeds into one ranked list of unseen songs."""
    genre_total = sum(w for w in taste["genres"].values() if w > 0) or 1.0
    scored: dict[str, dict] = {}
    for seed_weight, items in seeds_hits:
        for rank, meta in enumerate(items):
            vid = meta.get("id")
            artist, title = subsonic.track_names(meta)
            key = store_mod.track_key(artist, title)
            if not vid or key in known:
                continue
            entry = scored.setdefault(key, {"meta": meta, "score": 0.0, "hits": 0, "key": key, "akey": store_mod.artist_key(artist)})
            entry["score"] += seed_weight / (rank + 3)
            entry["hits"] += 1
    for e in scored.values():
        genres = artist_genres(e["meta"].get("artist") or e["meta"].get("channel") or "") or []
        e["score"] += 0.15 * sum(max(taste["genres"].get(g.lower(), 0), 0) for g in genres) / genre_total
        e["score"] *= 1 + 0.25 * (e["hits"] - 1)  # several seeds agreeing is the strongest signal
    return sorted(scored.values(), key=lambda e: e["score"], reverse=True)


def limit_per_artist(ranked: list[dict], total: int, per_artist: int = 2) -> list[dict]:
    seen: dict[str, int] = {}
    out = []
    for e in ranked:
        n = seen.get(e["akey"], 0)
        if n >= per_artist:
            continue
        seen[e["akey"]] = n + 1
        out.append(e)
        if len(out) >= total:
            break
    return out


async def build(st: "store_mod.Store", user: str, radio: Radio, resolve: Resolve, latest: Latest,
                now: float, variant: int = 0, total: int = 30) -> dict:
    taste = st.taste(user, now=now)
    rng = random.Random(f"{week_id(now)}:{variant}:{user}")
    seeds = pick_seeds(taste, rng)
    if not seeds:
        return {"week": week_id(now), "songs": [], "needsHistory": True, "seeds": []}
    known = st.played_keys(user)
    async def one(song: dict, weight: float):
        sid = str(song.get("id", ""))
        vid = sid[3:] if sid.startswith("yt-") else await resolve(song.get("artist", ""), song.get("title", ""))
        items = await radio(vid) if vid else []
        return (max(weight, 0.2), items, f"{song.get('artist', '')} – {song.get('title', '')}")

    results = await asyncio.gather(*(one(song, w) for _, song, w in seeds), return_exceptions=True)
    hits = [(w, items) for r in results if not isinstance(r, Exception) for w, items, _ in [r] if items]
    used = [name for r in results if not isinstance(r, Exception) for _, items, name in [r] if items]
    ranked = limit_per_artist(score_candidates(hits, known, taste, st.artist_genres), total)
    songs = []
    for e in ranked:
        s = subsonic.make_song(e["meta"])
        s["reason"] = "Based on your recent listening" if e["hits"] < 2 else f"Matches {e['hits']} of your favourites"
        songs.append(s)
    # a few fresh releases from the artists you play most
    fresh = []
    top_artists = sorted(((w, a) for a, w in taste["artists"].items() if w > 0), reverse=True)[:3]
    for _, akey in top_artists:
        for meta in (await latest(taste["artistNames"][akey]))[:2]:
            artist, title = subsonic.track_names(meta)
            k = store_mod.track_key(artist, title)
            if k in known or any(subsonic.remote_id(meta["id"]) == s["id"] for s in songs + fresh):
                continue
            s = subsonic.make_song(meta)
            s["reason"] = f"New from {taste['artistNames'][akey]}"
            fresh.append(s)
    for i, s in enumerate(fresh):
        songs.insert(min(len(songs), 3 + i * 4), s)
    return {"week": week_id(now), "songs": songs[: total + 6], "needsHistory": False, "seeds": used, "generated": now}
