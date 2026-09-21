"""服务：手动跑一轮判断。

存在的理由是 A2 的 P3——"装完 5 分钟内出现第一条留痕"。日落可能还在几个钟头之后，
等它不是产品；给一个手动触发点，用户当场就能看到第一条链跑完。

`samples` 就是 ADR-0009 的试跑入口：同一条代码路径，只是多采几次。
上限 10 次不是保守，是保护用户的账单——一次判断 ≈400 input tokens，
手滑填 100 就是 4 万次调用换不来任何额外信息（中位数在第 5 次之后基本不动）。
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .runtime import ButlerRuntime

SERVICE_RUN_CYCLE = "run_cycle"
MAX_SAMPLES = 10

SCHEMA = vol.Schema({
    vol.Optional("samples", default=1): vol.All(cv.positive_int, vol.Range(max=MAX_SAMPLES)),
})


def _runtimes(hass: HomeAssistant) -> list[ButlerRuntime]:
    return [r for r in hass.data.get(DOMAIN, {}).values() if isinstance(r, ButlerRuntime)]


def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_RUN_CYCLE):
        return

    async def handle(call: ServiceCall) -> None:
        runtimes = _runtimes(hass)
        if not runtimes:
            raise HomeAssistantError("还没有配置好的实例：先在集成配置里完成 Jev Key 校验")
        samples = call.data.get("samples", 1)
        for runtime in runtimes:
            await runtime.async_run("手动", samples=samples)

    hass.services.async_register(DOMAIN, SERVICE_RUN_CYCLE, handle, schema=SCHEMA)


def async_unload_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_RUN_CYCLE):
        hass.services.async_remove(DOMAIN, SERVICE_RUN_CYCLE)
