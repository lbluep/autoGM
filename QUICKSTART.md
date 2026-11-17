# 腥城 — Quick Start

Environment: Waydroid 1.6.3, Android 13 x86_64 + libndk (ARM64 translation).
Project dir: `/home/fengg/playground/autogame/xingcheng-android`

## Start

```bash
cd /home/fengg/playground/autogame/xingcheng-android
./start.sh
```

That runs 5 steps and takes ~1–2 min on this machine:

1. `setup-binder.sh` — creates the binder device nodes (asks for your password)
2. starts `waydroid-container` (asks for your password again)
3. starts the session and opens the Android window
4. waits for boot, then `adb connect 192.168.240.112:5555`
5. installs the APK if missing, then launches `com.x.zombie.ZombieActivityJS`

**Run it as your normal user — never with `sudo ./start.sh`.** A session started as root
has no `XDG_RUNTIME_DIR` / `WAYLAND_DISPLAY` and exits immediately, leaving
`Session: STOPPED` and no window. The script now refuses to run as root and sets both
variables itself.

Approve both polkit dialogs when they appear; without them the container will not start.

The window is created by `waydroid show-full-ui`, and **it must be the process that starts
the session**. `waydroid session start` on its own runs headless (no window), and running
`show-full-ui` while a session already exists only unfreezes the container and exits
immediately — again no window. That is why start.sh stops any headless session before
launching `show-full-ui`, and why it treats a fast-exiting `show-full-ui` as a failure.

The game window opens portrait, centered on the screen. **Do not close that window** —
Waydroid freezes the container when the UI window goes away, and the HWC HAL can crash,
leaving a black screen.

## Stop

Stop the recorder first (see below), then stop Android:

```bash
./stop.sh
```

## Record gameplay (schema v3)

### 1. Prepare the game

Start the game with `./start.sh` and leave the Android window open. Before a formal
recording, dismiss the top-left JS GAME floating icon so that it does not cover the wood
value: click the icon, turn off **Floating window toggle**, confirm, then use the panel's
top-left back arrow. The game says a restart is required if you later want to re-enable
that floating icon.

Only run one recorder at a time. Check for an existing one with:

```bash
pgrep -af '[c]ollect/record.py'
```

### 2. Start recording

In a dedicated terminal:

```bash
cd /home/fengg/playground/autogame/xingcheng-android
python3 collect/record.py start --goal "Describe the strategic goal of this session"
```

Keep this process running. It prints the episode directory and also writes its path to
`data/recordings/LATEST.txt`.

### 3. Send operations to the recorder

Open a second terminal and set:

```bash
cd /home/fengg/playground/autogame/xingcheng-android
EP=$(cat data/recordings/LATEST.txt)
```

Every action must be sent through `cmd.fifo` so that it has aligned before/after frames,
HUD state, timestamps, and an action record:

```bash
echo 'tap X Y' > "$EP/cmd.fifo"
echo 'drag X0 Y0 X1 Y1 DURATION_MS optional navigation intent' > "$EP/cmd.fifo"
echo 'key 4' > "$EP/cmd.fifo"                  # Android Back
echo 'wait 5000' > "$EP/cmd.fifo"               # wait 5 seconds
echo 'mark describe what you are looking for' > "$EP/cmd.fifo"
echo 'state' > "$EP/cmd.fifo"                   # extra HUD state snapshot
```

Coordinates are full-display coordinates (`1440x873`), not coordinates relative to the
game crop. A safe city-drag start area is approximately `x=480..900, y=120..640`.
Dragging is only for revealing off-screen content; add a short intent when useful.

**Direct mouse clicks in the Waydroid window are not injected through this recorder.**
They may appear in the uniform frame stream, but they will not create entries in
`actions.jsonl` or `steps.jsonl`. Use the FIFO commands above, or ask the agent to operate
the game while this recorder is running. Do not perform purchases, upgrades, reward
claims, or other resource-consuming actions without explicit approval.

For strategy data, optionally link a decision to its later outcome:

```bash
echo 'plan 观察资源随时间的变化' > "$EP/cmd.fifo"
echo 'decide {"intent":"观察资源增长","options":["原地等待","执行其他操作"],"chosen":"原地等待","expected":"资源数值增加","risk":"none"}' > "$EP/cmd.fifo"
echo 'wait 5000' > "$EP/cmd.fifo"
echo 'outcome D1 检查等待后的资源状态' > "$EP/cmd.fifo"
```

The `decide` command prints an ID such as `D1`; use that ID in `outcome`. Keep pending
outcomes in the same recorder process. HUD numeric values remain `null` until labels are
added to `$EP/state/labels.jsonl` as `{"field","hash","value"}` rows; then run
`echo 'labels' > "$EP/cmd.fifo"` to reload them.

### 4. Stop and inspect recording

```bash
EP=$(cat data/recordings/LATEST.txt)
echo 'stop' > "$EP/cmd.fifo"
```

The main files are:

- `frames.jsonl` and `frames/*.jpg`: uniform frame stream.
- `actions.jsonl`: high-level actions.
- `steps.jsonl`, `keyframes/`, and `raw/`: aligned action samples and original screenshots.
- `state.jsonl` and `state/regions/`: HUD state timeline and deduplicated region images.
- `decisions.jsonl`: plans, decisions, and delayed outcomes.
- `markers.jsonl`: navigation goals and other annotations.
- `meta.json`: geometry, APK version, HUD regions, schema, and recorder source hash.

The earlier measured storage rate was about **626 MiB/hour**; check disk space before a
long session. After `stop` has been processed, `./stop.sh` can be used to shut down
Android.

## Everyday actions

```bash
# take a screenshot
adb exec-out screencap -p > shot.png

# tap (display coordinates, 1440x873 — the game sits at x 472..969)
adb shell input tap 720 791      # Confirm button on the announcement screen
adb shell input keyevent 4       # Back

# game logs
adb logcat --pid=$(adb shell pidof c.x.snow.sj) -t 100

# container state
waydroid status
```

## When something looks wrong

| Symptom | Fix |
|---|---|
| `Android 窗口启动失败` / no window after start.sh | `./stop.sh && ./start.sh` (a headless `waydroid session start` must be stopped first) |
| Black screen / `Container: FROZEN` | `./stop.sh && ./start.sh` |
| `SurfaceFlinger` crash loop | same: stop, then start (a UI window must stay open) |
| `adb devices` shows `offline` | `adb disconnect && adb connect 192.168.240.112:5555` |
| `/dev/binder` missing after reboot | `pkexec ./setup-binder.sh` (start.sh does it automatically) |
| Container won't start | `pkexec systemctl restart waydroid-container` |
| Game not installed | `adb install -r /home/fengg/Downloads/ldjya261113_cps001_30002.apk` |

## Notes

- Login is required on first run (`login.winterzerogame.cc`); this image has no Google
  Play services.
- The APK is arm64-only; it runs through `libndk_translation.so` 0.2.3. If a future
  update ships x86 libs or the translation layer breaks, re-apply `./install-libndk.sh`.
- Full details and removal: see [INSTALL_MANIFEST.md](INSTALL_MANIFEST.md), and
  `./uninstall.sh` to undo everything.
