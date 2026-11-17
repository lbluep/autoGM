#!/usr/bin/env python3
"""训练数据采集器：连续帧流 + 高层动作流 + (obs, action, obs_next) 样本。

用法：
  collect/record.py start --goal "..."     # 后台常驻，创建 data/recordings/<episode>/
  echo 'plan 升级兵营' > <episode>/cmd.fifo
  echo 'decide {"intent":"升级兵营","options":["升级兵营","攒资源"],"chosen":"升级兵营","expected":"解锁T2兵","action_tag":"UPGRADE_OPEN"}' > <episode>/cmd.fifo

action_tag 为可选显式宏标签，取值见 training/export_dataset.py 的 MACROS；
写入后导出时优先使用，不再依赖中文措辞推断。
  echo 'tap 520 792' > <episode>/cmd.fifo
  echo 'outcome D1 兵营进入建造倒计时' > <episode>/cmd.fifo
  echo 'state' > <episode>/cmd.fifo
  echo 'stop' > <episode>/cmd.fifo

设计要点：
- 所有时间戳用 time.monotonic()；每帧记录 t_req/t_done，因为 screencap 延迟可达 1s
- 动作注入（adb input）不经过 /dev/input，所以动作由本进程直接记录，source=agent
- 均匀帧存裁剪后的 JPEG（训练输入），动作前后另存原始 PNG（分析）+ 裁剪 JPEG（训练）
- 拖拽只记录起终点、持续时间、可选目的和前后画面，不采集轨迹或估计相机位移
- B 层状态：HUD 区域指纹去重保存，动作前后/空闲间隔各留状态点；数值由 labels 表补充
- C 层决策：plan→decide→动作→outcome，把长链条结果与当初的决策显式关联
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import collect as C

REC = C.DATA / "recordings"
LATEST = REC / "LATEST.txt"
UNIFORM_DT = 0.30
STATE_DT = 60.0          # 空闲时的状态快照间隔；动作前后另有快照
PKG = "c.x.snow.sj"
SCHEMA_VERSION = 3

# 显示坐标；区域偏小，只覆盖数值本体，避免背景动画影响指纹
HUD_REGIONS = {
    "wood":    (478, 0, 80, 38),
    "stone":   (568, 0, 82, 38),
    "diamond": (858, 2, 54, 26),
    "power":   (680, 40, 100, 34),
    "quest":   (576, 624, 260, 40),
}


def mono():
    return time.monotonic()


def dhash(img):
    """64 bit 感知指纹：对整体变暗/遮罩不敏感，只对内容结构变化敏感。"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = 0
    for r in range(8):
        for c in range(8):
            bits = (bits << 1) | int(g[r, c] > g[r, c + 1])
    return bits


