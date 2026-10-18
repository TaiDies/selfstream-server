"""Server-driven screens: the app draws whatever sections the server describes.

A screen is {"title", "actions": [...], "sections": [...]}. The app knows a fixed set of section types
(see SECTION_TYPES) and nothing about what they are for, so new tabs, rearranged pages and new buttons can be
shipped by updating the server alone. Anything that is not in that vocabulary is ignored by the app.

Actions a button/card/section can carry:
  {"type": "play", "songs": [...], "shuffle": false}   play these songs
  {"type": "tab", "tab": "<tab id>"}                    switch to a tab
  {"type": "open", "kind": "album|artist|playlist|remotePlaylist", "id": "...", "name": "..."}
  {"type": "reload", "params": {"refresh": "1"}}        load this screen again with extra parameters
  {"type": "randomSongs"}                               play a random mix from the library
  {"type": "savePlaylist", "name": "...", "songs": [...]}
"""
from __future__ import annotations

import time

SECTION_TYPES = ("header", "pills", "buttons", "link", "chips", "hero", "tiles", "bars", "songs", "songcards", "cards",
                 "ranks", "note", "text")

RANGE_OPTIONS = [{"id": "week", "label": "7 days"}, {"id": "month", "label": "30 days"},
                 {"id": "year", "label": "Year"}, {"id": "all", "label": "All time"}]


def listening(seconds: int) -> str:
    h, m = int(seconds) // 3600, (int(seconds) % 3600) // 60
    if h:
        return f"{h}h {m}m"
    return f"{m}m" if m else f"{int(seconds)}s"


def _cards(kind: str, items: list[dict], sub=lambda i: "") -> list[dict]:
    return [{"kind": kind, "id": i["id"], "title": i.get("name") or i.get("title") or "", "subtitle": sub(i),
             "cover": i.get("coverArt") or i.get("cover") or ""} for i in items]


def home(recent: list[dict], most_played: list[dict], week_hours: float, streak: int, playlists: list[dict],
         features: set[str]) -> dict:
    sections: list[dict] = [{"type": "buttons", "items": [
        {"label": "Shuffle my library", "icon": "shuffle", "action": {"type": "randomSongs"}}]}]
    pills = []
    if week_hours:
        pills.append({"icon": "schedule", "label": f"{week_hours:.1f} h this week"})
    if streak > 1:
        pills.append({"icon": "fire", "label": f"{streak} day streak"})
    if pills:
        sections.append({"type": "pills", "items": pills})
    if recent:
        sections += [{"type": "header", "title": "Recently played"}, {"type": "songcards", "songs": recent}]
    if playlists:
        sections += [{"type": "header", "title": "Your playlists", "action": {"label": "See all", "action": {"type": "tab", "tab": "library"}}},
                     {"type": "cards", "items": _cards("playlist", playlists[:12], lambda p: f"{p.get('songCount', 0)} songs")}]
    if most_played:
        sections += [{"type": "header", "title": "Most played this week"},
                     {"type": "songs", "songs": most_played[:5], "numbered": True}]
    if "discover" in features:
        sections.append({"type": "link", "icon": "discover", "title": "Your weekly Discover",
                         "subtitle": "New songs picked from what you actually listen to",
                         "action": {"type": "tab", "tab": "discover"}})
    if "stats" in features:
        sections.append({"type": "link", "icon": "activity", "title": "Your activity",
                         "subtitle": "Hours listened, top songs, artists and genres",
                         "action": {"type": "screen", "screen": "activity", "title": "Your activity"}})
    if not recent and not playlists:
        sections.append({"type": "note", "icon": "headphones",
                         "text": "Search for a song, play it, and your history, playlists and recommendations start filling in here."})
    return {"title": "{greeting}", "settings": True, "sections": sections}


ACTIVITY_LINK = {"type": "link", "icon": "activity", "title": "Your activity",
                 "subtitle": "Hours listened, top songs, artists and genres",
                 "action": {"type": "screen", "screen": "activity", "title": "Your activity"}}

ACTIVITY_BUTTON = {"icon": "activity", "tooltip": "Your activity",
                   "action": {"type": "screen", "screen": "activity", "title": "Your activity"}}


def discover(d: dict, stats: bool = True) -> dict:
    # Activity is a button here, not a tab of its own
    actions = ([ACTIVITY_BUTTON] if stats else []) + [
        {"icon": "refresh", "tooltip": "New mix", "action": {"type": "reload", "params": {"refresh": "1"}}}]
    if d.get("needsHistory"):
        return {"title": "Discover", "actions": actions, "sections": [{"type": "note", "icon": "discover", "text":
                "Listen to a few songs first.\nYour weekly Discover is built from what you play, how long you play it, and what you skip."}]
                + ([ACTIVITY_LINK] if stats else [])}
    songs = d.get("songs") or []
    week = str(d.get("week", "")).split("-W")[-1]
    seeds = d.get("seeds") or []
    sections: list[dict] = ([ACTIVITY_LINK] if stats else []) + [{"type": "header", "title": f"Week {week} mix", "big": True,
                             "subtitle": (f"Based on {', '.join(seeds[:3])}{' and more' if len(seeds) > 3 else ''}" if seeds else "")},
                            {"type": "songs", "songs": songs, "buttons": True, "showReason": True,
                             "extra": [{"label": "Save as playlist", "icon": "playlist_add",
                                        "action": {"type": "savePlaylist", "name": f"Discover week {week}", "songs": songs}}]}]
    if not songs:
        sections.insert(1, {"type": "note", "icon": "discover", "text":
                            "No suggestions came back right now. Try the refresh button."})
    sections.append({"type": "text", "small": True, "text":
                     "Tip: like the songs you enjoy. Liked songs teach Discover what you want more of."})
    return {"title": "Discover", "actions": actions, "sections": sections}


