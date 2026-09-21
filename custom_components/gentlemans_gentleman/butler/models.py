"""领域对象。全部可 JSON 序列化——策略与留痕的格式归内核所有（A1 第3条）。

用词严格对齐根目录 `CONTEXT.md`：读数 / 快照 / 上下文 / 状态 / 判断 / 动作门 /
分支动作表 / 留痕。**这里没有宿主词汇**：实体 id 只是字符串，动词是平台无关的。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ------------------------------------------------------------------ 读数与快照

OBSERVED = "实测"        # 宿主直读
COMPUTED = "推算"        # 窗口聚合，必须记录口径
EXTERNAL = "外部事实"     # 来自家居之外的事实
INFERRED = "推断"        # 模型给的，必须记录是哪个模型


@dataclass(frozen=True)
class Reading:
    """读数：从传感器与外部事实源读到的客观数值。只存事实，不存判断。"""

    key: str
    value: Any
    source: str
    origin: str = ""       # 出处：实体 id / 语义字段 / 聚合口径
    unit: str = ""

    def format(self) -> str:
        return f"{self.value}{self.unit}" if self.unit else str(self.value)

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value, "source": self.source,
                "origin": self.origin, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict) -> Reading:
        return cls(key=d["key"], value=d["value"], source=d["source"],
                   origin=d.get("origin", ""), unit=d.get("unit", ""))


@dataclass(frozen=True)
class Snapshot:
    """快照：一次判断所依据的、同一时刻的一组读数。"""

    at: str
    readings: tuple[Reading, ...]

    def value_of(self, key: str) -> Any:
        for r in self.readings:
            if r.key == key:
                return r.value
        raise KeyError(f"快照里没有读数 {key!r}")

    def to_dict(self) -> dict:
        return {"at": self.at, "readings": [r.to_dict() for r in self.readings]}


# -------------------------------------------------------------------- 判断形状

class Shape(str, Enum):
    """判断的三种形式。规范词是中文这三个；Jev 的原语名只是适配器的内部编码。"""

    BINARY = "是非"
    CHOICE = "多选一"
    ORDINAL = "有序打分"

    @property
    def provider_code(self) -> str:
        """Jev 侧的原语名。换 Provider 时改这里，别处不许出现这三个字符串。"""
        return {"是非": "noul", "多选一": "choice", "有序打分": "score"}[self.value]


@dataclass(frozen=True)
class Judgment:
    """问模型什么、什么形状、分界线在哪、各个答案叫什么状态。

    `status_of` 把判断值映射成**状态**（用户说得出的定性描述）。缺了它，
    状态实体就只能显示一个小数——那违反"状态是定性描述"这条定义（ADR-0012）。
    """

    shape: Shape
    question: str
    threshold: float
    status_of: dict[str, str] = field(default_factory=dict)
    options: tuple[str, ...] = ()
    criteria: dict[str, str] = field(default_factory=dict)

    def status_for(self, value: Any) -> str:
        return self.status_of.get(str(value), str(value))

    def to_dict(self) -> dict:
        return {"shape": self.shape.value, "question": self.question,
                "threshold": self.threshold, "status_of": self.status_of,
                "options": list(self.options), "criteria": self.criteria}

    @classmethod
    def from_dict(cls, d: dict) -> Judgment:
        return cls(shape=Shape(d["shape"]), question=d["question"],
                   threshold=float(d["threshold"]),
                   status_of=dict(d.get("status_of", {})),
                   options=tuple(d.get("options", ())),
                   criteria=dict(d.get("criteria", {})))


# ------------------------------------------------------------------ 动作与分支

@dataclass(frozen=True)
class Action:
    """动作：对一个实体的一次操作意图，用平台无关的动词表达。"""

    entity_id: str
    verb: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"entity_id": self.entity_id, "verb": self.verb, "params": self.params}

    @classmethod
    def from_dict(cls, d: dict) -> Action:
        return cls(entity_id=d["entity_id"], verb=d["verb"], params=dict(d.get("params", {})))


@dataclass(frozen=True)
class Branch:
    """分支动作表的一行：按序命中第一条即停。`when` 为空即兜底分支。

    三种比较式：`{"is": v}` 等于、`{"op": ">=", "value": x}` 有序打分、`{}` 兜底。
    """

    when: dict[str, Any]
    actions: tuple[Action, ...]

    @property
    def is_default(self) -> bool:
        return not self.when

    def matches(self, value: Any) -> bool:
        if self.is_default:
            return True
        if "is" in self.when:
            return str(value) == str(self.when["is"])
        op, target = self.when["op"], float(self.when["value"])
        if not isinstance(value, (int, float)):
            return False
        return {"<": value < target, "<=": value <= target, ">": value > target,
                ">=": value >= target, "==": value == target}[op]

    def to_dict(self) -> dict:
        return {"when": self.when, "actions": [a.to_dict() for a in self.actions]}

    @classmethod
    def from_dict(cls, d: dict) -> Branch:
        return cls(when=dict(d.get("when", {})),
                   actions=tuple(Action.from_dict(a) for a in d.get("actions", ())))


# ------------------------------------------------------------------------ 策略

@dataclass(frozen=True)
class ReadingSpec:
    """要采哪个读数。`kind` 决定它从哪个端口进来。"""

    key: str
    kind: str          # entity 实测 | external 外部事实 | duration/time 推算
    ref: str           # 实体 id / 外部语义字段 / duration 的目标实体 / time 的格式
    window: int = 0     # duration 的回看窗口（秒）
    state: str = ""     # duration 要测哪个状态的连续时长，如 motion 的 "off"
    unit: str = ""

    def to_dict(self) -> dict:
        return {"key": self.key, "kind": self.kind, "ref": self.ref,
                "window": self.window, "state": self.state, "unit": self.unit}

    @classmethod
    def from_dict(cls, d: dict) -> ReadingSpec:
        return cls(key=d["key"], kind=d["kind"], ref=d["ref"],
                   window=int(d.get("window", 0)), state=str(d.get("state", "")),
                   unit=d.get("unit", ""))


@dataclass(frozen=True)
class Safety:
    """安全策略：阻止明显不该由 AI 单独决定的执行。它是领域模型的一部分。"""

    allowed_entities: tuple[str, ...] = ()
    excludes: tuple[tuple[str, ...], ...] = ()      # 互斥组：绝不允许同时开启
    cooldown_seconds: int = 0                        # 防抖
    fallback: tuple[Action, ...] = ()                # 安全回退：模型不可达时做什么

    def to_dict(self) -> dict:
        return {"allowed_entities": list(self.allowed_entities),
                "excludes": [list(g) for g in self.excludes],
                "cooldown_seconds": self.cooldown_seconds,
                "fallback": [a.to_dict() for a in self.fallback]}

    @classmethod
    def from_dict(cls, d: dict) -> Safety:
        return cls(allowed_entities=tuple(d.get("allowed_entities", ())),
                   excludes=tuple(tuple(g) for g in d.get("excludes", ())),
                   cooldown_seconds=int(d.get("cooldown_seconds", 0)),
                   fallback=tuple(Action.from_dict(a) for a in d.get("fallback", ())))


@dataclass
class Policy:
    """策略：一条意图的可执行落地，也是用户配置与增删改查的单元。"""

    id: str
    name: str
    trigger: dict[str, Any]
    readings: tuple[ReadingSpec, ...]
    judgment: Judgment
    branches: tuple[Branch, ...]
    safety: Safety = Safety()
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "trigger": self.trigger,
                "readings": [r.to_dict() for r in self.readings],
                "judgment": self.judgment.to_dict(),
                "branches": [b.to_dict() for b in self.branches],
                "safety": self.safety.to_dict(), "enabled": self.enabled}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, d: dict) -> Policy:
        return cls(id=d["id"], name=d["name"], trigger=dict(d.get("trigger", {})),
                   readings=tuple(ReadingSpec.from_dict(r) for r in d["readings"]),
                   judgment=Judgment.from_dict(d["judgment"]),
                   branches=tuple(Branch.from_dict(b) for b in d["branches"]),
                   safety=Safety.from_dict(d.get("safety", {})),
                   enabled=d.get("enabled", True))

    @classmethod
    def from_json(cls, s: str) -> Policy:
        return cls.from_dict(json.loads(s))


# ------------------------------------------------------------ 决策往返与判定

@dataclass(frozen=True)
class DecisionRequest:
    """向模型提问的一次往返。上下文是喂给模型的，状态是模型给出的——方向相反。"""

    trace_id: str
    policy_id: str
    judgment: Judgment
    state_text: str

    def to_dict(self) -> dict:
        return {"trace_id": self.trace_id, "policy_id": self.policy_id,
                "shape": self.judgment.shape.provider_code,
                "question": self.judgment.question, "state_text": self.state_text}


@dataclass(frozen=True)
class DecisionResponse:
    """一次判断的返回。必须记住是哪个模型给的——今天的留痕是明天的实验数据。"""

    value: Any
    confidence: float
    model: str

    def to_dict(self) -> dict:
        return {"value": self.value, "confidence": round(self.confidence, 4),
                "model": self.model}


@dataclass(frozen=True)
class Verdict:
    """动作门的输出。**判断成功不等于执行成功**，两者绝不混记。"""

    status: str                       # 状态名（模型给的定性描述）
    outcome: str                      # execute | noop | suppressed | fallback
    reason: str
    actions: tuple[Action, ...] = ()
    samples: tuple[DecisionResponse, ...] = ()
    branch_index: int | None = None
    dropped: tuple[tuple[Action, str], ...] = ()   # 被安全/仲裁丢弃的动作与理由
    #: 真正与阈值比较的那个数。三种形状算法不同（是非＝"是"的概率中位数、多选一＝
    #: 多数选项的置信度中位数、有序打分＝置信度中位数），所以只能由动作门给出——
    #: 让界面自己去猜（读留痕文本？），界面与决策就会分叉（ADR-0012）。
    probability: float | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "outcome": self.outcome, "reason": self.reason,
                "probability": self.probability, "branch_index": self.branch_index,
                "actions": [a.to_dict() for a in self.actions],
                "samples": [s.to_dict() for s in self.samples],
                "dropped": [[a.to_dict(), why] for a, why in self.dropped]}


def median(values: list[float]) -> float:
    """N 次采样的中位数。一次试跑里"动不动手"由它决定（ADR-0009）。"""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0
