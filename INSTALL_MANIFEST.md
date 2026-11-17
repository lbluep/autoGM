# Xingcheng (腥城) Android Environment — Install Manifest

Recorded 2026-11-14 on this machine. Everything listed here was either created or modified
by the local agent; `uninstall.sh` reverses it.

## 1. Application under test

| Item | Value |
|---|---|
| File | `/home/fengg/Downloads/ldjya261113_cps001_30002.apk` |
| Size | 277 MB |
| SHA-256 | `21f2d307367e72040c304f1b27b42cc526a5e9d562505c67c814db5b94f79a66` |
| Package | `c.x.snow.sj` |
| Version | 3.0.2 (versionCode 30002) |
| Label | 腥城 |
| minSdk / targetSdk | 28 (Android 9) / 33 (Android 13) |
| Launcher activity | `com.x.zombie.ZombieActivityJS` (portrait only) |
| Native ABIs | **arm64-v8a only** — `libMyGame.so` (Cocos2d-x 3.x), `libtapsdkcore.so` |
| Graphics | OpenGL ES 2.0 |
| First-run download | ~346 MB / 5,153 files from `https://cdn.winterzerogame.cc/images/download_res/Android/` |
| Login backend | `https://login.winterzerogame.cc/gameservice/getServer` (WebView) |

## 2. Why Waydroid, not QEMU

The APK contains no x86/x86_64 code, so an x86 Android image needs ARM64 binary
translation. Translation layers (`libndk`, `libhoudini`) exist only for Android 11/13,
which makes Android 11 the practical floor — the declared minSdk 28 / Android 9 cannot
be reached on x86 hardware (Android 9 x86 images ship ARM32-only Houdini).

Waydroid shares the host kernel and GPU: native CPU speed, ~1 GB RAM, seconds to boot.
QEMU/BlissOS would need 2 GB+ of the 3.7 GB host RAM and has no GPU acceleration.

## 3. Host environment (unchanged facts)

- Ubuntu 22.04, kernel 6.8.0-52-generic, GNOME **Wayland** session
- Intel i5-5250U (2C/4T), 3.7 GB RAM, 22 GB free disk, Intel HD 5500
- `/dev/kvm` usable (not used by Waydroid)
- `binder_linux` available in `linux-modules-extra`, no Secure Boot

## 4. Installed packages (apt)

| Package | Version | Note |
|---|---|---|
| waydroid | 1.6.3 | from the pre-configured `repo.waydro.id` |
| lxc | 1:5.0.0~git2209-g5a7b9ce67-0ubuntu1.1 | dependency |
| liblxc1 | 1:5.0.0~git2209-g5a7b9ce67-0ubuntu1.1 | dependency |
| lxcfs | 5.0.0-0ubuntu2 | dependency |
| python3-gbinder | 1.3.1 | from waydro.id |
| libgbinder | 1.1.45 | from waydro.id |
| libglibutil | 1.0.80 | from waydro.id |
| adb | (repo package) | used for install/launch/screenshots |
| python3-pip | 22.0.2+dfsg-1ubuntu0.7 | needed by waydroid_script |

Pip packages (system-wide, for `waydroid_script`): `inquirerpy 0.3.4`, `pfzy 0.3.4`,
`prompt_toolkit 3.0.53`, `requests 2.32.3`, `tqdm 4.67.1`.

## 5. Android image (pre-existing, reused — not downloaded)

`/var/lib/waydroid/images/system.img` 1.7 GB, `vendor.img` 492 MB (both dated 2026-01-10).
LineageOS `lineage_waydroid_x86_64`, Android 13 (API 33), userdebug, VANILLA (no GApps),
vendor type MAINLINE, `mount_overlays = true`.

## 6. Changes made to the host

1. **Kernel module** `binder_linux` loaded; persistence files created:
   - `/etc/modules-load.d/waydroid-binder.conf`
   - `/etc/modprobe.d/waydroid-binder.conf`
2. **binderfs**: `mount -t binder binder /dev/binderfs`, then bind mounts
   `/dev/binder`, `/dev/hwbinder`, `/dev/vndbinder` from it (kernel exposes no static nodes).
   Re-created at each boot by `setup-binder.sh`.
3. **waydroid-container.service**: unmasked (was left masked by an earlier attempt), enabled.
4. `/var/lib/waydroid/waydroid.cfg`: binder entries changed from `anbox-binder` /
   `anbox-vndbinder` / `anbox-hwbinder` to `binder` / `vndbinder` / `hwbinder`.