def activity(s: dict, rng: str) -> dict:
    sections: list[dict] = [{"type": "chips", "param": "range", "selected": rng, "options": RANGE_OPTIONS}]
    if s["plays"] == 0 and s["totalSeconds"] == 0:
        sections.append({"type": "note", "icon": "activity", "text": "No listening recorded in this period yet. Play some music and come back."})
        return {"title": "Your activity", "sections": sections}
    long = s["totalSeconds"] >= 3600
    sections.append({"type": "hero", "value": f"{s['totalHours']:.1f}" if long else str(round(s["totalSeconds"] / 60)),
                     "label": "hours listened" if long else "minutes listened",
                     "note": f"{s['lifetimeHours']:.1f} hours with Selfstream in total"})
    sections.append({"type": "tiles", "items": [
        {"value": str(s["plays"]), "label": "plays"}, {"value": str(s["uniqueTracks"]), "label": "different songs"},
        {"value": str(s["uniqueArtists"]), "label": "artists"}, {"value": str(s["newArtists"]), "label": "new artists"},
        {"value": str(s["currentStreak"]), "label": "day streak"}, {"value": f"{round(s['avgMinutesPerDay'])}m", "label": "per day"}]})
    if s["byHour"] and s["totalSeconds"] > 0:
        sections += [{"type": "header", "title": "When you listen"},
                     {"type": "bars", "title": "Day of week", "values": s["byWeekday"], "labels": list("MTWTFSS")},
                     {"type": "bars", "title": "Time of day", "values": s["byHour"],
                      "labels": [str(h) if h % 6 == 0 else "" for h in range(24)]}]
    series = s["series"]
    if len(series) > 1:
        sections += [{"type": "header", "title": "Over time"},
                     {"type": "bars", "title": "", "values": [e["seconds"] for e in series],
                      "labels": ["/".join(e["day"].split("-")[1:]) if i % 7 == 0 else "" for i, e in enumerate(series)]}]
    if s["topTracks"]:
        sections += [{"type": "header", "title": "Top songs"},
                     {"type": "songs", "songs": [t["song"] for t in s["topTracks"][:10]], "numbered": True}]
    if s["topArtists"]:
        top = s["topArtists"][0]["seconds"] or 1
        sections += [{"type": "header", "title": "Top artists"}, {"type": "ranks", "items": [
            {"title": a["name"], "subtitle": f"{listening(a['seconds'])} · {a['plays']} plays",
             "fraction": a["seconds"] / top, "cover": a.get("coverArt", ""), "circle": True} for a in s["topArtists"][:10]]}]
    if s["topGenres"]:
        top = max(g["seconds"] for g in s["topGenres"]) or 1
        sections += [{"type": "header", "title": "Top genres"}, {"type": "ranks", "items": [
            {"title": g["name"], "subtitle": listening(g["seconds"]), "fraction": g["seconds"] / top} for g in s["topGenres"][:8]]},
            {"type": "text", "small": True, "text": "Genres come from your library tags, Deezer and MusicBrainz, looked up in the background. New songs fill in over time."}]
    else:
        sections.append({"type": "text", "text": "Genres will appear here once the server has looked up the music you play."})
    if s["topAlbums"]:
        sections += [{"type": "header", "title": "Top albums"}, {"type": "cards", "items": [
            {"kind": "album", "id": a.get("albumId", ""), "title": a["name"], "subtitle": a["artist"], "cover": a.get("coverArt", "")}
            for a in s["topAlbums"][:10]]}]
    sections.append({"type": "text", "text": f"Skip rate {round(s['skipRate'] * 100)}% · longest streak {s['longestStreak']} days · {s['activeDays']} active days"})
    return {"title": "Your activity", "sections": sections}


def manifest(features: list[str], name: str = "Selfstream") -> dict:
    """What this server offers. The app builds its tabs and hides what is missing from this."""
    have = set(features)
    tabs = [{"id": "home", "title": "Home", "icon": "home", "kind": "screen", "screen": "home"},
            {"id": "search", "title": "Search", "icon": "search", "kind": "search"},
            {"id": "library", "title": "Library", "icon": "library", "kind": "library"}]
    if "discover" in have:
        tabs.append({"id": "discover", "title": "Discover", "icon": "discover", "kind": "screen", "screen": "discover"})
    strings = {"searchHint": "Search your library and all of YouTube Music.\nSongs you have not downloaded stream instantly; like one to keep it."}
    return {"name": name, "version": 1, "generated": int(time.time()), "features": sorted(have), "tabs": tabs,
            "sections": list(SECTION_TYPES), "strings": strings}
