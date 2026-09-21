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
