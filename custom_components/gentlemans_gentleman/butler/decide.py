"""动作门：判断与执行之间的确定性关卡。

模型只提供判断，**是否执行、执行什么由这里决定**；AI 从不直接控制设备。
N 次采样在这里收成一次结论：中位数决定结果，于是界面上看到的分布与那个结论之间
有一个可复算的算法（ADR-0009）。

阈值的含义**随判断形状改变**（ADR-0015），别把三者混成一条：

- 是非   —— 阈值就是"是 / 否"的分界本身，不存在"过不过地板"；分支用 `{"is": true/false}` 挑
- 多选一 —— 阈值是置信度地板，不达标就抑制不动手；分支用 `{"is": 选项}` 挑
- 有序打分 —— 阈值同样是置信度地板；挑分支交给 `{"op": ">=", "value": x}` 这类区间比较
"""
from __future__ import annotations

from .models import Action, DecisionResponse, Judgment, Policy, Shape, Verdict, median


def decide(policy: Policy, samples: list[DecisionResponse]) -> Verdict:
    judgment = policy.judgment
    if not samples:
        return fallback(policy, "没有可用采样")
    if judgment.shape is Shape.BINARY:
        return _decide_binary(policy, judgment, samples)
    if judgment.shape is Shape.CHOICE:
        return _decide_choice(policy, judgment, samples)
    return _decide_ordinal(policy, judgment, samples)


def fallback(policy: Policy, reason: str,
             samples: tuple[DecisionResponse, ...] = ()) -> Verdict:
    """安全回退：模型不可达时**仍然要有动作**——什么都不做在家居里有时是危险的。

    回退动作同样要过安全筛查：不能因为出了错就绕过白名单与互斥组。
    """
    kept, dropped = _screen(policy, list(policy.safety.fallback))
    return _verdict(policy, "未知", "fallback", reason, list(samples), tuple(dropped),
                    actions=tuple(kept))


# ------------------------------------------------------------------ 三种形状

def _decide_binary(policy: Policy, j: Judgment, samples) -> Verdict:
    p_yes = round(median([s.confidence for s in samples]), 4)
    value = p_yes >= j.threshold
    op = "≥" if value else "<"
    return _emit(policy, j, j.status_for(value), samples, value,
                 f"对「是」的把握中位数 {p_yes:.2f} {op} 阈值 {j.threshold:.2f}",
                 probability=p_yes)


def _decide_choice(policy: Policy, j: Judgment, samples) -> Verdict:
    grouped: dict[str, list[float]] = {}
    for s in samples:
        grouped.setdefault(str(s.value), []).append(s.confidence)
    value = max(grouped, key=lambda k: (len(grouped[k]), median(grouped[k])))
    conf = median(grouped[value])
    status = j.status_for(value)
    conf = round(conf, 4)
    if conf < j.threshold:
        return _verdict(policy, status, "suppressed",
                        f"多数选项 {value} 的置信度中位数 {conf:.2f} < 阈值 {j.threshold:.2f}",
                        samples, probability=conf)
    return _emit(policy, j, status, samples, value,
                 f"{len(grouped[value])}/{len(samples)} 次判定为 {value}，"
                 f"置信度中位数 {conf:.2f} ≥ 阈值 {j.threshold:.2f}", probability=conf)


def _decide_ordinal(policy: Policy, j: Judgment, samples) -> Verdict:
    score = median([float(s.value) for s in samples])
    conf = median([s.confidence for s in samples])
    status = j.status_for(round(score, 2))
    conf = round(conf, 4)
    if conf < j.threshold:
        return _verdict(policy, status, "suppressed",
                        f"打分 {score:.2f}，但置信度中位数 {conf:.2f} < 阈值 {j.threshold:.2f}",
                        samples, probability=conf)
    return _emit(policy, j, status, samples, score,
                 f"打分中位数 {score:.2f}，置信度 {conf:.2f} ≥ 阈值 {j.threshold:.2f}",
                 probability=conf)


# -------------------------------------------------------------- 分支与筛查

def _emit(policy: Policy, j: Judgment, status: str, samples, value, reason: str,
          probability: float | None = None) -> Verdict:
    index, actions = _match_branch(policy, value)
    if index is None:
        return _verdict(policy, status, "suppressed",
                        f"{reason}；但判断值 {value!r} 没有对应分支，不动手", samples,
                        probability=probability)
    kept, dropped = _screen(policy, actions)
    if not kept:
        if dropped:      # 想动但被安全策略拦下
            return _verdict(policy, status, "suppressed",
                            f"{reason}；动作被安全策略拦下：" + "；".join(w for _, w in dropped),
                            samples, tuple(dropped), index, probability=probability)
        # 命中的分支本来就是"什么都不做"——这是决定，不是被拦
        return _verdict(policy, status, "noop", f"{reason}；按该分支不该动手", samples,
                        (), index, probability=probability)
    return _verdict(policy, status, "execute", reason, samples, tuple(dropped),
                    index, tuple(kept), probability=probability)


def _verdict(policy: Policy, status: str, outcome: str, reason: str, samples,
             dropped=(), branch_index=None, actions=(), probability=None) -> Verdict:
    return Verdict(status=status, outcome=outcome, reason=reason, actions=actions,
                   samples=tuple(samples), branch_index=branch_index, dropped=dropped,
                   probability=probability)


def _match_branch(policy: Policy, value) -> tuple[int | None, list[Action]]:
    """分支动作表：按序命中第一条即停。"""
    for i, branch in enumerate(policy.branches):
        if branch.is_default or branch.matches(value):
            return i, list(branch.actions)
    return None, []


def _screen(policy: Policy, actions: list[Action]):
    """白名单与互斥组。它们保护的是设备与钱，与判断无关（ADR-0006）。"""
    allowed = policy.safety.allowed_entities
    targets = {a.entity_id for a in actions}
    kept: list[Action] = []
    dropped: list[tuple[Action, str]] = []
    for a in actions:
        if allowed and a.entity_id not in allowed:
            dropped.append((a, f"{a.entity_id} 不在允许实体白名单内"))
            continue
        clash = next((g for g in policy.safety.excludes
                      if a.entity_id in g and len(set(g) & targets) > 1), None)
        if clash:
            dropped.append((a, f"互斥组 {list(clash)} 同时被指向"))
            continue
        kept.append(a)
    return kept, dropped
