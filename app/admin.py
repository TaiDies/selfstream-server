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


# ------------------------------------------------------------------- auth
_sessions: dict[str, tuple[str, float]] = {}  # token -> (user, expires)
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


def session_user(request: Request) -> str | None:
    token = request.cookies.get(COOKIE, "")
    entry = _sessions.get(token)
    if not entry:
        return None
    if entry[1] < time.time():
        _sessions.pop(token, None)
        return None
    return entry[0]


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
        _sessions[token] = (user, time.time() + SESSION_SECONDS)
        core._prune(_sessions, 50)
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        res = _json({"user": user})
        res.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict", secure=secure,
                       path="/" + PREFIX)
        return res

    user = session_user(request)
    if user is None:
        return _err(401, "Sign in first.")

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
function show(signedIn){$('login').classList.toggle('hide',signedIn);$('app').classList.toggle('hide',!signedIn);if(!signedIn)$('bar').style.display='none'}
async function enter(){
  const r=await api('settings');
  if(!r.ok){show(false);return}
  groups=r.data.groups;edits={};$('who').textContent='Signed in as '+r.data.user;show(true);render();loadStatus();
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
