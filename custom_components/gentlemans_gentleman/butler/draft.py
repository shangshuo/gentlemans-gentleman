"""策略草稿：编译器的输出规范与验收（修正案 A4）。

草稿只有一个用途——**让用户核对**。所以这里定死三件事：

1. 草稿的形状就是策略的形状（`Policy` 的 JSON），模型不许发明字段；
2. 模型只能在**用户当场勾选的候选实体集**里选实体与动词，越界即整份作废（fail-closed）；
3. `safety.allowed_entities` 由校验器写成候选实体集——**模型没有资格填它**。

`spec()` 产出的那段话同时是**用户看得懂的四字段说明**：界面上的"怎么写"与喂给模型的
"怎么写"必须同源，否则文档与行为会在两次改动里分叉。
"""
from __future__ import annotations

import json
from typing import Any

from .models import Policy, Shape
from .ports import CompileFailed
from .samples import SAMPLE_POLICIES

#: 触发在薄壳里的语义是"什么时候醒一次"（一轮判断跑的是全部启用策略，ADR-0010）
TRIGGERS = {
    "sunset": "日落之后醒一次，可带 offset_minutes（整数，晚几分钟看）",
    "sunrise": "日出之后醒一次，同上",
    "interval": "每隔 minutes 分钟醒一次",
}

#: 读数从哪个端口进来（`context.collect` 逐条实现，别发明了第四种）
KINDS = {
    "entity": "实体的当前值。ref 必须是候选实体之一",
    "duration": "某实体连续处于某状态已多少分钟。ref 必须是候选实体，state 写 on/off 这类，window 是回看秒数",
    "time": "此刻钟点。ref 只能是 hh:mm 或 hour",
    "external": "外部事实。ref 必须是下面「可用外部语义字段」之一",
}

#: 语义字段 → 给用户和模型看的一句话说明。**键必须与外部事实适配器一致**，由单测执法
#: （`tests/test_draft.py`）：这里漂了，编译出来的读数就会在运行期静默缺席。
EXTERNAL_FIELDS = {
    "outdoor_temp": "室外温度（℃）",
    "apparent_temp": "室外体感温度（℃）",
    "humidity": "室外相对湿度（%）",
    "cloud_cover": "云量（%）",
    "precipitation": "降水量（mm）",
    "wind_speed": "风速（km/h）",
    "is_daylight": "此刻室外是不是白天（true/false）",
    "outdoor_irradiance": "室外太阳辐射强度（W/m²，天黑透没透的诚实读数）",
    "pm25": "PM2.5（µg/m³）",
    "pm10": "PM10（µg/m³）",
    "us_aqi": "美国 AQI 空气质量指数",
}

EXAMPLE_ID = "sunset_light"


def _need(cond: bool, message: str) -> None:
    if not cond:
        raise CompileFailed(message)


# ------------------------------------------------------------------ 给模型的规范

