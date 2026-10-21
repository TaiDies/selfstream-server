"""Admin page: /selfstream/admin

Only Navidrome accounts with the administrator role can sign in (the login is checked against Navidrome itself).
The settings an admin changes are saved to SETTINGS_FILE and applied at once, no restart needed; they win over the
values in /etc/selfstream/selfstream.env. "Reset" removes a saved value, so the env file value applies again.
"""
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

import core
import subsonic

store = None  # the Store, set at startup (main.py)

PREFIX = "selfstream/admin"
SETTINGS_FILE = Path(os.environ.get("SETTINGS_FILE", str(core.STATE_FILE.with_name("selfstream-settings.json"))))
COOKIES_FILE = Path(os.environ.get("COOKIES_FILE", str(core.STATE_FILE.with_name("cookies.txt"))))
SESSION_SECONDS = 8 * 3600
MAX_COOKIES_BYTES = 1_000_000


def _s(key, label, kind, group, help="", **kw):
    return {"key": key, "label": label, "type": kind, "group": group, "help": help, **kw}


SETTINGS = [
    _s("REMOTE_ENABLED", "YouTube results in apps", "bool", "YouTube and search",
       "Search in the apps also finds YouTube tracks (marked as cloud). Play them to stream. Off = your library only."),
    _s("SEARCH_SOURCE", "Search source", "choice", "YouTube and search",
       "YouTube Music finds songs with the real artist, album and cover. YouTube finds any video.",
       choices=[["ytmusic", "YouTube Music (songs)"], ["youtube", "YouTube (any video)"]]),
    _s("REMOTE_RESULTS", "Results per search", "int", "YouTube and search", "How many YouTube tracks a search adds.", min=1, max=30),
    _s("REMOTE_MAX_SECONDS", "Longest track (seconds)", "int", "YouTube and search",
       "Results longer than this (mixes, podcasts, streams) are skipped.", min=60, max=7200),
    _s("REMOTE_WAIT_SECONDS", "Search wait (seconds)", "float", "YouTube and search",
       "How long a search waits for YouTube before answering with library results only.", min=1, max=30),
    _s("REMOTE_TITLE_PREFIX", "Marker for cloud tracks", "text", "YouTube and search",
       "Optional text in front of the title of tracks that are not downloaded yet, for example a cloud symbol.", maxlen=8),
    _s("REMOTE_BROWSE", "YouTube artists and albums", "bool", "YouTube and search",
       "Show YouTube artists and albums in search and let apps open them."),
    _s("REMOTE_ARTISTS", "Artists per search", "int", "YouTube and search", "", min=0, max=10),
    _s("REMOTE_ALBUMS", "Albums per search", "int", "YouTube and search", "", min=0, max=20),

    _s("ALLOW_LIKE_DOWNLOAD", "Allow \"like downloads to the server\"", "bool", "Downloads",
       "Off = the switch is removed from the apps and liking a YouTube track never downloads it."),
    _s("LIKE_DOWNLOADS", "Liking downloads by default", "bool", "Downloads",
       "What a like does for apps that do not say (plain Subsonic apps). Needs the option above."),
    _s("FOLLOW_DOWNLOADS", "Following downloads new music", "bool", "Downloads",
       "Following an artist, album or playlist also downloads what is new in it. Off = it only keeps a list."),
    _s("AUDIO_FORMAT", "Download format", "choice", "Downloads", "Applies to new downloads.",
       choices=[[f, f] for f in ("opus", "mp3", "m4a", "flac", "aac", "vorbis")]),
    _s("EMBED_COVER", "Embed cover in files", "bool", "Downloads", "Writes a square cover image into each downloaded file."),
    _s("DOWNLOAD_WAIT_SECONDS", "Download wait (seconds)", "int", "Downloads",
       "How long streaming a not yet downloaded track waits for its download.", min=30, max=3600),
    _s("YTDLP_UPDATE_HOURS", "Update yt-dlp every (hours)", "float", "Downloads", "", min=1, max=168),

    _s("DISCORD_CLIENT_ID", "Discord application ID", "text", "Apps",
       "Lets the Windows app show what you play on Discord. Create one at discord.com/developers/applications. Empty = off.",
       maxlen=25, pattern=r"^(\d{15,25})?$"),
    _s("ALLOW_USER_RENAME", "Users can change their own username", "bool", "Apps",
       "Navidrome only lets administrators change usernames. With this on and a delegate admin account set below, the server "
       "makes the change for users who ask for it in the app."),
    _s("EXTENSION_API", "Selfstream features in apps", "bool", "Apps",
       "Off = this server behaves like a plain Navidrome: apps lose Discover, following, extra playlists and more."),

    _s("PROXY_LOG", "Log every app request", "bool", "Logging", "Writes one line per request to the log (passwords are hidden)."),
]
SPEC = {s["key"]: s for s in SETTINGS}
DEFAULTS = {k: getattr(core, k) for k in SPEC}  # what the env file / built-in defaults gave at startup
overrides: dict = {}


