#!/bin/bash
# Fix binder wiring: Waydroid cfg still references Anbox binder nodes.
set -u
LOG=/home/fengg/playground/autogame/xingcheng-android/logs/binder-fix.log
exec > >(tee -a "$LOG") 2>&1
echo "=== binder fix $(date -Is) ==="

systemctl stop waydroid-container
pkill -f 'waydroid container start' 2>/dev/null
sleep 1

# ensure binderfs mounted
if ! mountpoint -q /dev/binderfs; then
  mkdir -p /dev/binderfs
  mount -t binder binder /dev/binderfs
fi

# replace symlinks with real bind mounts so LXC can bind-mount them
for n in binder hwbinder vndbinder; do
  if [ -L "/dev/$n" ]; then rm -f "/dev/$n"; fi
  if [ ! -e "/dev/$n" ]; then touch "/dev/$n"; fi
  if ! mountpoint -q "/dev/$n"; then
    mount --bind "/dev/binderfs/$n" "/dev/$n"
  fi
done
ls -l /dev/binder /dev/hwbinder /dev/vndbinder

# point waydroid.cfg at the real binder devices
sed -i -E 's/^binder = .*/binder = binder/; s/^vndbinder = .*/vndbinder = vndbinder/; s/^hwbinder = .*/hwbinder = hwbinder/' /var/lib/waydroid/waydroid.cfg
grep -E '^(binder|vndbinder|hwbinder) =' /var/lib/waydroid/waydroid.cfg

systemctl start waydroid-container
sleep 5
systemctl is-active waydroid-container
grep 'anbox-binder' /var/lib/waydroid/lxc/waydroid/config_nodes || echo "config_nodes: no anbox references"
echo "=== done $(date -Is) ==="
