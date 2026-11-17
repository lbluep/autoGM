#!/bin/bash
# Install libndk (Google ARM64 translation) into the Waydroid Android 13 image via overlayfs.
set -u
LOG=/home/fengg/playground/autogame/xingcheng-android/logs/libndk.log
exec > >(tee -a "$LOG") 2>&1
echo "=== libndk install $(date -Is) ==="

echo "--- stop container/session ---"
pkill -f 'waydroid session' 2>/dev/null
systemctl stop waydroid-container
sleep 2

echo "--- python deps ---"
command -v pip3 >/dev/null || apt-get install -y python3-pip 2>&1 | tail -3
python3 -m pip install --quiet tqdm requests InquirerPy 2>&1 | tail -5
python3 -c "import InquirerPy, requests, tqdm; print('deps ok')"

echo "--- backup config ---"
cp -n /var/lib/waydroid/waydroid.cfg /home/fengg/playground/autogame/xingcheng-android/backup/waydroid.cfg.orig
cp -n /var/lib/waydroid/waydroid_base.prop /home/fengg/playground/autogame/xingcheng-android/backup/waydroid_base.prop.orig

echo "--- run waydroid_script: install libndk ---"
cd /tmp/wds/waydroid_script-main || exit 1
python3 main.py -a 13 install libndk 2>&1 | tail -40
echo "script_rc=${PIPESTATUS[0]}"

echo "--- resulting props ---"
sed -n '/\[properties\]/,$p' /var/lib/waydroid/waydroid.cfg | head -20
echo "--- overlay files ---"
ls /var/lib/waydroid/overlay/system/bin 2>/dev/null | head
ls /var/lib/waydroid/overlay/system/lib64 2>/dev/null | grep -i ndk | head
echo "=== done $(date -Is) ==="
