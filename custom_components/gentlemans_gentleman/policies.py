"""策略清单的真值只有一个地方：config entry 的 `data["policies"]`（ADR-0013）。

这里不放任何判断逻辑——那是内核的事。这个模块只做三件事：读出来、写回去、
以及把内置样例策略按用户选的实体绑好。

为什么不用 `.storage` 单独存一份：`async_update_entry` 已经负责持久化与通知重载，
再开一个文件就是**两个写入方自称真值**——ADR-0013 取消常驻阈值实体的同一条理由。
"""
from __future__ import annotations

import logging
from typing import Any

from .butler.models import Policy
from .butler.samples import SAMPLE_POLICIES
from .const import CONF_POLICIES

_LOGGER = logging.getLogger(__name__)


def load(entry) -> list[Policy]:
    """把 entry 里的策略清单读成内核对象。读不动的策略跳过并留下日志，不整体崩。"""
    out: list[Policy] = []
    for raw in entry.data.get(CONF_POLICIES, []):
        try:
            out.append(Policy.from_dict(raw))
        except Exception as exc:
            _LOGGER.warning(
                "策略 %s 读不出来，已跳过：%s", raw.get("id", "?"), exc)
    return out


def dump(policies: list[Policy]) -> list[dict[str, Any]]:
    return [p.to_dict() for p in policies]


def external_keys(policies: list[Policy]) -> list[str]:
    """这些策略要哪些外部事实——预热协调器据此决定该批量取什么。"""
    return sorted({s.ref for p in policies for s in p.readings if s.kind == "external"})


def duration_entities(policies: list[Policy]) -> list[str]:
    return sorted({s.ref for p in policies for s in p.readings if s.kind == "duration"})


def sample_policy(cover: str, motion: str, light: str) -> Policy:
    """内置样例策略 ＋ 用户选的实体绑定（ADR-0011：表单补全那一步）。

    样例同时是**表单的活文档**：用户看到一条现成策略，就知道四个字段各填什么。
    """
    raw = dict(SAMPLE_POLICIES["sunset_light"])
    readings = []
    for spec in raw["readings"]:
        if spec["kind"] == "entity":
            spec = {**spec, "ref": cover}
        elif spec["kind"] == "duration":
            spec = {**spec, "ref": motion}
        readings.append(spec)
    branches = []
    for branch in raw["branches"]:
        actions = [{**a, "entity_id": light} for a in branch["actions"]]
        branches.append({**branch, "actions": actions})
    raw.update({"readings": readings, "branches": branches})
    return Policy.from_dict(raw)
