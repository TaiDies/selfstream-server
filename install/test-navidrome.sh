#!/usr/bin/env bash
# A second, plain Navidrome next to selfstream, for trying the app WITHOUT the selfstream extras.
# Run inside the selfstream container (as root):
#   curl -fsSL https://raw.githubusercontent.com/TaiDies/selfstream-server/main/install/test-navidrome.sh | bash
# It listens on port 4534 (change with PORT=...), shows the same /music folder, keeps its own users and
# data in /var/lib/navidrome-test, and has no selfstream proxy: no YouTube, playlists sync, Discover, etc.
# Remove it again with:  curl -fsSL <same url> | ACTION=remove bash
set -Eeuo pipefail
PORT="${PORT:-4534}"
DATA=/var/lib/navidrome-test
UNIT=/etc/systemd/system/navidrome-test.service

[[ $EUID -eq 0 ]] || { echo "run this as root"; exit 1; }

if [[ "${ACTION:-install}" == "remove" ]]; then
  systemctl disable --now navidrome-test 2>/dev/null || true
  rm -rf "$UNIT" "$DATA"
  systemctl daemon-reload
  echo "Removed the test Navidrome."
  exit 0
fi

[[ -x /opt/navidrome/navidrome ]] || { echo "Navidrome is not installed here (/opt/navidrome/navidrome)"; exit 1; }
mkdir -p "$DATA"
chown selfstream:selfstream "$DATA" 2>/dev/null || true

cat > "$UNIT" <<EOF
[Unit]
Description=Navidrome (plain test copy, no selfstream)
After=network.target

[Service]
User=selfstream
Group=selfstream
Environment=ND_MUSICFOLDER=/music
Environment=ND_DATAFOLDER=$DATA
Environment=ND_ADDRESS=0.0.0.0
Environment=ND_PORT=$PORT
Environment="ND_SCANSCHEDULE=@every 1m"
Environment="ND_SCANNER_SCHEDULE=@every 1m"
ExecStart=/opt/navidrome/navidrome
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now navidrome-test >/dev/null 2>&1
for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$PORT/ping" >/dev/null 2>&1 && break; sleep 1; done
systemctl is-active --quiet navidrome-test || { echo "it did not start: journalctl -u navidrome-test"; exit 1; }

IP=$(hostname -I | awk '{print $1}')
echo
echo "Plain Navidrome is running:  http://$IP:$PORT"
echo "Open it once in a browser and create an admin user (this copy has its own users),"
echo "then sign in to the app with that address and login."
echo "Remove it later:  ACTION=remove bash install/test-navidrome.sh"
