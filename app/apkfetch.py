"""Keeps the newest Android test build on this server, so the app can update itself from it.

Run by `update` (and the installer). It does nothing unless /etc/selfstream/android-update.conf exists:

    REPO=<owner>/<private repo with the Android releases>
    TOKEN=<GitHub fine-grained token, read-only: Contents + Metadata>

The newest release asset named selfstream-<version>-<build>.apk is downloaded into /var/lib/selfstream/app (older ones are
removed). It never fails the update: any problem is printed and skipped.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONF = Path(os.environ.get("ANDROID_UPDATE_CONF", "/etc/selfstream/android-update.conf"))
DEST = Path(os.environ.get("ANDROID_APP_DIR", "/var/lib/selfstream/app"))
APK_NAME = re.compile(r"^selfstream-(\d+(?:\.\d+)*)(?:\+\d+)?-(\d+)\.apk$")
API = "https://api.github.com"


def read_conf(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip("'\"")
    return out


def pick(releases: list) -> tuple[int, str, int] | None:
    """(build, file name, asset id) of the APK with the highest build number among the releases' assets."""
    best = None
    for rel in releases if isinstance(releases, list) else []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        for a in rel.get("assets") or []:
            m = APK_NAME.match(str(a.get("name") or ""))
            if m and isinstance(a.get("id"), int) and (best is None or int(m.group(2)) > best[0]):
                best = (int(m.group(2)), a["name"], a["id"])
    return best


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get_json(url: str, token: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "selfstream"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _download(url: str, token: str, dest: Path) -> None:
    """GitHub answers an asset request with a redirect to a signed address; the token must not be sent on to it."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream",
                                               "User-Agent": "selfstream"})
    try:
        resp = urllib.request.build_opener(_NoRedirect).open(req, timeout=60)
    except urllib.error.HTTPError as e:
        if e.code not in (301, 302, 303, 307, 308) or not e.headers.get("Location"):
            raise
        resp = urllib.request.urlopen(urllib.request.Request(e.headers["Location"], headers={"User-Agent": "selfstream"}), timeout=300)
    with resp, open(dest, "wb") as f:
        while chunk := resp.read(1 << 20):
            f.write(chunk)


def run(get_json=_get_json, download=_download) -> int:
    if not CONF.is_file():
        return 0  # not set up: nothing to do
    conf = read_conf(CONF.read_text())
    repo, token = conf.get("REPO", ""), conf.get("TOKEN", "")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not token:
        print(f"  (Android build: {CONF} needs REPO=owner/name and TOKEN=...)")
        return 0
    try:
        found = pick(get_json(f"{API}/repos/{repo}/releases?per_page=30", token))
        if not found:
            print("  (Android build: no selfstream-<version>-<build>.apk found in the releases)")
            return 0
        build, name, asset = found
        DEST.mkdir(parents=True, exist_ok=True)
        target = DEST / name
        if target.is_file() and target.stat().st_size > 1_000_000:
            print(f"  Android build {build} is already on this server")
        else:
            part = DEST / (name + ".part")
            download(f"{API}/repos/{repo}/releases/assets/{asset}", token, part)
            if part.stat().st_size < 1_000_000:  # an error page, not an app
                part.unlink()
                print("  (Android build: the download was too small to be an app, kept the old one)")
                return 0
            os.replace(part, target)
            print(f"  Downloaded the Android build {build} ({name})")
        for old in DEST.glob("selfstream-*.apk"):
            if old != target and APK_NAME.match(old.name):
                old.unlink()
        for f in DEST.glob("*.apk"):
            try:
                os.chmod(f, 0o644)
            except OSError:
                pass
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"  (Android build: could not fetch it: {type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(run())
