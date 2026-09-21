"""上下文构建：把策略声明的读数清单，变成一组读数和一段喂给模型的文字。

每条读数必须自报出处（实测 / 推算 / 外部事实），推算量必须记录口径——
"无移动 40 分钟"不写窗口就没法复算（CONTEXT.md：上下文来源三分类）。
"""
from __future__ import annotations

from typing import Any

from .models import COMPUTED, EXTERNAL, OBSERVED, Policy, Reading, ReadingSpec, Snapshot
from .ports import ExternalContextProvider, PlatformAdapter


def collect(policy: Policy, adapter: PlatformAdapter,
            external: ExternalContextProvider | None, at: str) -> tuple[Snapshot, list[str]]:
    """采一次快照。返回快照与失败清单（失败不抛异常，交给动作门走安全回退）。"""
    readings: list[Reading] = []
    failures: list[str] = []

    entities = [s.ref for s in policy.readings if s.kind == "entity"]
    duration_specs = [s for s in policy.readings if s.kind == "duration"]
    external_keys = [s.ref for s in policy.readings if s.kind == "external"]

    base: dict[str, Any] = {}
    if entities:
        try:
            snap = adapter.snapshot(entities, at)
            by_ref = {r.origin: r.value for r in snap.readings}
            for spec in (s for s in policy.readings if s.kind == "entity"):
                if spec.ref in by_ref:
                    base[spec.key] = by_ref[spec.ref]
                else:
                    # 没有事实就不该有读数——喂给模型一条 None 等于让它猜
                    failures.append(f"实体 {spec.ref} 没进快照，读数 {spec.key} 缺席")
        except Exception as exc:                      # 宿主读不到≠判断成功，交给回退
            failures.append(f"实体读数失败：{exc}")

    for spec in duration_specs:
        try:
            seconds = adapter.duration_in_state(
                spec.ref, spec.state or "off", at, spec.window or 3600)
            base[spec.key] = round(seconds / 60.0, 1)      # 口径写进 origin，分钟是喂给模型的单位
        except Exception as exc:
            failures.append(f"推算量 {spec.key} 失败：{exc}")

    for spec in (s for s in policy.readings if s.kind == "time"):
        base[spec.key] = _clock_of(at, spec.ref)

    if external_keys:
        if external is None:
            failures.append(f"未接入外部事实源，缺 {external_keys}")
        else:
            try:
                got = external.fetch(external_keys, at)
                for spec in (s for s in policy.readings if s.kind == "external"):
                    if spec.ref in got:
                        base[spec.key] = got[spec.ref]
                    else:
                        failures.append(f"外部事实缺字段 {spec.ref}")
            except Exception as exc:
                failures.append(f"外部事实失败：{exc}")

    for spec in policy.readings:
        if spec.key not in base:
            continue
        readings.append(Reading(key=spec.key, value=base[spec.key],
                                source=_source(spec), origin=_origin(spec), unit=spec.unit))
    return Snapshot(at=at, readings=tuple(readings)), failures


def _clock_of(at: str, fmt: str) -> str:
    """从快照时刻取钟点。不读墙上时钟——判断必须能凭留痕复算。"""
    hhmm = at[11:16] if len(at) >= 16 else at
    return hhmm if fmt != "hour" else hhmm.split(":")[0]


def _origin(spec: ReadingSpec) -> str:
    if spec.kind == "duration":
        return f"{spec.ref} 连续处于 {spec.state or 'off'}，回看 {spec.window or 3600}s"
    if spec.kind == "time":
        return "快照时刻的本地钟点"
    return spec.ref


def _source(spec: ReadingSpec) -> str:
    return {"entity": OBSERVED, "duration": COMPUTED,
            "time": COMPUTED, "external": EXTERNAL}[spec.kind]


def state_text(snapshot: Snapshot) -> str:
    """喂给模型的文本：一行一个读数。这是"当前状况"的机器可读描述。"""
    return "\n".join(f"{r.key}: {r.format()}" for r in snapshot.readings) or "（无可用读数）"
