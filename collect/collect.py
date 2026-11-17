#!/usr/bin/env python3
"""界面采集工具：截图、状态识别、差分、动作记录。

设计要点见 SPEC.md：
- screen 身份只依赖固定 HUD 锚点，不受主城相机平移影响
- observe 未变化时返回 UNCHANGED（不产生读图成本）
- 相机移动与内容变化分开判定
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path("/home/fengg/playground/autogame/xingcheng-android")
DATA = ROOT / "data"
SERIAL = "192.168.240.112:5555"
W, H = 1440, 873

# 游戏渲染区（去掉左右黑边）
GA = (472, 0, 497, 807)
# 固定 HUD 锚点：必须完全落在 HUD 内，不能压到会移动的场景
ANCHORS = [
    (472, 0, 120, 52),      # 左上头像 / 品牌
    (905, 42, 63, 68),      # 右上 Recharge 按钮本体
    (881, 486, 55, 150),    # 右侧 Quests / Mail 按钮本体
    (472, 748, 497, 125),   # 底部导航栏
]
HUD_RATIO = 0.35   # 单个锚点内变化格子占比超过该值才算 HUD 变化
HUD_THRESHOLD = 25.0
SCENE_THRESHOLD = 6.0
SHIFT_THRESHOLD = 10  # px


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sh(cmd, **kw):
    return subprocess.run(cmd, check=False, **kw)


def session_dir():
    meta = json.loads((DATA / "meta.json").read_text())
    return DATA / "sessions" / meta["session"]


def capture(dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        subprocess.run(["adb", "exec-out", "screencap", "-p"], stdout=f, check=True)
    return dest


def decode(path: Path) -> bytes:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    if len(out) != W * H:
        raise SystemExit(f"unexpected frame size {len(out)} for {path}")
    return out


def thumb(src: Path, dst: Path, width=360):
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf",
         f"crop={GA[2]}:{GA[3]}:{GA[0]}:{GA[1]},scale={width}:-1:flags=lanczos",
         str(dst)], check=True)
    return dst


def settle(max_wait=5.0, thr=2.5, capture_fn=None):
    """等画面稳定：连续两帧场景差异低于阈值。可注入截图函数以串行采集。"""
    snap = capture_fn or capture
    t0 = time.time()
    prev = None
    tmp = Path("/tmp/_settle.png")
    last = None
    while time.time() - t0 < max_wait:
        snap(tmp)
        f = decode(tmp)
        if prev is not None:
            last = mean_diff(prev, f, GA, stride=4)
            if last < thr:
                return last
        prev = f
        time.sleep(0.35)
    return last


def dhash(frame: bytes, region, size=8):
    x0, y0, w, h = region

    def px(x, y):
        # 2x2 均值，避免单像素噪声把指纹抖动成不同值
        x = min(max(x, 0), W - 2)
        y = min(max(y, 0), H - 2)
        i = y * W + x
        return (frame[i] + frame[i + 1] + frame[i + W] + frame[i + W + 1]) // 4

    bits = 0
    for gy in range(size):
        row = []
        for gx in range(size + 1):
            row.append(px(x0 + int(gx * w / size), y0 + int(gy * h / size)))
        for i in range(size):
            bits = (bits << 1) | (1 if row[i] > row[i + 1] else 0)
    return "%016x" % bits


def anchors_hash(frame: bytes):
    return [dhash(frame, r) for r in ANCHORS]


def hamming(a, b):
    return sum(bin(int(x, 16) ^ int(y, 16)).count("1") for x, y in zip(a, b))


def mean_diff(a: bytes, b: bytes, region, stride=2):
    x0, y0, w, h = region
    s = 0
    n = 0
    for y in range(y0, y0 + h, stride):
        base = y * W
        for x in range(x0, x0 + w, stride):
            s += abs(a[base + x] - b[base + x])
            n += 1
    return s / n if n else 0.0


def hud_ratio(a: bytes, b: bytes, region, cell=10, thr=HUD_THRESHOLD):
    """锚点区域内“明显变化”的格子占比，避免整块 mean 被小动画带偏。"""
    x0, y0, w, h = region
    changed = 0
    total = 0
    for y in range(y0, y0 + h - cell, cell):
        for x in range(x0, x0 + w - cell, cell):
            s = 0
            n = 0
            for yy in range(y, y + cell, 2):
                base = yy * W
                for xx in range(x, x + cell, 2):
                    s += abs(a[base + xx] - b[base + xx])
                    n += 1
            total += 1
            if s / n > thr:
                changed += 1
    return changed / total if total else 0.0


def hud_change(a: bytes, b: bytes):
    best = (0.0, ANCHORS[0])
    for r in ANCHORS:
        v = hud_ratio(a, b, r)
        if v > best[0]:
            best = (v, r)
    return best


def changed_bbox(a: bytes, b: bytes, region, thr=18, cell=10):
    x0, y0, w, h = region
    minx, miny, maxx, maxy = 10 ** 9, 10 ** 9, -1, -1
    for y in range(y0, y0 + h - cell, cell):
        for x in range(x0, x0 + w - cell, cell):
            s = 0
            n = 0
            for yy in range(y, y + cell, 2):
                base = yy * W
                for xx in range(x, x + cell, 2):
                    s += abs(a[base + xx] - b[base + xx])
                    n += 1
            if s / n > thr:
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x + cell), max(maxy, y + cell)
    if maxx < 0:
        return None
    return (minx, miny, maxx - minx, maxy - miny)


def coarse_shift(a: bytes, b: bytes, region, factor=8, maxd=160, dy_limit=None, dx_limit=None):
    """粗估两帧之间的内容位移；dy_limit/dx_limit 用于限制单轴搜索范围。"""
    x0, y0, w, h = region
    aw, ah = w // factor, h // factor

    def sample(f):
        return [f[(y0 + y * factor) * W + x0 + x * factor]
                for y in range(ah) for x in range(aw)]

    A, B = sample(a), sample(b)
    md = maxd // factor
    pw, ph = aw // 2, ah // 2
    px0, py0 = aw // 4, ah // 4

    def score(dx, dy):
        s = 0
        n = 0
        for yy in range(0, ph, 2):
            sy = py0 + yy
            ty = sy + dy
            if ty < 0 or ty >= ah:
                return None
            for xx in range(0, pw, 2):
                sx = px0 + xx
                tx = sx + dx
                if tx < 0 or tx >= aw:
                    continue
                s += abs(A[sy * aw + sx] - B[ty * aw + tx])
                n += 1
        return s / n if n > 50 else None

    best = (0, 0, 1e9)
    ym = dy_limit if dy_limit is not None else md
    xm = dx_limit if dx_limit is not None else md
    for dy in range(-ym, ym + 1):
        for dx in range(-xm, xm + 1):
            if dx == 0 and dy == 0:
                continue
            v = score(dx, dy)
            if v is not None and v < best[2]:
                best = (dx * factor, dy * factor, v)

    # 等距地形容易匹配出假的垂直位移：纯水平解释得分接近时，强制 dy=0
    if best[1] != 0 and best[0] != 0:
        v0 = score(best[0] // factor, 0)
        if v0 is not None and v0 < best[2] * 1.35:
            best = (best[0], 0, v0)
    # 内部匹配得到的是“把 B 对齐到 A”的偏移，取反后即内容位移（与手指同向）
    return (-best[0], -best[1], best[2])


def state_file(screen):
    return DATA / "states" / screen / "current.json"


def load_state(screen):
    p = state_file(screen)
    return json.loads(p.read_text()) if p.exists() else None


def save_state(screen, st):
    p = state_file(screen)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, ensure_ascii=False, indent=2))


def append_step(rec):
    with open(session_dir() / "steps.jsonl", "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def step_no():
    p = DATA / "step.txt"
    n = int(p.read_text()) + 1 if p.exists() else 1
    p.write_text(str(n))
    return n


def rel(p: Path):
    return str(p.relative_to(DATA))


def snap(screen, action, wait_before=0):
    if wait_before:
        time.sleep(wait_before)
        settle()
    step = step_no()
    raw = session_dir() / "raw" / f"{step:04d}_{screen}.png"
    capture(raw)
    th = thumb(raw, session_dir() / "screens" / f"{step:04d}_{screen}.png")
    frame = decode(raw)
    return step, raw, th, frame


def cmd_init(_args):
    DATA.mkdir(parents=True, exist_ok=True)
    meta_p = DATA / "meta.json"
    if not meta_p.exists():
        sid = datetime.now().strftime("%Y%m%dT%H%M")
        meta_p.write_text(json.dumps({
            "session": sid,
            "started": now(),
            "serial": SERIAL,
            "display": [W, H],
            "game_area": GA,
            "anchors": ANCHORS,
            "thresholds": {"hud": HUD_THRESHOLD, "scene": SCENE_THRESHOLD,
                           "shift_px": SHIFT_THRESHOLD},
        }, ensure_ascii=False, indent=2))
        (DATA / "step.txt").write_text("0")
    sd = session_dir()
    for sub in ("raw", "screens", "templates", "crops"):
        (sd / sub).mkdir(parents=True, exist_ok=True)
    (DATA / "states").mkdir(exist_ok=True)
    print(json.dumps({"ok": True, "session": session_dir().name,
                      "dir": str(session_dir())}, ensure_ascii=False))


def cmd_shot(args):
    step, raw, th, frame = snap(args.screen, None)
    h = anchors_hash(frame)
    st = load_state(args.screen) or {
        "screen": args.screen, "rev": 1, "created": now(),
        "camera": {"x": 0, "y": 0}, "elements": [],
    }
    st.update({"hash": h, "updated": now(), "shot": rel(raw)})
    save_state(args.screen, st)
    append_step({"step": step, "ts": now(), "from": None,
                 "action": {"type": "start"}, "to": args.screen,
                 "shot": rel(raw), "thumb": rel(th), "hash": h,
                 "changed": True, "note": args.note or ""})
    print(json.dumps({"step": step, "state": args.screen, "hash": h,
                      "thumb": rel(th), "raw": rel(raw)}, ensure_ascii=False))


def cmd_observe(args):
    step, raw, th, frame = snap(args.screen, None)
    st = load_state(args.screen)
    if not st:
        raise SystemExit(f"unknown state {args.screen}; use shot first")
    prev_raw = Path(st["shot"]) if st.get("shot") else None
    prev_raw = DATA / prev_raw if prev_raw else None
    if not prev_raw or not prev_raw.exists():
        raise SystemExit("no previous frame to compare")
    prev = decode(prev_raw)

    hd, hud_region = hud_change(prev, frame)
    sd = mean_diff(prev, frame, GA)
    shift = (0, 0, 0.0)
    status = "UNCHANGED"
    crop = None

    if hd > HUD_RATIO:
        status = "CHANGED_HUD"
        box = changed_bbox(prev, frame, hud_region, thr=14)
    elif sd > SCENE_THRESHOLD:
        shift = coarse_shift(prev, frame, GA)
        if abs(shift[0]) >= SHIFT_THRESHOLD or abs(shift[1]) >= SHIFT_THRESHOLD:
            status = "CAMERA_MOVED"
            st["camera"]["x"] += shift[0]
            st["camera"]["y"] += shift[1]
        else:
            status = "CHANGED_SCENE"
            box = changed_bbox(prev, frame, GA)
    else:
        box = None

    if status in ("CHANGED_HUD", "CHANGED_SCENE"):
        if box:
            pad = 12
            bx = max(GA[0], box[0] - pad)
            by = max(GA[1], box[1] - pad)
            bw = min(GA[0] + GA[2] - bx, box[2] + pad * 2)
            bh = min(GA[1] + GA[3] - by, box[3] + pad * 2)
            crop = session_dir() / "crops" / f"{step:04d}_{args.screen}_diff.png"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw),
                            "-vf", f"crop={bw}:{bh}:{bx}:{by},scale=360:-1:flags=lanczos",
                            str(crop)], check=True)
            st["rev"] += 1
            st["hash"] = anchors_hash(frame)
        else:
            status = "UNCHANGED"

    st["shot"] = rel(raw)
    st["updated"] = now()
    save_state(args.screen, st)
    append_step({"step": step, "ts": now(), "from": args.screen,
                 "action": {"type": "observe"}, "to": args.screen,
                 "shot": rel(raw), "thumb": rel(th), "hash": st["hash"],
                 "hud_ratio": round(hd, 2), "scene_diff": round(sd, 2),
                 "shift": [shift[0], shift[1]], "changed": status != "UNCHANGED"})
    print(json.dumps({"step": step, "status": status, "hud_ratio": round(hd, 2),
                      "scene_diff": round(sd, 2), "shift": [shift[0], shift[1]],
                      "rev": st["rev"],
                      "crop": rel(crop) if crop else None}, ensure_ascii=False))


def cmd_tap(args):
    subprocess.run(["adb", "shell", "input", "tap",
                    str(args.x), str(args.y)], check=True)
    step, raw, th, frame = snap(args.to, None, wait_before=args.wait)
    h = anchors_hash(frame)
    st = load_state(args.to) or {"screen": args.to, "rev": 1, "created": now(),
                                 "camera": {"x": 0, "y": 0}, "elements": []}
    if args.to != args.src:
        st.update({"hash": h, "updated": now(), "shot": rel(raw)})
        save_state(args.to, st)
    append_step({"step": step, "ts": now(), "from": args.src,
                 "action": {"type": "tap", "element": args.element,
                            "coord": [args.x, args.y]},
                 "to": args.to, "shot": rel(raw), "thumb": rel(th),
                 "hash": h, "changed": True, "note": args.note or ""})
    print(json.dumps({"step": step, "from": args.src, "to": args.to,
                      "thumb": rel(th), "raw": rel(raw)}, ensure_ascii=False))


def cmd_back(args):
    subprocess.run(["adb", "shell", "input", "keyevent", "4"], check=True)
    step, raw, th, frame = snap(args.to, None, wait_before=args.wait)
    h = anchors_hash(frame)
    st = load_state(args.to)
    if st:
        st.update({"hash": h, "updated": now(), "shot": rel(raw)})
        save_state(args.to, st)
    append_step({"step": step, "ts": now(), "from": args.src,
                 "action": {"type": "back"}, "to": args.to,
                 "shot": rel(raw), "thumb": rel(th), "hash": h, "changed": True})
    print(json.dumps({"step": step, "from": args.src, "to": args.to,
                      "thumb": rel(th)}, ensure_ascii=False))


def cmd_pan(args):
    # 慢速拖拽：500ms，避免惯性；位移与手指移动基本 1:1
    cx, cy = 720, 400
    x1, y1 = cx - args.dx // 2, cy - args.dy // 2
    x2, y2 = cx + args.dx // 2, cy + args.dy // 2
    subprocess.run(["adb", "shell", "input", "swipe",
                    str(x1), str(y1), str(x2), str(y2), str(args.duration)],
                   check=True)
    step, raw, th, frame = snap(args.screen, None, wait_before=args.wait)
    st = load_state(args.screen)
    measured = None
    if st and st.get("shot"):
        prev_raw = DATA / st["shot"]
        if prev_raw.exists():
            prev = decode(prev_raw)
            span = max(abs(args.dx), abs(args.dy)) + 64
            dyl = 2 if args.dy == 0 else None   # 纯水平拖拽：限制垂直搜索 ±16px
            dx, dy, _ = coarse_shift(prev, frame, GA, maxd=min(400, span), dy_limit=dyl)
            measured = [dx, dy]
            # 自己发起的拖拽以命令值为准；实测只用于诊断边界夹取
            delta = [args.dx, args.dy]
            bad = (args.dx and dx * args.dx < 0) or (args.dy and dy * args.dy < 0)
            if measured and not bad:
                if args.dx and abs(dx) < abs(args.dx) * 0.85:
                    delta[0] = dx
                if args.dy and abs(dy) < abs(args.dy) * 0.85:
                    delta[1] = dy
            st["camera"]["x"] += delta[0]
            st["camera"]["y"] += delta[1]
    if st:
        st.update({"shot": rel(raw), "updated": now()})
        save_state(args.screen, st)
    append_step({"step": step, "ts": now(), "from": args.screen,
                 "action": {"type": "pan", "dx": args.dx, "dy": args.dy,
                            "duration_ms": args.duration},
                 "to": args.screen, "shot": rel(raw), "thumb": rel(th),
                 "shift_measured": measured,
                 "changed": bool(measured and (abs(measured[0]) > 3 or abs(measured[1]) > 3))})
    print(json.dumps({"step": step, "action": "pan", "commanded": [args.dx, args.dy],
                      "measured": measured,
                      "camera": st["camera"] if st else None,
                      "thumb": rel(th)}, ensure_ascii=False))


def cmd_annotate(args):
    st = load_state(args.screen)
    if not st:
        raise SystemExit(f"unknown state {args.screen}")
    payload = json.loads(Path(args.file).read_text()) if args.file else json.loads(args.json)
    if "elements" in payload:
        st["elements"] = payload["elements"]
    if "kind" in payload:
        st["kind"] = payload["kind"]
    if "notes" in payload:
        st["notes"] = payload["notes"]
    st["rev"] += 1
    st["updated"] = now()
    save_state(args.screen, st)
    print(json.dumps({"ok": True, "screen": args.screen, "rev": st["rev"],
                      "elements": len(st.get("elements", []))}, ensure_ascii=False))


def cmd_identify(args):
    tmp = Path("/tmp/_identify.png")
    capture(tmp)
    h = anchors_hash(decode(tmp))
    cands = []
    for p in (DATA / "states").glob("*/current.json"):
        s = json.loads(p.read_text())
        cands.append([hamming(h, s["hash"]), s["screen"]])
    cands.sort()
    best = cands[0] if cands else None
    matched = bool(best and best[0] <= args.max_distance)
    print(json.dumps({"screen": best[1] if matched else None,
                      "distance": best[0] if best else None,
                      "candidates": cands[:3], "matched": matched},
                     ensure_ascii=False))


def cmd_status(_args):
    meta = json.loads((DATA / "meta.json").read_text())
    states = []
    for p in sorted((DATA / "states").glob("*/current.json")):
        s = json.loads(p.read_text())
        states.append({"screen": s["screen"], "rev": s["rev"],
                       "elements": len(s.get("elements", [])),
                       "camera": s.get("camera")})
    print(json.dumps({"session": meta["session"],
                      "step": int((DATA / "step.txt").read_text()),
                      "states": states}, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    p = sub.add_parser("shot")
    p.add_argument("screen")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_shot)

    p = sub.add_parser("observe")
    p.add_argument("screen")
    p.set_defaults(func=cmd_observe)

    p = sub.add_parser("tap")
    p.add_argument("src")
    p.add_argument("element")
    p.add_argument("x", type=int)
    p.add_argument("y", type=int)
    p.add_argument("to")
    p.add_argument("--wait", type=float, default=3.0)
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_tap)

    p = sub.add_parser("back")
    p.add_argument("src")
    p.add_argument("to")
    p.add_argument("--wait", type=float, default=3.5)
    p.set_defaults(func=cmd_back)

    p = sub.add_parser("pan")
    p.add_argument("screen")
    p.add_argument("dx", type=int)
    p.add_argument("dy", type=int)
    p.add_argument("--duration", type=int, default=500)
    p.add_argument("--wait", type=float, default=1.6)
    p.set_defaults(func=cmd_pan)

    p = sub.add_parser("annotate")
    p.add_argument("screen")
    p.add_argument("--file")
    p.add_argument("--json")
    p.set_defaults(func=cmd_annotate)

    sub.add_parser("status").set_defaults(func=cmd_status)

    p = sub.add_parser("identify")
    p.add_argument("--max-distance", type=int, default=64)
    p.set_defaults(func=cmd_identify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
