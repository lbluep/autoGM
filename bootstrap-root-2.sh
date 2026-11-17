#!/bin/bash
# Fix pass 2: complete waydroid install, create binder devices, unmask service.
set -u
export DEBIAN_FRONTEND=noninteractive
LOG=/home/fengg/playground/autogame/xingcheng-android/logs/bootstrap2.log
exec > >(tee -a "$LOG") 2>&1
echo "=== bootstrap2 start $(date -Is) ==="

echo "--- [1/5] unmask waydroid-container ---"
systemctl unmask waydroid-container.service 2>&1
systemctl daemon-reload

echo "--- [2/5] apt-get update (waydroid repo) ---"
apt-get update 2>&1 | tail -8

echo "--- [3/5] install waydroid ---"
apt-get install -y --fix-missing waydroid 2>&1 | tail -25
echo "install_rc=${PIPESTATUS[0]}"

if ! command -v waydroid >/dev/null; then
  echo "--- fallback: direct fetch python3-gbinder ---"
  curl -sL -o /tmp/python3-gbinder.deb https://repo.waydro.id/dists/jammy/python3-gbinder_1.3.1_amd64.deb
  ls -l /tmp/python3-gbinder.deb
  dpkg -i /tmp/python3-gbinder.deb 2>&1 | tail -10
  apt-get install -y waydroid 2>&1 | tail -20
fi

echo "--- [4/5] binder devices ---"
rmmod binder_linux 2>/dev/null
modprobe binder_linux devices="binder,hwbinder,vndbinder"
sleep 1
ls -l /dev/binder /dev/hwbinder /dev/vndbinder 2>&1
if [ ! -e /dev/binder ]; then
  echo "static nodes missing, trying binderfs"
  mkdir -p /dev/binderfs
  mount -t binder binder /dev/binderfs 2>&1
  ls -l /dev/binderfs 2>&1
fi

echo "--- [5/5] start service ---"
systemctl enable --now waydroid-container 2>&1
systemctl status waydroid-container --no-pager -l 2>&1 | head -12
waydroid version 2>&1
echo "=== bootstrap2 done $(date -Is) ==="
