#!/bin/bash
# Re-create binderfs + device nodes Waydroid needs (root only).
set -u
if ! mountpoint -q /dev/binderfs; then
  mkdir -p /dev/binderfs
  mount -t binder binder /dev/binderfs
fi
for n in binder hwbinder vndbinder; do
  if [ -L "/dev/$n" ]; then rm -f "/dev/$n"; fi
  if [ ! -e "/dev/$n" ]; then touch "/dev/$n"; fi
  mountpoint -q "/dev/$n" || mount --bind "/dev/binderfs/$n" "/dev/$n"
done
echo "binder devices ready"
