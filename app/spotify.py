"""Read a public Spotify playlist from its embed page (no login, no developer app).
Spotify's own API only returns songs for playlists you own, so the public embed page is used instead. It is not an official
interface: if Spotify changes it, this stops working, and only the server needs fixing.
"""
import json
import re

_LINK = re.compile(r"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?(?:embed/)?playlist/|spotify:playlist:)([A-Za-z0-9]{10,30})", re.I)
_SHORT = re.compile(r"https?://(?:spotify\.link|[\w-]+\.spotify\.app\.link|spotify\.app\.link)/[\w?=&%./-]*", re.I)
_NEXT = re.compile(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en"}
PREFIX = "spotify-"
MAX_TRACKS = 100


def link_id(text: str) -> str:
    """The playlist id in a Spotify link ('' when the text is not one)."""
    m = _LINK.search(text or "")
    return m.group(1) if m else ""


def _find(node, key: str):
    """First value stored under `key` anywhere inside the parsed page (the page layout changes now and then)."""
    if isinstance(node, dict):
        if isinstance(node.get(key), list) and node[key]:
            return node[key]
        for v in node.values():
            r = _find(v, key)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find(v, key)
            if r:
                return r
    return None


def _entity(node):
    if isinstance(node, dict):
        if isinstance(node.get("trackList"), list):
            return node
        for v in node.values():
            r = _entity(v)
            if r:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _entity(v)
            if r:
                return r
    return None


def parse_embed(html: str) -> dict | None:
    """{title, cover, tracks: [{title, artist, seconds}]} from an embed page, or None when it is not understood."""
    m = _NEXT.search(html or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    ent = _entity(data)
    if not ent:
        return None
    tracks = []
    for t in ent.get("trackList") or []:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        artist = str(t.get("subtitle") or "").replace(" ", " ").strip()
        if not title:
            continue
        ms = t.get("duration")
        tracks.append({"title": title, "artist": artist, "seconds": round(ms / 1000) if isinstance(ms, (int, float)) and ms > 0 else 0})
    if not tracks:
        return None
    cover = ""
    art = ent.get("coverArt") or ent.get("visualIdentity") or {}
    sources = art.get("sources") or art.get("image") or []
    if isinstance(sources, list) and sources and isinstance(sources[-1], dict):
        cover = str(sources[-1].get("url") or "")
    return {"title": str(ent.get("title") or ent.get("name") or "Spotify playlist"), "cover": cover,
            "author": str(ent.get("subtitle") or ""), "tracks": tracks[:MAX_TRACKS]}


async def fetch(playlist_id: str) -> dict | None:
    import httpx  # imported here so the pure-logic tests need no web stack

    try:
        async with httpx.AsyncClient(timeout=10, headers=UA, follow_redirects=True) as c:
            r = await c.get(f"https://open.spotify.com/embed/playlist/{playlist_id}")
    except httpx.HTTPError:
        return None
    return parse_embed(r.text) if r.status_code == 200 else None


async def resolve_short(text: str) -> str:
    """The playlist id behind a short share link (spotify.link/..., what the phone apps share), or ''."""
    m = _SHORT.search(text or "")
    if not m:
        return ""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15, headers=UA, follow_redirects=True) as c:
            r = await c.get(m.group(0))
    except httpx.HTTPError:
        return ""
    return link_id(str(r.url)) or link_id(r.text)
