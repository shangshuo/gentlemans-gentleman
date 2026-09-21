"""宿主适配器：内核的 `PlatformAdapter` 在 Home Assistant 上的落地。

**线程约定是这个文件最重要的一段**，写错的表现是死锁而不是报错：

    内核是同步的     → 整条判断链跑在 `hass.async_add_executor_job` 的线程里
    HA 不许跨线程碰  → 这里每次访问 `hass.states` 都用
                       `asyncio.run_coroutine_threadsafe(coro, hass.loop).result()`
                       把动作送回事件循环再等
    服务调用         → `hass.services.call(blocking=True)`，它本身就是
                       `run_coroutine_threadsafe(async_call, loop).result()`
                       （homeassistant/core.py:2828），正是为"在别的线程里同步等
                       一次服务"准备的

    ⚠️ 推论：这个适配器**绝不能在事件循环线程上被调用**——那是让循环等它自己。
       唯一合法的入口是 `runtime.ButlerRuntime.fire()`，它负责把链丢进 executor。

动词表是平台无关的（`CONTEXT.md`：动作用动词表达，不是 service 名）。换宿主时这张表
整张换掉，策略与内核一个字都不动——这就是 A1 三个端口里"宿主"那一层的存在意义。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.util import dt as dt_util

from .butler.models import Action, Reading, Snapshot
from .butler.ports import PlatformAdapter
from .const import HOST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

#: 动词 → {域: 服务名}。不在表里的组合就是配置错误，运行时如实报失败，不猜。
VERB_SERVICES: dict[str, dict[str, str]] = {
    "turn_on": {"light": "turn_on", "switch": "turn_on", "fan": "turn_on",
                "humidifier": "turn_on", "input_boolean": "turn_on"},
    "turn_off": {"light": "turn_off", "switch": "turn_off", "fan": "turn_off",
                 "humidifier": "turn_off", "input_boolean": "turn_off"},
    "open": {"cover": "open_cover", "vacuum": "start"},
    "close": {"cover": "close_cover", "vacuum": "return_to_base"},
    "set_position": {"cover": "set_cover_position"},
}

#: 域 → 取哪个属性当读数。缺省用 `state` 本身。
VALUE_ATTR: dict[str, str] = {"cover": "current_position"}


def coerce(raw: Any) -> Any:
    """HA 的状态一律是字符串；能读成数字就读成数字，喂给模型的上下文才不像一堆引号。

    读不成数字就原样返回——`on`/`closed` 这些本身就是我们要的事实。
    """
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return raw


def service_for(action: Action) -> tuple[str, str] | None:
    """把这个动作翻译成 (服务域, 服务名)；翻不出来返回 None。"""
    if "." not in action.entity_id:
        return None
    domain = action.entity_id.split(".", 1)[0]
    service = VERB_SERVICES.get(action.verb, {}).get(domain)
    return (domain, service) if service else None


def reason_of(exc: BaseException) -> str:
    """把异常变成一句**在线程里也取得到**的话。

    这不是风格问题：带翻译键的 HA 异常（`ServiceNotFound` 等）的 `__str__` 会去查翻译表，
    而翻译表只许在事件循环上取。在线程里 `"%s" % exc` 会抛
    `async_get_hass called from the wrong thread`——于是"记录一次执行失败"这个动作
    本身把整条判断链炸掉。这条是被真 HA 测试逼出来的，不是想象出来的。
    """
    if getattr(exc, "translation_key", None):
        holders = getattr(exc, "translation_placeholders", None)
        return (f"{type(exc).__name__} {dict(holders)}" if holders
                else type(exc).__name__)
    return f"{type(exc).__name__}: {exc}"[:200]


def value_of(entity_id: str, state: str, attributes: dict) -> Any:
    """一个实体在上下文里代表的那个值：窗帘看开度，其余看状态本身。"""
    domain = entity_id.split(".", 1)[0]
    attr = VALUE_ATTR.get(domain)
    if attr and attributes.get(attr) is not None:
        return coerce(attributes[attr])
    return coerce(state)


def still_seconds(state_last_changed: datetime | None, now: datetime, window: int) -> float:
    """连续处于当前状态多久（秒），封顶在 window。

    **口径：用状态的 `last_changed`，不查 history。** 内核问的是"连续处于某状态已多久"，
    而调用方只在当前状态就是所问状态时才来问——于是 `now - last_changed` 就是答案，
    不需要 recorder、不需要历史查询（那会把数据库变成判断链的依赖）。
    `window` 是封顶：查不到更早的事实，就只说自己知道多少。
    """
    if state_last_changed is None:
        return 0.0
    started = state_last_changed
    if started.tzinfo is None:
        started = dt_util.as_local(started)
    elapsed = (dt_util.as_local(now) - started).total_seconds()
    return max(0.0, min(elapsed, float(window)))


class HaHost(PlatformAdapter):
    """同步接口，内部全部 hop 回事件循环。见模块开头的线程约定。"""

    def __init__(self, hass):
        self.hass = hass

    # ------------------------------------------------------------- 端口三动作

    def snapshot(self, entity_ids: list[str], at: str) -> Snapshot:
        rows = self._hop(self._read_states(entity_ids))
        readings = tuple(Reading(key=eid, value=value_of(eid, state, attrs),
                                 source="实测", origin=eid) for eid, state, attrs in rows)
        return Snapshot(at=at, readings=readings)

    def duration_in_state(self, entity_id: str, state: Any, at: str, window: int) -> float:
        current, changed = self._hop(self._read_state(entity_id))
        if current is None or current != str(state):
            return 0.0                              # 现在不是这个状态：时长从零算起
        return still_seconds(changed, self._parse(at), window)

    def execute(self, action: Action, at: str) -> bool:
        """在线程里同步发出一个服务调用；**做成与否以服务层是否报错为准**。"""
        target = service_for(action)
        if target is None:
            _LOGGER.warning("动作 %s %s 没有对应的 HA 服务，跳过（是表单没约束住，还是策略写错了？）",
                            action.verb, action.entity_id)
            return False
        domain, service = target
        data = {ATTR_ENTITY_ID: action.entity_id, **action.params}
        try:
            # call() 自己就是 run_coroutine_threadsafe(...).result()，所以能在这里用
            self.hass.services.call(domain, service, data, blocking=True)
        except Exception as exc:
            _LOGGER.warning("执行 %s.%s %s 失败：%s", domain, service, action.entity_id,
                            reason_of(exc))
            return False
        return True

    # ---------------------------------------------------------------- 事件循环

    async def _read_states(self, entity_ids: list[str]):
        """只在事件循环上跑：读状态是 `@callback`，跨线程读等于读一份可能正在变的字典。"""
        out = []
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            if state is not None:
                out.append((entity_id, state.state, dict(state.attributes)))
        return out

    async def _read_state(self, entity_id: str):
        state = self.hass.states.get(entity_id)
        return (state.state if state else None, state.last_changed if state else None)

    def _hop(self, coro):
        """把一次动作送回事件循环执行，在当前线程等结果。

        在事件循环线程上调用自己就是死锁，而死锁在日志里长得像"什么都没发生"。
        所以这里把它变成一次明确的报错。
        """
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.hass.loop:
            coro.close()                            # 不留下"协程从未被等待"的告警
            raise RuntimeError(
                "宿主适配器只能在事件循环线程之外调用：判断链必须由 "
                "hass.async_add_executor_job 送进线程（见 host.py 模块开头的线程约定）")
        return asyncio.run_coroutine_threadsafe(coro, self.hass.loop).result(HOST_TIMEOUT)

    @staticmethod
    def _parse(at: str) -> datetime:
        parsed = dt_util.parse_datetime(at)
        if parsed is None:                          # 内核给的应该是 ISO，但不赌
            parsed = dt_util.now()
        return parsed