# --------------------------------------------------------------- settings
def coerce(spec: dict, value):
    """Check one value against its setting, return it in the right type or raise ValueError."""
    kind = spec["type"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError("must be on or off")
        return value
    if kind in ("int", "float"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("must be a number")
        if kind == "int":
            if not float(value).is_integer():
                raise ValueError("must be a whole number")
            value = int(value)
        else:
            value = float(value)
        if not spec["min"] <= value <= spec["max"]:
            raise ValueError(f"must be between {spec['min']} and {spec['max']}")
        return value
    if kind == "choice":
        if value not in [c[0] for c in spec["choices"]]:
            raise ValueError("not one of the choices")
        return value
    if kind == "text":
        if not isinstance(value, str):
            raise ValueError("must be text")
        value = value.strip()
        if len(value) > spec.get("maxlen", 200):
            raise ValueError("too long")
        if spec.get("pattern") and not re.match(spec["pattern"], value):
            raise ValueError("not valid")
        return value
    raise ValueError("unknown type")


def load_overrides() -> None:
    """Called at startup: apply the saved admin settings over the env file values."""
    overrides.clear()
    try:
        data = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        spec = SPEC.get(key)
        if not spec:
            continue
        try:
            overrides[key] = coerce(spec, value)
        except ValueError:
            continue
        setattr(core, key, overrides[key])
    apply_cookies()


def _save() -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides, indent=1))
    os.replace(tmp, SETTINGS_FILE)


def change(changes: dict) -> dict:
    """Apply {key: value, key: None (= back to the default)}. All or nothing; returns {key: problem} on errors."""
    problems, new = {}, {}
    for key, value in changes.items():
        spec = SPEC.get(key)
        if not spec:
            problems[key] = "unknown setting"
            continue
        if value is None:
            new[key] = None
            continue
        try:
            new[key] = coerce(spec, value)
        except ValueError as e:
            problems[key] = str(e)
    if problems:
        return problems
    for key, value in new.items():
        if value is None:
            overrides.pop(key, None)
            setattr(core, key, DEFAULTS[key])
        else:
            overrides[key] = value
            setattr(core, key, value)
    _save()
    return {}


def settings_view() -> list[dict]:
    groups: dict[str, list] = {}
    for spec in SETTINGS:
        item = {k: v for k, v in spec.items() if k != "group"}
        item.update(value=getattr(core, spec["key"]), default=DEFAULTS[spec["key"]], overridden=spec["key"] in overrides)
        groups.setdefault(spec["group"], []).append(item)
    return [{"name": n, "items": i} for n, i in groups.items()]


# ---------------------------------------------------------------- cookies
def apply_cookies() -> None:
    """yt-dlp signs in to YouTube with the saved cookies file (unless the env file already names one)."""
    args = [a for a in core.YTDLP_EXTRA]
    if "--cookies" in args:
        i = args.index("--cookies")
        if i + 1 < len(args) and args[i + 1] == str(COOKIES_FILE):
            del args[i : i + 2]
    if COOKIES_FILE.exists() and "--cookies" not in args:
        args = ["--cookies", str(COOKIES_FILE)] + args
    core.YTDLP_EXTRA = args


def cookies_info() -> dict:
    try:
        st = COOKIES_FILE.stat()
    except OSError:
        return {"present": False}
    text = COOKIES_FILE.read_text(errors="ignore")
    return {"present": True, "bytes": st.st_size, "modified": int(st.st_mtime),
            "youtube": ".youtube.com" in text or "youtube.com" in text}


def save_cookies(text: str) -> str | None:
    """Store a Netscape cookies.txt. Returns a problem text or None."""
    text = text.replace("\r\n", "\n").strip("\n") + "\n"
    if len(text.encode()) > MAX_COOKIES_BYTES:
        return "That file is too big for a cookies.txt."
    lines = [l for l in text.split("\n") if l.strip() and not l.startswith("#") or l.startswith("#HttpOnly_")]
    if not lines or not all(len(l.split("\t")) == 7 for l in lines):
        return "That does not look like a cookies.txt in the Netscape format (7 tab-separated columns per line)."
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = COOKIES_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, COOKIES_FILE)
    apply_cookies()
    return None


def remove_cookies() -> None:
    COOKIES_FILE.unlink(missing_ok=True)
    apply_cookies()


# ------------------------------------------------------- delegate admin
# Navidrome lets only administrators change a username. A user asks the server (proving who they are), and the
# server makes the change with this administrator account, which an admin saved on the admin page.
DELEGATE_FILE = Path(os.environ.get("DELEGATE_FILE", str(core.STATE_FILE.with_name("delegate.json"))))
USERNAME_OK = re.compile(r"^[^\s\x00-\x1f][^\x00-\x1f]{0,62}[^\s\x00-\x1f]$|^[^\s\x00-\x1f]$")


def delegate_load() -> tuple[str, str] | None:
    try:
        d = json.loads(DELEGATE_FILE.read_text())
        return (d["user"], d["password"]) if d.get("user") and d.get("password") else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def delegate_info() -> dict:
    d = delegate_load()
    return {"set": d is not None, "user": d[0] if d else "", "enabled": core.ALLOW_USER_RENAME}


def delegate_save(user: str, password: str) -> None:
    DELEGATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = DELEGATE_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"user": user, "password": password}, f)
    os.replace(tmp, DELEGATE_FILE)


