#!/bin/bash
# Start the Xingcheng Android environment. Run as your normal user (not root).
set -eu
PROJECT=/home/fengg/playground/autogame/xingcheng-android
APK=/home/fengg/Downloads/ldjya261113_cps001_30002.apk
PKG=c.x.snow.sj
ACT=c.x.snow.sj/com.x.zombie.ZombieActivityJS
mkdir -p "$PROJECT/logs"

# 必须用普通用户运行：以 root 启动的 waydroid session 会立刻退出（无 XDG_RUNTIME_DIR/Wayland）
if [ "$(id -u)" -eq 0 ]; then
  echo "请勿使用 sudo 或 root 运行本脚本，直接 ./start.sh 即可。" >&2
  exit 1
fi
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"

# 1. binder device nodes (root via pkexec prompt)
pkexec "$PROJECT/setup-binder.sh"

# 2. container
pkexec systemctl start waydroid-container
sleep 2

# 3. session + UI window (keep this window open; closing it freezes the container)
# 关键：窗口只能由 show-full-ui 自己拉起 session 时创建（background_start=false）。
#   - `waydroid session start` 是后台模式，永远不会创建窗口
#   - session 已存在时再跑 show-full-ui，它只会解冻然后立刻退出，同样没有窗口
# 所以这里只启动 show-full-ui；若已有后台 session，先停掉再切换成带窗口模式。
if ! pgrep -f 'waydroid show-full-ui' >/dev/null; then
  if pgrep -f 'waydroid session start' >/dev/null; then
    echo "检测到无窗口的后台 session，切换为带窗口模式…"
    timeout 20 waydroid session stop >/dev/null 2>&1 || true
    sleep 3
  fi
  setsid nohup waydroid show-full-ui > "$PROJECT/logs/ui.log" 2>&1 < /dev/null &
fi
sleep 10
if ! pgrep -f 'waydroid show-full-ui' >/dev/null; then
  echo "Android 窗口启动失败，日志末尾：" >&2
  tail -6 "$PROJECT/logs/ui.log" >&2 || true
  exit 1
fi

# 4. wait for boot, then adb
for i in $(seq 1 60); do
  adb connect 192.168.240.112:5555 >/dev/null 2>&1 || true
  st=$(timeout 10 adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')
  [ "$st" = "1" ] && break
  sleep 2
done

# 5. install if missing, then launch
if ! timeout 15 adb shell pm list packages 2>/dev/null | grep -q "$PKG"; then
  echo "installing $APK"
  timeout 600 adb install -r "$APK"
fi
timeout 30 adb shell am start -n "$ACT" >/dev/null 2>&1 || true

waydroid status
