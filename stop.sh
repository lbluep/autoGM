#!/bin/bash
# Stop the Xingcheng Android environment. Run as your normal user.
set -u
timeout 10 adb disconnect >/dev/null 2>&1
pkill -f 'waydroid show-full-ui' 2>/dev/null
pkill -f 'waydroid session' 2>/dev/null
pkexec systemctl stop waydroid-container
echo "stopped"
