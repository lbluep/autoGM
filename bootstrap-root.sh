#!/bin/bash
# Root bootstrap for the Xingcheng (腥城) Android environment.
# Run as root:  pkexec /home/fengg/playground/autogame/xingcheng-android/bootstrap-root.sh
# Records every change so uninstall.sh can reverse it.

set -u
export DEBIAN_FRONTEND=noninteractive
LOG=/home/fengg/playground/autogame/xingcheng-android/logs/bootstrap.log
mkdir -p /home/fengg/playground/autogame/xingcheng-android/logs
exec > >(tee -a "$LOG") 2>&1
echo "=== bootstrap start $(date -Is) ==="

echo "--- [1/4] apt install waydroid ---"
apt-get install -y waydroid 2>&1 | tail -30
echo "apt_exit=$?"

echo "--- [2/4] load binder_linux ---"
if ! lsmod | grep -q '^binder_linux'; then
  modprobe binder_linux devices="binder,hwbinder,vndbinder"
fi
lsmod | grep binder_linux
ls -l /dev/binder /dev/hwbinder /dev/vndbinder 2>&1

echo "--- [3/4] persist module across boots ---"
printf 'binder_linux\n' > /etc/modules-load.d/waydroid-binder.conf
printf 'options binder_linux devices="binder,hwbinder,vndbinder"\n' > /etc/modprobe.d/waydroid-binder.conf

echo "--- [4/4] waydroid-container service ---"
systemctl enable --now waydroid-container
systemctl status waydroid-container --no-pager -l | head -15

echo "--- versions ---"
waydroid version 2>&1
dpkg -l | awk '/waydroid|lxc|gbinder/ {print $1, $2, $3}'
echo "=== bootstrap done $(date -Is) ==="
