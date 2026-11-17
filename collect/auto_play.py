#!/usr/bin/env python3
"""规则化自动采集：不依赖大模型，自己截图、定位、点击、打标签。

用法（在项目 .venv 中运行）：
  .venv/bin/python collect/auto_play.py --camp 560,270 --cycles 5
  .venv/bin/python collect/auto_play.py --camp 774,310 --cycles 3 --dry-run
  .venv/bin/python collect/auto_play.py --camp 560,270 --upgrade --keep-recording
  .venv/bin/python collect/auto_play.py --cycles 100        # 不给坐标，模板自动找营

找营：
  - data/templates/*.png 多尺度 matchTemplate 产生候选点（只负责候选，不负责确认）；
  - 选中后点最右圆钮，能打开练兵界面才认定是营地（行为判定，不依赖小字 OCR），
    因此步兵/骑兵/弓兵营通用，误开其他面板会 BACK 返回城市；
  - 当前视野找不到时向四个方向平移搜索，营地位置缓存复用，失效自动重找。

原理：
  - 所有点击通过 record.py 的 cmd.fifo 注入，因此 frames/steps/decisions 全部落盘；
  - 每个动作前写一条 decide（含 action_tag），导出时即为带标签样本；
  - 建筑面板的 Details/Upgrade/Train 是青色圆形图标，用颜色连通域定位，
    不按固定坐标硬点，因此相机平移后仍然可用；
  - 只用免费加速（Free 气泡），绝不点 Train Now / Upgrade Now（钻石）。

安全：
  - --cycles 限制轮数，连续 --max-failures 次打不开面板即停止；
  - --dry-run 只识别不点击，用于先验证定位是否正确。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "data" / "recordings"
SERIAL = "192.168.240.112:5555"
GA = (472, 0, 497, 807)          # 游戏区 x,y,w,h
SCREEN_W, SCREEN_H = 1440, 873
TEMPLATE_DIR = ROOT / "data" / "templates"
OFFSET_FILE = TEMPLATE_DIR / "camp_offsets.json"  # 标准视野下营地的固定平移量，自校准后缓存


# ---------- 基础设备操作 ----------

def screencap():
    raw = subprocess.run(["adb", "-s", SERIAL, "exec-out", "screencap", "-p"],
                         capture_output=True, check=True).stdout
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("screencap 解码失败")
    return img


def cyan_blobs(img, y_min, y_max, min_area):
    """青色圆形/按钮的连通域中心（全屏坐标），按 x 排序。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (80, 80, 80), (115, 255, 255))
    mask[:y_min, :] = 0
    mask[y_max:, :] = 0
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    blobs = [(int(x + w / 2), int(y + h / 2), int(a))
             for x, y, w, h, a in stats[1:] if a > min_area]
    return sorted(blobs)


def panel_buttons(img):
    """建筑选中后的三个圆钮：左 Details / 中 Upgrade(Speedup) / 右 Train。"""
    return cyan_blobs(img, 200, 520, 600)


def train_button(img):
    """练兵界面底部 Train 按钮；不存在则说明不在练兵界面。"""
    blobs = cyan_blobs(img, 700, SCREEN_H, 1000)
    return max(blobs, key=lambda b: b[2])[:2] if blobs else None


def load_templates():
    templates = []
    for path in sorted(TEMPLATE_DIR.glob("*.png")):
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            templates.append((path.stem, img))
    return templates


def match_candidates(img, templates, scales=(0.45, 0.6, 0.75, 0.9, 1.0, 1.15, 1.3, 1.5),
                     threshold=0.35, topk=6, roi=None):
    """多尺度模板匹配，返回 [(name, score, cx, cy)] 全屏坐标，已做近邻去重。

    roi=(x0,y0,x1,y1) 限定搜索区域（全屏坐标），用于避开 UI 面板误匹配。
    """
    if not templates:
        return []
    gx, gy, gw, gh = GA
    x0, y0, x1, y1 = roi if roi else (gx, gy + 60, gx + gw, gy + 620)
    gray = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    hits = []
    for name, tpl in templates:
        for scale in scales:
            t = cv2.resize(tpl, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            th, tw = t.shape
            if th >= gray.shape[0] or tw >= gray.shape[1]:
                continue
            res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= threshold)
            for py, px in zip(ys, xs):
                hits.append((name, float(res[py, px]), int(px + tw / 2 + x0), int(py + th / 2 + y0)))
    hits.sort(key=lambda h: -h[1])
    picked = []
    for name, score, cx, cy in hits:
        if all((cx - px) ** 2 + (cy - py) ** 2 > 40 ** 2 for _, _, px, py in picked):
            picked.append((name, score, cx, cy))
        if len(picked) >= topk:
            break
    return picked


