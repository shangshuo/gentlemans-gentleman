"""薄壳的常量：只有这里允许出现 HA 侧的键名与配置项名字。

**键名不带供应商名**（修正案 A4）：配置项里写着 `jev_key`，等于在结构上宣称"这个插件
判断时只能用 Jev"——而用户要的恰恰相反，插件本身才是核心。两个模型角色各有三个字段，
换端点、换模型、换供应商都在界面里完成。
"""
from __future__ import annotations

from .butler.adapters import JEV_ENDPOINT

DOMAIN = "gentlemans_gentleman"

# 触发方式。内核不认宿主事件——触发由薄壳翻译成"什么时候跑一次判断链"
TRIGGER_SUNSET = "sunset"
TRIGGER_SUNRISE = "sunrise"
TRIGGER_INTERVAL = "interval"

#: 判别器（运行期判断）。方言是 Jev 的私有形状，见 `butler/adapters/jev.py`
CONF_DECISION_BASE_URL = "decision_base_url"
CONF_DECISION_MODEL = "decision_model"
CONF_DECISION_KEY = "decision_key"
#: 编译器（配置期把意图编译成策略草稿）。方言是 OpenAI 兼容的 chat/completions
CONF_COMPILER_BASE_URL = "compiler_base_url"
CONF_COMPILER_MODEL = "compiler_model"
CONF_COMPILER_KEY = "compiler_key"

CONF_TIMEOUT = "timeout_seconds"
CONF_POLICIES = "policies"

DEFAULT_DECISION_BASE_URL = JEV_ENDPOINT
DEFAULT_DECISION_MODEL = "jev-latest"
DEFAULT_COMPILER_MODEL = ""            # 空＝未配置；未配置时"用一段想法新增"如实报错
DEFAULT_TIMEOUT = 3.0                  # 实测中位 1.4s、峰值 2.6s；冷连接的首请求最贴边
COMPILER_TIMEOUT = 45.0                # 一次编译是用户在表单前等，不是夜间判断（本地模型慢）
HOST_TIMEOUT = 10.0                    # 薄壳把一次 hass 访问送回事件循环的等待上限
CYCLE_TIMEOUT = 60.0                   # 一整条链的预算：设备卡住时不能让运行时永久挂着一个线程
EXTERNAL_TTL = 900                     # 外部缓存预热周期，对齐 Open-Meteo 天气的 interval
TRIAL_SAMPLES = 3                      # 试跑采样次数（ADR-0009）

#: v0.1 的 entry 用的键；迁移只改名，不猜用户没填过的编译器配置（ADR-0017 第 1 条）
LEGACY_KEYS = {"jev_key": CONF_DECISION_KEY, "jev_model": CONF_DECISION_MODEL}
