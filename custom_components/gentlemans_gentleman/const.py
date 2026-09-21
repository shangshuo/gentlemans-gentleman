"""薄壳的常量：只有这里允许出现 HA 侧的键名与配置项名字。"""
from __future__ import annotations

DOMAIN = "gentlemans_gentleman"

# 触发方式。内核不认宿主事件——触发由薄壳翻译成"什么时候跑一次判断链"
TRIGGER_SUNSET = "sunset"
TRIGGER_SUNRISE = "sunrise"
TRIGGER_INTERVAL = "interval"

CONF_JEV_KEY = "jev_key"
CONF_JEV_MODEL = "jev_model"
CONF_TIMEOUT = "timeout_seconds"
CONF_POLICIES = "policies"

DEFAULT_JEV_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 3.0           # 实测中位 1.4s、峰值 2.6s；冷连接的首请求最贴边
HOST_TIMEOUT = 10.0             # 薄壳把一次 hass 访问送回事件循环的等待上限
CYCLE_TIMEOUT = 60.0            # 一整条链的预算：设备卡住时不能让运行时永久挂着一个线程
EXTERNAL_TTL = 900              # 外部缓存预热周期，对齐 Open-Meteo 天气的 interval