5. `/var/lib/waydroid/lxc/waydroid/config_nodes`: `/dev/anbox-binder*` replaced with
   `/dev/binder*` (this file is only written by `waydroid init`, so it was patched directly).
6. `/var/lib/waydroid/waydroid_base.prop`: `ro.adb.secure=1` → `0` (adb over TCP without
   on-device authorization; container is host-local and reachable only on `waydroid0`).
7. **libndk ARM64 translation** installed into the overlay (not into the images):
   - `/var/lib/waydroid/overlay/system/bin/arm64`, `.../lib64/arm64`, `.../lib/arm64`,
     `.../etc/init/resetprop.rc`, `.../etc/binfmt_misc/arm64_exe`
   - props in `waydroid.cfg [properties]`: `ro.dalvik.vm.native.bridge=libndk_translation.so`,
     `ro.ndk_translation.version=0.2.3`, `ro.enable.native.bridge.exec=1`, `abilist` incl. arm64-v8a
   - source archive (MD5 `0b2207c490fcb400aa5c87fcf0d52d38`) cached at
     `/home/root/.cache/waydroid-script/downloads/libndktranslation.zip`
8. **Network**: Waydroid creates bridge `waydroid0` = `192.168.240.1/24`, container = DHCP
   `192.168.240.112`, adb on `192.168.240.112:5555`.
9. **Android userdata**: game installed; `/home/fengg/.local/share/waydroid/data` ≈ 340 MB.

## 7. Project directory

`/home/fengg/playground/autogame/xingcheng-android/`

| File | Purpose |
|---|---|
| `start.sh` | binder setup + container + session + UI + adb + launch game |
| `stop.sh` | stop session/UI/container |
| `setup-binder.sh` | re-create binderfs and nodes (root) |
| `uninstall.sh` | remove all of the above (root; `--keep-data`, `--purge-project`) |
| `bootstrap-root.sh`, `bootstrap-root-2.sh` | original install passes (kept as record) |
| `fix-binder.sh`, `fetch-libndk.sh`, `install-libndk.sh` | remediation passes (kept as record) |
| `logs/` | bootstrap, session, ui, libndk, logcat logs |
| `shots/` | verification screenshots |

## 8. Verification results

- Android 13 boots: `sys.boot_completed=1`
- `ro.product.cpu.abilist` contains `arm64-v8a`; native bridge = `libndk_translation.so` 0.2.3
- `adb install` equivalent succeeded; `c.x.snow.sj` present
- `am start -n c.x.snow.sj/com.x.zombie.ZombieActivityJS` → `Status: ok`
- Process alive, `lib/arm64-v8a` mapped through libndk, GL surface renders the game
  (see `shots/shot3.png`, `shots/final.png`)
- Announcement screen dismissed with `adb shell input tap 720 791`
- Login screen (`JS GAME`, quick registration) appeared — `shots/after_confirm.png`
- Game entered the world: "Stage 1: Base Construction" — `shots/entering.png`
- Input injection works (`input tap`, `input keyevent`)
- Note: the game window is portrait and centered on a 1440x873 display. Screen
  coordinates are absolute (display) coordinates; `wm size` reports 1440x873.

## 9. Known issues

1. `composer@2.1-service` (hwcomposer.waydroid.so) has a crash-on-exit bug in
   `xdg_toplevel_handle_close`; it also logged one HIDL threadpool abort at container
   start. Killing/Closing the Waydroid UI window can leave SurfaceFlinger in a
   crash loop. If the screen stays black: `./stop.sh && ./start.sh`.
2. Because of (1), the container must keep a UI window attached; Waydroid freezes the
   container when the window closes.
3. SourceForge throughput here is ~32 KB/s, so re-downloading OTA images
   (or upgrading to the 2026-09/10 builds) is impractical; the 2026-01 image pair is in use.
4. Game is portrait-only, letterboxed inside the 1440x873 display.
5. Login is required (`login.winterzerogame.cc`); the VANILLA image has no Google Play
   services, so any Google/TapTap account flow inside a WebView is untested.
6. `waydroid shell` requires root; use `adb` for normal operations.

## 10. Related notes

- `ENV_FINGERPRINT.md` — recorded (but **not applied**) findings on environment
  fingerprints (`ro.product.model`, `ro.build.fingerprint`, ABI/native bridge, kernel,
  GL, sensors) plus the exact overlay-based change and rollback procedure.
  No system change was made for this.
