"""薄壳入口：装配内核、转发平台、管生命周期。

这里只翻译"宿主的生命周期 ↔ 内核对象"，**一条判断逻辑都不写**（A1 第1条）。
判断链在 `runtime.ButlerRuntime` 里装配，端口实现各在 `host.py` 与 `butler/adapters/`。

状态用 `hass.data` 而不是 `entry.runtime_data` 承载：后者是较新 HA 的写法，
而我们唯一验过的现场 HA 是 2024.3.3——用哪一版都得写死一个，那就写兼容面更广的那个。
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .runtime import ButlerRuntime
from .services import async_setup_services, async_unload_services

PLATFORMS = [Platform.SENSOR]
_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """一切配置都走 UI：不需要用户碰 `configuration.yaml`（A2 的 P2）。"""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = ButlerRuntime(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = runtime

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await runtime.async_start()
    async_setup_services(hass)
    # 策略与阈值都住在 entry.data 里，改完必须重建运行时——不留旧对象继续跑（ADR-0013）
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    _LOGGER.info("已装配 %d 条策略，留痕写到 %s", len(runtime.policies), runtime.tracer.path)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """更新监听只在 `async_update_entry` 真的改了内容时触发一次，不需要自己防重入。"""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if runtime is not None:
        runtime.started = False
    if not any(hass.data.get(DOMAIN, {}).values()):
        async_unload_services(hass)
    return unloaded
