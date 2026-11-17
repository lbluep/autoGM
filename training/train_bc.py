#!/usr/bin/env python3
"""CPU 行为克隆基线；按 episode 划分训练集和验证集。"""

import argparse
import copy
import json
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

from model import PolicyNet

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VAL = ["20261114T213955", "20261114T231633"]


class BCDataset(Dataset):
    def __init__(self, images, samples, indices):
        self.images = images
        self.samples = samples
        self.indices = indices

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, item):
        index = int(self.indices[item])
        frames = self.images[self.samples["seq_frames"][index]]
        return {
            "frames": torch.from_numpy(frames.copy()).float().div_(255.0),
            "state": torch.from_numpy(self.samples["seq_state"][index].copy()),
            "mask": torch.from_numpy(self.samples["seq_mask"][index].copy()),
            "macro": torch.tensor(self.samples["macro"][index]),
            "action_type": torch.tensor(self.samples["action_type"][index]),
            "coords": torch.from_numpy(self.samples["coords"][index].copy()),
            "coord_mask": torch.tensor(self.samples["coord_mask"][index]),
            "duration": torch.tensor(self.samples["duration"][index]),
            "duration_mask": torch.tensor(self.samples["duration_mask"][index]),
        }


def class_weights(labels, classes):
    counts = np.bincount(labels, minlength=classes).astype(np.float32)
    weights = np.zeros(classes, dtype=np.float32)
    present = counts > 0
    weights[present] = 1.0 / np.sqrt(counts[present])
    weights[present] *= present.sum() / weights[present].sum()
    return torch.tensor(np.minimum(weights, 5.0))


def masked_smooth_l1(prediction, target, mask):
    loss = F.smooth_l1_loss(prediction, target, reduction="none").mean(dim=-1)
    return (loss * mask).sum() / mask.sum().clamp_min(1.0)