def spec(catalog: list[dict]) -> str:
    """系统提示词：输出形状 + 封闭选项集 + 一条完整例子。"""
    lines = [
        "你在把用户的一段生活意图编译成一条可执行的家居策略草稿。",
        "只输出一个 JSON 对象，不要输出解释、前后缀或代码围栏。",
        "你没有任何控制权：这份草稿必须经人核对与试跑才会生效，"
        "所以宁可写保守的判断句，也不要用满候选实体。",
        "",
        "JSON 形状：",
        '{"name": 策略名(不超过20字，说人话),',
        ' "trigger": {"type": 三种之一, "offset_minutes"?: 整数, "minutes"?: 整数},',
        ' "readings": [{"key": 中文名, "kind": 四种之一, "ref": ..., '
        '"unit"?: 单位, "state"?: 状态, "window"?: 秒}, ...],',
        ' "judgment": {"shape": "是非"|"多选一"|"有序打分", "question": 要问模型的那句话,',
        '   "threshold": 0 到 1 的小数, "status_of": {判断值 → 状态名},',
        '   "options": [多选一/有序打分的选项，按序], "criteria": {每个选项的判据描述}},',
        ' "branches": [{"when": {"is": 判断值} | {"op": 比较符, "value": 数} | {},'
        ' "actions": [{"entity_id":..., "verb":..., "params": {...}}]}, ...],',
        ' "safety": {"cooldown_seconds": 整数, "excludes": [[互斥实体...]], "fallback"?: [动作]}}',
        "",
        "触发 type 只有这三种可用：" + "；".join(f"{k}——{v}" for k, v in TRIGGERS.items()),
        "读数 kind 只有这四种可用：" + "；".join(f"{k}——{v}" for k, v in KINDS.items()),
        "",
        "可用外部语义字段（ref 只能从这里取）：",
        *[f"- {k}：{v}" for k, v in EXTERNAL_FIELDS.items()],
        "",
        "候选实体集（entity_id 与动词只能从这里取，越界整份作废）：",
        *[f"- {e['entity_id']}｜{e.get('name') or '（无名）'}｜当前 {e.get('state', '未知')}"
          f"｜可用动词：{', '.join(e.get('verbs') or ()) or '无'}" for e in catalog],
        "",
        "判断的三种形状，threshold 的含义各不相同，别混："
        "「是非」的 threshold 是「是」的概率分界；"
        "「多选一」与「有序打分」的 threshold 是置信度地板，不达标就抑制不动手。",
        "「是非」的 status_of 必须含 \"True\" 与 \"False\" 两个键；"
        "「多选一」必须为每个选项给状态名。状态名是用户看得见的定性描述（\"在补觉\"），"
        "不是数字也不是概率。",
        "branches 按序命中第一条即停，**最后一条必须是兜底分支**（when 为空对象 {}，"
        "里面可以没有任何动作）。判据 criteria 要写给模型看的具体事实描述，不要复述问题。",
        "不要填写 id、enabled、allowed_entities——这三项由系统写入。",
        "",
        "一条完整例子（只说明形状，内容不要照抄）：",
        json.dumps(SAMPLE_POLICIES[EXAMPLE_ID], ensure_ascii=False),
    ]
    return "\n".join(lines)


def user_prompt(intent: str, catalog: list[dict]) -> str:
    return (f"用户意图：{intent}\n\n"
            f"请只使用这些实体：{', '.join(e['entity_id'] for e in catalog)}")


# ------------------------------------------------------------------ 验收（fail-closed）

def validate_draft(raw: Any, catalog: list[dict], policy_id: str) -> dict:
    """把模型吐出的对象验收成一份可核对的草稿。任何不合格都抛 `CompileFailed`，
    消息里必须指出**是哪一项**——用户要能在表单上改它。"""
    _need(isinstance(raw, dict), "草稿不是一个 JSON 对象，重来一次试试")
    allowed = {e["entity_id"]: e for e in catalog}
    _need(bool(allowed), "没有勾选任何候选实体，编译器无从下手")
    _need(isinstance(raw.get("name"), str) and raw["name"].strip(), "草稿缺少 name")
    _check_trigger(raw.get("trigger"))
    _check_readings(raw.get("readings"), allowed)
    _check_judgment(raw.get("judgment"))
    _check_branches(raw.get("branches"), allowed)

    draft = dict(raw)
    draft["id"] = policy_id                    # 模型给的 id 一律不算
    draft["enabled"] = True
    safety = dict(raw.get("safety") or {})
    if "cooldown_seconds" not in safety:
        safety["cooldown_seconds"] = 0
    for key in ("excludes", "fallback"):
        safety[key] = safety.get(key) or []
    safety["allowed_entities"] = sorted(allowed)   # 第三件事：白名单由系统写
    draft["safety"] = safety

    try:
        policy = Policy.from_dict(draft)
    except (KeyError, TypeError, ValueError) as exc:
        raise CompileFailed(f"草稿结构不合法：{type(exc).__name__}: {exc}") from exc
    return policy.to_dict()


def _check_trigger(trigger: Any) -> None:
    _need(isinstance(trigger, dict), "草稿缺少 trigger")
    kind = trigger.get("type")
    _need(kind in TRIGGERS, f"触发方式 {kind!r} 不支持，只能用 {'/'.join(TRIGGERS)}")
    if kind == "interval":
        minutes = trigger.get("minutes")
        _need(isinstance(minutes, int) and minutes > 0, "interval 触发必须给 minutes（正整数）")
    for field in ("offset_minutes", "minutes"):
        value = trigger.get(field)
        if value is not None:
            _need(isinstance(value, int), f"trigger 的 {field} 必须是整数分钟，不是 {value!r}")


