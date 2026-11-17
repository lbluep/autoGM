"""按住拖拽时读取全城建筑名称与屏幕坐标。

原理：拖拽过程中（手指未抬起）游戏会在每栋建筑上方显示名称。
实现：发起一次长时长 swipe（默认 6 秒），滑到中途截图，此时手指仍按下。

用法：
    .venv/bin/python collect/hold_scan.py            # 读一次，打印并保存
    .venv/bin/python collect/hold_scan.py --keep     # 保留截图到 data/shots
"""
import argparse
import json
import subprocess
import threading
import time
from pathlib import Path

import cv2

from auto_play import Ocr, SERIAL, screencap


def adb(*args):
    subprocess.run(["adb", "-s", SERIAL, "shell", *map(str, args)])

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "labels" / "buildings.json"
SHOTS = ROOT / "data" / "shots"

# 拖拽方向：从中心向右下小幅移动即可触发"按住"状态，位移小可避免视野漂移
HOLDS = [((700, 380), (660, 380)),
         ((700, 380), (740, 380)),
         ((700, 380), (700, 340)),
         ((700, 380), (700, 420))]


def hold_capture(start, end, duration_ms=6000, at=1.6):
    """长时长滑动进行到 at 秒时截图，此时手指仍按在屏幕上。"""
    threading.Thread(target=adb, args=("input", "swipe", *start, *end, duration_ms),
                     daemon=True).start()
    time.sleep(at)
    img = screencap()
    time.sleep(max(0.0, duration_ms / 1000.0 - at))
    return img


def read_names(img, ocr, min_score=0.55):
    """返回 [(name, x, y)]，过滤掉 UI 文案和纯数字。"""
    skip = ("Stage", "Quest", "Events", "Alliance", "Shop", "Level", "Unlock",
            "Details", "Upgrade", "Train", "Speedup", "Claim", "Go", "Base")
    seen, out = [], []
    for text, x, y, score in ocr.texts(img):
        text = text.strip()
        if score < min_score or len(text) < 3:
            continue
        if any(k in text for k in skip) or text.replace(",", "").isdigit():
            continue
        if not (470 < x < 960 and 60 < y < 620):
            continue
        if any((x - px) ** 2 + (y - py) ** 2 < 60 ** 2 for _, px, py in out):
            continue
        out.append((text, x, y))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保存截图到 data/shots")
    args = ap.parse_args()

    ocr = Ocr()
    merged = {}
    for start, end in HOLDS:
        img = hold_capture(start, end)
        names = read_names(img, ocr)
        print(f"hold {start}->{end}: {len(names)} 个名称", flush=True)
        for name, x, y in names:
            print(f"  {x:4d},{y:4d}  {name}", flush=True)
            merged.setdefault(name, []).append([x, y])
        if args.keep:
            SHOTS.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(SHOTS / f"hold_{start[0]}_{start[1]}_{end[0]}.png"), img)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    print(f"已写入 {OUT}（{len(merged)} 个建筑）")


if __name__ == "__main__":
    main()
