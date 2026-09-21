"""配置流程：填 Key → 当场验通 → 绑实体 → 建 entry（A2 的 P2 与 P3）。

两条设计约束值得单独说：

**验通与探活是同一个动作的两遍用法。** A2 要求"首次启用必须通过验证测试才可用"，
而实测冷连接的首个 Jev 请求要 2.1–2.6s。这里在流程里真打一次：既证明 Key 有效，
也让用户把 TLS 握手的钱付在填表的时候，而不是付在第一次夜间触发上。

**报错分得清，才不用把异常文本甩给用户。** `ProviderUnavailable` 的 `__cause__`
保留着传输层的原始异常，所以 401、超时、DNS 不通落到三个不同的错误键上——
一句"模型不可达"没法让人知道该去查 Key 还是查网线（A2 P7：配置错误在 UI 内报错）。
"""
from __future__ import annotations

import logging
import urllib.error

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (EntitySelector, EntitySelectorConfig,
                                             TextSelector, TextSelectorConfig,
                                             TextSelectorType)

from .butler.adapters import JevProvider
from .butler.ports import ProviderUnavailable
from .const import (CONF_JEV_KEY, CONF_JEV_MODEL, CONF_POLICIES, CONF_TIMEOUT,
                    DEFAULT_JEV_MODEL, DEFAULT_TIMEOUT, DOMAIN)
from .policies import sample_policy
from .runtime import PROBE_REQUEST

_LOGGER = logging.getLogger(__name__)


def classify(exc: ProviderUnavailable) -> str:
    """把一次失败归到 UI 上看得懂的那条消息。认不出来就落 `unreachable`，不抛新异常。"""
    cause = exc.__cause__
    if isinstance(cause, urllib.error.HTTPError):
        return "auth_failed" if cause.code in (401, 403) else "http_error"
    # socket.timeout 自 3.10 起就是内置 TimeoutError 的别名，一个写法就够
    if isinstance(cause, TimeoutError):
        return "timeout"
    return "unreachable"


class ButlerConfigFlow(ConfigFlow, domain=DOMAIN):
    """单实例：一台 HA 一个 entry。多个 entry 会让"哪个阈值在生效"重新变成两个真值。"""

    VERSION = 1

    def __init__(self) -> None:
        self._setup: dict = {}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            key = user_input[CONF_JEV_KEY].strip()
            provider = JevProvider(key, model=DEFAULT_JEV_MODEL,
                                   timeout_seconds=DEFAULT_TIMEOUT)
            try:
                await self.hass.async_add_executor_job(provider.evaluate, PROBE_REQUEST)
            except ProviderUnavailable as exc:
                _LOGGER.info("配置阶段的 Jev 校验没通过：%s", exc)
                errors["base"] = classify(exc)
            else:
                self._setup = {CONF_JEV_KEY: key, CONF_JEV_MODEL: DEFAULT_JEV_MODEL,
                               CONF_TIMEOUT: DEFAULT_TIMEOUT}
                return await self.async_step_entities()

        schema = vol.Schema({
            vol.Required(CONF_JEV_KEY): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="off"))})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_entities(self, user_input: dict | None = None) -> ConfigFlowResult:
        """绑实体：ADR-0011 四字段里的"读哪些数、动哪个设备"，内置样例给默认值。

        窗帘与人体感应允许留空——**这个产品不要求用户配备某个传感器**（ADR-0004），
        留空的读数会在上下文里缺席并被记进留痕，而不是被一个假数字顶替。
        """
        if user_input is not None:
            policy = sample_policy(user_input.get("cover") or "cover.bedroom",
                                   user_input.get("motion") or "binary_sensor.motion",
                                   user_input["light"])
            return self.async_create_entry(
                title="Gentleman's Gentleman",
                data={**self._setup, CONF_POLICIES: [policy.to_dict()]})

        schema = vol.Schema({
            vol.Required("light"): EntitySelector(EntitySelectorConfig(domain="light")),
            vol.Optional("cover"): EntitySelector(EntitySelectorConfig(domain="cover")),
            vol.Optional("motion"): EntitySelector(EntitySelectorConfig(domain="binary_sensor")),
        })
        return self.async_show_form(step_id="entities", data_schema=schema)