async def rename_as_admin(client, old: str, new: str) -> None:
    """Rename a Navidrome account for a user who asked for it. Raises NdError with something fit to show."""
    if not core.ALLOW_USER_RENAME:
        raise NdError(403, "The server admin has turned off username changes.")
    new = new.strip()
    if not USERNAME_OK.match(new):
        raise NdError(400, "That username is not valid.")
    cred = delegate_load()
    if cred is None:
        raise NdError(503, "Username changes are not set up on this server yet. Ask the server admin.")
    token = await navidrome_token(client, *cred)
    if not token:
        raise NdError(503, "The server's admin account for username changes does not work any more. Tell the server admin.")
    users = await nd(client, token, "GET", "/api/user", params=[("_start", "0"), ("_end", "1000")])
    users = users if isinstance(users, list) else []
    if any(u.get("userName", "").lower() == new.lower() and u.get("userName") != old for u in users):
        raise NdError(409, "That username is already taken.")
    me = next((u for u in users if u.get("userName") == old), None)
    if me is None:
        raise NdError(404, "Account not found.")
    if new == old:
        return
    full = {k: v for k, v in me.items() if k != "password"}  # the whole record: admin role etc. stay as they are
    full["userName"] = new
    full["name"] = new
    await nd(client, token, "PUT", f"/api/user/{me['id']}", full)
    after = await nd(client, token, "GET", f"/api/user/{me['id']}")
    if after.get("userName") != new:
        raise NdError(502, "Navidrome did not change the username.")
    if store is not None:
        store.rename_user(old, new)
    if cred[0] == old:
        delegate_save(new, cred[1])
    core.log(f"account {old} renamed to {new} at the user's request")


# ------------------------------------------------------------------- auth
_sessions: dict[str, tuple[str, float, str]] = {}  # token -> (user, expires, Navidrome token)
COOKIE = "ss_admin"


async def is_navidrome_admin(client, user: str, password: str) -> bool:
    """Ask Navidrome itself: the login must work and the account must have the administrator role."""
    salt = secrets.token_hex(6)
    token = hashlib.md5((password + salt).encode()).hexdigest()
    params = [("u", user), ("t", token), ("s", salt), ("v", subsonic.API_VERSION), ("c", "selfstream-admin"), ("f", "json"),
              ("username", user)]
    try:
        resp = await client.send(client.build_request("GET", "/rest/getUser", params=params))
        data = json.loads(resp.content)["subsonic-response"]
        return resp.status_code == 200 and data["status"] == "ok" and data["user"].get("adminRole") is True
    except Exception:
        return False


async def navidrome_token(client, user: str, password: str) -> str:
    """Navidrome's own login token (for its account API); '' when it cannot be had."""
    try:
        resp = await client.send(client.build_request("POST", "/auth/login", headers={"content-type": "application/json"},
                                                      content=json.dumps({"username": user, "password": password}).encode()))
        return str(json.loads(resp.content).get("token", "")) if resp.status_code == 200 else ""
    except Exception:
        return ""


def _entry(request: Request):
    token = request.cookies.get(COOKIE, "")
    entry = _sessions.get(token)
    if not entry:
        return None
    if entry[1] < time.time():
        _sessions.pop(token, None)
        return None
    return entry


def session_user(request: Request) -> str | None:
    entry = _entry(request)
    return entry[0] if entry else None


# --------------------------------------------------------------- accounts
class NdError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


async def nd(client, token: str, method: str, path: str, body=None, params=None):
    """One call to Navidrome's account API as the signed-in administrator."""
    if not token:
        raise NdError(401, "Sign out and in again to manage accounts.")
    headers = {"x-nd-authorization": f"Bearer {token}", "content-type": "application/json"}
    content = json.dumps(body).encode() if body is not None else None
    try:
        resp = await client.send(client.build_request(method, path, params=params or [], headers=headers, content=content))
    except Exception as e:
        raise NdError(502, f"Navidrome is not reachable ({type(e).__name__})")
    try:
        data = json.loads(resp.content) if resp.content else {}
    except ValueError:
        data = {}
    if resp.status_code == 401:
        raise NdError(401, "Sign out and in again to manage accounts.")
    if resp.status_code >= 400:
        errs = data.get("errors") if isinstance(data, dict) else None
        msg = ", ".join(map(str, errs.values())) if isinstance(errs, dict) and errs else (data.get("error") if isinstance(data, dict) else None)
        raise NdError(resp.status_code, str(msg or f"Navidrome refused ({resp.status_code})"))
    return data


def _account(u: dict, counts: dict) -> dict:
    name = u.get("userName", "")
    return {"id": u.get("id"), "userName": name, "name": u.get("name", ""), "isAdmin": bool(u.get("isAdmin")),
            "lastLoginAt": u.get("lastLoginAt") or u.get("lastAccessAt") or "",
            "data": counts.get(name, {"playlists": 0, "plays": 0, "follows": 0})}


