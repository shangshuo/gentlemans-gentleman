"""状态实体：每条策略一个，它的 `state` 就是模型给出的那个**定性状态**（ADR-0012）。

不放概率、不放小数——按 `CONTEXT.md` 的界线，状态是"用户说得出、看得见的定性描述"，
概率是**这一次判断的依据**，所以它和阈值、留痕 id、本次读数摘要一起住 attributes。
用户在仪表盘与自己的自动化里写的应该是 `is "在补觉"`，不是 `state | float > 0.65`。

N 次采样的分布**不进实体**：日常只判断 1 次，只有试跑才采 N 次，
一个平时恒为空的量不适合当常驻实体的职责（ADR-0012）。
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN
from .runtime import ButlerRuntime


async def async_setup_entry(hass, entry, async_add_entities):
    """实体清单跟着策略走：加一条策略多一个实体，删一条时由整条 entry 重载负责收干净。"""
    runtime: ButlerRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PolicyStatusSensor(runtime, entry, policy.id)
                        for policy in runtime.policies])


class PolicyStatusSensor(SensorEntity):
    """一条策略的当前状态。事件驱动：一轮判断跑完由运行时推一次，不轮询。"""

    # translation_key 要求 has_entity_name=True，而我们的名字是策略名拼出来的（动态），
    # 所以这里明确用完整名，不走翻译
    _attr_has_entity_name = False

    def __init__(self, runtime: ButlerRuntime, entry, policy_id: str) -> None:
        self._runtime = runtime
        policy = runtime.policy(policy_id)
        self._policy_id = policy_id
        self._attr_unique_id = f"{entry.entry_id}:{policy_id}"
        self._attr_name = f"{policy.name if policy else policy_id}·状态"

    @property
    def suggested_object_id(self) -> str | None:
        """实体号取自**策略 id**，不取自名字。

        两个理由，第二个是硬故障：中文经 HA 的 slugify（NFKD 后只留 ascii）会塌成
        空串，于是实体号变成随机的 `sensor.unnamed_entity_x`，用户在自己的自动化里
        根本没法引用它；而就算名字能 slugify，改一次策略显示名也会把实体号换掉，
        把所有引用它的自动化留在原地。`sensor.status_sunset_light` 才是可以写进 YAML
        的那个东西。
        """
        return f"status_{self._policy_id}"

    @property
    def native_value(self) -> str:
        status = self._runtime.status.get(self._policy_id)
        return status.status if status else "没跑过"

    @property
    def extra_state_attributes(self) -> dict:
        status = self._runtime.status.get(self._policy_id)
        return status.attributes() if status else {"结论": "这一轮还没跑过"}

    async def async_added_to_hass(self) -> None:
        """挂上运行时：每轮判断结束写一次状态，注销时自己摘掉监听。"""
        self.async_on_remove(self._runtime.async_add_listener(self.async_write_ha_state))
        await super().async_added_to_hass()
