"""Find genres for the music you listen to.

Order of sources: the tag Navidrome already has for library songs (comes with the song itself), then Deezer (free, no
key; the genres of the album the track is on), then MusicBrainz (free, community tags for the artist). The result is
remembered per artist, so each artist is looked up once. Everything is best effort: no network means no genres, not an
error.
"""
import asyncio
import re

import store as store_mod
import subsonic

MB_HEADERS = {"User-Agent": "selfstream/0.3 (self-hosted music server)", "Accept": "application/json"}
IGNORE = {"musique", "music", "other", "autre", "unknown", "all", "alternative music"}


def clean(names: list[str], limit: int = 4) -> list[str]:
    out, seen = [], set()
    for n in names:
        n = re.sub(r"\s+", " ", (n or "").strip())
        if n and n.lower() not in IGNORE and n.lower() not in seen and len(n) < 40:
            seen.add(n.lower())
            out.append(n.title() if n.islower() else n)
    return out[:limit]


async def deezer_genres(http, artist: str, title: str) -> list[str]:
    q = f'artist:"{artist}" track:"{title}"'
    r = await http.get("https://api.deezer.com/search", params={"q": q, "limit": 1})
    data = (r.json().get("data") or []) if r.status_code == 200 else []
    if not data:
        return []
    album_id = (data[0].get("album") or {}).get("id")
    if not album_id:
        return []
    r = await http.get(f"https://api.deezer.com/album/{album_id}")
    if r.status_code != 200:
        return []
    return clean([g.get("name", "") for g in (r.json().get("genres") or {}).get("data") or []])


async def musicbrainz_genres(http, artist: str) -> list[str]:
    r = await http.get("https://musicbrainz.org/ws/2/artist/", params={"query": f'artist:"{artist}"', "fmt": "json", "limit": 3},
                       headers=MB_HEADERS)
    if r.status_code != 200:
        return []
    want = subsonic.norm(artist)
    for a in r.json().get("artists") or []:
        if subsonic.norm(a.get("name", "")) == want and int(a.get("score", 0)) >= 85:
            tags = sorted(a.get("tags") or [], key=lambda t: t.get("count", 0), reverse=True)
            return clean([t.get("name", "") for t in tags if t.get("count", 0) >= 1])
    return []


async def lookup(http, artist: str, title: str) -> tuple[list[str], str]:
    """(genres, source) for an artist; ([], '') when nothing was found."""
    for source, call in (("deezer", lambda: deezer_genres(http, artist, title)), ("musicbrainz", lambda: musicbrainz_genres(http, artist))):
        try:
            genres = await call()
        except Exception:
            genres = []
        if genres:
            return genres, source
        if source == "musicbrainz":
            await asyncio.sleep(1.1)  # MusicBrainz asks for at most one request per second
    return [], ""


async def run_once(st: "store_mod.Store", http, batch: int = 12) -> int:
    """Look up genres for tracks that have none. Returns how many artists were looked up."""
    st.apply_artist_genres()
    seen: set[str] = set()
    looked = 0
    for t in st.tracks_without_genres(80):
        akey = store_mod.artist_key(t["artist"])
        if akey in seen:
            continue
        seen.add(akey)
        known = st.artist_genres(t["artist"])
        if known is not None:  # looked up before (maybe with no result): do not hammer the services
            continue
        genres, source = await lookup(http, store_mod._first_artist(t["artist"]), t["title"])
        st.set_artist_genres(t["artist"], genres, source or "none")
        looked += 1
        if looked >= batch:
            break
    st.apply_artist_genres()
    return looked


async def worker(st, http, interval: float = 45.0) -> None:
    while True:
        try:
            await run_once(st, http)
        except Exception:
            pass
        await asyncio.sleep(interval)
