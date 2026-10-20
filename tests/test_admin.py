"""The admin page: only Navidrome administrators get in, settings are validated, saved and applied live."""
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from test_remote import FakeClient, FakeResp, asgi  # noqa: E402  (also sets the test environment)

from starlette.applications import Starlette  # noqa: E402
from starlette.responses import Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

import admin  # noqa: E402
import core  # noqa: E402

USERS = {"admin": ("pw1", True), "bob": ("pw2", False)}


class Navidrome(FakeClient):
    async def send(self, req, stream=False, follow_redirects=False):
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
        self.client = Navidrome()
        self.saved = {k: getattr(core, k) for k in admin.SPEC}
        self.saved_extra = list(core.YTDLP_EXTRA)
        admin.SETTINGS_FILE.unlink(missing_ok=True)
        admin.COOKIES_FILE.unlink(missing_ok=True)
        admin.overrides.clear()
        admin._sessions.clear()

        async def endpoint(request):
            res = await admin.handle(request, request.path_params["path"], self.client)
            return res if res is not None else Response("PASSTHROUGH")

        self.app = Starlette(routes=[Route("/{path:path}", endpoint, methods=["GET", "POST", "DELETE"])])

    def tearDown(self):
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
