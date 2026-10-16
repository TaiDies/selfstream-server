#!/usr/bin/env bash
# A second, separate copy of the OLD setup (Navidrome + YouTube search/stream/download proxy, nothing else),
# for trying the Selfstream app against a server WITHOUT the selfstream extras.
# Run inside the selfstream container (as root):
#   curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream-server/main/install/test-navidrome.sh | bash
#
#   app  ->  :8081  proxy (YouTube search, streaming, heart = download)  ->  :4534 Navidrome (127.0.0.1 only)
#
# The proxy is the same code as the main one, started with EXTENSION_API=0: no playlists sync, history, Discover,
# Activity, Share, Discord or screens. The app sees a plain Subsonic server. It has its own users, music folder
# (/var/lib/navidrome-test/music, so downloads do not touch your real library) and database.
# Remove it again with:  curl -fsSL <same url> | ACTION=remove bash
set -Eeuo pipefail
ND_PORT="${ND_PORT:-4534}"
PORT="${PORT:-8081}"
DATA=/var/lib/navidrome-test
MUSIC="$DATA/music"
ND_UNIT=/etc/systemd/system/navidrome-test.service
PX_UNIT=/etc/systemd/system/selfstream-test.service

[[ $EUID -eq 0 ]] || { echo "run this as root"; exit 1; }

if [[ "${ACTION:-install}" == "remove" ]]; then
  systemctl disable --now selfstream-test navidrome-test 2>/dev/null || true
  rm -rf "$ND_UNIT" "$PX_UNIT" "$DATA"
  systemctl daemon-reload
  echo "Removed the test setup."
  exit 0
fi

[[ -x /opt/navidrome/navidrome ]] || { echo "Navidrome is not installed here (/opt/navidrome/navidrome)"; exit 1; }
[[ -d /opt/selfstream/app ]] || { echo "selfstream is not installed here (/opt/selfstream/app)"; exit 1; }
mkdir -p "$MUSIC"
chown -R selfstream:selfstream "$DATA"

cat > "$ND_UNIT" <<EOF
[Unit]
Description=Navidrome (test copy, behind selfstream-test)
After=network.target

[Service]
User=selfstream
Group=selfstream
Environment=ND_MUSICFOLDER=$MUSIC
Environment=ND_DATAFOLDER=$DATA
Environment=ND_ADDRESS=127.0.0.1
Environment=ND_PORT=$ND_PORT
Environment="ND_SCANSCHEDULE=@every 1m"
Environment="ND_SCANNER_SCHEDULE=@every 1m"
ExecStart=/opt/navidrome/navidrome
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# same settings as the main proxy (yt-dlp options, cookies, formats), then the test overrides
cat > "$PX_UNIT" <<EOF
[Unit]
Description=selfstream proxy, test copy (plain Subsonic, no extension API)
After=network.target navidrome-test.service
Wants=navidrome-test.service

[Service]
User=selfstream
Group=selfstream
EnvironmentFile=/etc/selfstream/selfstream.env
Environment=MUSIC_DIR=$MUSIC
Environment=NAVIDROME_URL=http://127.0.0.1:$ND_PORT
Environment=STATE_FILE=$DATA/selfstream-state.json
Environment=DB_FILE=$DATA/selfstream.db
Environment=EXTENSION_API=0
Environment=DISCORD_CLIENT_ID=
WorkingDirectory=/opt/selfstream/app
ExecStart=/opt/selfstream/venv/bin/uvicorn main:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable navidrome-test selfstream-test >/dev/null 2>&1
systemctl restart navidrome-test selfstream-test
for _ in $(seq 1 25); do curl -fsS "http://127.0.0.1:$PORT/rest/ping.view?v=1.16.1&c=t&f=json" >/dev/null 2>&1 && break; sleep 1; done
systemctl is-active --quiet navidrome-test  || { echo "Navidrome did not start: journalctl -u navidrome-test"; exit 1; }
systemctl is-active --quiet selfstream-test || { echo "The proxy did not start: journalctl -u selfstream-test"; exit 1; }
if curl -fsS "http://127.0.0.1:$PORT/selfstream/api/v1/ping" >/dev/null 2>&1; then
  echo "Warning: the extension API still answers. Run 'update' so the container has the latest server code, then run this again."
fi

IP=$(hostname -I | awk '{print $1}')
echo
echo "Test setup is running:  http://$IP:$PORT"
echo "Open it once in a browser and create an admin user (this copy has its own users and library),"
echo "then sign in to the app with that address and login."
echo "Remove it later:  curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream-server/main/install/test-navidrome.sh | ACTION=remove bash"
