"""留痕：一次判断的完整链路记录。默认只存本地 JSONL，导出是用户主动行为。"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from typing import Any


def new_trace_id() -> str:
    return uuid.uuid4().hex[:12]


class Tracer:
    """追加式 JSONL。一行一个事件，带 trace_id 可以把整条链串起来。

    写留痕在判断链的每一步都会发生，多线程下用一把锁兜住行的完整性——
    一行写坏 = 一条 Trace 不可复算，这是产品资产。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, trace_id: str, event: str, /, **data: Any) -> None:
        # 事件体里混进同名字段时会顶掉位置参数（一次判断链的字段本来就该带 trace_id），
        # 所以前两个参数设为仅位置传入，并以链路 id 为准——
        # 不让某个对象的序列化格式悄悄改写它属于哪儿。
        data.pop("trace_id", None)
        line = json.dumps({"trace_id": trace_id, "event": event, **data},
                          ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read(self, trace_id: str | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            entry = json.loads(raw)
            if trace_id is None or entry.get("trace_id") == trace_id:
                out.append(entry)
        return out


# ------------------------------------------------------------------ 编译留痕
#
# 留痕的事件词汇归内核所有（A1 第3条）：编译器不只在 HA 上用，换个宿主这些事件名与
# 算法也得是同一套，否则"编译可用率"会变成每个宿主各算一遍的三个数。
# 为什么不做成脚本去测：ADR-0017 第 8 条——测量必须长在产品的使用路径上。

COMPILE = "compile"                  # 发起了一次编译（成或败都要记）
COMPILE_SAVED = "compile_saved"      # 这份草稿最终被用户在向导里保存

INTENT_HEAD = 30                     # 意图只存前这么多字：够看出哪句话被理解偏了


def compile_trace_id() -> str:
    """一次编译一条链：草稿与后来的"保存"要能串起来。"""
    return new_trace_id()


def intent_head(intent: str) -> str:
    return intent.strip()[:INTENT_HEAD]


def compile_summary(events: list[dict]) -> dict:
    """编译可用率的算法只写这一处：诊断包、脚本、以后的界面都调它，别各算各的。

    三个数是**分母不同**的三件事，混成一个"成功率"就等于没有测量：
    - 过校验率 `草稿/编译`——端点与提示词行不行（失败多为"没吐 JSON"或"越出候选实体集"）；
    - 保存率 `保存/草稿`——编出来的东西你还要不要。**这一项才是 ADR-0017 待办①要的量**。
      没记保存的草稿可能是用户看完就放弃了，也可能他只是中途关掉了对话框——
      界面上取消不会产生事件，所以这个数是**下界**，别当结论用。
    """
    compiled = [e for e in events if e.get("event") == COMPILE]
    drafted = [e for e in compiled if e.get("ok")]
    saved = {e.get("trace_id") for e in events if e.get("event") == COMPILE_SAVED}
    return {"编译": len(compiled), "过校验": len(drafted), "保存": len(saved & {
        e.get("trace_id") for e in drafted}), "失败原因": sorted({
        str(e.get("reason", ""))[:60] for e in compiled if not e.get("ok")})}
