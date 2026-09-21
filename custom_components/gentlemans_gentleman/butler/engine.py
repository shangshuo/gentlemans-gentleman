"""闭环编排：采读数 → 组上下文 → 判断 → 动作门 → 执行 → 全程留痕。

**试跑与运行时共用这一条路径**（ADR-0009 的硬约束）：两者唯一的差别是采样次数，
所以这里没有任何 `if trial_run` 分支。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from . import context as _context
from .decide import decide, fallback
from .models import Action, DecisionRequest, Policy, Snapshot, Verdict
from .ports import DecisionProvider, ExternalContextProvider, PlatformAdapter, ProviderUnavailable
from .trace import Tracer, new_trace_id

SAMPLES_ON_TRIAL = 3      # 一次试跑采样几次；日常运行只采 1 次（否则成本 ×N）


@dataclass
class Outcome:
    """一条策略在这一轮里的完整交代。

    `snapshot` 是给宿主看的：ADR-0012 要求状态实体的 attributes 里有"本次上下文的
    读数摘要"，而那份读数只在采集点存在过一次——不留在这里，界面就只能给用户一个
    没有依据的状态名。
    """

    policy_id: str
    verdict: Verdict
    executed: tuple[Action, ...] = ()
    skipped_reason: str = ""
    snapshot: Snapshot | None = None


@dataclass
class CycleReport:
    trace_id: str
    outcomes: list[Outcome] = field(default_factory=list)


class Engine:
    def __init__(self, adapter: PlatformAdapter, provider: DecisionProvider,
                 tracer: Tracer, external: ExternalContextProvider | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.adapter = adapter
        self.provider = provider
        self.tracer = tracer
        self.external = external
        self.clock = clock
        self._last_executed: dict[str, float] = {}

    def trial_run(self, policies: list[Policy], at: str) -> CycleReport:
        """试跑的入口：同一条路径，只是多采几次。**这里没有任何特例分支。**"""
        return self.run_cycle(policies, at, samples=SAMPLES_ON_TRIAL)

    def run_cycle(self, policies: list[Policy], at: str, samples: int = 1) -> CycleReport:
        trace_id = new_trace_id()
        report = CycleReport(trace_id=trace_id)
        live = [p for p in policies if p.enabled]
        self.tracer.record(trace_id, "cycle_start", at=at, policies=[p.id for p in live],
                           samples=samples)

        verdicts: list[tuple[Policy, Verdict]] = []
        for policy in live:
            verdict, snapshot = self._evaluate(policy, at, trace_id, samples)
            report.outcomes.append(Outcome(policy.id, verdict, snapshot=snapshot))
            if verdict.actions:          # 回退动作同样要真发出去——"什么都不做"不是安全选项
                verdicts.append((policy, verdict))

        self._arbitrate_and_execute(trace_id, report, at, verdicts)
        return report

    # ------------------------------------------------------------ 单条判断链

    def _evaluate(self, policy: Policy, at: str, trace_id: str, samples: int
                  ) -> tuple[Verdict, Snapshot]:
        snapshot, failures = _context.collect(policy, self.adapter, self.external, at)
        self.tracer.record(trace_id, "snapshot", policy_id=policy.id,
                           snapshot=snapshot.to_dict(), failures=failures)
        state_text = _context.state_text(snapshot)
        request = DecisionRequest(trace_id=trace_id, policy_id=policy.id,
                                  judgment=policy.judgment, state_text=state_text)
        self.tracer.record(trace_id, "decision_request", **request.to_dict())

        responses = []
        for _ in range(samples):
            try:
                response = self.provider.evaluate(request)
            except ProviderUnavailable as exc:
                self.tracer.record(trace_id, "provider_error", policy_id=policy.id,
                                   error=str(exc))
                return self._finish(trace_id, policy,
                                    fallback(policy, f"模型不可达：{exc}", tuple(responses)),
                                    snapshot)
            responses.append(response)
            self.tracer.record(trace_id, "decision_response", policy_id=policy.id,
                               **response.to_dict())

        verdict = decide(policy, responses)
        return self._finish(trace_id, policy, verdict, snapshot)

    def _finish(self, trace_id: str, policy: Policy, verdict: Verdict,
                snapshot: Snapshot) -> tuple[Verdict, Snapshot]:
        self.tracer.record(trace_id, "verdict", policy_id=policy.id, **verdict.to_dict())
        return verdict, snapshot

    # ---------------------------------------------- 跨策略仲裁与执行（ADR-0010）

    def _arbitrate_and_execute(self, trace_id: str, report: CycleReport, at: str,
                               verdicts: list[tuple[Policy, Verdict]]) -> None:
        winner: dict[str, tuple[Policy, Action]] = {}
        for policy, verdict in verdicts:
            for action in verdict.actions:
                if action.entity_id in winner:
                    loser, _ = winner[action.entity_id]
                    self.tracer.record(trace_id, "conflict", entity=action.entity_id,
                                       winner=policy.id, loser=loser.id,
                                       rule="后到者胜")
                winner[action.entity_id] = (policy, action)

        executed: dict[str, list[Action]] = {p.id: [] for p, _ in verdicts}

        for entity, (policy, action) in winner.items():
            reason = self._cooled(policy)
            if reason:
                self.tracer.record(trace_id, "cooldown", policy_id=policy.id,
                                   entity=entity, reason=reason)
                continue
            ok = self.adapter.execute(action, at)
            self.tracer.record(trace_id, "execution", policy_id=policy.id,
                               action=action.to_dict(), ok=ok)
            if ok:
                executed[policy.id].append(action)
                self._last_executed[policy.id] = self.clock()

        for outcome in report.outcomes:
            outcome.executed = tuple(executed.get(outcome.policy_id, ()))
            if outcome.verdict.outcome == "execute" and not outcome.executed:
                outcome.skipped_reason = "动作被冷却或冲突仲裁全部拦下"

    def _cooled(self, policy: Policy) -> str:
        limit = policy.safety.cooldown_seconds
        if not limit:
            return ""
        last = self._last_executed.get(policy.id)
        if last is None:
            return ""
        elapsed = self.clock() - last
        return f"距上次执行 {elapsed:.0f}s < 冷却 {limit}s" if elapsed < limit else ""