async def accounts_view(client, token: str) -> dict:
    users = await nd(client, token, "GET", "/api/user", params=[("_start", "0"), ("_end", "500"), ("_sort", "userName"), ("_order", "ASC")])
    counts = store.user_counts() if store is not None else {}
    listed = [_account(u, counts) for u in users] if isinstance(users, list) else []
    names = {a["userName"] for a in listed}
    orphans = [{"userName": n, "data": c, **store.user_details(n)} for n, c in counts.items()
               if n not in names and n not in ("apikey", "\0swap")]
    orphans.sort(key=lambda o: o.get("lastPlay") or 0, reverse=True)
    return {"accounts": listed, "orphans": orphans}


# ---------------------------------------------------------------- handler
def _json(data, status=200, headers=None) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store", **(headers or {})})


def _err(status: int, message: str, headers=None) -> JSONResponse:
    return _json({"error": message}, status, headers)


async def handle(request: Request, path: str, client) -> Response | None:
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        return None
    sub = path[len(PREFIX):].strip("/")
    method = request.method

    if sub == "":
        if method not in ("GET", "HEAD"):
            return _err(405, "method not allowed")
        return HTMLResponse(PAGE, headers={
            "Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                                       "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"})

    if method != "GET" and request.headers.get("x-selfstream-admin") != "1":
        return _err(403, "missing header")  # a form on another site cannot set this

    if sub == "login" and method == "POST":
        body = await _body(request)
        user, password = str(body.get("username", "")).strip(), str(body.get("password", ""))
        if not user or not password or not await is_navidrome_admin(client, user, password):
            return _err(401, "Wrong username or password, or this account is not an administrator.")
        token = secrets.token_urlsafe(32)
        _sessions[token] = (user, time.time() + SESSION_SECONDS, await navidrome_token(client, user, password))
        core._prune(_sessions, 50)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        res = _json({"user": user})
        res.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict", secure=secure,
                       path="/" + PREFIX)
        return res

    entry = _entry(request)
    if entry is None:
        return _err(401, "Sign in first.")
    user, nd_token = entry[0], entry[2]

    if sub == "logout" and method == "POST":
        _sessions.pop(request.cookies.get(COOKIE, ""), None)
        res = _json({"ok": True})
        res.delete_cookie(COOKIE, path="/" + PREFIX)
        return res
    if sub == "settings" and method == "GET":
        return _json({"user": user, "groups": settings_view()})
    if sub == "settings" and method == "POST":
        changes = (await _body(request)).get("changes")
        if not isinstance(changes, dict) or not changes:
            return _err(400, "nothing to change")
        problems = change(changes)
        if problems:
            return _json({"error": "Some values are not valid.", "problems": problems}, 400)
        core.log(f"admin: {user} changed {', '.join(sorted(changes))}")
        return _json({"user": user, "groups": settings_view()})
    if sub.startswith("accounts") or sub == "transfer":
        try:
            return await _accounts(request, sub, method, user, nd_token, client)
        except NdError as e:
            return _err(e.status if e.status in (400, 401, 403, 404, 409, 422) else 502, e.message)
    if sub == "delegate" and method == "GET":
        return _json(delegate_info())
    if sub == "delegate" and method == "POST":
        b = await _body(request)
        name, password = str(b.get("username", "")).strip(), str(b.get("password", ""))
        if not name or not password or not await is_navidrome_admin(client, name, password) or not await navidrome_token(client, name, password):
            return _err(400, "That account does not work, or it is not an administrator.")
        delegate_save(name, password)
        core.log(f"admin: {user} set {name} as the account that makes username changes")
        return _json(delegate_info())
    if sub == "delegate" and method == "DELETE":
        DELEGATE_FILE.unlink(missing_ok=True)
        core.log(f"admin: {user} removed the delegate admin account")
        return _json(delegate_info())
    if sub == "status" and method == "GET":
        return _json(await status())
    if sub == "cookies" and method == "POST":
        problem = save_cookies(str((await _body(request)).get("content", "")))
        if problem:
            return _err(400, problem)
        core.log(f"admin: {user} saved the YouTube cookies")
        return _json(cookies_info())
    if sub == "cookies" and method == "DELETE":
        remove_cookies()
        core.log(f"admin: {user} removed the YouTube cookies")
        return _json(cookies_info())
    if sub == "ytdlp-update" and method == "POST":
        code, out, err = await core.run(core.YTDLP, "-U", timeout=180)
        core.log(f"admin: {user} updated yt-dlp (exit {code})")
        return _json({"ok": code == 0, "output": "\n".join((out + err).strip().splitlines()[-8:])})
    return _err(404, "not found")


async def _accounts(request: Request, sub: str, method: str, admin_user: str, token: str, client) -> Response:
    body = await _body(request) if method in ("POST", "PUT") else {}
    if sub == "accounts" and method == "GET":
        return _json(await accounts_view(client, token))
    if sub == "accounts" and method == "POST":
        name, password = str(body.get("userName", "")).strip(), str(body.get("password", ""))
        if not name or not password:
            return _err(400, "Give the new account a username and a password.")
        await nd(client, token, "POST", "/api/user", {"userName": name, "name": name, "password": password, "isAdmin": bool(body.get("isAdmin"))})
        core.log(f"admin: {admin_user} created the account {name}")
        return _json(await accounts_view(client, token))
    m = re.fullmatch(r"accounts/([\w-]+)", sub)
    if m and method == "PUT":
        uid = m.group(1)
        cur = await nd(client, token, "GET", f"/api/user/{uid}")
        old = cur.get("userName", "")
        new_name = str(body.get("userName", old)).strip() or old
        full = {k: v for k, v in cur.items() if k != "password"}  # Navidrome saves the whole record
        full["userName"] = new_name
        if new_name != old:
            full["name"] = new_name
        if "isAdmin" in body:
            if old == admin_user and not body["isAdmin"]:
                return _err(400, "You cannot take the administrator role from the account you are signed in with.")
            full["isAdmin"] = bool(body["isAdmin"])
        if body.get("password"):
            full["password"] = str(body["password"])
        await nd(client, token, "PUT", f"/api/user/{uid}", full)
        if new_name != old:
            after = await nd(client, token, "GET", f"/api/user/{uid}")
            if after.get("userName") != new_name:
                return _err(409, "Navidrome did not change the username.")
            if store is not None:
                store.rename_user(old, new_name)  # playlists, history and follows go with the name
        core.log(f"admin: {admin_user} changed the account {old}" + (f" (now {new_name})" if new_name != old else ""))
        return _json(await accounts_view(client, token))
    if m and method == "DELETE":
        uid = m.group(1)
        cur = await nd(client, token, "GET", f"/api/user/{uid}")
        if cur.get("userName") == admin_user:
            return _err(400, "You cannot delete the account you are signed in with.")
        await nd(client, token, "DELETE", f"/api/user/{uid}")
        core.log(f"admin: {admin_user} deleted the account {cur.get('userName')}")
        return _json(await accounts_view(client, token))
    if sub == "transfer" and method == "POST":
        src, dst, mode = str(body.get("from", "")), str(body.get("to", "")), str(body.get("mode", "move"))
        if store is None:
            return _err(503, "database not ready")
        if not src or not dst or src == dst:
            return _err(400, "Pick two different accounts.")
        if mode not in ("move", "copy", "swap"):
            return _err(400, "Mode must be move, copy or swap.")
        store.transfer(src, dst, mode)
        core.log(f"admin: {admin_user} {mode} saved data {src} -> {dst}")
        return _json(await accounts_view(client, token))
    return _err(404, "not found")


async def _body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def status() -> dict:
    _, ytdlp, _ = await core.run(core.YTDLP, "--version", timeout=20)
    _, deno, _ = await core.run("deno", "--version", timeout=20)
    running = sum(1 for j in core.jobs.values() if j.get("status") not in ("done", "error", "failed"))
    return {
        "yt-dlp": ytdlp.strip() or "not found",
        "deno": (deno.splitlines() or ["not found"])[0],
        "cookies": cookies_info(),
        "liked_cloud_tracks": len(core.starred_remote),
        "downloads_running": running,
        "music_dir_writable": os.access(core.MUSIC_DIR, os.W_OK),
        "state_dir_writable": os.access(core.STATE_FILE.parent, os.W_OK),
        "settings_file": str(SETTINGS_FILE),
    }


PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>selfstream admin</title>
<style>
:root{--bg:#111;--card:#1b1b1b;--line:#2c2c2c;--text:#eee;--mute:#999;--acc:#1db954;--on:#000;--bad:#ff6b6b}
@media (prefers-color-scheme:light){:root{--bg:#f4f4f4;--card:#fff;--line:#ddd;--text:#161616;--mute:#666;--acc:#138a3d;--on:#fff;--bad:#c0392b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 system-ui,sans-serif}
main{max-width:780px;margin:0 auto;padding:20px 16px 96px}
h1{font-size:22px;margin:8px 0 4px}h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--mute);margin:26px 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:4px 16px}
.row{display:flex;gap:16px;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}.row .t{flex:1;min-width:0}.row b{font-weight:600}
.help{color:var(--mute);font-size:13px;margin-top:2px}.def{font-size:12px;color:var(--mute)}
.def a{color:var(--acc);cursor:pointer;margin-left:6px}
input[type=text],input[type=number],input[type=password],select,textarea{background:var(--bg);color:var(--text);border:1px solid var(--line);
 border-radius:8px;padding:8px 10px;font:inherit;min-width:0}
input[type=number]{width:110px}input[type=text].w,select{width:200px}textarea{width:100%;height:110px;font:12px monospace}
.sw{position:relative;width:44px;height:26px;flex:none}.sw input{opacity:0;position:absolute;inset:0;margin:0;cursor:pointer;z-index:1}
.sw i{position:absolute;inset:0;background:var(--line);border-radius:13px;transition:.15s}
.sw i:after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;border-radius:50%;background:#fff;transition:.15s}
.sw input:checked+i{background:var(--acc)}.sw input:checked+i:after{transform:translateX(18px)}
button{font:inherit;padding:8px 14px;border-radius:8px;border:0;background:var(--acc);color:var(--on);cursor:pointer;font-weight:600}
button.g{background:transparent;color:var(--text);border:1px solid var(--line)}button:disabled{opacity:.4;cursor:default}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:12px 16px;display:none}
.bar div{max-width:780px;margin:0 auto;display:flex;gap:10px;align-items:center;justify-content:space-between}
.err{color:var(--bad);font-size:13px;margin-top:4px}.ok{color:var(--acc)}
#login{max-width:360px;margin:12vh auto 0}#login .card{padding:18px}#login input{width:100%;margin:6px 0}
.top{display:flex;justify-content:space-between;align-items:center;gap:12px}.hide{display:none!important}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 16px;padding:12px 0;font-size:14px}.kv span:nth-child(odd){color:var(--mute)}
pre{white-space:pre-wrap;font-size:12px;color:var(--mute);margin:8px 0 0}
</style>
<main>
<div id=login class=hide><h1>selfstream admin</h1><p class=help>Sign in with a Navidrome administrator account.</p>
 <div class=card><input id=u type=text placeholder=Username autocomplete=username><input id=p type=password placeholder=Password autocomplete=current-password>
 <div id=lerr class=err></div><p><button id=lgo style="width:100%">Sign in</button></p></div></div>
