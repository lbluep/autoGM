# Xingcheng Android VM — Conversation History and Local-Agent Handoff

Date: 2026-11-14

## Purpose

Continue the work on the user's local Linux computer. The immediate goal is to inspect the real game APK, determine its minimum supported Android version and CPU architecture, and configure the lightest compatible Android environment that can actually run the game.

The remote agent cannot access the user's local `/home/fengg` directory. This document transfers the relevant conversation history and technical state to a local agent.

## 1. Current task

Local APK to inspect:

```text
/home/fengg/Downloads/ldjya261113_cps001_30002.apk
```

The APK has **not yet been inspected**. Its package name, `minSdkVersion`, `targetSdkVersion`, native ABIs, signature, and integrity are unknown.

The user wants to:

1. Find the oldest Android version supported by this APK.
2. Balance compatibility and speed rather than defaulting to Android 11.
3. Install and test that Android environment locally.
4. Record every installation path and system change for complete removal later.
5. Avoid modifying the existing Windows XP image.

## 2. Conversation history

### Initial game questions

- The user initially asked in Chinese for the optimal hero and troop configuration method for `腥城`.
- The previous agent proposed a generic optimization method based on candidate lineup enumeration, range constraints, repeated battle reports, and minimizing losses. No official combat formula was found.
- When asked whether game code could be found, the agent found only third-party download pages, not reliable public source code. Those pages contained conflicting versions, package names, sizes, and genre descriptions.

### Virtual environment discussion

- The user asked whether Android or iOS could run in a local virtual machine.
- The agent recommended Android over iOS. iOS Simulator generally cannot run an ordinary commercial App Store IPA, while Android can run in an emulator or VM.
- The user then asked the agent to configure the best environment for this game.

### Remote machine investigation

The available execution environment was a remote Ubuntu 24.04 container, not the user's computer. Checks found:

| Item | Remote result |
|---|---|
| CPU | 3 virtual CPUs, Xeon 8582C |
| RAM | 5.8 GB |
| Disk | Approximately 1.1 TB available |
| `/dev/kvm` | Absent |
| Container privileges | No `CAP_SYS_ADMIN` or `CAP_NET_ADMIN` |
| Kernel modules | `/lib/modules` absent; binder modules could not be installed |
| Display | No physical display or existing X server |
| QEMU | QEMU 8.2 available from Ubuntu packages |

Consequences:

- Waydroid and redroid were not practical in this container.
- Android could only be run with QEMU TCG software emulation.
- There was no hardware GPU acceleration.
- The remote result should **not** be used to predict local performance, especially because the user reports that Windows XP runs quickly locally.

### Option D installation and test

The user explicitly selected an experimental local-in-remote-container QEMU setup and requested complete installation records.

The previous agent created:

```text
/workspace/option-d-android-vm/
├── INSTALL_MANIFEST.md
├── start.sh
├── stop.sh
├── uninstall.sh
├── vmctl.py
├── qmp_touch.py
├── iso/
├── disks/bliss.qcow2
├── run/qmp.sock
└── shots/
```

Installed remote packages recorded in the manifest:

```text
qemu-system-x86
qemu-utils
xvfb
x11vnc
novnc
websockify
unzip
p7zip-full
android-tools-adb
```

Python package:

```text
vncdotool
```

Remote test configuration:

- BlissOS 14 x86_64, Android 11.
- 3 virtual CPUs.
- Initially 3.5 GB RAM, reduced to 2 GB after the container OOM-killed the VM.
- No KVM; QEMU TCG only.
- Virtual display through Xvfb, x11vnc, and noVNC.
- VNC on port 5901 and noVNC on port 6080, bound to localhost.
- QMP socket added at `/workspace/option-d-android-vm/run/qmp.sock`.

Observed results:

