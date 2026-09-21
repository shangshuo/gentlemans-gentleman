"""三个端口，三个插槽（A1 第2条）。内核只认这些抽象，不认任何具体宿主或模型。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import Action, Snapshot


class ProviderUnavailable(Exception):
    """模型不可达 / 超时。触发安全回退，绝不静默什么都不做。"""


class PlatformAdapter(ABC):
    """宿主：读快照、查历史、执行动作。V1 唯一实现是 Home Assistant。"""

    @abstractmethod
    def snapshot(self, entity_ids: list[str], at: str) -> Snapshot:
        """取一组实体的当前读数。"""

    @abstractmethod
    def duration_in_state(self, entity_id: str, state: Any, at: str, window: int) -> float:
        """该实体连续处于某状态已多少秒，最多回看 window 秒。"""

    @abstractmethod
    def execute(self, action: Action, at: str) -> bool:
        """执行一个动作，返回**事实层面**是否做成。"""


class DecisionProvider(ABC):
    """判断模型。V1 唯一实现是 Jev Cloud；本地模型将来照插。"""

    name: str = "abstract"

    @abstractmethod
    def evaluate(self, request: Any) -> Any:
        """一次判断。失败抛 ProviderUnavailable。"""

    @abstractmethod
    def capabilities(self) -> dict:
        """支持哪些判断形状，供开工前自检。"""


class ExternalContextProvider(ABC):
    """外部事实：天气、空气质量等家居之外的事实。V1 唯一实现是 Open-Meteo。"""

    name: str = "abstract"

    @abstractmethod
    def fetch(self, keys: list[str], at: str) -> dict[str, Any]:
        """一次批量取回语义字段；实现内部必须合并请求并缓存（ADR-0002）。"""
