#!/usr/bin/env bash
# install_frame_server.sh — run ONCE on the listener Pi, after BirdNET-Pi is up.
#
# Sets up two small systemd services for the wireless Inky Frame:
#   birdframe-web     serves ~/birdframe on port 8090 (the Frame fetches today.jpg)
#   birdframe-render  runs render_frame.py every 15 minutes via a timer
#
# It sits beside the station's own web server rather than inside it: the
# AvianVisitors Caddy config is fail-closed and would 404 an unknown path.
#
# Usage (from the folder holding render_frame.py):
#   bash install_frame_server.sh
# Then check:  http://<pi-ip>:8090/today.jpg  from another device.
set -euo pipefail

USER_NAME="$(id -un)"
HOME_DIR="$(eval echo "~${USER_NAME}")"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME_DIR}/birdframe-app"
OUT_DIR="${HOME_DIR}/birdframe"
PORT=8090

[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user, not root (sudo is used where needed)."; exit 1; }
[ -f "${SRC_DIR}/render_frame.py" ] || { echo "render_frame.py not found next to this script."; exit 1; }

echo "==> installing renderer to ${APP_DIR}"
mkdir -p "${APP_DIR}" "${OUT_DIR}"
cp "${SRC_DIR}/render_frame.py" "${APP_DIR}/"
if [ -f "${SRC_DIR}/south-acre-nest.png" ]; then
  cp "${SRC_DIR}/south-acre-nest.png" "${APP_DIR}/"
  echo "   house-style nest installed"
else
  echo "   (no south-acre-nest.png alongside - the maker's nest will be used)"
fi
python3 -c "import PIL" 2>/dev/null || sudo apt-get install -y python3-pil

echo "==> writing systemd units"
sudo tee /etc/systemd/system/birdframe-web.service >/dev/null <<EOF
[Unit]
Description=Bird frame image server (port ${PORT})
After=network-online.target
Wants=network-online.target

[Service]
User=${USER_NAME}
WorkingDirectory=${OUT_DIR}
ExecStart=/usr/bin/python3 -m http.server ${PORT} --directory ${OUT_DIR}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo tee /etc/systemd/system/birdframe-render.service >/dev/null <<EOF
[Unit]
Description=Render today's birds for the Inky Frame
After=network-online.target

[Service]
Type=oneshot
User=${USER_NAME}
ExecStart=/usr/bin/python3 ${APP_DIR}/render_frame.py --out ${OUT_DIR}
EOF

sudo tee /etc/systemd/system/birdframe-render.timer >/dev/null <<EOF
[Unit]
Description=Render bird frame every 15 minutes

[Timer]
OnBootSec=3min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now birdframe-web.service birdframe-render.timer

echo "==> first render (forced, ignores quiet hours)"
/usr/bin/python3 "${APP_DIR}/render_frame.py" --out "${OUT_DIR}" --force || \
  echo "   (first render failed — is BirdNET-Pi running and has it heard anything yet?)"

IP="$(hostname -I | awk '{print $1}')"
echo
echo "Done. Open this from your phone or laptop on the same wifi:"
echo "    http://${IP}:${PORT}/today.jpg"
echo "Put that exact address into IMAGE_URL in the Frame's main.py,"
echo "and give the Pi a fixed address (DHCP reservation) in your router."