def hamming64(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def jlog(path, rec):
    with open(path, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Recorder:
    def __init__(self, goal):
        self.ep = REC / datetime.now().strftime("%Y%m%dT%H%M%S")
        for sub in ("frames", "keyframes", "raw"):
            (self.ep / sub).mkdir(parents=True, exist_ok=True)
        self.goal = goal
        self.capture_state = (None, "uniform")  # (step_id, pre/action/settle/post/uniform)
        self.capture_lock = threading.Lock()
        self.stop_flag = False
        self.frame_idx = 0
        self.gap_count = 0
        self.step_idx = 0
        self.recent = []          # 最近的均匀帧 (idx, t_done)
        self.tmp = self.ep / "raw" / "_uniform.png"
        (self.ep / "state" / "regions").mkdir(parents=True, exist_ok=True)
        self.state_idx = 0
        self.state_lock = threading.Lock()
        self.last_state_t = mono()
        self.dec_idx = 0
        self.plan_idx = 0
        self.plan_id = None
        self.active_dec = None
        self.pending_dec = {}      # dec_id -> {field: hash}，用于延迟结果对比
        self.labels = self.load_labels()
        pkg_out = subprocess.run(
            ["adb", "shell", "dumpsys", "package", PKG],
            capture_output=True, text=True, timeout=10).stdout
        app_version = {}
        for line in pkg_out.splitlines():
            line = line.strip()
            for key in ("versionCode", "versionName"):
                if line.startswith(key + "="):
                    app_version[key] = line.split("=", 1)[1].split()[0]
        (self.ep / "meta.json").write_text(json.dumps({
            "schema_version": SCHEMA_VERSION,
            "episode": self.ep.name, "goal": goal,
            "started_wall": C.now(), "started_mono": mono(),
            "serial": C.SERIAL, "package": PKG, "app_version": app_version,
            "display": [C.W, C.H], "game_area": C.GA,
            "target_fps_uniform": round(1 / UNIFORM_DT, 2),
            "state_interval_s": STATE_DT,
            "hud_regions": {k: list(v) for k, v in HUD_REGIONS.items()},
            "clock": "time.monotonic (seconds), host",
            "phase_convention": "frame 的 step/phase 按 t_req 时的录制状态标记",
            "coord_convention": "action 用显示坐标(1440x873)+游戏区归一化",
            "drag_role": "高层导航原语：露出当前画面外目标；intent 可选，不记录轨迹或相机位移",
            "state_convention": "HUD 区域指纹去重保存；数值来自 state/labels.jsonl 的 hash→value 标注，缺失为 null",
            "decision_convention": "plan→decide→动作(dec)→outcome(延迟状态对比)，用于长链条策略",
            "recorder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source": "agent (adb input 注入)",
        }, ensure_ascii=False, indent=2))
        LATEST.write_text(str(self.ep))

    # ---------- 帧流 ----------
    def snap(self, dest):
        # adb 并发 screencap 会把单次采集拖到十几秒；所有入口共用同一把锁。
        with self.capture_lock:
            return C.capture(dest)

    def grab(self, dest):
        t0 = mono()
        self.snap(dest)
        t1 = mono()
        return t0, t1

    def crop(self, src, dst, jpeg=True):
        dst.parent.mkdir(parents=True, exist_ok=True)
        vf = f"crop={C.GA[2]}:{C.GA[3]}:{C.GA[0]}:{C.GA[1]}"
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf]
        if jpeg:
            cmd += ["-q:v", "3"]
        cmd.append(str(dst))
        subprocess.run(cmd, check=True)

    def frame_loop(self):
        while not self.stop_flag:
            step_id, phase = self.capture_state
            t_end = mono() + UNIFORM_DT
            self.frame_idx += 1
            i = self.frame_idx
            try:
                t0, t1 = self.grab(self.tmp)
                out = self.ep / "frames" / f"{i:06d}.jpg"
                self.crop(self.tmp, out)
                rec = {"i": i, "step": step_id, "phase": phase,
                       "t_req": round(t0, 3), "t_done": round(t1, 3),
                       "latency_ms": round((t1 - t0) * 1000),
                       "path": f"frames/{i:06d}.jpg"}
                jlog(self.ep / "frames.jsonl", rec)
                self.recent.append((i, t1))
                self.recent = self.recent[-8:]
                # 空闲时按固定间隔留状态点，复用本帧原始图，不额外采集
                if phase == "uniform" and mono() - self.last_state_t >= STATE_DT:
                    self.last_state_t = mono()
                    self.snapshot(self.tmp, "interval")
            except Exception as e:
                self.gap_count += 1
                jlog(self.ep / "frames.jsonl",
                     {"i": i, "step": step_id, "phase": phase,
                      "t": round(mono(), 3), "gap": True, "error": str(e)})
            time.sleep(max(0, t_end - mono()))

    # ---------- 动作 ----------
    def norm(self, x, y):
        gx, gy, gw, gh = C.GA
        return [round((x - gx) / gw, 4), round((y - gy) / gh, 4)]

    def screen_match(self, raw):
        unknown = {"id": None, "distance": None, "margin": None}
        try:
            h = C.anchors_hash(C.decode(raw))
            matches = []
            for d in (C.DATA / "states").iterdir():
                p = d / "current.json"
                if not p.exists():
                    continue
                st = json.loads(p.read_text())
                if st.get("hash"):
                    matches.append((C.hamming(h, st["hash"]), d.name))
            if not matches:
                return unknown
            matches.sort()
            dist, name = matches[0]
            margin = matches[1][0] - dist if len(matches) > 1 else None
            return {"id": name if dist <= 64 else None,
                    "distance": dist, "margin": margin}
        except Exception:
            return unknown

    # ---------- 状态时间线（B 层） ----------
    def load_labels(self):
        """state/labels.jsonl: {"field","hash","value"}，数值标注与图像解耦。"""
        labels = {}
        p = self.ep / "state" / "labels.jsonl"
        if not p.exists():
            return labels
        for line in p.read_text().splitlines():
            try:
                r = json.loads(line)
                labels[(r["field"], r["hash"])] = r["value"]
            except Exception:
                continue
        return labels

    def state_of(self, raw_path):
        """从已有原始截图提取 HUD 状态；按区域指纹去重存储，不额外占用 adb。"""
        img = cv2.imread(str(raw_path), cv2.IMREAD_COLOR)
        if img is None:
            return None
        fields = {}
        for name, (x, y, w, h) in HUD_REGIONS.items():
            crop = img[y:y + h, x:x + w]
            ok, buf = cv2.imencode(".png", crop)
            if not ok:
                continue
            digest = hashlib.sha1(buf.tobytes()).hexdigest()[:16]
            path = self.ep / "state" / "regions" / f"{name}__{digest}.png"
            if not path.exists():
                path.write_bytes(buf.tobytes())
            fields[name] = {"hash": digest,
                            "ph": f"{dhash(crop):016x}",
                            "value": self.labels.get((name, digest))}
        return fields

    def snapshot(self, raw_path, reason):
        """写一条 state.jsonl，返回快照摘要（含 snap id 与字段指纹）。"""
        fields = self.state_of(raw_path)
        if not fields:
            return None
        with self.state_lock:
            self.state_idx += 1
            snap_id = self.state_idx
        rec = {"snap": snap_id, "t": round(mono(), 3), "wall": C.now(),
               "reason": reason, "step": self.step_idx,
               "screen": self.screen_match(raw_path)["id"],
               "fields": fields}
        jlog(self.ep / "state.jsonl", rec)
        return rec

    def field_hashes(self, snap):
        return {k: {"hash": v["hash"], "ph": v["ph"], "value": v["value"]}
                for k, v in snap["fields"].items()}

    @staticmethod
    def changed_fields(before, after, thr=8):
        """按感知指纹判断内容是否真的变了；整体变暗不算变化。"""
        if not before or not after:
            return []
        out = []
        for name, b in before.items():
            a = after.get(name)
            if not a:
                continue
            if hamming64(b["ph"], a["ph"]) > thr:
                out.append(name)
        return sorted(out)

    def act(self, action, fn, settle_after=True):
        obs_prev = list(self.recent[-2:])
        gap_start = self.gap_count
        self.step_idx += 1
        self.capture_state = (self.step_idx, "pre")
        pre_raw, pre_jpg, pre_t0, pre_t1 = self.grab_pre()
        t0 = mono()
        self.capture_state = (self.step_idx, "action")
        fn()
        t1 = mono()
        if settle_after:
            self.capture_state = (self.step_idx, "settle")
            st = C.settle(capture_fn=self.snap)
        else:
            st = None
        t2 = mono()
        self.capture_state = (self.step_idx, "post")
        post_raw, post_jpg, post_t0, post_t1 = self.grab_post()
        self.capture_state = (None, "uniform")
        before = self.screen_match(pre_raw)
        after = self.screen_match(post_raw)
        state_before = self.snapshot(pre_raw, "action_pre")
        state_after = self.snapshot(post_raw, "action_post")
        step = {
            "step": self.step_idx,
            "plan": self.plan_id,
            "dec": self.active_dec,
            "state_before": self.field_hashes(state_before) if state_before else None,
            "state_after": self.field_hashes(state_after) if state_after else None,
            # 不同界面的 HUD 区域不可比：整屏换面板时不是数值变化
            "state_comparable": bool(before["id"] and before["id"] == after["id"]),
            "state_changed": self.changed_fields(
                state_before["fields"] if state_before else None,
                state_after["fields"] if state_after else None)
            if before["id"] and before["id"] == after["id"] else None,
            "t": round(t0, 3),
            "obs": f"keyframes/{self.step_idx:04d}_pre.jpg",
            "obs_raw": f"raw/{self.step_idx:04d}_pre.png",
            "obs_t_req": round(pre_t0, 3),
            "obs_t_done": round(pre_t1, 3),
            "obs_prev": [f"frames/{i:06d}.jpg" for i, _ in obs_prev],
            "obs_next": f"keyframes/{self.step_idx:04d}_post.jpg",
            "obs_next_raw": f"raw/{self.step_idx:04d}_post.png",
            "obs_next_t_req": round(post_t0, 3),
            "obs_next_t_done": round(post_t1, 3),
            "action": action,
            "t_action_end": round(t1, 3),
            "t_settled": round(t2, 3),
            "settle_ms": round((t2 - t1) * 1000) if settle_after else None,
            "settle_diff": round(st, 3) if st is not None else None,
            "settle_stable": (st < 2.5) if settle_after and st is not None else
                             (False if settle_after else None),
            "screen_before": before["id"],
            "screen_before_distance": before["distance"],
            "screen_before_margin": before["margin"],
            "screen_after": after["id"],
            "screen_after_distance": after["distance"],
            "screen_after_margin": after["margin"],
            "gap": self.gap_count > gap_start,
        }
        jlog(self.ep / "actions.jsonl", {"step": self.step_idx, "t": round(t0, 3),
                                         "t_done": round(t1, 3), **action})
        jlog(self.ep / "steps.jsonl", step)
        print(json.dumps({"step": self.step_idx, "action": action["type"],
                          "intent": action.get("intent"),
                          "screen": [step["screen_before"], step["screen_after"]]},
                         ensure_ascii=False), flush=True)

    def grab_pre(self):
        raw = self.ep / "raw" / f"{self.step_idx:04d}_pre.png"
        t0, t1 = self.grab(raw)
        jpg = self.ep / "keyframes" / f"{self.step_idx:04d}_pre.jpg"
        self.crop(raw, jpg)
        return raw, jpg, t0, t1

    def grab_post(self):
        raw = self.ep / "raw" / f"{self.step_idx:04d}_post.png"
        t0, t1 = self.grab(raw)
        jpg = self.ep / "keyframes" / f"{self.step_idx:04d}_post.jpg"
        self.crop(raw, jpg)
        return raw, jpg, t0, t1

    def handle(self, line):
        p = line.split()
        if not p:
            return
        cmd = p[0]
        if cmd == "stop":
            self.stop_flag = True
            return
        if cmd == "tap":
            x, y = int(p[1]), int(p[2])
            a = {"type": "tap", "x": x, "y": y, "xy_norm": self.norm(x, y),
                 "source": "agent"}
            self.act(a, lambda: subprocess.run(
                ["adb", "shell", "input", "tap", str(x), str(y)]))
        elif cmd == "drag":
            x0, y0, x1, y1, dur = map(int, p[1:6])
            a = {"type": "drag", "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                 "duration_ms": dur,
                 "start_norm": self.norm(x0, y0), "end_norm": self.norm(x1, y1),
                 "source": "agent"}
            intent = " ".join(p[6:]).strip()
            if intent:
                a["intent"] = intent
            self.act(a, lambda: subprocess.run(
                ["adb", "shell", "input", "swipe",
                 str(x0), str(y0), str(x1), str(y1), str(dur)]))
        elif cmd == "key":
            a = {"type": "key", "code": int(p[1]), "source": "agent"}
            self.act(a, lambda: subprocess.run(
                ["adb", "shell", "input", "keyevent", p[1]]))
        elif cmd == "wait":
            ms = int(p[1])
            a = {"type": "wait", "duration_ms": ms, "source": "agent"}
            self.act(a, lambda: time.sleep(ms / 1000), settle_after=False)
        elif cmd == "mark":
            jlog(self.ep / "markers.jsonl",
                 {"t": round(mono(), 3), "after_step": self.step_idx,
                  "text": line[5:].strip()})
        # ---------- 决策链（C 层） ----------
        elif cmd == "plan":
            self.plan_idx += 1
            self.plan_id = f"P{self.plan_idx}"
            jlog(self.ep / "decisions.jsonl",
                 {"event": "plan", "plan": self.plan_id, "t": round(mono(), 3),
                  "wall": C.now(), "after_step": self.step_idx,
                  "text": line[5:].strip()})
            print(json.dumps({"plan": self.plan_id}, ensure_ascii=False), flush=True)
        elif cmd == "decide":
            payload = json.loads(line[7:].strip())
            self.dec_idx += 1
            dec_id = f"D{self.dec_idx}"
            self.active_dec = dec_id
            self.snap(self.ep / "raw" / "_state.png")
            snap = self.snapshot(self.ep / "raw" / "_state.png", "decision")
            if snap:
                self.pending_dec[dec_id] = {"screen": snap["screen"],
                                            "fields": self.field_hashes(snap)}
            jlog(self.ep / "decisions.jsonl",
                 {"event": "decide", "dec": dec_id, "plan": self.plan_id,
                  "t": round(mono(), 3), "wall": C.now(),
                  "after_step": self.step_idx, "state_snap": snap["snap"] if snap else None,
                  "screen": snap["screen"] if snap else None,
                  "state": snap["fields"] if snap else None, **payload})
            print(json.dumps({"decide": dec_id}, ensure_ascii=False), flush=True)
        elif cmd == "outcome":
            dec_id = p[1]
            note = " ".join(p[2:]).strip()
            self.snap(self.ep / "raw" / "_state.png")
            snap = self.snapshot(self.ep / "raw" / "_state.png", "outcome")
            before = self.pending_dec.pop(dec_id, None)
            comparable = bool(before and snap and before["screen"] == snap["screen"])
            changed = self.changed_fields(before["fields"], snap["fields"]) \
                if comparable else None
            jlog(self.ep / "decisions.jsonl",
                 {"event": "outcome", "dec": dec_id, "plan": self.plan_id,
                  "t": round(mono(), 3), "wall": C.now(),
                  "after_step": self.step_idx,
                  "state_snap": snap["snap"] if snap else None,
                  "state": snap["fields"] if snap else None,
                  "screens": [before["screen"] if before else None,
                              snap["screen"] if snap else None],
                  "state_comparable": comparable,
                  "changed_fields": changed, "note": note})
            if self.active_dec == dec_id:
                self.active_dec = None
            print(json.dumps({"outcome": dec_id, "changed": changed},
                             ensure_ascii=False), flush=True)
        elif cmd == "state":
            self.snap(self.ep / "raw" / "_state.png")
            snap = self.snapshot(self.ep / "raw" / "_state.png", "manual")
            print(json.dumps(snap, ensure_ascii=False), flush=True)
        elif cmd == "labels":
            # 重新载入人工/OCR 标注；之后的快照即可带数值
            self.labels = self.load_labels()
            print(json.dumps({"labels": len(self.labels)}, ensure_ascii=False), flush=True)

    def run(self):
        th = threading.Thread(target=self.frame_loop, daemon=True)
        th.start()
        fifo = self.ep / "cmd.fifo"
        os.mkfifo(fifo)
        # 常驻写端，避免 echo 写入后读端收到 EOF；必须 O_RDWR：
        # 先 open(fifo,"w") 会阻塞等读端，与后开的读端形成死锁
        keep = os.fdopen(os.open(fifo, os.O_RDWR))
        with open(fifo, "r") as r:
            while not self.stop_flag:
                line = r.readline()   # 不能用 for line in r：预读缓冲会卡住 FIFO
                if not line:
                    time.sleep(0.05)
                    continue
                try:
                    self.handle(line.strip())
                except Exception as e:
                    print("ERROR", e, flush=True)
        keep.close()
        th.join(timeout=2)
        print(json.dumps({"episode": str(self.ep), "steps": self.step_idx,
                          "frames": self.frame_idx}), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start"])
    ap.add_argument("--goal", default="")
    args = ap.parse_args()
    r = Recorder(args.goal)
    print(json.dumps({"episode": str(r.ep)}, ensure_ascii=False), flush=True)
    r.run()


if __name__ == "__main__":
    main()
