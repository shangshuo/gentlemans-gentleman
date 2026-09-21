"""端口实现：把外部世界接进内核（A1 第2条的三个插槽各有一个默认插头）。

这里放的是**与宿主无关**的适配器——Jev 与 Open-Meteo 都不认识 Home Assistant，
换宿主时它们原样可用，所以它们属于内核资产，跟着 `butler/` 一起折叠进发布包。
宿主侧的东西（HA 适配器、薄壳）不放在这里。
"""
from .jev import JevProvider
from .open_meteo import FIELD_MAP, OpenMeteoProvider

__all__ = ["JevProvider", "OpenMeteoProvider", "FIELD_MAP"]