class Ocr:
    """延迟加载 OCR，只在需要读文字时初始化一次。"""

    def __init__(self):
        self.engine = None

    def _ready(self):
        if self.engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self.engine = RapidOCR()

    def line(self, img):
        """单行识别，用于按钮下方的小标签。"""
        self._ready()
        result, _ = self.engine(img, use_det=False, use_cls=False, use_rec=True)
        return result[0][0] if result else ""

    def texts(self, img):
        if self.engine is None:
            from rapidocr_onnxruntime import RapidOCR
            self.engine = RapidOCR()
        x, y, w, h = GA
        crop = cv2.resize(img[y:y + h, x:x + w], None, fx=2, fy=2,
                          interpolation=cv2.INTER_CUBIC)
        result, _ = self.engine(crop)
        out = []
        for box, text, score in result or []:
            cx = int(sum(p[0] for p in box) / 4 / 2 + x)
            cy = int(sum(p[1] for p in box) / 4 / 2)
            out.append((text, cx, cy, score))
        return out


# ---------- 录制器接口 ----------

class Recorder:
    def __init__(self, goal, keep):
        self.keep = keep
        self.started = False
        self.ep = None
        self.fifo = None
        latest = REC / "LATEST.txt"
        # FIFO 有读者才算可用：录制器退出后 fifo 文件仍在，直接写入会卡死
        if latest.exists():
            ep = Path(latest.read_text().strip())
            if self.fifo_ready(ep / "cmd.fifo"):
                self.ep, self.fifo = ep, ep / "cmd.fifo"
        if self.fifo is None:
            previous = latest.read_text().strip() if latest.exists() else ""
            log = open("/tmp/auto_record.log", "w")
            subprocess.Popen([sys.executable, str(ROOT / "collect" / "record.py"),
                              "start", "--goal", goal], cwd=ROOT,
                             stdout=log, stderr=log)
            self.started = True
            deadline = time.time() + 30
            while time.time() < deadline:
                if latest.exists():
                    text = latest.read_text().strip()
                    if text and text != previous:  # 必须等到新 episode，避免读到旧路径
                        ep = Path(text)
                        if self.fifo_ready(ep / "cmd.fifo"):
                            self.ep, self.fifo = ep, ep / "cmd.fifo"
                            break
                time.sleep(1)
        if self.fifo is None:
            raise RuntimeError("录制器启动失败，见 /tmp/auto_record.log")
        print(f"episode: {self.ep}", flush=True)

    @staticmethod
    def fifo_ready(path):
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return False
        os.close(fd)
        return True

    def send(self, line):
        # 非阻塞打开：录制器不在时立即报错，而不是卡在 FIFO 上
        fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "w") as fh:
            fh.write(line + "\n")

    def decide(self, tag, intent, chosen, expected="", risk="none"):
        payload = {"intent": intent, "chosen": chosen, "expected": expected,
                   "risk": risk, "action_tag": tag, "source": "auto_play"}
        self.send("decide " + json.dumps(payload, ensure_ascii=False))

    def last_dec(self):
        path = self.ep / "decisions.jsonl"
        if not path.exists():
            return None
        for line in reversed(path.read_text().splitlines()):
            row = json.loads(line)
            if row.get("event") == "decide":
                return row["dec"]
        return None

    def outcome(self, note):
        dec = self.last_dec()
        if dec:
            self.send(f"outcome {dec} {note}")

    def stop(self):
        if self.started and not self.keep:
            self.send("stop")