- Direct kernel boot parameters were used to bypass a GRUB issue.
- Android 11 reached the desktop after approximately 22–25 minutes.
- `System UI isn't responding` appeared.
- adb remained `offline`, so an APK could not be pushed.
- Mouse clicks and keyboard input partially worked, but VNC mouse input could not perform the lock-screen swipe.
- `virtio-multitouch-pci` was added at the user's request.
- QMP `mtt` events could be injected successfully.
- Passing `device=mtouch0` caused QEMU to exit abnormally, so that option was omitted.
- The final QMP touch/launcher/adb test sequence had not produced a verified successful result when work paused.
- The VM was stopped to release CPU and RAM. No remote background task should be assumed to still be running.

The remote cleanup command is:

```bash
bash /workspace/option-d-android-vm/uninstall.sh
```

This cleanup is for the remote workspace only. Do not run it on the user's local computer.

### Windows XP image discussion

The user asked about:

```text
/home/fengg/winxp.img
```

Local output supplied by the user:

```text
/usr/bin/qemu-system-i386
/usr/bin/qemu-system-x86_64

/home/fengg/winxp.img: QEMU QCOW2 Image (v3), 12884901888 bytes
image: /home/fengg/winxp.img
file format: qcow2
virtual size: 12 GiB (12884901888 bytes)
disk size: 8.89 GiB
cluster_size: 65536
compat: 1.1
compression type: zlib
lazy refcounts: false
refcount bits: 16
corrupt: false
extended l2: false
```

The user reports that this Windows XP VM runs fast enough locally. Therefore:

- `/usr/bin/qemu-system-i386` and `/usr/bin/qemu-system-x86_64` are confirmed to exist locally.
- The Windows XP image is healthy QCOW2, not raw.
- Do not use `format=raw` for this image.
- Do not modify, delete, overwrite, or attach this XP image to the Android VM.
- Local KVM availability still needs to be checked explicitly; fast XP alone is not proof.

### Search for the lowest supported Android version

Web research produced unreliable and conflicting claims:

| Source | Advertised information | Problem |
|---|---|---|
| TVmao `kow腥城` | v1.21.71, 630.47 MB, Android 4.0+ | Conflicting description and untrusted download source |
| 七匣子 `kow腥城` | v2.1.1, 353.09 MB, “Android 5.2+” | “Android 5.2” is not a normal public Android release; page metadata is unreliable |
| 第一手游网 `腥城` | v3.3.6, 118.21 MB, Android 4.1+ | No verifiable APK manifest |
| 87G `腥城` | v1.0.2, 183.61 MB, package `com.yuwang.zhidu` | Download target is broken |

Additional metadata from 七匣子:

```text
Package: com.yxxinglin.xzid65808
MD5: 96a0ae31dc706cc99046c14c524a437f
```

Additional metadata from 87G:

```text
Package: com.yuwang.zhidu
MD5: FAB8D2F93F3FB5DB7AD05362D437367E
Advertised URL: https://down.87g.com/apk/xingcheng.apk
```

The 87G URL redirects to an error page and does not download an APK. Browser verification confirmed the broken link.

Another advertised URL was:

```text
https://down13.hyds360.com/app/xingchengchengjin.apk
```

It did not yield a usable APK through the available network path.

A screenshot review confirmed that the visible game title is `腥城`. The previous agent began investigating a possible connection to `King of Wasteland`, but this identification is **not confirmed** and must not be used to infer APK requirements.

The reliable way to determine the minimum Android version is therefore to inspect the actual local APK, not these download-page claims.

## 3. Required local-agent procedure

### Step 1 — Preserve existing files

Do not modify:

```text
/home/fengg/winxp.img
```

Do not execute the APK directly on the Linux host. Static inspection is safe; runtime testing belongs inside the Android environment.

### Step 2 — Inspect the APK

Use `aapt`, `aapt2`, or Android SDK `apkanalyzer`, installing only the missing tool if necessary.

Example with `aapt`:

```bash
aapt dump badging /home/fengg/Downloads/ldjya261113_cps001_30002.apk
```

