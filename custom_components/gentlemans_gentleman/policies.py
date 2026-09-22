"""策略清单的真值只有一个地方：config entry 的 `data["policies"]`（ADR-0013）。

这里不放任何判断逻辑——那是内核的事。这个模块只做四件事：读出来、写回去、
把用户勾选的实体整成候选实体集（编译器的封闭选项集，ADR-0017 第 4 条）、
以及把表单上那几个常用字段写回草稿。

为什么不用 `.storage` 单独存一份：`async_update_entry` 已经负责持久化与通知重载，
再开一个文件就是**两个写入方自称真值**——ADR-0013 取消常驻阈值实体的同一条理由。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant

from .butler.models import Policy
from .const import CONF_POLICIES
from .host import verbs_for

_LOGGER = logging.getLogger(__name__)


def load(entry) -> list[Policy]:
    """把 entry 里的策略清单读成内核对象。读不动的策略跳过并留下日志，不整体崩。"""
    out: list[Policy] = []
    for raw in entry.data.get(CONF_POLICIES, []):
        try:
            out.append(Policy.from_dict(raw))
        except Exception as exc:
            _LOGGER.warning("策略 %s 读不出来，已跳过：%s", raw.get("id", "?"), exc)
    return out


def dump(policies: list[Policy]) -> list[dict[str, Any]]:
    return [p.to_dict() for p in policies]


def external_keys(policies: list[Policy]) -> list[str]:
    """这些策略要哪些外部事实——预热据此决定该批量取什么。"""
    return sorted({s.ref for p in policies for s in p.readings if s.kind == "external"})


def duration_entities(policies: list[Policy]) -> list[str]:
    return sorted({s.ref for p in policies for s in p.readings if s.kind == "duration"})


# --------------------------------------------------------------- 候选实体集

def entity_catalog(hass: HomeAssistant, entity_ids: list[str]) -> list[dict]:
    """用户当场勾选的实体 → 内核编译器要的候选实体集。

    每项带 `verbs`：这个实体的域会做哪些动词。**一处勾选，三处生效**——提示词里的
    封闭选项集、动作门的放行范围、读数可引用的来源（ADR-0017 第 4 条）。
    已经不存在的实体直接跳过：拿一个查不到的实体去编译，报错点会远得看不懂。
    """
    out: list[dict] = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            continue
        out.append({"entity_id": entity_id,
                    "name": str(state.attributes.get("friendly_name") or entity_id),
                    "state": state.state,
                    "verbs": verbs_for(entity_id.split(".", 1)[0])})
    return out


def next_policy_id(entry, name: str) -> str:
    """策略 id 由系统生成：模型给的 id 不算，用户也不该被迫理解 id 是什么。"""
    stem = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_")[:24] or "policy"
    taken = {p.get("id") for p in entry.data.get(CONF_POLICIES, [])}
    candidate, n = stem, 2
    while candidate in taken:
        candidate, n = f"{stem}_{n}", n + 1
    return candidate


# ------------------------------------------------------------------ 表单回写

def apply_edits(draft: dict, form: dict) -> dict:
    """把向导上那几个常用字段写回草稿。

    **v0.2 的界面只改得动这些**：名字、判断句、阈值、那一个动作、冷却秒数。读数清单
    与判据（criteria）由编译器产出，要改就重编译或删了重加——把 20 个字段的树硬塞进
    HA 的扁平表单，只会得到一个没人敢碰的表单。校验器同时限定"最多一条带动作的分支"，
    所以这里可以放心整张替换动作。
    """
    out = {**draft, "name": form["name"].strip(), "enabled": True}
    judgment = {**draft["judgment"], "question": form["question"].strip(),
                "threshold": float(form["threshold"])}
    out["judgment"] = judgment
    safety = {**draft.get("safety", {}), "cooldown_seconds": int(form.get("cooldown") or 0)}
    out["safety"] = safety

    branches = [dict(b) for b in draft["branches"]]
    target = next((i for i, b in enumerate(branches) if b.get("actions")), None)
    entity_id, verb = form.get("action_entity"), form.get("action_verb")
    if entity_id and verb:
        action = {"entity_id": entity_id, "verb": verb, "params": {}}
        if target is None:                       # 草稿原本一条动作都没有：加在兜底分支之前
            default = next((i for i, b in enumerate(branches) if not b.get("when")), len(branches))
            branches.insert(max(0, default), {"when": {}, "actions": [action]})
        else:
            branches[target] = {**branches[target], "actions": [action]}
    elif target is not None:
        branches[target] = {**branches[target], "actions": []}
    out["branches"] = branches
    return out


def draft_summary(draft: dict) -> str:
    """给向导看的只读摘要：这条草稿要看哪些数、按什么判据判。

    表单装不下这些（HA 的表单是扁平字段），但用户核对草稿时**必须看得见**——
    A2 的 P4：决策可解释。
    """
    lines = [f"**触发**：{draft['trigger'].get('type')} {draft['trigger'].get('offset_minutes', '')}".rstrip()]
    lines.append("**读数**：" + "、".join(
        f"{r['key']}（{r['kind']} ← {r['ref']}）" for r in draft["readings"]))
    judgment = draft["judgment"]
    lines.append(f"**判断**：{judgment['question']}｜形状 {judgment['shape']}"
                 f"｜状态 {' / '.join(judgment.get('status_of', {}).values())}")
    for key, text in (judgment.get("criteria") or {}).items():
        lines.append(f"- 判据 {key}：{text}")
    for branch in draft["branches"]:
        when = branch.get("when") or {}
        label = "兜底" if not when else "、".join(f"{k}={v}" for k, v in when.items())
        acted = "、".join(f"{a['verb']} {a['entity_id']}" for a in branch["actions"]) or "什么都不做"
        lines.append(f"- 分支 {label} → {acted}")
    return "\n".join(lines)