def _check_readings(readings: Any, allowed: dict) -> None:
    _need(isinstance(readings, list) and readings, "草稿缺少 readings——没有读数就无法判断")
    seen: set[str] = set()
    for spec in readings:
        _need(isinstance(spec, dict), f"读数必须是个对象，不是 {spec!r}")
        key = spec.get("key")
        _need(isinstance(key, str) and key.strip(), "读数缺少 key（这个名字会出现在上下文里）")
        _need(key not in seen, f"读数 key 重复：{key}")
        seen.add(key)
        kind = spec.get("kind")
        _need(kind in KINDS, f"读数 {key} 的 kind {kind!r} 不支持，只能用 {'/'.join(KINDS)}")
        ref = spec.get("ref")
        if kind in ("entity", "duration"):
            _need(ref in allowed, f"读数 {key} 引用了候选实体之外的 {ref!r}")
        elif kind == "external":
            _need(ref in EXTERNAL_FIELDS, f"读数 {key} 的外部语义字段 {ref!r} 不存在")
        else:
            _need(ref in ("hh:mm", "hour"), f"读数 {key} 的 time 只能是 hh:mm 或 hour")


def _check_judgment(judgment: Any) -> None:
    _need(isinstance(judgment, dict), "草稿缺少 judgment")
    shape = judgment.get("shape")
    _need(shape in [s.value for s in Shape],
          f"判断形状 {shape!r} 不支持，只能用 是非/多选一/有序打分")
    _need(isinstance(judgment.get("question"), str) and judgment["question"].strip(),
          "judgment 缺少 question（要问模型的那句话）")
    threshold = judgment.get("threshold")
    _need(isinstance(threshold, (int, float)) and 0.0 <= float(threshold) <= 1.0,
          "threshold 必须是 0 到 1 之间的小数")
    status_of = judgment.get("status_of") or {}
    options = judgment.get("options") or []
    if shape == Shape.BINARY.value:
        _need({"True", "False"} <= set(status_of),
              "「是非」的 status_of 必须给出 True 与 False 两个状态名")
    elif shape == Shape.CHOICE.value:
        _need(len(options) >= 2, "「多选一」至少要两个 options")
        missing = [o for o in options if o not in status_of]
        _need(not missing, f"「多选一」这些选项没有状态名：{missing}")
    else:
        _need(len(options) >= 2, "「有序打分」的 options 是按序的档位，至少两个")


def _check_branches(branches: Any, allowed: dict) -> None:
    _need(isinstance(branches, list) and branches, "草稿缺少 branches")
    _need(not branches[-1].get("when"),
          "branches 的最后一条必须是兜底分支（when 写成空对象 {}）")
    for branch in branches:
        _need(isinstance(branch, dict) and isinstance(branch.get("actions"), list),
              "每个分支都要有 actions（可以是空数组）")
        if "when" in branch and branch["when"]:
            when = branch["when"]
            if "op" in when:
                _need(when["op"] in ("<", "<=", ">", ">=", "=="),
                      f"分支的比较符 {when['op']!r} 不支持")
        for action in branch["actions"]:
            _check_action(action, allowed)
    for action in (a for b in branches for a in b["actions"]):
        _need(isinstance(action.get("params") or {}, dict), "动作的 params 必须是个对象")


def _check_action(action: Any, allowed: dict) -> None:
    _need(isinstance(action, dict), f"动作必须是个对象，不是 {action!r}")
    entity_id = action.get("entity_id")
    _need(entity_id in allowed, f"动作引用了候选实体之外的 {entity_id!r}")
    verbs = set(allowed[entity_id].get("verbs") or ())
    verb = action.get("verb")
    _need(verb in verbs,
          f"{entity_id} 不支持动词 {verb!r}，它只会：{', '.join(sorted(verbs)) or '（没有可用动词）'}")