Record at least:

- Package name and version.
- `sdkVersion` / `minSdkVersion`.
- `targetSdkVersion`.
- `native-code` entries.
- Permissions relevant to networking and device identification.
- SHA-256 of the APK.

Also inspect native libraries if `aapt` does not report them:

```bash
unzip -l /home/fengg/Downloads/ldjya261113_cps001_30002.apk
```

Look for directories such as:

```text
lib/armeabi-v7a/
lib/arm64-v8a/
lib/x86/
lib/x86_64/
```

An APK containing only ARM native libraries may fail on ordinary x86 Android even when its `minSdkVersion` is compatible. ARM translation or an ARM Android image may then be necessary.

### Step 3 — Inspect local virtualization capabilities

Check:

```bash
ls -l /dev/kvm
lscpu
free -h
```

Also determine:

- Linux distribution and desktop session type.
- Available RAM and disk space.
- GPU/rendering support.
- Whether Waydroid, Android Studio Emulator, QEMU, or another maintained emulator is appropriate.

Prefer hardware acceleration when available. Do not repeat the remote TCG setup locally if `/dev/kvm` is usable.

### Step 4 — Select the oldest compatible Android version

Use the APK's actual `minSdkVersion` as the lower bound, then account for:

- Native ABI compatibility.
- Required Google Play or third-party login services.
- Graphics API requirements.
- Emulator/Android-x86 image stability.
- Whether the game server still accepts that client version.

A lightweight Android 7.1 image without Google Apps was only a provisional suggestion. It is **not a confirmed compatible target** and should not be installed solely on that suggestion.

### Step 5 — Test incrementally

1. Boot the selected Android environment.
2. Verify input, networking, adb, and rendering before installing the game.
3. Install the APK through adb.
4. Capture installation errors verbatim, especially `INSTALL_FAILED_OLDER_SDK`, `INSTALL_FAILED_NO_MATCHING_ABIS`, and signature errors.
5. Launch the game and record whether it reaches login/gameplay.
6. Save screenshots and logs.
7. If the oldest version fails for a verified reason, move upward one Android release at a time rather than jumping directly to Android 11.

### Step 6 — Record and clean up

Create a new local `INSTALL_MANIFEST.md` containing:

- Every apt/pip/downloaded package.
- Every Android image and APK copied into the project.
- Every VM disk, configuration file, script, log, and screenshot.
- Every network port and listener.
- A startup and shutdown script.
- A complete uninstall script.

Keep project files under one task-specific local directory. Do not reuse or delete remote paths blindly.

## 4. Suggested prompt for the local agent

```text
Read xingcheng-android-local-handoff.md and continue from it.

Inspect /home/fengg/Downloads/ldjya261113_cps001_30002.apk using aapt/apkanalyzer.
Determine package name, minSdkVersion, targetSdkVersion, native ABIs, and SHA-256.
Check local /dev/kvm, CPU, RAM, OS, and graphics support.
Choose the oldest compatible Android version that balances speed and compatibility.
Configure hardware acceleration if available, then test boot, adb, installation, and launch.
Do not execute the APK directly on the host.
Do not modify or delete /home/fengg/winxp.img.
Record all installed packages, files, disks, configurations, and ports in a local
INSTALL_MANIFEST.md and provide a complete uninstall script.
Do not rely on third-party webpages' Android-version claims; use the APK manifest.
```

## 5. Research sources

- [87G: 腥城手游 metadata and broken APK link](https://www.87g.com/youxi/89881.html)
- [七匣子: kow腥城 metadata](https://m.7xz.com/games/2130.html)
- [TVmao: kow腥城 metadata](https://www.tvmao.com/sjgame/19891.html)
- [第一手游网: 腥城 metadata](https://www.diyiyou.com/xiazai/381060.html)
- [Android emulator hardware acceleration documentation](https://developer.android.google.cn/studio/run/emulator-acceleration)
