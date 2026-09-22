"""端口实现：把外部世界接进内核（A1 第2条的四个插槽各有默认插头）。

这里放的是**与宿主无关**的适配器——Jev、Open-Meteo、OpenAI 兼容端点都不认识 Home
Assistant，换宿主时它们原样可用，所以它们属于内核资产，跟着 `butler/` 一起折叠进发布包。
宿主侧的东西（HA 适配器、薄壳）不放在这里。
"""
from .jev import ENDPOINT as JEV_ENDPOINT
from .jev import JevProvider
from .open_meteo import FIELD_MAP, OpenMeteoProvider
from .openai_compat import OpenAICompatCompiler

__all__ = ["JevProvider", "JEV_ENDPOINT", "OpenMeteoProvider", "FIELD_MAP",
           "OpenAICompatCompiler"]
