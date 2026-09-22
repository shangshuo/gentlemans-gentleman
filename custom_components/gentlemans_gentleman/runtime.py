"""判断链的运行时：装配三个端口、注册触发、把整条链丢进线程池、把结果交给状态实体。

一条链的走位（全项目只有这一处把内核接到宿主上）：

    触发 → async_run()（在事件循环上）
             └─ async_add_executor_job(engine.run_cycle)    内核同步跑在**另一个线程**
                  ├─ host.snapshot / duration_in_state      每次访问 hass 都 hop 回循环
                  ├─ provider.evaluate                      真调 Jev
                  ├─ decide（动作门）→ 跨策略仲裁 → 冷却
                  └─ host.execute                           hass.services.call(blocking)
             → 回到循环：记下结果 → 通知状态实体

**一轮跑的是所有启用策略，不是"触发的那一条"。** 这不是省事：跨策略仲裁必须在同一次
执行里比较多个策略才会发生（ADR-0010），逐条触发等于把仲裁拆成谁最后跑谁说了算。
所以触发在薄壳里的语义是"什么时候醒一次"，醒来把该看的都看一遍。

**并发只有一个前提：同一时刻只跑一条链。** 冷却表与仲裁表都是引擎的实例状态，两条链
并行会各自看到半个现场。锁在循环上拿（`asyncio.Lock`），且只在 `await` 之外持有——
跨线程持锁就是自锁。

⚠️ 外部事实必须**周期预热**（ADR-0002 实测：天气与空气质量两个主机冷取 ≈2＋2.5s）。
用户等的那一次触发必须落在缓存上，所以这里按 TTL 自己刷，与触发解耦。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.util import dt as dt_util

from .butler import (COMPILE, COMPILE_SAVED, SAMPLES_ON_TRIAL, Engine, Policy,
                           Tracer, compile_trace_id, intent_head)
from .butler.adapters import JevProvider, OpenAICompatCompiler, OpenMeteoProvider
from .butler.models import DecisionRequest, Judgment, Shape
from .butler.ports import CompileFailed, ProviderUnavailable
from .const import (COMPILER_TIMEOUT, CONF_COMPILER_BASE_URL, CONF_COMPILER_KEY,
                    CONF_COMPILER_MODEL, CONF_DECISION_BASE_URL, CONF_DECISION_KEY,
                    CONF_DECISION_MODEL, CONF_TIMEOUT, CYCLE_TIMEOUT,
                    DEFAULT_COMPILER_MODEL, DEFAULT_DECISION_BASE_URL,
                    DEFAULT_DECISION_MODEL, DEFAULT_TIMEOUT, DOMAIN, EXTERNAL_TTL,
                    TRIGGER_INTERVAL, TRIGGER_SUNRISE, TRIGGER_SUNSET)
from .host import HaHost
from .policies import entity_catalog, external_keys, load

_LOGGER = logging.getLogger(__name__)

SUN_EVENTS = {TRIGGER_SUNSET: "sunset", TRIGGER_SUNRISE: "sunrise"}

#: 启动探活与配置校验共用的一句问法：连通性检查不该把家庭信息发出去
PROBE_REQUEST = DecisionRequest(
    trace_id="warmup", policy_id="warmup",
    judgment=Judgment(shape=Shape.BINARY, question="这条消息收到了吗？", threshold=0.5),
    state_text="（连通性探活，不含家居信息）")



@dataclass
class PolicyStatus:
    """一条策略最近一次的交代。状态实体的 `state` 与 attributes 全从这里来。"""

    policy_id: str
    status: str = "没跑过"
    outcome: str = ""
    reason: str = ""
    probability: float | None = None
    threshold: float | None = None
    trace_id: str = ""
    model: str = ""
    at: str = ""
    readings: dict[str, str] = field(default_factory=dict)
    samples: tuple[float, ...] = ()
    executed: tuple[str, ...] = ()

    def attributes(self) -> dict[str, Any]:
        """状态实体的 attributes：**只放"这一次判断"的东西**（ADR-0012）。

        这里刻意没有 `samples`。N 次采样的分布属于试跑的临时产物，进了常驻实体
        就等于让"看完即散"的东西常驻——而且日常只有 1 次采样，那个数组平时是空的、
        一旦有值又是上一把试跑的残留，两个方向都在说谎。向导要看的分布走
        `async_trial()` 的返回值，不落实体。
        """
        return {"结论": self.outcome, "理由": self.reason, "把握度": self.probability,
                "阈值": self.threshold, "模型": self.model, "判断时刻": self.at,
                "留痕": self.trace_id, "本次依据": self.readings,
                "已发出": list(self.executed)}


class ButlerRuntime:
    """一个 config entry 一个实例：密钥、策略、留痕与状态都住在这里。"""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self.policies: list[Policy] = load(entry)
        self.status: dict[str, PolicyStatus] = {}
        self.host = HaHost(hass)
        self.provider = JevProvider(entry.data.get(CONF_DECISION_KEY, ""),
                                    model=entry.data.get(CONF_DECISION_MODEL,
                                                         DEFAULT_DECISION_MODEL),
                                    base_url=entry.data.get(CONF_DECISION_BASE_URL,
                                                            DEFAULT_DECISION_BASE_URL),
                                    timeout_seconds=float(entry.data.get(
                                        CONF_TIMEOUT, DEFAULT_TIMEOUT)))
        self.compiler = _compiler(entry.data)
        self.external = OpenMeteoProvider(hass.config.latitude, hass.config.longitude)
        self.tracer = Tracer(Path(hass.config.path("traces", f"{DOMAIN}.jsonl")))
        self.engine = Engine(self.host, self.provider, self.tracer, external=self.external)
        self._listeners: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self.started = False

    # ------------------------------------------------------------------ 生命周期

    async def async_start(self) -> None:
        """注册触发与预热。幂等：HA 的重载会先 unload，重复 start 不该出现，但也不炸。"""
        if self.started:
            return
        self.started = True

        warm = self.hass.async_create_task(self._warm_up())
        self.entry.async_on_unload(warm.cancel)

        for event in {SUN_EVENTS[p.trigger.get("type", "")] for p in self.policies
                      if p.trigger.get("type") in SUN_EVENTS}:
            self.entry.async_on_unload(
                self.hass.bus.async_listen(event, self._on_sun))

        intervals = [int(p.trigger.get("minutes", 10)) for p in self.policies
                     if p.trigger.get("type") == TRIGGER_INTERVAL]
        if intervals:
            every = timedelta(minutes=max(1, min(intervals)))
            self.entry.async_on_unload(async_track_time_interval(
                self.hass, self._on_interval, every, name=f"{DOMAIN} 定时"))

        # 外部事实按 TTL 自刷；provider 内部还有各自的 interval 门，不多打
        self.entry.async_on_unload(async_track_time_interval(
            self.hass, lambda _now: self._refresh_external(),
            timedelta(seconds=EXTERNAL_TTL), name=f"{DOMAIN} 外部预热"))

    async def _warm_up(self) -> None:
        """启动时各打一次外部依赖：冷连接的首个 Jev 请求实测 2.1–2.6s，别让用户等到它。"""
        await asyncio.gather(self._probe_provider(), self._refresh_external(),
                             return_exceptions=True)

    async def _probe_provider(self) -> None:
        def call() -> None:
            try:
                self.provider.evaluate(PROBE_REQUEST)
            except ProviderUnavailable as exc:
                _LOGGER.warning("启动时模型不可达，第一次真判断会走安全回退：%s", exc)

        await self.hass.async_add_executor_job(call)

    async def _refresh_external(self, _now=None) -> None:
        keys = external_keys(self.policies)
        if not keys:
            return

        def fetch() -> None:
            try:
                self.external.fetch(keys, dt_util.now().isoformat(timespec="seconds"))
            except ProviderUnavailable as exc:
                _LOGGER.warning("外部事实取不到，本轮判断会缺这些读数 %s：%s", keys, exc)

        await self.hass.async_add_executor_job(fetch)

    # ------------------------------------------------------------------ 触发与执行

    async def _on_interval(self, _now=None) -> None:
        await self.async_run("定时")

    @callback
    def _on_sun(self, event) -> None:
        """日落/日出只是"该看一眼了"；晚几分钟由策略自己的 offset 决定。"""
        offsets = [int(p.trigger.get("offset_minutes", 0)) for p in self.policies
                   if p.trigger.get("type") == event.event_type]
        seconds = max(0, max(offsets, default=0)) * 60
        reason = f"{event.event_type}＋{seconds // 60} 分钟"
        if not seconds:
            self.hass.async_create_task(self.async_run(reason))
            return
        # 直接把 async 函数交给 async_call_later，让 HA 自己决定在哪个上下文里起任务。
        # 之前写成 lambda: hass.async_create_task(...) 被 HA 的线程保护当场抓住：
        # 定时器回调里再手动起任务，等于绕过了它对"谁在什么线程上碰循环"的约定。
        async def delayed(_now=None) -> None:
            await self.async_run(reason)

        self.entry.async_on_unload(async_call_later(self.hass, seconds, delayed))

    async def async_run(self, reason: str, samples: int = 1,
                        policies: list[Policy] | None = None) -> dict[str, PolicyStatus]:
        """跑一轮。`samples` > 1 就是**试跑**——同一条路径，只是多采几次（ADR-0009）。

        `policies` 是给向导用的"含未保存草稿"的那一份：草稿试跑走的是与日常**完全相同**
        的代码路径（ADR-0009 不许为试跑特判），只是这一次要看的策略清单由界面提供。
        返回本轮之后的全部状态；试跑要看的 N 个采样在 `PolicyStatus.samples` 里。
        """
        chain = self.policies if policies is None else policies
        if not chain:
            return self.status
        async with self._lock:
            at = dt_util.now().isoformat(timespec="seconds")
            try:
                # 预算挂在"整条链"上，不挂在每个服务调用上：hass.services.call(blocking)
                # 自己不带超时（它就是 run_coroutine_threadsafe(...).result()，
                # core.py:2828），设备卡住时只有这一层能放手。
                report = await asyncio.wait_for(
                    self.hass.async_add_executor_job(
                        self.engine.run_cycle, chain, at, samples),
                    CYCLE_TIMEOUT)
            except TimeoutError as exc:
                _LOGGER.error("%s：一轮判断超过 %ss 没跑完，这一轮作废。"
                              " executor 线程可能仍挂着，设备状态未知。", reason, CYCLE_TIMEOUT)
                raise ButlerRunFailed(f"一轮判断超过 {CYCLE_TIMEOUT:.0f}s 未完成") from exc
            except Exception as exc:                       # 线程里的意外不能让触发器失踪
                _LOGGER.exception("%s 这一轮判断链没跑完", reason)
                raise ButlerRunFailed(str(exc)) from exc
            for outcome in report.outcomes:
                self.status[outcome.policy_id] = _status_of(outcome, report.trace_id, at, chain)
            self._notify()
            acted = sum(1 for o in report.outcomes if o.executed)
            _LOGGER.info("%s：判断 %d 条 × %d 次，动手 %d 条，留痕 %s",
                         reason, len(report.outcomes), samples, acted, report.trace_id)
            return self.status

    async def async_trial(self, policy_id: str | None = None,
                          policies: list[Policy] | None = None) -> dict[str, PolicyStatus]:
        """试跑入口（向导用）。同一条路径，只是采样次数换成试跑的次数。"""
        statuses = await self.async_run("试跑", samples=SAMPLES_ON_TRIAL, policies=policies)
        if policy_id is None:
            return statuses
        return {key: value for key, value in statuses.items() if key == policy_id}

    async def async_compile(self, intent: str, entity_ids: list[str], policy_id: str
                            ) -> tuple[dict, str]:
        """把一段意图编译成草稿，返回（草稿，这条编译链的留痕 id）。

        **每次编译都记一行**（成或败都记）：ADR-0017 待办①"编译可用率"要的数字必须从
        真实使用里长出来，而不是让用户另开终端跑脚本——那等于在产品的 UI 配置之外再开
        一个凭据来源与一条只有开发者会走的路（A2 总原则把它判为产品缺陷）。
        """
        catalog = entity_catalog(self.hass, entity_ids)
        if len(catalog) != len(entity_ids):
            raise ButlerCompileFailed("勾选的实体里有已经不存在的，去掉它再试一次")
        compiler = self.compiler
        if compiler is None:
            raise ButlerCompileFailed("还没有配置编译器：在集成配置里填上编译端点，"
                                      "或者先用表单手工补一条策略")
        trace_id = compile_trace_id()

        def call() -> dict:
            return compiler.compile(intent, catalog, policy_id)

        started = self.engine.clock()
        try:
            draft = await self.hass.async_add_executor_job(call)
        except CompileFailed as exc:
            self.tracer.record(trace_id, COMPILE, ok=False, reason=str(exc),
                               intent_head=intent_head(intent), intent_len=len(intent),
                               compiler=compiler.model)
            _LOGGER.info("编译失败：%s", exc)
            raise ButlerCompileFailed(str(exc)) from exc
        self.tracer.record(
            trace_id, COMPILE, ok=True, intent_head=intent_head(intent),
            intent_len=len(intent), compiler=compiler.model,
            草稿={"name": draft["name"], "形状": draft["judgment"]["shape"],
                  "阈值": draft["judgment"]["threshold"],
                  "读数": [r["key"] for r in draft["readings"]],
                  "动作": [f"{a['verb']} {a['entity_id']}"
                           for b in draft["branches"] for a in b["actions"]]},
            耗时秒=round(self.engine.clock() - started, 2))
        return draft, trace_id

    def record_compile_saved(self, trace_id: str) -> None:
        """草稿被用户保存——这是"可用率"的分子，与"编译"那一行按 trace_id 配对。"""
        self.tracer.record(trace_id, COMPILE_SAVED)

    def policy(self, policy_id: str) -> Policy | None:
        return next((p for p in self.policies if p.id == policy_id), None)

    # -------------------------------------------------------------- 状态实体接口

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            try:
                listener()
            except ValueError:                 # 已注销的实体自己摘掉了监听
                continue
            except Exception:
                _LOGGER.exception("状态实体刷新失败")


class ButlerRunFailed(HomeAssistantError):
    """一轮判断链在宿主侧炸了。给服务调用看的是人话，不是 traceback。"""


class ButlerCompileFailed(HomeAssistantError):
    """编译不出草稿。给界面看的是"哪一项不合规、去改哪里"，不是 traceback（A4 第二道闸门）。"""


def _compiler(data: dict) -> OpenAICompatCompiler | None:
    """编译器只在填表时用：没配端点就返回 None，运行期一行代码都不为它改变。"""
    base_url = (data.get(CONF_COMPILER_BASE_URL) or "").strip()
    model = (data.get(CONF_COMPILER_MODEL) or DEFAULT_COMPILER_MODEL).strip()
    if not base_url or not model:
        return None
    return OpenAICompatCompiler(base_url, model, data.get(CONF_COMPILER_KEY, ""),
                                timeout_seconds=COMPILER_TIMEOUT)


def _status_of(outcome, trace_id: str, at: str, policies: list[Policy]) -> PolicyStatus:
    verdict = outcome.verdict
    policy = next((p for p in policies if p.id == outcome.policy_id), None)
    readings = {r.key: r.format() for r in (outcome.snapshot.readings
                                            if outcome.snapshot else ())}
    return PolicyStatus(
        policy_id=outcome.policy_id,
        status=verdict.status or "未知",
        outcome=verdict.outcome,
        reason=verdict.reason,
        probability=verdict.probability,
        threshold=policy.judgment.threshold if policy else None,
        trace_id=trace_id,
        model=verdict.samples[-1].model if verdict.samples else "",
        at=at,
        readings=readings,
        samples=tuple(round(s.confidence, 3) for s in verdict.samples),
        executed=tuple(f"{a.entity_id} → {a.verb}" for a in outcome.executed))
