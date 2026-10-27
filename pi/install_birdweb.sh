#!/usr/bin/env bash
# install_birdweb.sh - run on the listener Pi to start sending today's birds to
# Pete's app. Safe to run more than once.
#
#   First run:  makes the Pi a key for the south-acre-birds repository and
#               prints it. Add it on GitHub as a deploy key (with write access).
#   Second run: checks GitHub accepts the key, installs publish_web.py and a
#               15-minute timer, and makes the first publish.
#
# Usage (from the folder holding publish_web.py):
#   bash install_birdweb.sh
set -euo pipefail

REPO_PATH="ianpeake-ai/south-acre-birds"
KEY="${HOME}/.ssh/birdweb_deploy"
APP_DIR="${HOME}/birdframe-app"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user, not root."; exit 1; }
[ -f "${SRC_DIR}/publish_web.py" ] || { echo "publish_web.py not found next to this script."; exit 1; }
[ -f "${APP_DIR}/render_frame.py" ] || { echo "Install the frame server first (render_frame.py missing)."; exit 1; }

mkdir -p "${HOME}/.ssh" && chmod 700 "${HOME}/.ssh"

if [ ! -f "${KEY}" ]; then
  ssh-keygen -q -t ed25519 -N "" -C "birdear deploy key (south-acre-birds)" -f "${KEY}"
fi

if ! grep -q "Host github-birdweb" "${HOME}/.ssh/config" 2>/dev/null; then
  cat >> "${HOME}/.ssh/config" <<EOF

Host github-birdweb
    HostName github.com
    User git
    IdentityFile ${KEY}
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
EOF
  chmod 600 "${HOME}/.ssh/config"
fi

echo "==> checking GitHub accepts the Pi's key"
auth="$(ssh -T github-birdweb 2>&1 || true)"     # ssh -T always "fails"; read what it says
if ! grep -q "successfully authenticated" <<<"${auth}"; then
  echo
  echo "Not yet. On GitHub, open ${REPO_PATH} -> Settings -> Deploy keys -> Add deploy key."
  echo "Title: birdear.   Tick 'Allow write access'.   Paste this whole line as the key:"
  echo
  cat "${KEY}.pub"
  echo
  echo "Then run this script again."
  exit 0
fi
echo "   yes"

echo "==> installing publish_web.py"
cp "${SRC_DIR}/publish_web.py" "${APP_DIR}/"
sed -i 's/\r$//' "${APP_DIR}/publish_web.py"
python3 -c "import PIL.features as f; import sys; sys.exit(0 if f.check('webp') else 1)" \
  || sudo apt-get install -y python3-pil

echo "==> writing the 15-minute timer"
sudo tee /etc/systemd/system/birdweb-publish.service >/dev/null <<EOF
[Unit]
Description=Send today's birds to the South Acre app
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$(id -un)
Nice=10
ExecStart=/usr/bin/python3 ${APP_DIR}/publish_web.py
EOF

sudo tee /etc/systemd/system/birdweb-publish.timer >/dev/null <<EOF
[Unit]
Description=Send today's birds to the South Acre app every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload

echo "==> first publish"
/usr/bin/python3 "${APP_DIR}/publish_web.py" --force

# start the timer only after the first publish, so the two can't collide
sudo systemctl enable --now birdweb-publish.timer

echo
echo "Done. If this is the first time, turn on GitHub Pages:"
echo "   ${REPO_PATH} -> Settings -> Pages -> Deploy from a branch -> gh-pages / (root) -> Save"
echo "The app is then at https://ianpeake-ai.github.io/south-acre-birds/"
