#!/usr/bin/env bash
# harden_network.sh - keep the listener Pi on the wifi, and keep a record if
# it falls off. Run once, as pete, on the Pi. Safe to run again.
#
# Written after 4 November: the station kept identifying birds all day, but
# from about 08:00 nothing outside could reach it, so the app and the Frame
# froze until the Pi was restarted. With no log kept across restarts, the
# cause could not be proved; a wifi drop fits everything that was seen.
#
#   1. keeps a log that survives restarts (capped at 200 MB)
#   2. turns off wifi power saving, which is known to drop Pi connections
#   3. adds a watchdog: every 5 minutes, if the router doesn't answer, the
#      wifi is turned off and on; if that fails, the Pi restarts - at most
#      once every 6 hours, so a router that's down all day can't send the Pi
#      round in circles
#   4. puts a time limit on the app-publishing and picture-making jobs, so a
#      stuck run can't silently block them for the rest of the day
#
# Usage:  bash harden_network.sh
set -euo pipefail

[ "$(id -u)" -ne 0 ] || { echo "Run as pete, not root (it uses sudo itself)."; exit 1; }

echo "==> 1. a log that survives restarts (capped at 200 MB)"
sudo mkdir -p /etc/systemd/journald.conf.d /var/log/journal
printf '[Journal]\nStorage=persistent\nSystemMaxUse=200M\n' \
  | sudo tee /etc/systemd/journald.conf.d/50-southacre.conf >/dev/null
sudo systemd-tmpfiles --create --prefix /var/log/journal
sudo systemctl restart systemd-journald
echo "   done"

echo "==> 2. wifi power saving off"
CONN="$(nmcli -t -f NAME,TYPE connection show --active | awk -F: '$2 ~ /wireless/ {print $1; exit}')"
if [ -n "${CONN}" ]; then
  sudo nmcli connection modify "${CONN}" 802-11-wireless.powersave 2
  echo "   set for the '${CONN}' connection (from the next reconnect or restart)"
else
  echo "   no active wifi connection found - skipped"
fi
if command -v iw >/dev/null && sudo iw dev wlan0 set power_save off 2>/dev/null; then
  echo "   and switched off now"
fi

echo "==> 3. the wifi watchdog"
sudo tee /usr/local/bin/wifi-watchdog >/dev/null <<'WATCHDOG'
#!/bin/sh
# Every 5 minutes: does the router answer? If not, turn the wifi off and on.
# If that hasn't helped after a minute, restart the Pi - but no more than once
# every 6 hours. Everything it does is logged: journalctl -t wifi-watchdog
router() {
  gw="$(ip route show default 2>/dev/null | awk '{print $3; exit}')"
  echo "${gw:-192.168.4.1}"
}
ping -c 3 -W 5 "$(router)" >/dev/null 2>&1 && exit 0
logger -t wifi-watchdog "router $(router) not answering - turning the wifi off and on"
nmcli radio wifi off
sleep 5
nmcli radio wifi on
sleep 60
if ping -c 3 -W 5 "$(router)" >/dev/null 2>&1; then
  logger -t wifi-watchdog "back on the wifi"
  exit 0
fi
stamp=/var/lib/wifi-watchdog.last-restart
if [ ! -e "$stamp" ] || [ $(( $(date +%s) - $(stat -c %Y "$stamp") )) -gt 21600 ]; then
  touch "$stamp"
  logger -t wifi-watchdog "still off the wifi - restarting the Pi"
  systemctl reboot
else
  logger -t wifi-watchdog "still off the wifi; restarted within 6 hours, so waiting"
fi
WATCHDOG
sudo chmod 755 /usr/local/bin/wifi-watchdog

sudo tee /etc/systemd/system/wifi-watchdog.service >/dev/null <<'UNIT'
[Unit]
Description=Reconnect the wifi if the router stops answering

[Service]
Type=oneshot
ExecStart=/usr/local/bin/wifi-watchdog
TimeoutStartSec=5min
UNIT

sudo tee /etc/systemd/system/wifi-watchdog.timer >/dev/null <<'UNIT'
[Unit]
Description=Check the wifi every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
UNIT
echo "   installed"

echo "==> 4. time limits on the app and picture jobs"
for unit in birdweb-publish:10min birdframe-render:15min; do
  name="${unit%%:*}"; limit="${unit##*:}"
  sudo mkdir -p "/etc/systemd/system/${name}.service.d"
  printf '[Service]\nTimeoutStartSec=%s\n' "${limit}" \
    | sudo tee "/etc/systemd/system/${name}.service.d/50-timeout.conf" >/dev/null
  echo "   ${name}: ${limit}"
done

sudo systemctl daemon-reload
sudo systemctl enable --now wifi-watchdog.timer >/dev/null 2>&1

echo
echo "==> check"
[ -n "${CONN}" ] && echo "   wifi power saving: $(nmcli -g 802-11-wireless.powersave connection show "${CONN}") (2 = off)"
echo "   watchdog timer: $(systemctl is-active wifi-watchdog.timer)"
echo "   log kept across restarts: $( [ -d /var/log/journal ] && echo yes || echo no)"
echo
echo "All done. To see if the watchdog ever has to act:  journalctl -t wifi-watchdog"