class Player:
    PLAY_ROI = (495, 100, 945, 555)   # 只在地图中央识别，避开左右两侧 UI 面板

    def __init__(self, rec, dry_run=False, settle=0.8, anchor=None):
        self.rec = rec
        self.dry = dry_run
        self.settle = settle
        self.ocr = Ocr()
        self.actions = 0
        self.templates = load_templates()
        self.anchor = anchor          # 已验证的营地坐标（当前视野内）
        if not self.templates:
            print("警告：data/templates 为空，只能使用 --camp 固定坐标", flush=True)

    def tap(self, x, y, tag, intent, chosen, expected="", risk="none"):
        print(f"[{tag}] tap {x},{y}  {intent}", flush=True)
        self.rec.decide(tag, intent, chosen, expected, risk)
        self.actions += 1
        if not self.dry:
            self.rec.send(f"tap {x} {y}")
        time.sleep(self.settle)

    def wait_for(self, fn, timeout=12):
        """轮询直到条件满足；FIFO 命令经录制器异步执行，固定 sleep 不可靠。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = fn()
            if result:
                return result
            time.sleep(1.0)
        return None

    def find_free(self):
        """城市上方的免费加速气泡；返回中心坐标或 None。"""
        for text, x, y, score in self.ocr.texts(screencap()):
            if text.strip().lower() == "free" and y < 500 and score > 0.7:
                return x, y
        return None

    def open_panel(self, anchor):
        """选中建筑并返回三个圆钮；失败返回 None。"""
        if not (60 <= anchor[1] - GA[1] <= 620):
            return None  # 只允许点城市区域，HUD 一律不点
        for _ in range(2):  # 已选中的建筑再点一次会取消选中，需要补点
            self.tap(*anchor, "OPEN_BUILDING", "选中建筑", "点建筑", "弹出 Details/Upgrade/Train")
            buttons = self.wait_for(lambda: panel_buttons(screencap()), timeout=4)
            if buttons and len(buttons) >= 3:
                return buttons
        return None

    def back_to_city(self):
        self.rec.decide("BACK", "关闭面板返回城市", "BACK", "恢复城市视野")
        self.actions += 1
        if not self.dry:
            self.rec.send("key KEYCODE_BACK")
        time.sleep(1.2)

    def pan(self, dx, dy):
        self.rec.decide("DRAG", "平移视野", f"{dx},{dy}", "视野移动")
        self.actions += 1
        if not self.dry:
            self.rec.send(f"drag 700 400 {700 + dx} {400 + dy} 600 视野平移")
        time.sleep(1.6)

    def reset_corner(self):
        """一直朝同一方向拖到地图边界，相机被夹住后就是可复现的起点（实测帧差 <1）。"""
        for _ in range(8):
            self.pan(400, 0)
        for _ in range(8):
            self.pan(0, 400)

    def load_offsets(self):
        try:
            return json.loads(OFFSET_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def save_offset(self, name, cell, pos):
        offsets = self.load_offsets()
        offsets[name] = {"cell": [cell[0], cell[1]], "pos": list(pos)}
        OFFSET_FILE.write_text(json.dumps(offsets, indent=2, ensure_ascii=False) + "\n")

    def goto_cell(self, cell, step=(330, 330)):
        """从左上角起点按格子索引平移；拖拽方向与相机移动相反。"""
        self.pan(-cell[1] * step[0], -cell[0] * step[1])

    def goto_cached_camp(self):
        """用上次扫描到的格子精确回到营地。"""
        for name, entry in self.load_offsets().items():
            if not self.ensure_city():
                return False
            self.reset_corner()
            self.goto_cell(entry["cell"])
            if self.try_open_training(tuple(entry["pos"])):
                self.anchor = tuple(entry["pos"])
                print(f"  复用格子 {entry['cell']} 打开 {name}", flush=True)
                return True
        return False

    def scan_base(self, rows=4, cols=4, step=(330, 330)):
        """Base 从左上角按网格扫描：模板只给候选，能打开练兵界面才算营地。"""
        if not self.ensure_city():
            return False
        self.reset_corner()
        for row in range(rows):
            for col in range(cols):
                img = screencap()
                for name, score, cx, cy in match_candidates(
                        img, self.templates, threshold=0.45, topk=1, roi=self.PLAY_ROI):
                    print(f"  候选 {name} ({cx},{cy}) {score:.2f} cell({row},{col})", flush=True)
                    if self.try_open_training((cx, cy)):
                        self.save_offset(name, (row, col), (cx, cy))
                        self.anchor = (cx, cy)
                        print(f"  确认营地 {name} cell({row},{col}) ({cx},{cy})", flush=True)
                        return True
                if col < cols - 1:
                    self.pan(-step[0], 0)
            # 回到本行最左，再下移一行
            self.pan((cols - 1) * step[0], 0)
            if row < rows - 1:
                self.pan(0, -step[1])
        return False

    def in_city(self):
        """城市是亮色雪地，菜单/资料页是深色全屏；用平均亮度区分，不必 OCR。"""
        img = screencap()
        x, y, w, h = GA
        gray = cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
        return float(gray.mean()) > 80 and train_button(img) is None

    def ensure_city(self):
        # 深色全屏菜单（Shop/Profile 等）通常右上角有 X，BACK 对其中一些无效，交替尝试
        for attempt in range(4):
            if self.in_city():
                return True
            if attempt % 2 == 0:
                print("  不在城市视野，点右上角 X 关闭", flush=True)
                self.tap(937, 26, "CONFIRM_POPUP", "关闭全屏菜单", "X", "恢复城市视野")
            else:
                print("  不在城市视野，按 BACK 返回", flush=True)
                self.back_to_city()
            time.sleep(1.0)
        return self.in_city()

    def ensure_panel(self):
        """找到任一有三圆钮的建筑面板（升级用）。"""
        if not self.ensure_city():
            return None
        for name, score, cx, cy in match_candidates(screencap(), self.templates,
                                                   threshold=0.5, topk=2):
            buttons = self.open_panel((cx, cy))
            if buttons:
                self.anchor = (cx, cy)
                return buttons
        for entry in self.load_offsets().values():
            buttons = self.open_panel(tuple(entry["pos"]))
            if buttons:
                self.anchor = tuple(entry["pos"])
                return buttons
        return None

    def try_open_training(self, anchor):
        buttons = self.open_panel(anchor)
        if not buttons:
            return False
        # 营地有 3 个圆钮（Details/Upgrade/Train），生产建筑只有 2 个，先用这个廉价过滤
        if len(buttons) < 3:
            self.rec.outcome("只有两个按钮，不是营地")
            self.back_to_city()
            return False
        # Train 不一定在最右，逐个尝试（右优先）
        ordered = sorted(buttons, key=lambda b: -b[0])
        for index, btn in enumerate(ordered):
            if index:
                buttons = self.open_panel(anchor)  # 重新选中再试下一个按钮
                if not buttons:
                    return False
                btn = min(buttons, key=lambda b: abs(b[0] - btn[0]) + abs(b[1] - btn[1]))
            self.tap(btn[0], btn[1], "TRAIN_OPEN", "进入练兵界面", "Train",
                     "显示单位/成本/数量")
            if self.wait_for(lambda: train_button(screencap()), timeout=8):
                return True
            self.rec.outcome("打开的不是练兵界面，返回城市")
            self.back_to_city()
        return False

    def ensure_training(self):
        """优先复用已扫描到的格子；失败则从左上角重新网格扫描。行为判定，各兵营通用。"""
        if self.anchor and self.try_open_training(self.anchor):
            return True
        if self.goto_cached_camp():
            return True
        return self.scan_base()

    def train_once(self):
        if not self.ensure_training():
            return False
        btn = train_button(screencap())
        if not btn:
            print("  未进入练兵界面", flush=True)
            return False
        self.tap(btn[0], btn[1], "TRAIN_START", "开始训练", "Train",
                 "消耗资源并进入训练倒计时", "consumes: wood/meat")
        closed = self.wait_for(lambda: train_button(screencap()) is None, timeout=10)
        self.rec.outcome("训练已开始并自动返回城市" if closed else "点击后仍停留练兵界面")
        return bool(closed)

    def free_speedup(self):
        pos = self.find_free()
        if not pos:
            return False
        self.tap(pos[0], pos[1], "FREE_SPEEDUP", "用免费加速", "Free",
                 "立即完成建造/训练")
        self.rec.outcome("已点击 Free 免费加速")
        return True


def parse_xy(text):
    x, y = text.split(",")
    return int(x), int(y)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--camp", default=None, help="可选初始营地坐标；不给则模板自动搜索")
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--settle", type=float, default=0.8, help="点击后基础等待秒数")
    parser.add_argument("--train-wait", type=float, default=25.0, help="每轮训练后的等待秒数")
    parser.add_argument("--upgrade", action="store_true", help="每轮先尝试 Upgrade 并用免费加速")
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-recording", action="store_true")
    args = parser.parse_args()

    anchor = parse_xy(args.camp) if args.camp else None
    rec = Recorder("规则脚本自动采集：练兵/升级/免费加速", args.keep_recording)
    player = Player(rec, dry_run=args.dry_run, settle=args.settle, anchor=anchor)
    failures = 0
    try:
        for cycle in range(1, args.cycles + 1):
            print(f"== 第 {cycle}/{args.cycles} 轮 ==", flush=True)
            if args.upgrade:
                buttons = player.ensure_panel()
                if buttons:
                    mid = buttons[1]
                    player.tap(mid[0], mid[1], "UPGRADE_OPEN", "打开升级", "Upgrade",
                               "显示资源需求与耗时", "consumes: wood/stone")
                    # 升级确认弹窗为固定全屏布局，右侧按钮为普通升级（非钻石）
                    player.tap(835, 718, "CONFIRM_UPGRADE", "确认普通升级", "Upgrade",
                               "进入建造倒计时", "consumes: wood/stone")
                    player.free_speedup()
            if not args.upgrade:
                player.free_speedup()
            ok = player.train_once()
            failures = 0 if ok else failures + 1
            if failures >= args.max_failures:
                print("连续失败，停止。请检查模板或视野。", flush=True)
                break
            time.sleep(args.train_wait)
    except KeyboardInterrupt:
        print("手动中断", flush=True)
    finally:
        try:
            rec.send("mark auto_play 结束")
            rec.stop()
        except OSError as exc:
            print(f"录制器已退出：{exc}", flush=True)
        print(f"动作数 {player.actions}，数据目录 {rec.ep}", flush=True)


if __name__ == "__main__":
    main()
