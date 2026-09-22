"""内置样例策略：**日落开灯，但别照醒补觉的我**。

它既是第一个端到端用例，也是策略表单的活文档（ADR-0011）——用户看一条现成策略，
就知道"触发／读数／判断／动作"四个字段各填什么。策略格式归内核所有（A1 第3条）。
"""
from .models import Policy

SUNSET_LIGHT = {
    "id": "sunset_light",
    "name": "日落开灯，但别照醒补觉的我",
    "trigger": {"type": "sunset", "offset_minutes": 10},
    "readings": [
        # Open-Meteo 没有 lux 字段，"天黑透没黑透"的诚实读数是辐射 W/m²（见适配器）
        {"key": "室外亮度", "kind": "external", "ref": "outdoor_irradiance", "unit": " W/m²"},
        {"key": "卧室窗帘开度", "kind": "entity", "ref": "cover.bedroom", "unit": " %"},
        {"key": "已连续静止分钟", "kind": "duration", "ref": "binary_sensor.motion",
         "state": "off", "window": 7200, "unit": " 分钟"},
        {"key": "此刻钟点", "kind": "time", "ref": "hh:mm"},
    ],
    "judgment": {
        "shape": "是非",
        "question": "主人此刻是在睡觉或需要静养吗？",
        "threshold": 0.65,
        "status_of": {"True": "在补觉", "False": "清醒"},
        "criteria": {
            "true": "屋里安静、窗帘拉着、人长时间没动，或已是深夜——多半在睡",
            "false": "还有活动、窗帘开着、天还没黑透——醒着，需要照明",
        },
    },
    "branches": [
        {"when": {"is": False}, "actions": [
            {"entity_id": "light.bedroom", "verb": "turn_on", "params": {}}]},
        {"when": {}, "actions": []},          # 在补觉：什么都不做，别照醒他
    ],
    "safety": {
        # 候选实体集在真实策略里由薄壳写入（ADR-0017 第 4 条）；样例是文档，
        # 就把它照实写出来——空白名单现在意味着"什么都不许做"，不写就等于教错。
        "allowed_entities": ["binary_sensor.motion", "cover.bedroom", "light.bedroom"],
        "cooldown_seconds": 1800,
    },
    "enabled": True,
}

SAMPLE_POLICIES: dict[str, dict] = {SUNSET_LIGHT["id"]: SUNSET_LIGHT}


def sample_policy(policy_id: str) -> Policy:
    """按 id 取内置样例（表单的默认值来源）。"""
    return Policy.from_dict(SAMPLE_POLICIES[policy_id])
