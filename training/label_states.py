#!/usr/bin/env python3
"""离线 OCR 回填 HUD 数值：state/regions/*.png -> state/labels.jsonl。

只处理数值字段（wood/stone/diamond/power），quest 是文本不做数值标注。
每个 (field, hash) 只识别一次；无法解析成合法数值时跳过，避免写脏标签。
"""

import argparse
import json
import re
from pathlib import Path

import cv2
from rapidocr_onnxruntime import RapidOCR

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "data" / "recordings"
FIELDS = ("wood", "stone", "diamond", "power")
NUMBER = re.compile(r"^(\d{1,3}(,\d{3})+|\d+)(\.\d+)?[KM]?$")


def normalize(text):
    """OCR 文本 -> 规范数值字符串（如 446.3K / 42,929）；不合法返回 None。"""
    t = text.strip().replace(" ", "").replace("，", ",").replace("。", ".")
    t = t.replace("O", "0").replace("o", "0").rstrip(".")
    t = re.sub(r"^0+(?=\d)", "", t)  # 图标误识别产生的前导零
    if t and NUMBER.match(t):
        return t
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recordings", type=Path, default=REC)
    parser.add_argument("--episodes", nargs="*", default=None, help="默认全部 episode")
    args = parser.parse_args()

    engine = RapidOCR()
    for ep in sorted(p for p in args.recordings.iterdir() if p.is_dir()):
        if args.episodes and ep.name not in args.episodes:
            continue
        regions = ep / "state" / "regions"
        if not regions.is_dir():
            continue
        label_path = ep / "state" / "labels.jsonl"
        existing = {}
        if label_path.exists():
            for line in label_path.read_text().splitlines():
                if line.strip():
                    row = json.loads(line)
                    existing[(row["field"], row["hash"])] = row["value"]

        new_rows = []
        stats = {"ok": 0, "skip": 0, "cached": 0}
        for path in sorted(regions.glob("*.png")):
            field, _, digest = path.stem.partition("__")
            if field not in FIELDS or not digest:
                continue
            if (field, digest) in existing:
                stats["cached"] += 1
                continue
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is None:
                stats["skip"] += 1
                continue
            img = cv2.resize(img, None, fx=6, fy=6, interpolation=cv2.INTER_CUBIC)
            img = cv2.copyMakeBorder(img, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=255)
            result, _ = engine(img, use_det=False, use_cls=False, use_rec=True)
            text = result[0][0] if result else ""
            value = normalize(text)
            if value is None:
                stats["skip"] += 1
                continue
            new_rows.append({"field": field, "hash": digest, "value": value, "raw": text})
            stats["ok"] += 1

        if new_rows:
            with label_path.open("a") as fh:
                for row in new_rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"{ep.name}: 新增 {stats['ok']}，跳过 {stats['skip']}，已有 {stats['cached']}", flush=True)


if __name__ == "__main__":
    main()
