import torch
from torch import nn


class PolicyNet(nn.Module):
    """双帧 CNN + 决策序列 GRUCell，输出宏动作和连续动作参数。"""

    def __init__(self, num_macros, num_action_types, state_dim=10):
        super().__init__()
        self.cnn = nn.Sequential(
            nn.Conv2d(2, 16, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(16, 32, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(32, 64, 3, stride=2, padding=1), nn.ReLU(),
            nn.Conv2d(64, 128, 3, stride=2, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d((3, 5)), nn.Flatten(),
            nn.Linear(128 * 3 * 5, 256), nn.ReLU(),
        )
        self.gru = nn.GRUCell(256 + state_dim, 128)
        self.dropout = nn.Dropout(0.1)
        self.macro = nn.Linear(128, num_macros)
        self.action_type = nn.Linear(128, num_action_types)
        self.coords = nn.Linear(128, 4)
        self.duration = nn.Linear(128, 1)

    def forward(self, frames, state, mask):
        batch, steps, channels, height, width = frames.shape
        features = self.cnn(frames.reshape(batch * steps, channels, height, width))
        features = features.reshape(batch, steps, -1)
        features = torch.cat([features, state], dim=-1)
        hidden = frames.new_zeros((batch, 128))
        for index in range(steps):
            candidate = self.gru(features[:, index], hidden)
            hidden = torch.where(mask[:, index:index + 1] > 0, candidate, hidden)
        hidden = self.dropout(hidden)
        return {
            "macro": self.macro(hidden),
            "action_type": self.action_type(hidden),
            "coords": self.coords(hidden),
            "duration": self.duration(hidden).squeeze(-1),
        }
