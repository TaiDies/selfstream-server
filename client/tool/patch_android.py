"""Patch the Android project that `flutter create` generates (run from the client/ folder in CI).

Adds what a background music player needs: permissions, the audio service + media button receiver,
cleartext HTTP (the server is usually on a home network), AudioServiceActivity, and minSdk 24.
Kept as a script so the generated platform folders never need to be committed.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "android" / "app"
errors = []


def patch_manifest() -> None:
    path = ROOT / "src" / "main" / "AndroidManifest.xml"
    text = path.read_text(encoding="utf-8")
    if "com.ryanheise.audioservice.AudioService" in text:
        return
    if "xmlns:tools" not in text:
        text = text.replace("<manifest ", '<manifest xmlns:tools="http://schemas.android.com/tools" ', 1)
    perms = [
        "android.permission.INTERNET",
        "android.permission.WAKE_LOCK",
        "android.permission.FOREGROUND_SERVICE",
        "android.permission.FOREGROUND_SERVICE_MEDIA_PLAYBACK",
        "android.permission.POST_NOTIFICATIONS",
    ]
    lines = "".join(
        f'    <uses-permission android:name="{p}"/>\n' for p in perms if f'"{p}"' not in text
    )
    text = text.replace("<application", lines + "    <application", 1)
    text = text.replace("<application", '<application android:usesCleartextTraffic="true"', 1)
    text = re.sub(r'android:label="[^"]*"', 'android:label="Selfstream"', text, count=1)
    components = """
        <service android:name="com.ryanheise.audioservice.AudioService"
            android:foregroundServiceType="mediaPlayback"
            android:exported="true" tools:ignore="Instantiatable">
            <intent-filter>
                <action android:name="android.media.browse.MediaBrowserService"/>
            </intent-filter>
        </service>
        <receiver android:name="com.ryanheise.audioservice.MediaButtonReceiver"
            android:exported="true" tools:ignore="Instantiatable">
            <intent-filter>
                <action android:name="android.intent.action.MEDIA_BUTTON"/>
            </intent-filter>
        </receiver>
    """
    text = text.replace("</application>", components + "</application>", 1)
    path.write_text(text, encoding="utf-8")
    print("patched AndroidManifest.xml")


def patch_activity() -> None:
    found = list((ROOT / "src" / "main").rglob("MainActivity.*"))
    if not found:
        errors.append("MainActivity not found")
        return
    for path in found:
        text = path.read_text(encoding="utf-8")
        text = text.replace("io.flutter.embedding.android.FlutterActivity", "com.ryanheise.audioservice.AudioServiceActivity")
        text = re.sub(r"\bFlutterActivity\b", "AudioServiceActivity", text)
        path.write_text(text, encoding="utf-8")
        print(f"patched {path.name}")


def patch_gradle() -> None:
    for name in ("build.gradle.kts", "build.gradle"):
        path = ROOT / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        new = re.sub(r"minSdk(Version)?\s*=?\s*flutter\.minSdkVersion", "minSdk = 24" if name.endswith(".kts") else "minSdkVersion 24", text)
        if new == text and "minSdk" not in text:
            errors.append(f"could not set minSdk in {name}")
        path.write_text(new, encoding="utf-8")
        print(f"patched {name}")
        return
    errors.append("no app build.gradle found")


for step in (patch_manifest, patch_activity, patch_gradle):
    step()
if errors:
    print("ERRORS:", *errors, sep="\n  ")
    sys.exit(1)
