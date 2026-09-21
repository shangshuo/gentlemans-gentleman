"""诊断包（A2 的 P6 ＋ 修正案 A3 的"可验证姿态"）。

A3 的立场是：**在 HA 上给集成配置加密是不可能的**（config entry 明文存 `.storage`，
0o644），所以能承诺的不是"存得安全"，而是"我们不经手、不外传"——具体到代码就是三件事：
密钥只出现在 `entry.data` 一个地方；日志与留痕不写它；诊断包导出前必过脱敏。
`tests/test_shell.py` 里有一条测试专门盯这三件事，改了就算违约。
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .const import CONF_JEV_KEY, DOMAIN
from .runtime import ButlerRuntime


async def async_get_config_entry_diagnostics(hass: HomeAssistant,
                                             entry: ConfigEntry) -> dict:
    """一份"能定位问题但不含密钥"的现场快照。"""
    runtime: ButlerRuntime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    # 用整段 REDACTED 而不是"留首尾四个字符"：那八个字符仍是这把 Key 的一部分，
    # 而诊断包是要发给别人看的
    data = async_redact_data(dict(entry.data), {CONF_JEV_KEY})
    diagnostics: dict = {
        "配置": data,
        "策略": [{"id": p.id, "名字": p.name, "启用": p.enabled,
                  "判断形状": p.judgment.shape.value, "阈值": p.judgment.threshold,
                  "读数": [r.key for r in p.readings],
                  "触发": p.trigger} for p in (runtime.policies if runtime else [])],
    }
    if runtime is None:
        diagnostics["状态"] = "运行时未装配（集成可能没起来）"
        return diagnostics
    diagnostics["状态"] = {key: status.attributes() for key, status in runtime.status.items()}
    diagnostics["模型能力"] = runtime.provider.capabilities()
    diagnostics["外部事实"] = {
        "能力": runtime.external.capabilities(),
        "本次进程 HTTP 次数": runtime.external.http_calls,
    }
    diagnostics["留痕文件"] = str(runtime.tracer.path)
    return diagnostics
