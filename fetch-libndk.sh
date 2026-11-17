#!/bin/bash
# Pre-fetch the libndk archive from codeload (github.com is unreachable on this network).
set -u
LOG=/home/fengg/playground/autogame/xingcheng-android/logs/libndk-fetch.log
exec > >(tee -a "$LOG") 2>&1
echo "=== fetch libndk $(date -Is) ==="
pkill -f 'main.py -a 13' 2>/dev/null
sleep 1
DEST=/home/root/.cache/waydroid-script/downloads
mkdir -p "$DEST"
curl -L --retry 3 --connect-timeout 20 -o "$DEST/libndktranslation.zip" \
  "https://codeload.github.com/supremegamers/vendor_google_proprietary_ndk_translation-prebuilt/zip/68734c52556d3d7a6db34c603dd9276915c29f2f"
echo "curl_rc=$?"
ls -lh "$DEST/libndktranslation.zip"
echo -n "md5: "; md5sum "$DEST/libndktranslation.zip" | cut -d' ' -f1
echo "expected: 0b2207c490fcb400aa5c87fcf0d52d38"
unzip -l "$DEST/libndktranslation.zip" | head -3
echo "=== done $(date -Is) ==="