def run_epoch(model, loader, device, optimizer, macro_weight, action_weight):
    training = optimizer is not None
    model.train(training)
    totals = {"loss": 0.0, "macro_correct": 0, "action_correct": 0, "coord_error": 0.0}
    predictions, labels = [], []
    for batch in loader:
        batch = {key: value.to(device) for key, value in batch.items()}
        with torch.set_grad_enabled(training):
            output = model(batch["frames"], batch["state"], batch["mask"])
            macro_loss = F.cross_entropy(output["macro"], batch["macro"], weight=macro_weight)
            action_loss = F.cross_entropy(
                output["action_type"], batch["action_type"], weight=action_weight
            )
            coord_loss = masked_smooth_l1(output["coords"], batch["coords"], batch["coord_mask"])
            duration_loss = (
                F.smooth_l1_loss(output["duration"], batch["duration"], reduction="none")
                * batch["duration_mask"]
            ).sum() / batch["duration_mask"].sum().clamp_min(1.0)
            loss = macro_loss + 0.5 * action_loss + 0.5 * coord_loss + 0.1 * duration_loss
            if training:
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
        size = len(batch["macro"])
        totals["loss"] += float(loss.detach()) * size
        totals["macro_correct"] += int((output["macro"].argmax(-1) == batch["macro"]).sum())
        totals["action_correct"] += int((output["action_type"].argmax(-1) == batch["action_type"]).sum())
        coord_rows = batch["coord_mask"] > 0
        if coord_rows.any():
            totals["coord_error"] += float(
                (output["coords"][coord_rows] - batch["coords"][coord_rows]).abs().sum()
            )
        predictions.extend(output["macro"].argmax(-1).detach().cpu().tolist())
        labels.extend(batch["macro"].detach().cpu().tolist())
    count = len(loader.dataset)
    coord_count = float(loader.dataset.samples["coord_mask"][loader.dataset.indices].sum())
    metrics = {
        "loss": totals["loss"] / count,
        "macro_accuracy": totals["macro_correct"] / count,
        "action_type_accuracy": totals["action_correct"] / count,
        "coord_mae": totals["coord_error"] / max(1.0, coord_count * 4),
    }
    return metrics, predictions, labels


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "training")
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--val-episodes", nargs="+", default=DEFAULT_VAL)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    torch.set_num_threads(args.threads)
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = torch.device("cpu")
    meta = json.loads((args.data / "meta.json").read_text())
    samples = np.load(args.data / "samples.npz")
    images = np.load(args.data / "images.npy", mmap_mode="r")
    episodes = samples["episode"]
    val_indices = np.flatnonzero(np.isin(episodes, args.val_episodes))
    train_indices = np.flatnonzero(~np.isin(episodes, args.val_episodes))
    if not len(train_indices) or not len(val_indices):
        raise SystemExit("训练集或验证集为空")
    unseen = set(samples["macro"][val_indices]) - set(samples["macro"][train_indices])
    if unseen:
        raise SystemExit(f"验证集包含训练集中没有的宏动作: {sorted(unseen)}")

    train_set = BCDataset(images, samples, train_indices)
    val_set = BCDataset(images, samples, val_indices)
    generator = torch.Generator().manual_seed(args.seed)
    train_loader = DataLoader(
        train_set, batch_size=args.batch_size, shuffle=True,
        num_workers=0, generator=generator,
    )
    val_loader = DataLoader(val_set, batch_size=args.batch_size, num_workers=0)
    model = PolicyNet(len(meta["macros"]), len(meta["action_types"]), len(meta["state_fields"]) * 2)
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=1e-4)
    macro_weight = class_weights(samples["macro"][train_indices], len(meta["macros"])).to(device)
    action_weight = class_weights(samples["action_type"][train_indices], len(meta["action_types"])).to(device)
    parameter_count = sum(parameter.numel() for parameter in model.parameters())

    best_loss = float("inf")
    best_epoch = 0
    best_state = None
    stale = 0
    log_path = args.data / "train_log.jsonl"
    log_path.write_text("")
    for epoch in range(1, args.epochs + 1):
        train_metrics, _, _ = run_epoch(
            model, train_loader, device, optimizer, macro_weight, action_weight
        )
        with torch.no_grad():
            val_metrics, _, _ = run_epoch(
                model, val_loader, device, None, macro_weight, action_weight
            )
        record = {
            "epoch": epoch, "train": train_metrics, "val": val_metrics,
            "parameter_count": parameter_count,
        }
        with log_path.open("a") as file:
            file.write(json.dumps(record) + "\n")
        if epoch == 1 or epoch % 10 == 0:
            print(json.dumps(record), flush=True)
        if val_metrics["loss"] < best_loss:
            best_loss = val_metrics["loss"]
            best_epoch = epoch
            best_state = copy.deepcopy(model.state_dict())
            stale = 0
        else:
            stale += 1
            if stale >= args.patience:
                print(f"early_stop epoch={epoch} best_epoch={best_epoch}", flush=True)
                break

    model.load_state_dict(best_state)
    with torch.no_grad():
        val_metrics, predictions, labels = run_epoch(
            model, val_loader, device, None, macro_weight, action_weight
        )
    per_macro = {}
    for index, name in enumerate(meta["macros"]):
        support = sum(label == index for label in labels)
        if support:
            correct = sum(pred == index and label == index for pred, label in zip(predictions, labels))
            per_macro[name] = {"support": support, "correct": correct, "accuracy": correct / support}
    metrics = {
        "best_epoch": best_epoch, "train_samples": len(train_set), "val_samples": len(val_set),
        "train_episodes": sorted(set(episodes[train_indices].tolist())),
        "val_episodes": args.val_episodes, "parameter_count": parameter_count,
        "val": val_metrics, "per_macro": per_macro,
        "note": "样本量仅用于验证管线，不代表自主实机能力",
    }
    checkpoint = {
        "model_state": model.state_dict(),
        "model_config": {
            "num_macros": len(meta["macros"]),
            "num_action_types": len(meta["action_types"]),
            "state_dim": len(meta["state_fields"]) * 2,
        },
        "macros": meta["macros"], "action_types": meta["action_types"],
        "state_fields": meta["state_fields"], "image_shape": meta["image_shape"],
        "history": meta["history"], "metrics": metrics,
    }
    torch.save(checkpoint, args.data / "bc_baseline.pt")
    (args.data / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(json.dumps(metrics, ensure_ascii=False))


if __name__ == "__main__":
    main()
