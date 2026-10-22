"""The admin page: only Navidrome administrators get in, settings are validated, saved and applied live."""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_remote import FakeClient, FakeResp, asgi  # noqa: E402  (also sets the test environment)

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

import admin  # noqa: E402
import core  # noqa: E402
import store  # noqa: E402

USERS = {"admin": ("pw1", True), "bob": ("pw2", False)}


class Navidrome(FakeClient):
    """Also Navidrome's own account API (/auth/login, /api/user), as an administrator sees it."""

    def __init__(self):
        super().__init__()
        self.accounts = {
            "1": {"id": "1", "userName": "admin", "name": "Admin", "isAdmin": True, "email": "a@x"},
            "2": {"id": "2", "userName": "bob", "name": "Bob", "isAdmin": False, "email": ""},
        }
        self.puts = []

    def build_request(self, method, url, params=None, headers=None, content=None):
        r = super().build_request(method, url, params, headers, content)
        r.content = content
        return r

    async def _native(self, req):
        ok = lambda data, st=200: FakeResp(st, json.dumps(data).encode(), [("Content-Type", "application/json")])
        if req.url == "/auth/login":
            b = json.loads(req.content)
            pw = USERS.get(b["username"], (None, False))[0]
            return ok({"token": "jwt-" + b["username"], "id": "1"}) if pw == b["password"] else ok({"error": "bad"}, 401)
        if req.headers.get("x-nd-authorization") != "Bearer jwt-admin":
            return ok({"error": "no"}, 401)
        if req.url == "/api/user" and req.method == "GET":
            return ok(list(self.accounts.values()))
        if req.url == "/api/user" and req.method == "POST":
            b = json.loads(req.content)
            uid = str(len(self.accounts) + 1)
            self.accounts[uid] = {"id": uid, "userName": b["userName"], "name": b["name"], "isAdmin": b["isAdmin"], "email": ""}
            return ok(self.accounts[uid])
        uid = req.url.rsplit("/", 1)[1]
        if uid not in self.accounts:
            return ok({"error": "not found"}, 404)
        if req.method == "GET":
            return ok(self.accounts[uid])
        if req.method == "PUT":
            b = json.loads(req.content)
            self.puts.append(b)
            self.accounts[uid] = {k: v for k, v in b.items() if k not in ("password", "currentPassword")}
            return ok({"id": uid})
        if req.method == "DELETE":
            del self.accounts[uid]
            return ok({"id": uid})

    async def send(self, req, stream=False, follow_redirects=False):
        if req.url == "/auth/login" or req.url.startswith("/api/user"):
            return await self._native(req)
        if req.url.removesuffix(".view") == "/rest/getUser":
            self.calls.append(req)
            p = dict(req.params)
            pw, is_admin = USERS.get(p.get("u"), (None, False))
            ok = pw is not None and p.get("t") == hashlib.md5((pw + p.get("s", "")).encode()).hexdigest()
            body = {"subsonic-response": {"status": "ok", "user": {"username": p.get("u"), "adminRole": is_admin}}} if ok else \
                   {"subsonic-response": {"status": "failed", "error": {"code": 40}}}
            return FakeResp(200, json.dumps(body).encode(), [("Content-Type", "application/json")])
        return await super().send(req, stream, follow_redirects)


class AdminTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = Navidrome()
        self.saved = {k: getattr(core, k) for k in admin.SPEC}
        self.saved_extra = list(core.YTDLP_EXTRA)
        admin.SETTINGS_FILE.unlink(missing_ok=True)
        admin.COOKIES_FILE.unlink(missing_ok=True)
        admin.DELEGATE_FILE.unlink(missing_ok=True)
        admin.overrides.clear()
        admin._sessions.clear()
        self.store = store.Store(os.path.join(self.tmp.name, "t.db"))
        admin.store = self.store

        async def endpoint(request):
            res = await admin.handle(request, request.path_params["path"], self.client)
            return res if res is not None else Response("PASSTHROUGH")

        self.app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "PUT", "DELETE"])])

    def tearDown(self):
        admin.store = None
        admin.DELEGATE_FILE.unlink(missing_ok=True)
        self.tmp.cleanup()
        for k, v in self.saved.items():
            setattr(core, k, v)
        core.YTDLP_EXTRA = self.saved_extra
        admin.SETTINGS_FILE.unlink(missing_ok=True)
        admin.COOKIES_FILE.unlink(missing_ok=True)

    async def call(self, method, sub, body=None, cookie=None, header=True):
        headers = {"content-type": "application/json"}
        if header:
            headers["x-selfstream-admin"] = "1"
        if cookie:
            headers["cookie"] = f"{admin.COOKIE}={cookie}"
        r = await asgi(self.app, method, "/selfstream/admin" + ("/" + sub if sub else ""), "", headers=headers,
                       body=json.dumps(body).encode() if body is not None else b"")
        try:
            r["json"] = json.loads(r["body"])
        except ValueError:
            r["json"] = None
        return r

    async def login(self, user="admin", password="pw1"):
        r = await self.call("POST", "login", {"username": user, "password": password})
        token = None
        if r["status"] == 200:
            token = r["headers"]["set-cookie"].split(";")[0].split("=", 1)[1]
        return r, token

    async def test_the_page_loads_without_a_login_and_holds_no_secrets(self):
        r = await self.call("GET", "")
        self.assertEqual(r["status"], 200)
        self.assertIn(b"selfstream admin", r["body"])
        self.assertEqual(r["headers"]["x-frame-options"], "DENY")
        self.assertNotIn(b"pw1", r["body"])

    async def test_other_paths_are_not_handled(self):
        r = await asgi(self.app, "GET", "/selfstream/api/jobs")
        self.assertEqual(r["body"], b"PASSTHROUGH")

    async def test_only_navidrome_administrators_can_sign_in(self):
        for user, password in (("bob", "pw2"), ("admin", "wrong"), ("nobody", "x"), ("", "")):
            r, _ = await self.login(user, password)
            self.assertEqual(r["status"], 401, user)
        self.assertEqual(r["json"]["error"], "Wrong username or password, or this account is not an administrator.")
        r, token = await self.login()
        self.assertEqual(r["status"], 200)
        self.assertIn("httponly", r["headers"]["set-cookie"].lower())
        self.assertIn("samesite=strict", r["headers"]["set-cookie"].lower())
        self.assertEqual((await self.call("GET", "settings", cookie=token))["status"], 200)

    async def test_everything_else_needs_a_session(self):
        for method, sub in (("GET", "settings"), ("GET", "status"), ("POST", "settings"), ("POST", "cookies"),
                            ("DELETE", "cookies"), ("POST", "ytdlp-update"), ("POST", "logout")):
            self.assertEqual((await self.call(method, sub, {"changes": {"PROXY_LOG": True}}))["status"], 401, sub)
        self.assertEqual((await self.call("GET", "settings", cookie="made-up"))["status"], 401)

    def seed(self):
        pl = self.store.create_playlist("bob", "Bobs mix")
        self.store.record_play("bob", {"id": "1", "title": "A", "artist": "X"}, 100, 200, ts=1_800_000_000.0)
        self.store.create_playlist("admin", "Admins")
        return pl

    async def test_accounts_list_with_their_saved_data(self):
        self.seed()
        _, token = await self.login()
        r = await self.call("GET", "accounts", cookie=token)
        self.assertEqual(r["status"], 200)
        by = {a["userName"]: a for a in r["json"]["accounts"]}
        self.assertEqual(by["bob"]["data"]["playlists"], 1)
        self.assertEqual(by["bob"]["data"]["plays"], 1)
        self.assertTrue(by["admin"]["isAdmin"])
        self.assertEqual((await self.call("GET", "accounts"))["status"], 401)

    async def test_create_change_and_delete_accounts(self):
        _, token = await self.login()
        r = await self.call("POST", "accounts", {"userName": "eve", "password": "pw", "isAdmin": False}, cookie=token)
        self.assertEqual(r["status"], 200)
        self.assertIn("eve", [a["userName"] for a in r["json"]["accounts"]])
        r = await self.call("PUT", "accounts/2", {"password": "new"}, cookie=token)
        sent = self.client.puts[-1]
        self.assertEqual(sent["password"], "new")
        self.assertEqual((sent["userName"], sent["name"], sent["isAdmin"]), ("bob", "Bob", False))  # nothing else is lost
        r = await self.call("PUT", "accounts/2", {"isAdmin": True}, cookie=token)
        self.assertTrue(self.client.accounts["2"]["isAdmin"])
        self.assertEqual((await self.call("PUT", "accounts/1", {"isAdmin": False}, cookie=token))["status"], 400)
        self.assertEqual((await self.call("DELETE", "accounts/1", cookie=token))["status"], 400)
        r = await self.call("DELETE", "accounts/2", cookie=token)
        self.assertNotIn("bob", [a["userName"] for a in r["json"]["accounts"]])

    async def test_renaming_an_account_takes_its_saved_data_along(self):
        self.seed()
        _, token = await self.login()
        r = await self.call("PUT", "accounts/2", {"userName": "robert"}, cookie=token)
        self.assertEqual(r["status"], 200)
        self.assertEqual(self.client.accounts["2"]["name"], "robert")
        self.assertEqual([p["name"] for p in self.store.playlists("robert")], ["Bobs mix"])
        self.assertEqual(self.store.playlists("bob"), [])

    async def test_lost_data_is_listed_with_hints(self):
        self.seed()
        _, token = await self.login()
        await self.call("PUT", "accounts/2", {"userName": "robert"}, cookie=token)
        self.store.create_playlist("bob", "Old name playlist")  # data saved after a rename on another path: under a name with no account
        r = await self.call("GET", "accounts", cookie=token)
        lost = r["json"]["orphans"]
        self.assertEqual([o["userName"] for o in lost], ["bob"])
        self.assertEqual(lost[0]["playlistNames"], ["Old name playlist"])
        await self.call("POST", "transfer", {"from": "bob", "to": "robert", "mode": "move"}, cookie=token)
        self.assertEqual((await self.call("GET", "accounts", cookie=token))["json"]["orphans"], [])

    async def test_a_user_who_is_not_admin_can_rename_through_the_delegate_admin(self):
        self.seed()
        with self.assertRaises(admin.NdError) as e:  # nothing set up yet
            await admin.rename_as_admin(self.client, "bob", "robert")
        self.assertEqual(e.exception.status, 503)
        _, token = await self.login()
        self.assertEqual((await self.call("POST", "delegate", {"username": "bob", "password": "pw2"}, cookie=token))["status"], 400)
        self.assertEqual((await self.call("POST", "delegate", {"username": "admin", "password": "pw1"}, cookie=token))["json"]["set"], True)
        self.assertNotIn("pw1", (await self.call("GET", "delegate", cookie=token))["body"].decode())
        await admin.rename_as_admin(self.client, "bob", "robert")
        self.assertEqual(self.client.accounts["2"]["userName"], "robert")
        self.assertFalse(self.client.accounts["2"]["isAdmin"])
        self.assertEqual([p["name"] for p in self.store.playlists("robert")], ["Bobs mix"])
        for bad, status in (("admin", 409), ("ROBERT2 ", None), ("", 400), ("a\nb", 400)):
            try:
                await admin.rename_as_admin(self.client, "robert", bad)
                self.assertIsNone(status, bad)
            except admin.NdError as err:
                self.assertEqual(err.status, status, bad)
        core.ALLOW_USER_RENAME = False
        try:
            with self.assertRaises(admin.NdError) as e:
                await admin.rename_as_admin(self.client, "robert", "other")
            self.assertEqual(e.exception.status, 403)
        finally:
            core.ALLOW_USER_RENAME = True

    async def test_the_web_page_guard_needs_an_admin_session(self):
        from starlette.requests import Request

        def req(cookie=None, header=False):
            h = []
            if cookie:
                h.append((b"cookie", f"{admin.COOKIE}={cookie}".encode()))
            if header:
                h.append((b"x-selfstream-admin", b"1"))
            return Request({"type": "http", "method": "POST", "path": "/", "headers": h, "query_string": b""})

        self.assertEqual(admin.guard_problem(req())[0], 401)
        _, token = await self.login()
        self.assertIsNone(admin.guard_problem(req(token)))
        self.assertEqual(admin.guard_problem(req(token), write=True)[0], 403)
        self.assertIsNone(admin.guard_problem(req(token, header=True), write=True))
        _, bob = await self.login("bob", "pw2")
        self.assertIsNone(bob)  # not an administrator: no session at all

    def test_only_youtube_links_may_be_downloaded_from_the_page(self):
        for ok in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://music.youtube.com/watch?v=x", "http://youtu.be/abc", "https://YOUTUBE.com/x"):
            self.assertTrue(core.is_youtube_url(ok), ok)
        for bad in ("http://192.168.1.1/admin", "http://localhost:4533/", "file:///etc/passwd", "https://evil.com/youtube.com", "https://youtube.com.evil.com/x", "ftp://youtube.com/x", ""):
            self.assertFalse(core.is_youtube_url(bad), bad)

    def test_jobs_shown_on_the_page_carry_no_paths(self):
        job = {"id": "1", "url": "u", "status": "error", "created": 1.0, "error": f"cannot write {core.MUSIC_DIR}/a/b.opus", "task": object(), "file": "/x"}
        out = core.public_job(job)
        self.assertNotIn(str(core.MUSIC_DIR), out["error"])
        self.assertNotIn("task", out)
        self.assertNotIn("file", out)

    async def test_transfer_move_copy_and_swap(self):
        self.seed()
        _, token = await self.login()
        r = await self.call("POST", "transfer", {"from": "bob", "to": "admin", "mode": "copy"}, cookie=token)
        self.assertEqual(r["status"], 200)
        self.assertEqual(len(self.store.playlists("bob")), 1)
        self.assertEqual(len(self.store.playlists("admin")), 2)
        self.assertEqual(len(self.store.history("admin")), 1)
        await self.call("POST", "transfer", {"from": "bob", "to": "carol", "mode": "move"}, cookie=token)
        self.assertEqual(self.store.playlists("bob"), [])
        self.assertEqual(len(self.store.playlists("carol")), 1)
        await self.call("POST", "transfer", {"from": "carol", "to": "admin", "mode": "swap"}, cookie=token)
        self.assertEqual(len(self.store.playlists("carol")), 2)
        self.assertEqual(len(self.store.playlists("admin")), 1)
        self.assertEqual((await self.call("POST", "transfer", {"from": "a", "to": "a"}, cookie=token))["status"], 400)

    async def test_changes_need_the_admin_header(self):
        _, token = await self.login()
        r = await self.call("POST", "settings", {"changes": {"PROXY_LOG": True}}, cookie=token, header=False)
        self.assertEqual(r["status"], 403)

    async def test_logout_ends_the_session(self):
        _, token = await self.login()
        await self.call("POST", "logout", cookie=token)
        self.assertEqual((await self.call("GET", "settings", cookie=token))["status"], 401)

    async def test_wrong_passwords_are_never_locked_out(self):
        for _ in range(20):
            await self.login("admin", "wrong")
        r, _ = await self.login()
        self.assertEqual(r["status"], 200)

    async def test_settings_are_applied_at_once_and_saved(self):
        _, token = await self.login()
        r = await self.call("POST", "settings", {"changes": {"ALLOW_LIKE_DOWNLOAD": False, "REMOTE_RESULTS": 5, "AUDIO_FORMAT": "mp3"}}, cookie=token)
        self.assertEqual(r["status"], 200)
        self.assertFalse(core.ALLOW_LIKE_DOWNLOAD)
        self.assertEqual(core.REMOTE_RESULTS, 5)
        self.assertEqual(core.AUDIO_FORMAT, "mp3")
        self.assertEqual(json.loads(admin.SETTINGS_FILE.read_text())["REMOTE_RESULTS"], 5)
        import api
        self.assertNotIn("like-download", api.features())
        items = {i["key"]: i for g in r["json"]["groups"] for i in g["items"]}
        self.assertTrue(items["REMOTE_RESULTS"]["overridden"])
        self.assertFalse(items["PROXY_LOG"]["overridden"])

    async def test_bad_values_change_nothing(self):
        _, token = await self.login()
        before = core.REMOTE_RESULTS
        for changes in ({"REMOTE_RESULTS": 999}, {"REMOTE_RESULTS": "5"}, {"AUDIO_FORMAT": "exe"}, {"PROXY_LOG": "yes"},
                        {"DISCORD_CLIENT_ID": "abc"}, {"NOT_A_SETTING": 1}, {"REMOTE_RESULTS": 5, "AUDIO_FORMAT": "exe"}):
            r = await self.call("POST", "settings", {"changes": changes}, cookie=token)
            self.assertEqual(r["status"], 400, changes)
            self.assertTrue(r["json"]["problems"])
        self.assertEqual(core.REMOTE_RESULTS, before)
        self.assertFalse(admin.SETTINGS_FILE.exists())

    async def test_reset_goes_back_to_the_default(self):
        _, token = await self.login()
        default = admin.DEFAULTS["REMOTE_RESULTS"]
        await self.call("POST", "settings", {"changes": {"REMOTE_RESULTS": default + 2}}, cookie=token)
        await self.call("POST", "settings", {"changes": {"REMOTE_RESULTS": None}}, cookie=token)
        self.assertEqual(core.REMOTE_RESULTS, default)
        self.assertNotIn("REMOTE_RESULTS", json.loads(admin.SETTINGS_FILE.read_text()))

    async def test_saved_settings_come_back_after_a_restart(self):
        admin.SETTINGS_FILE.write_text(json.dumps({"REMOTE_RESULTS": 4, "FOLLOW_DOWNLOADS": False, "bogus": 1, "AUDIO_FORMAT": "nope"}))
        admin.load_overrides()
        self.assertEqual(core.REMOTE_RESULTS, 4)
        self.assertFalse(core.FOLLOW_DOWNLOADS)
        self.assertNotEqual(core.AUDIO_FORMAT, "nope")  # an invalid saved value is ignored
        self.assertEqual(sorted(admin.overrides), ["FOLLOW_DOWNLOADS", "REMOTE_RESULTS"])

    async def test_cookies_are_checked_stored_privately_and_used_by_ytdlp(self):
        _, token = await self.login()
        bad = await self.call("POST", "cookies", {"content": "hello world"}, cookie=token)
        self.assertEqual(bad["status"], 400)
        self.assertFalse(admin.COOKIES_FILE.exists())
        good = ".youtube.com\tTRUE\t/\tTRUE\t1893456000\tSID\tabc\n#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1893456000\tHSID\tdef"
        r = await self.call("POST", "cookies", {"content": "# Netscape HTTP Cookie File\n" + good}, cookie=token)
        self.assertEqual(r["status"], 200)
        self.assertTrue(r["json"]["present"] and r["json"]["youtube"])
        self.assertNotIn("abc", r["body"].decode())  # the content is never sent back
        self.assertEqual(oct(admin.COOKIES_FILE.stat().st_mode & 0o777), "0o600")
        self.assertEqual(core.YTDLP_EXTRA[:2], ["--cookies", str(admin.COOKIES_FILE)])
        status = (await self.call("GET", "status", cookie=token))["json"]
        self.assertTrue(status["cookies"]["present"])
        self.assertNotIn("abc", json.dumps(status))
        await self.call("DELETE", "cookies", cookie=token)
        self.assertFalse(admin.COOKIES_FILE.exists())
        self.assertNotIn("--cookies", core.YTDLP_EXTRA)

    async def test_status_and_ytdlp_update(self):
        _, token = await self.login()
        status = (await self.call("GET", "status", cookie=token))["json"]
        self.assertIn("yt-dlp", status)
        self.assertTrue(status["music_dir_writable"])


if __name__ == "__main__":
    unittest.main()
