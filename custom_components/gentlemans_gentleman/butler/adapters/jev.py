"""Jev 云：判断模型端口 V1 的唯一实现。

真实契约（2026-09-22 实测，可用 `python3 scripts/live_smoke.py` 复跑）：

    POST https://api.typesafe.ai/v1/systemone      Bearer TYPESAFE_API_KEY
    {"state": "…", "model": "jev-latest",
     "questions": {"q": {"type": "noul|choice|score", "instructions": "…", "criteria": …}}}
    → {"model": "jev-1.13.0",
       "answers": {"q": {"type": "noul",   "noul": 0.14}},
                 {"q": {"type": "choice", "choice": "none", "confidence": 0.42,
                        "probabilities": {"none": 0.62, …}}},
                 {"q": {"type": "score",  "score": 1.58, "confidence": 0.58,
                        "legend": {"0": "明显过冷", …}}}}

三条实测事实决定了下面这些写法，改代码前先读它们：

1. **noul 不给 confidence**，它返回的那个数本身就是"是"的概率。所以是非形状的
   把握度只能取它——`decide._decide_binary` 正是这么用的。
2. **choice 的 confidence ≠ 最大选项的概率**（实测选了 0.62 的选项却自陈 0.42）。
   它是模型自陈的确信。阈值按它校准，别拿选项概率当把握度。
3. Jev 只会 noul/choice/score，**没有生成能力**（ADR-0001）。`Shape.provider_code`
   是全项目唯一允许出现这三个字符串的地方（ADR-0015）。

超时按 ADR/实测结论取 3 秒：跨境直连中位 1.4s、峰值 2.6s，尾巴超出预算时
**抛 `ProviderUnavailable` 让内核走安全回退**，而不是干等——什么都不做在家居里
有时是危险的。

⚠️ 2026-09-22 复测出一个新事实：**冷连接的首个请求 2.1–2.6s（TLS 握手占大头），
之后稳定在 1.2–1.4s**。也就是说 3s 预算下最容易超时的恰好是进程起来后的第一次判断。
解法与 Open-Meteo 的缓存预热同类——**由 HA 薄壳在启动时打一次探活请求**，不改内核、
不改预算。（写薄壳时记得做，否则用户装完的第一次触发最容易走回退。）
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from ..models import DecisionResponse, Judgment, Shape
from ..ports import DecisionProvider, ProviderUnavailable

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
QUESTION_ID = "q"          # 一次往返只问一件事：状态作用域是每策略（ADR-0007）


class JevProvider(DecisionProvider):
    """一次 `evaluate` = 一次 HTTP = 一次采样。采样次数归内核管（引擎负责多采）。"""

    name = "jev_cloud"

    def __init__(self, api_key: str, *, model: str = "jev-latest",
                 timeout_seconds: float = 3.0, opener: Callable | None = None):
        if not api_key:
            raise ValueError("Jev 需要 API Key：空的 Key 只会让每次判断都走回退")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._open = opener or urllib.request.urlopen

    def capabilities(self) -> dict:
        return {"provider": self.name, "model": self.model,
                "shapes": [s.value for s in Shape],
                "primitives": [s.provider_code for s in Shape],
                "timeout_seconds": self.timeout_seconds}

    # -------------------------------------------------------------- 一次判断

    def evaluate(self, request: Any) -> DecisionResponse:
        payload = json.dumps({"state": request.state_text, "model": self.model,
                              "questions": {QUESTION_ID: _question(request.judgment)}},
                             ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(ENDPOINT, data=payload, method="POST", headers={
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"})
        try:
            with self._open(req, timeout=self.timeout_seconds) as resp:
                return self._parse(json.loads(resp.read().decode("utf-8")))
        except ProviderUnavailable:
            raise
        except Exception as exc:                       # 网络/超时/坏 JSON 一律算模型不可达
            raise ProviderUnavailable(f"Jev 不可达：{self._safe(str(exc))}") from exc

    def _safe(self, text: str) -> str:
        """A3：密钥绝不进日志与 Trace。异常文本理论上不含它，这里仍然机械地抹一遍。"""
        return text.replace(self.api_key, "***")[:200]

    # ------------------------------------------------------------------ 解析

    def _parse(self, raw: dict) -> DecisionResponse:
        try:
            answer = raw["answers"][QUESTION_ID]
            model = str(raw.get("model", "unknown"))   # 哪个模型给的，必须留痕
            if "noul" in answer:
                p = float(answer["noul"])
                return DecisionResponse(value=p >= 0.5, confidence=p, model=model)
            if "choice" in answer:
                return DecisionResponse(value=answer["choice"],
                                        confidence=float(answer["confidence"]), model=model)
            if "score" in answer:
                return DecisionResponse(value=float(answer["score"]),
                                        confidence=float(answer["confidence"]), model=model)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderUnavailable(f"Jev 回答无法解析：{type(exc).__name__}") from exc
        raise ProviderUnavailable(f"无法识别的回答形态：{sorted(answer)}")


def _question(judgment: Judgment) -> dict:
    """把内核的判断对象翻译成 Jev 的一道题。criteria 的形态**随形状改变**。"""
    primitive = judgment.shape.provider_code
    question: dict[str, Any] = {"type": primitive, "instructions": judgment.question}
    if judgment.shape is Shape.BINARY:
        if judgment.criteria:                       # {"true": …, "false": …}
            question["criteria"] = dict(judgment.criteria)
    elif judgment.shape is Shape.CHOICE:
        options = judgment.options or tuple(judgment.criteria)
        if options:
            question["criteria"] = {o: judgment.criteria.get(o, "") for o in options}
    else:                                          # 有序打分：选项就是**按序的档位名**
        if judgment.options:
            question["criteria"] = list(judgment.options)
    return question
