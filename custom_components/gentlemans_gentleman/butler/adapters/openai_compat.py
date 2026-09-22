"""OpenAI 兼容端点：编译器端口（A1 第2条的第四插槽，修正案 A4）的默认实现。

契约是业界事实标准——OpenAI 自家、Ollama、llama.cpp server、vLLM 都认这一套：

    POST <base_url>/chat/completions        Authorization: Bearer <key>（可无）
    {"model": "…", "temperature": 0, "max_tokens": 1500,
     "response_format": {"type": "json_object"},
     "messages": [{"role": "system", …}, {"role": "user", …}]}
    → {"choices": [{"message": {"content": "…"}}]}

四条写在这里的理由：

1. **base_url 由用户填**，所以要替他兜住三种写法：带不带尾斜杠、带不带 `/chat/completions`、
   只写 `host:port` 忘了 scheme（本机 Ollama 最常见）。
2. `response_format` 不是所有本地端点都实现。带上去请求会失败吗？不会——多数实现忽略未知
   字段；真正的问题在反方向：**它不工作时模型会输出散文**。所以这里既请求 JSON 模式，
   也自己从文本里抠出那个 JSON 对象（`extract_json`），两条腿走路。
3. **temperature 0**：草稿要可复现。同一段意图两次编译给出不同分支，用户会以为产品在掷骰子。
4. `max_tokens` 显式给：Ollama 默认 512 token，一份带四五个读数的草稿正好卡在边界上，
   截断的表现是"坏 JSON"——那比直接给足额度难查得多。

超时给到 45 秒：这是用户在表单前等一次编译，不是一轮夜间判断（那边是 3 秒预算，
`jev.py`）。本机小模型首次数十秒是常态，按运行期预算掐它只会得到一堆假故障。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from ..draft import spec, user_prompt, validate_draft
from ..ports import CompileFailed, PolicyCompiler

PROBE_REPLY = "OK"
ENDPOINT_SUFFIX = "/chat/completions"
#: 没写 scheme 时按本机/内网地址猜 http——局域网与 Tailscale 上的推理服务几乎都是明文
LOCAL_PREFIXES = ("localhost", "127.", "0.0.0.0", "192.168.", "10.", "172.16.", "100.")


class OpenAICompatCompiler(PolicyCompiler):
    """一次 `compile` = 一次 HTTP = 一份草稿。校验在 `..draft.validate_draft`，不在这里。"""

    name = "openai_compat"

    def __init__(self, base_url: str, model: str, api_key: str = "", *,
                 timeout_seconds: float = 45.0, opener: Callable | None = None):
        if not (base_url or "").strip():
            raise ValueError("编译器需要 base_url：本机端点也要写 http://127.0.0.1:11434/v1")
        if not (model or "").strip():
            raise ValueError("编译器需要模型名")
        self.base_url = base_url.strip()
        self.model = model.strip()
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = timeout_seconds
        self._open = opener or urllib.request.urlopen

    # ------------------------------------------------------------------ 传输

    def _url(self) -> str:
        base = self.base_url.rstrip("/")
        if "://" not in base:                       # 只写 host:port 时替他补 scheme
            host = base.split("/")[0]
            base = ("http://" if host.startswith(LOCAL_PREFIXES) else "https://") + base
        return base if base.endswith(ENDPOINT_SUFFIX) else base + ENDPOINT_SUFFIX

    def _post(self, body: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self._url(),
                                     data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     method="POST", headers=headers)
        try:
            with self._open(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except CompileFailed:
            raise
        except Exception as exc:                       # 网络/超时/坏 JSON/4xx 都算编译不出来
            raise CompileFailed(f"编译器不可用（{self._url()}）：{self._safe(str(exc))}") from exc

    def _safe(self, text: str) -> str:
        """A3：密钥绝不进日志与 Trace。异常文本理论上不含它，这里仍然机械地抹一遍。"""
        out = text
        if self.api_key:
            out = out.replace(self.api_key, "***")
        return out[:200]

    def _chat(self, messages: list[dict]) -> str:
        body = {"model": self.model, "messages": messages, "temperature": 0,
                "max_tokens": 1500, "response_format": {"type": "json_object"}}
        raw = self._post(body)
        try:
            return str(raw["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise CompileFailed(f"编译器回答无法解析：{type(exc).__name__}") from exc

    # ------------------------------------------------------------------ 一次编译

    def compile(self, intent: str, catalog: list[dict], policy_id: str) -> dict:
        """意图文本 → 校验过的草稿（`Policy` 的 JSON 形状）。失败抛 `CompileFailed`。"""
        content = self._chat([{"role": "system", "content": spec(catalog)},
                                  {"role": "user", "content": user_prompt(intent, catalog)}])
        return validate_draft(extract_json(content), catalog, policy_id)

    def probe(self) -> str:
        """配置流程当场验通：验的是**这个端点会不会听话地只回一个词**，不只是通不通。
        一个能连通但爱附带解释文字的端点，第一次编译就会失败——那个错误该在填表时暴露。"""
        content = self._chat([{"role": "user",
                                   "content": f"只回复 {PROBE_REPLY} 这两个字母，不要任何别的内容。"}])
        return content.strip()[:20]


def extract_json(text: str) -> Any:
    """从回答里抠出第一个完整的 JSON 对象。

    不用 `json.loads(text)` 是因为本地模型爱在 JSON 外面包一层 ```json 围栏或"好的，如下："。
    花括号计数时要认字符串：`{"a": "}"}"` 里那个转义引号与括号都在串内，数错了就会截断。
    """
    start = text.find("{")
    if start < 0:
        raise CompileFailed("回答里找不到 JSON 对象——多半是模型开始讲道理了，重试或换个编译器模型")
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except ValueError as exc:
                    raise CompileFailed(f"回答里的 JSON 无法解析：{exc}") from exc
    raise CompileFailed("回答被截断了（JSON 对象没有闭合）——调大编译器的输出上限或换个模型")
