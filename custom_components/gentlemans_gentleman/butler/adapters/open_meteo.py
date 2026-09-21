"""Open-Meteo：外部事实端口 V1 的默认实现（ADR-0002）。

免 Key、无需注册，实测直连可用。这里只守两条由**实测逼出来的硬约束**：

1. **必须批量合并。** 天气与空气质量是**两个不同主机**，逐字段打就是每条策略两次
   串行调用 ≈3.4s。本实现按主机分组，一组一次 HTTP，且一次把这组里**所有**已登记的
   语义字段都取回来——反正同一个响应里带着，多要几个字段不多花一次调用。
2. **必须 TTL 缓存。** 周期性的策略一小时就能打 6 次外部接口。缓存时长不自己编，
   **直接取响应里的 `current.interval`**（天气 900s、空气 3600s，实测自带），
   服务商不更新数据时我们也不浪费调用。

**语义字段名归内核，不归服务商**（ADR-0002）：策略里写 `outdoor_irradiance`，
`shortwave_radiation` 只出现在下面这张映射表里。换服务商时用户的策略资产不动。

冷缓存路径（两组 ≈2+2.5s）**不由内核解决**：HA 薄壳用 coordinator 按 TTL 预热，
用户真正等的那次触发是缓存命中（实测亚毫秒）。

⚠️ 为什么没有"室外照度（lx）"：Open-Meteo 不提供 lux 字段（实测 `illuminance`
直接 HTTP 400）。把 W/m² 乘个光效系数换成 lux 是个**假设**，不是事实，v0.1 不编——
`outdoor_irradiance` 就是"天黑透没黑透"的那个诚实读数。
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Callable

from ..ports import ExternalContextProvider, ProviderUnavailable

HOSTS = {
    "weather": "https://api.open-meteo.com/v1/forecast",
    "air": "https://air-quality-api.open-meteo.com/v1/air-quality",
}

#: 内核语义字段 → (主机分组, 服务商原始字段)。策略与表单只能用左边的名字。
FIELD_MAP: dict[str, tuple[str, str]] = {
    "outdoor_temp": ("weather", "temperature_2m"),
    "apparent_temp": ("weather", "apparent_temperature"),
    "humidity": ("weather", "relative_humidity_2m"),
    "cloud_cover": ("weather", "cloud_cover"),
    "precipitation": ("weather", "precipitation"),
    "wind_speed": ("weather", "wind_speed_10m"),
    "is_daylight": ("weather", "is_day"),
    "outdoor_irradiance": ("weather", "shortwave_radiation"),
    "pm25": ("air", "pm2_5"),
    "pm10": ("air", "pm10"),
    "us_aqi": ("air", "us_aqi"),
}

FALLBACK_TTL = 900.0        # 响应没带 interval 时的兜底缓存时长（秒）


class OpenMeteoProvider(ExternalContextProvider):
    """经纬度由宿主注入（HA 薄壳取 `hass.config.latitude/longitude`），用户零配置。"""

    name = "open_meteo"

    def __init__(self, latitude: float, longitude: float, *,
                 timeout_seconds: float = 5.0,
                 opener: Callable | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.latitude = latitude
        self.longitude = longitude
        self.timeout_seconds = timeout_seconds
        self._open = opener or urllib.request.urlopen
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}   # 分组 → (过期时刻, 值)
        self.http_calls = 0                                          # 给试跑与自检看的账

    def capabilities(self) -> dict:
        return {"provider": self.name, "hosts": sorted(HOSTS),
                "fields": sorted(FIELD_MAP)}

    # ------------------------------------------------------------------ 取数

    def fetch(self, keys: list[str], at: str) -> dict[str, Any]:
        """一次批量取回语义字段。同主机合并成一次 HTTP，命中缓存则零次。"""
        out: dict[str, Any] = {}
        for group in dict.fromkeys(FIELD_MAP[k][0] for k in keys if k in FIELD_MAP):
            values = self._group(group)
            out.update({k: values[k] for k in keys
                        if k in FIELD_MAP and FIELD_MAP[k][0] == group and k in values})
        return out

    def _group(self, group: str) -> dict[str, Any]:
        expires_at, values = self._cache.get(group, (0.0, {}))
        now = self._clock()
        if now < expires_at:
            return values
        current = self._request(group)
        ttl = float(current.get("interval") or FALLBACK_TTL)
        fields = {sem: raw for sem, (g, raw) in FIELD_MAP.items() if g == group}
        got = {sem: current[raw] for sem, raw in fields.items()
               if current.get(raw) is not None}
        self._cache[group] = (now + ttl, got)
        return got

    def _request(self, group: str) -> dict:
        fields = ",".join(raw for g, raw in FIELD_MAP.values() if g == group)
        url = (f"{HOSTS[group]}?latitude={self.latitude}&longitude={self.longitude}"
               f"&current={fields}")
        try:
            self.http_calls += 1
            with self._open(urllib.request.Request(url),
                            timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise ProviderUnavailable(f"Open-Meteo {group} 不可达：{type(exc).__name__}") from exc
        if body.get("error"):                        # 实测：坏请求是 HTTP 400 + {"error":true}
            raise ProviderUnavailable(f"Open-Meteo {group} 拒绝：{body.get('reason', '')}"[:200])
        data = body.get("current")
        if not isinstance(data, dict):
            raise ProviderUnavailable(f"Open-Meteo {group} 回答里没有 current")
        return data