<div id=app class=hide>
 <div class=top><div><h1>Server settings</h1><div class=help id=who></div></div><button class=g id=out>Sign out</button></div>
 <div id=groups></div>
 <h2>Accounts</h2>
 <div class=card><div id=accts></div>
  <div class=row style="display:block"><b>New account</b>
   <p><input type=text class=w id=nu placeholder=Username autocomplete=off> <input type=password class=w id=np placeholder=Password autocomplete=new-password>
   <label class=help><input type=checkbox id=na> administrator</label> <button id=ncreate>Create</button></p><div id=aerr class=err></div></div></div>
 <h2>Username changes by users</h2>
 <div class=card><div class=row style="display:block"><div class=help id=dstate></div>
  <p class=help>Navidrome lets only administrators change a username. Give the server an administrator account and it will make the change for any user who asks for it in the app (turn this off with the "Users can change their own username" setting). The password is kept on the server only, never shown again.</p>
  <p><input type=text class=w id=du placeholder="Administrator username" autocomplete=off> <input type=password class=w id=dp placeholder=Password autocomplete=new-password>
  <button id=dsave>Save</button> <button class=g id=ddel>Remove</button></p><div id=derr class=err></div></div></div>
 <h2>Lost data</h2>
 <div class=card><div class=row style="display:block"><div class=help>Saved data whose username no longer exists in Navidrome, usually because the account was renamed before data followed names. Give it to the account it belongs to.</div></div><div id=lost></div></div>
 <h2>Saved data between accounts</h2>
 <div class=card><div class=row style="display:block"><div class=help>Playlists, listening history, followed artists/albums and settings are kept per username. Hand them from one account to another here. Likes live in Navidrome and are not moved.</div>
  <p><select id=tf></select> &rarr; <select id=tt></select>
   <select id=tm><option value=move>Move (the first is left empty)</option><option value=copy>Copy (the first keeps its own)</option><option value=swap>Swap the two</option></select>
   <button id=tgo>Go</button></p><div id=terr class=err></div></div></div>
 <h2>YouTube sign-in (cookies)</h2>
 <div class=card><div class=row style="display:block"><div class=help id=cstate></div>
  <p class=help>Only needed when YouTube asks to sign in. Export your cookies with a browser extension as <b>cookies.txt</b> (Netscape format) and paste it or choose the file. It is stored on the server and never shown again.</p>
  <textarea id=ctext placeholder="# Netscape HTTP Cookie File"></textarea>
  <p><input type=file id=cfile accept=".txt,text/plain"> <button id=csave>Save cookies</button> <button class=g id=cdel>Remove</button></p><div id=cerr class=err></div></div></div>
 <h2>Server</h2>
 <div class=card><div class=kv id=status></div>
  <div class=row><div class=t><b>Update yt-dlp now</b><div class=help>Fixes most "can't download" problems after YouTube changes.</div><pre id=uout></pre></div><button id=upd class=g>Update</button></div></div>
