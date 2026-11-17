#!/usr/bin/env python3
"""把 recordings 导出为小型策略网络的行为克隆数据集。"""

import argparse
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "data" / "recordings"
OUT = ROOT / "data" / "training"
HEIGHT, WIDTH = 96, 160
HISTORY = 8
STATE_FIELDS = ["wood", "stone", "diamond", "power", "quest"]
MACROS = [
    "WAIT", "BACK", "DRAG", "TAP_XY", "HOME",
    "CONFIRM_POPUP", "OPEN_QUESTS", "CLAIM_FIRST", "GO_FIRST",
    "OPEN_BUILDING", "BUILDING_DETAILS",
    "TRAIN_OPEN", "TRAIN_START", "TRAIN_COUNT",
    "UPGRADE_OPEN", "CONFIRM_UPGRADE",
    "SPEEDUP_OPEN", "USE_SPEEDUP", "FREE_SPEEDUP",
]
ACTION_TYPES = ["tap", "drag", "key", "wait"]


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def numeric_value(value):
    """数值统一做 log 压缩；无法解析时由 mask 表示缺失。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip().replace(",", "")
        multiplier = 1.0
        if text[-1:].upper() == "K":
            multiplier, text = 1e3, text[:-1]
        elif text[-1:].upper() == "M":
            multiplier, text = 1e6, text[:-1]
        try:
            number = float(text) * multiplier
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return math.log1p(max(0.0, number)) / 20.0


def load_labels(ep):
    """离线 OCR/人工标注：state/labels.jsonl 的 (field, hash) -> value。"""
    labels = {}
    for row in read_jsonl(ep / "state" / "labels.jsonl"):
        labels[(row["field"], row["hash"])] = row["value"]
    return labels


def state_vector(state, labels=None):
    result = []
    for field in STATE_FIELDS:
        raw = (state or {}).get(field)
        value = None
        if isinstance(raw, dict):
            value = numeric_value(raw.get("value"))
            if value is None and labels:
                value = numeric_value(labels.get((field, raw.get("hash"))))
        result.extend([0.0 if value is None else value, float(value is not None)])
    return result


def macro_label(step, decision):
    action = step["action"]
    kind = action["type"]
    if kind == "wait":
        return "WAIT"
    if kind == "key":
        return "BACK"
    if kind == "drag":
        return "DRAG"

    # 首选显式标签：decide 里写 action_tag 即锁定宏类，不依赖措辞
    tag = str(decision.get("action_tag", "")).upper()
    if tag in MACROS:
        return tag

    intent = decision.get("intent", "")
    chosen = decision.get("chosen", "")
    text = intent + " " + chosen
    # 旧数据没有 action_tag，按关键词回退；顺序即优先级
    rules = [
        ("CONFIRM_POPUP", ("关闭公告",)),
        ("OPEN_QUESTS", ("打开Quests", "确认主线任务进度", "查看并领取可用任务奖励")),
        ("FREE_SPEEDUP", ("免费完成", "免费加速", "Free")),
        ("USE_SPEEDUP", ("加速道具",)),
        ("SPEEDUP_OPEN", ("Speedup具体成本", "查看加速")),
        ("BUILDING_DETAILS", ("详情", "Details")),
        ("TRAIN_COUNT", ("训练数量", "调整数量")),
        ("TRAIN_START", ("开始训练", "训练10个", "训练7个", "下单训练")),
        ("TRAIN_OPEN", ("练兵", "训练队列", "训练面板", "Train")),
        ("CONFIRM_UPGRADE", ("普通升级", "确认升级")),
        ("UPGRADE_OPEN", ("升级成本", "升级骑兵营", "升级兵营", "Upgrade")),
        ("OPEN_BUILDING", ("升级面板", "建造中兵营", "选中")),
        ("GO_FIRST", ("Go",)),
        ("CLAIM_FIRST", ("领取", "收取", "Claim")),
    ]
    for name, keys in rules:
        if any(key in text for key in keys):
            return name

    xy = action.get("xy_norm")
    if xy and abs(xy[0] - 0.9155) < 0.03 and abs(xy[1] - 0.6642) < 0.03:
        return "OPEN_QUESTS"
    return "TAP_XY"


def action_arrays(step, decision):
    action = step["action"]
    kind = action["type"]
    coords = np.zeros(4, dtype=np.float32)
    coord_mask = 0.0
    duration = 0.0
    duration_mask = 0.0
    if kind == "tap":
        xy = action["xy_norm"]
        coords[:] = [xy[0], xy[1], xy[0], xy[1]]
        coord_mask = 1.0
    elif kind == "drag":
        coords[:] = action["start_norm"] + action["end_norm"]
        coord_mask = 1.0
    if kind in ("tap", "drag", "wait") and action.get("duration_ms") is not None:
        duration = math.log1p(action["duration_ms"]) / math.log1p(30000)
        duration_mask = 1.0
    return {
        "macro": MACROS.index(macro_label(step, decision)),
        "action_type": ACTION_TYPES.index(kind),
        "coords": coords,
        "coord_mask": coord_mask,
        "duration": duration,
        "duration_mask": duration_mask,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--recordings", type=Path, default=REC)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    episodes = []
    for ep in sorted(p for p in args.recordings.iterdir() if p.is_dir()):
        steps = read_jsonl(ep / "steps.jsonl")
        decisions = {
            row["dec"]: row for row in read_jsonl(ep / "decisions.jsonl")
            if row.get("event") == "decide"
        }
        episodes.append((ep, steps, decisions, read_jsonl(ep / "frames.jsonl")))
    if not episodes:
        raise SystemExit("没有找到 steps.jsonl")
    labels_by_ep = {ep.name: load_labels(ep) for ep, _, _, _ in episodes}
    print(f"状态标注：{sum(len(v) for v in labels_by_ep.values())} 条")

    image_paths = []
    path_to_index = {}

    def register(ep, relative):
        key = (ep.name, relative)
        if key in path_to_index:
            return path_to_index[key]
        path = ep / relative
        if not path.exists():
            raise FileNotFoundError(path)
        path_to_index[key] = len(image_paths)
        image_paths.append((ep, relative))
        return path_to_index[key]

    # 均匀帧供后续自监督使用，动作关键帧供当前行为克隆使用。
    for ep, steps, _decisions, frames in episodes:
        for frame in frames:
            if not frame.get("gap") and frame.get("path"):
                register(ep, frame["path"])
        for step in steps:
            register(ep, step["obs"])
            if step.get("obs_prev"):
                register(ep, step["obs_prev"][-1])
            register(ep, step["obs_next"])

    args.output.mkdir(parents=True, exist_ok=True)
    images = np.lib.format.open_memmap(
        args.output / "images.npy", mode="w+", dtype=np.uint8,
        shape=(len(image_paths), HEIGHT, WIDTH),
    )
    image_index = []
    for i, (ep, relative) in enumerate(image_paths):
        image = cv2.imread(str(ep / relative), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise RuntimeError(f"无法解码 {ep / relative}")
        images[i] = cv2.resize(image, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)
        image_index.append({"episode": ep.name, "path": relative})
        if (i + 1) % 500 == 0 or i + 1 == len(image_paths):
            print(f"预处理图像 {i + 1}/{len(image_paths)}", flush=True)
    images.flush()

    count = sum(len(steps) for _, steps, _, _ in episodes)
    state_dim = len(STATE_FIELDS) * 2
    seq_frames = np.zeros((count, HISTORY, 2), dtype=np.int32)
    seq_state = np.zeros((count, HISTORY, state_dim), dtype=np.float32)
    seq_mask = np.zeros((count, HISTORY), dtype=np.float32)
    next_frame = np.zeros(count, dtype=np.int32)
    macro = np.zeros(count, dtype=np.int64)
    action_type = np.zeros(count, dtype=np.int64)
    coords = np.zeros((count, 4), dtype=np.float32)
    coord_mask = np.zeros(count, dtype=np.float32)
    duration = np.zeros(count, dtype=np.float32)
    duration_mask = np.zeros(count, dtype=np.float32)
    episode_names = []
    step_numbers = np.zeros(count, dtype=np.int32)
    sample_rows = []

    cursor = 0
    for ep, steps, decisions, _frames in episodes:
        pairs = []
        states = []
        used_dec = set()  # 一个 decide 只标记其后第一个非 wait 动作，避免后续无关点击被误标
        for step in steps:
            previous = step["obs_prev"][-1] if step.get("obs_prev") else step["obs"]
            pairs.append((register(ep, step["obs"]), register(ep, previous)))
            states.append(state_vector(step.get("state_before"), labels_by_ep[ep.name]))
        for i, step in enumerate(steps):
            history = list(range(max(0, i - HISTORY + 1), i + 1))
            offset = HISTORY - len(history)
            for j, step_index in enumerate(history):
                slot = offset + j
                seq_frames[cursor, slot] = pairs[step_index]
                seq_state[cursor, slot] = states[step_index]
                seq_mask[cursor, slot] = 1.0
            dec_id = step.get("dec")
            if step["action"]["type"] == "wait" or not dec_id or dec_id in used_dec:
                decision = {}
            else:
                decision = decisions.get(dec_id, {})
                used_dec.add(dec_id)
            labels = action_arrays(step, decision)
            macro[cursor] = labels["macro"]
            action_type[cursor] = labels["action_type"]
            coords[cursor] = labels["coords"]
            coord_mask[cursor] = labels["coord_mask"]
            duration[cursor] = labels["duration"]
            duration_mask[cursor] = labels["duration_mask"]
            next_frame[cursor] = register(ep, step["obs_next"])
            episode_names.append(ep.name)
            step_numbers[cursor] = step["step"]
            sample_rows.append({
                "sample": cursor, "episode": ep.name, "step": step["step"],
                "macro": MACROS[macro[cursor]],
                "action_type": ACTION_TYPES[action_type[cursor]],
                "obs": step["obs"], "obs_next": step["obs_next"],
            })
            cursor += 1

    np.savez_compressed(
        args.output / "samples.npz", seq_frames=seq_frames, seq_state=seq_state,
        seq_mask=seq_mask, next_frame=next_frame, macro=macro,
        action_type=action_type, coords=coords, coord_mask=coord_mask,
        duration=duration, duration_mask=duration_mask,
        episode=np.asarray(episode_names), step=step_numbers,
    )
    (args.output / "image_index.json").write_text(json.dumps(image_index, ensure_ascii=False))
    (args.output / "samples.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in sample_rows)
    )
    meta = {
        "version": 1,
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "image_shape": [HEIGHT, WIDTH], "history": HISTORY,
        "state_fields": STATE_FIELDS, "state_encoding": "log1p(value)/20 + presence_mask",
        "macros": MACROS, "action_types": ACTION_TYPES,
        "num_images": len(image_paths), "num_samples": count,
        "state_presence_ratio": round(
            float(seq_state[:, :, 1::2].sum()) / max(1, count * HISTORY * len(STATE_FIELDS)), 4),
        "episodes": dict(Counter(episode_names)),
        "macro_counts": {name: int((macro == i).sum()) for i, name in enumerate(MACROS)},
        "action_type_counts": {
            name: int((action_type == i).sum()) for i, name in enumerate(ACTION_TYPES)
        },
        "image_size_mb": round((args.output / "images.npy").stat().st_size / 1024**2, 2),
    }
    (args.output / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
