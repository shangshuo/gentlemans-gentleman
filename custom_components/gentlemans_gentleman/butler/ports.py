"""四个端口，四个插槽（A1 第2条；第四端口由修正案 A4 增补）。内核只认这些抽象，不认任何具体宿主或模型。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import Action, Snapshot


class ProviderUnavailable(Exception):
    """模型不可达 / 超时。触发安全回退，绝不静默什么都不做。"""


class CompileFailed(Exception):
    """编译不出可用草稿：端点不可达、输出不是合法草稿、或草稿越出了候选实体集。

    与 `ProviderUnavailable` **必须是两个异常**：编译失败发生在用户眼前，他该看到的
    是"哪一项不合规、去表单改哪里"；运行期不可达走的是安全回退。共用一个异常，
    就等于把用户的填表错误当成设备的故障。
    """


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
    """判别器：**运行期**判断屋子此刻处于什么状态。给的是概率与档位，不是文本。

    默认实现是 Jev Cloud，但端点、模型名、密钥三者都可配（修正案 A4）——它说的是
    自己的方言，换进来的实现必须说同一种方言，这不是"填个地址"能解决的。
    """

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


class PolicyCompiler(ABC):
    """编译器：**配置期**把用户写的意图编译成策略草稿（修正案 A4，第四端口）。

    它只在用户添加或修改策略的那一刻被调用，**不参与运行期的任何一次判断**；
    它的产物没有资格直接执行任何动作，必须经人核对与试跑（ADR-0005/0009）。

    `catalog` 是用户当场勾选的候选实体集，每项形如
    `{"entity_id": …, "name": …, "state": …, "verbs": […]}`——实体与动词都只能从里面取。
    """

    name: str = "abstract"
    model: str = ""      # 哪个模型给的草稿——留痕必须记得出来，与判别器同一道理

    @abstractmethod
    def compile(self, intent: str, catalog: list[dict], policy_id: str) -> dict:
        """返回一份验收过的草稿（`Policy` 的 JSON 形状）。失败抛 `CompileFailed`。"""