</div>
</main>
<div class=bar id=bar><div><span id=count></span><span><button class=g id=discard>Discard</button> <button id=save>Save changes</button></span></div></div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path,method='GET',body){
  const r=await fetch('/selfstream/admin/'+path,{method,headers:{'x-selfstream-admin':'1','content-type':'application/json'},body:body?JSON.stringify(body):undefined,credentials:'same-origin'});
  let j={};try{j=await r.json()}catch(e){}
  return {ok:r.ok,status:r.status,data:j};
}
let groups=[],edits={};
function fmt(it,v){return it.type==='bool'?(v?'on':'off'):(v===''?'empty':String(v))}
function control(it){
  const v=it.key in edits?edits[it.key]:it.value;
  if(it.type==='bool')return `<label class=sw><input type=checkbox data-k="${it.key}" ${v?'checked':''}><i></i></label>`;
  if(it.type==='choice')return `<select data-k="${it.key}">${it.choices.map(c=>`<option value="${esc(c[0])}" ${c[0]===v?'selected':''}>${esc(c[1])}</option>`).join('')}</select>`;
  if(it.type==='text')return `<input class=w type=text data-k="${it.key}" value="${esc(v)}">`;
  return `<input type=number data-k="${it.key}" value="${esc(v)}" min="${it.min}" max="${it.max}" step="${it.type==='int'?1:'any'}">`;
}
function render(){
  $('groups').innerHTML=groups.map(g=>`<h2>${esc(g.name)}</h2><div class=card>${g.items.map(it=>{
    const cur=it.key in edits?edits[it.key]:it.value;
    return `<div class=row><div class=t><b>${esc(it.label)}</b>${it.help?`<div class=help>${esc(it.help)}</div>`:''}
     <div class=def>${it.overridden?`Changed here · default ${esc(fmt(it,it.default))}<a data-reset="${it.key}">Reset</a>`:'Default'}${it.key in edits?' · <span class=ok>not saved</span>':''}</div>
     <div class=err id="e_${it.key}"></div></div>${control(it)}</div>`}).join('')}</div>`).join('');
  const n=Object.keys(edits).length;$('bar').style.display=n?'block':'none';$('count').textContent=n+(n===1?' change':' changes')+' not saved';
}
function find(k){for(const g of groups)for(const it of g.items)if(it.key===k)return it}
$('groups').addEventListener('input',e=>{
  const k=e.target.dataset.k;if(!k)return;const it=find(k);
  let v=it.type==='bool'?e.target.checked:e.target.value;
  if(it.type==='int'||it.type==='float'){if(v==='')return;v=Number(v)}
  if(v===it.value)delete edits[k];else edits[k]=v;
  const n=Object.keys(edits).length;$('bar').style.display=n?'block':'none';$('count').textContent=n+(n===1?' change':' changes')+' not saved';
});
$('groups').addEventListener('click',async e=>{
  const k=e.target.dataset.reset;if(!k)return;
  const r=await api('settings','POST',{changes:{[k]:null}});if(r.ok){delete edits[k];groups=r.data.groups;render()}
});
$('discard').onclick=()=>{edits={};render()};
$('save').onclick=async()=>{
  $('save').disabled=true;
  const r=await api('settings','POST',{changes:edits});$('save').disabled=false;
  document.querySelectorAll('.err[id^=e_]').forEach(x=>x.textContent='');
  if(r.ok){edits={};groups=r.data.groups;render();return}
  if(r.status===401)return show(false);
  for(const [k,m] of Object.entries(r.data.problems||{})){const el=$('e_'+k);if(el)el.textContent=m}
};
async function loadStatus(){
  const r=await api('status');if(!r.ok)return;const s=r.data,c=s.cookies;
  $('status').innerHTML=[['yt-dlp',s['yt-dlp']],['Deno (JavaScript for YouTube)',s.deno],['Liked YouTube tracks',s.liked_cloud_tracks],['Downloads running',s.downloads_running],
   ['Music folder writable',s.music_dir_writable?'yes':'NO'],['Settings saved in',s.settings_file]].map(([a,b])=>`<span>${esc(a)}</span><span>${esc(b)}</span>`).join('');
  $('cstate').innerHTML=c.present?`Cookies are saved (${(c.bytes/1024).toFixed(1)} KB${c.youtube?'':', but none for youtube.com'}).`:'No cookies saved: yt-dlp does not sign in.';
}
$('csave').onclick=async()=>{
  $('cerr').textContent='';const r=await api('cookies','POST',{content:$('ctext').value});
  if(r.ok){$('ctext').value='';loadStatus()}else $('cerr').textContent=r.data.error||'Could not save';
};
$('cdel').onclick=async()=>{await api('cookies','DELETE');loadStatus()};
$('cfile').onchange=async e=>{const f=e.target.files[0];if(f)$('ctext').value=await f.text()};
$('upd').onclick=async()=>{
  $('upd').disabled=true;$('uout').textContent='Updating...';
  const r=await api('ytdlp-update','POST');$('upd').disabled=false;$('uout').textContent=r.data.output||r.data.error||'';loadStatus();
};
let accts=[],orphans=[];
function dataText(d){return d.playlists+' playlists · '+d.plays+' plays · '+d.follows+' follows'}
function ago(ts){if(!ts)return 'never played';const d=(Date.now()/1000-ts)/86400;return d<1?'played today':d<2?'played yesterday':'last played '+Math.floor(d)+' days ago'}
function renderLost(){
  if(!orphans.length){$('lost').innerHTML='<div class=row><div class=t><span class=help>Nothing lost: every saved name matches an account.</span></div></div>';return}
  const opts=accts.map(a=>`<option value="${esc(a.userName)}">${esc(a.userName)}${a.data.playlists+a.data.plays+a.data.follows===0?' (empty)':''}</option>`).join('');
  $('lost').innerHTML=orphans.map(o=>`<div class=row><div class=t><b>${esc(o.userName)}</b>
   <div class=help>${esc(dataText(o.data))} · ${esc(ago(o.lastPlay))}</div>
   ${o.playlistNames.length?`<div class=help>Playlists: ${esc(o.playlistNames.join(', '))}</div>`:''}
   ${o.recentSongs.length?`<div class=help>Recently: ${esc(o.recentSongs.join(' · '))}</div>`:''}</div>
   <span><select data-from="${esc(o.userName)}">${opts}</select> <button data-give="${esc(o.userName)}">Give to</button></span></div>`).join('');
}
$('lost').addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b||!b.dataset.give)return;
  const from=b.dataset.give,to=b.parentElement.querySelector('select').value;
  if(!to||!confirm('Give the saved data of "'+from+'" to the account "'+to+'"?'))return;
  applyAccts(await api('transfer','POST',{from,to,mode:'move'}),'aerr');
});
async function loadDelegate(){
  const r=await api('delegate');if(!r.ok)return;
  $('dstate').textContent=r.data.set?'Set: usernames are changed with the account "'+r.data.user+'".':'Not set: users who are not administrators cannot change their username.';
}
$('dsave').onclick=async()=>{
  $('derr').textContent='';const r=await api('delegate','POST',{username:$('du').value,password:$('dp').value});
  if(r.ok){$('dp').value='';loadDelegate()}else $('derr').textContent=r.data.error||'Could not save';
};
$('ddel').onclick=async()=>{await api('delegate','DELETE');loadDelegate()};
function renderAccts(){
  renderLost();
  $('accts').innerHTML=accts.map(a=>`<div class=row><div class=t><b>${esc(a.userName)}</b>${a.isAdmin?' <span class=ok>admin</span>':''}
   <div class=help>${esc(dataText(a.data))}</div></div>
   <span><button class=g data-a=rename data-id="${esc(a.id)}" data-n="${esc(a.userName)}">Rename</button>
   <button class=g data-a=pw data-id="${esc(a.id)}" data-n="${esc(a.userName)}">Password</button>
   <button class=g data-a=admin data-id="${esc(a.id)}" data-n="${esc(a.userName)}" data-v="${a.isAdmin?0:1}">${a.isAdmin?'Remove admin':'Make admin'}</button>
   <button class=g data-a=del data-id="${esc(a.id)}" data-n="${esc(a.userName)}">Delete</button></span></div>`).join('');
  const names=[...accts.map(a=>a.userName),...orphans.map(o=>o.userName)];
  const opt=n=>{const o=orphans.find(x=>x.userName===n);return `<option value="${esc(n)}">${esc(n)}${o?' (no account, '+esc(dataText(o.data))+')':''}</option>`};
  const f=$('tf').value,t=$('tt').value;
  $('tf').innerHTML=names.map(opt).join('');$('tt').innerHTML=names.map(opt).join('');
  if(names.includes(f))$('tf').value=f;if(names.includes(t))$('tt').value=t;else if(names.length>1)$('tt').selectedIndex=1;
}
async function loadAccts(){
  const r=await api('accounts');
  if(r.ok){accts=r.data.accounts;orphans=r.data.orphans;renderAccts();$('aerr').textContent=''}else $('aerr').textContent=r.data.error||'Could not load accounts';
}
function applyAccts(r,el){
  if(r.ok){accts=r.data.accounts;orphans=r.data.orphans;renderAccts();$(el).textContent='';return true}
  $(el).textContent=r.data.error||'Failed';return false;
}
$('accts').addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b||!b.dataset.a)return;
  const id=encodeURIComponent(b.dataset.id),n=b.dataset.n;let r;
  if(b.dataset.a==='rename'){const v=prompt('New username for '+n+' (their saved data follows the name):',n);if(!v||v===n)return;r=await api('accounts/'+id,'PUT',{userName:v})}
  else if(b.dataset.a==='pw'){const v=prompt('New password for '+n+':');if(!v)return;r=await api('accounts/'+id,'PUT',{password:v})}
  else if(b.dataset.a==='admin'){r=await api('accounts/'+id,'PUT',{isAdmin:b.dataset.v==='1'})}
  else if(b.dataset.a==='del'){if(!confirm('Delete the account '+n+'? Their saved data stays on the server.'))return;r=await api('accounts/'+id,'DELETE')}
  applyAccts(r,'aerr');
});
$('ncreate').onclick=async()=>{
  const r=await api('accounts','POST',{userName:$('nu').value,password:$('np').value,isAdmin:$('na').checked});
  if(applyAccts(r,'aerr')){$('nu').value='';$('np').value='';$('na').checked=false}
};
$('tgo').onclick=async()=>{
  const f=$('tf').value,t=$('tt').value,m=$('tm').value;
  if(!confirm((m==='swap'?'Swap':m==='copy'?'Copy':'Move')+' saved data: '+f+' and '+t+'?'))return;
  applyAccts(await api('transfer','POST',{from:f,to:t,mode:m}),'terr');
};
function show(signedIn){$('login').classList.toggle('hide',signedIn);$('app').classList.toggle('hide',!signedIn);if(!signedIn)$('bar').style.display='none'}
async function enter(){
  const r=await api('settings');
  if(!r.ok){show(false);return}
  groups=r.data.groups;edits={};$('who').textContent='Signed in as '+r.data.user;show(true);render();loadStatus();loadAccts();loadDelegate();
}
async function login(){
  $('lerr').textContent='';$('lgo').disabled=true;
  const r=await api('login','POST',{username:$('u').value,password:$('p').value});$('lgo').disabled=false;
  if(r.ok){$('p').value='';enter()}else $('lerr').textContent=r.data.error||'Could not sign in';
}
$('lgo').onclick=login;$('p').onkeydown=e=>{if(e.key==='Enter')login()};$('u').onkeydown=e=>{if(e.key==='Enter')$('p').focus()};
$('out').onclick=async()=>{await api('logout','POST');show(false)};
enter();
</script></html>"""
