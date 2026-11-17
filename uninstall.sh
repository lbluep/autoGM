#!/bin/bash
# Remove everything this project added to the system.
# Usage: pkexec ./uninstall.sh [--keep-data] [--purge-project]
set -u
KEEP_DATA=0
PURGE_PROJECT=0
for a in "$@"; do
  case "$a" in
    --keep-data) KEEP_DATA=1 ;;
    --purge-project) PURGE_PROJECT=1 ;;
  esac
done
LOG=/home/fengg/playground/autogame/xingcheng-android/logs/uninstall.log
mkdir -p "$(dirname "$LOG")"
exec > >(tee -a "$LOG") 2>&1
echo "=== uninstall $(date -Is) ==="

echo "--- stop waydroid ---"
pkill -f 'waydroid session' 2>/dev/null
pkill -f 'waydroid show-full-ui' 2>/dev/null
pkill -f 'waydroid container' 2>/dev/null
systemctl disable --now waydroid-container 2>&1
systemctl unmask waydroid-container 2>&1

echo "--- purge packages installed for waydroid ---"
apt-get purge -y waydroid python3-gbinder libgbinder libglibutil \
  lxc lxc-utils liblxc1 liblxc-common lxcfs libpam-cgfs \
  bridge-utils uidmap python3-pip 2>&1 | tail -10
apt-get autoremove -y 2>&1 | tail -5

echo "--- pip packages ---"
python3 -m pip uninstall -y InquirerPy tqdm requests pfzy prompt_toolkit 2>&1 | tail -5

echo "--- cached libndk archive ---"
rm -rf /home/root/.cache/waydroid-script

echo "--- binder config ---"
rm -f /etc/modules-load.d/waydroid-binder.conf
rm -f /etc/modprobe.d/waydroid-binder.conf
for n in binder hwbinder vndbinder; do
  mountpoint -q "/dev/$n" && umount "/dev/$n"
  rm -f "/dev/$n"
done
mountpoint -q /dev/binderfs && umount /dev/binderfs
rmdir /dev/binderfs 2>/dev/null
rmmod binder_linux 2>/dev/null
echo "binder cleaned"

echo "--- waydroid data ---"
if [ "$KEEP_DATA" = "1" ]; then
  echo "keeping /var/lib/waydroid (images + config) as requested"
else
  rm -rf /var/lib/waydroid
  echo "removed /var/lib/waydroid"
fi

if [ "$PURGE_PROJECT" = "1" ]; then
  echo "removing project dir (this log is inside it)"
  rm -rf /home/fengg/playground/autogame/xingcheng-android
fi
echo "=== uninstall done $(date -Is) ==="
