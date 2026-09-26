"""平台配置。

配置来自 YAML（由 vendored PyYAML 解析），覆盖机群规模、蒸汽压力窗口、
模温目标与容差、硫化时长偏差窗口、冷却水限值与控制台页面清单。任何缺失
或越界取值都在装配阶段抛出 :class:`ConfigError`，不允许带着半份配置启动
控制链。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

import yaml

from .errors import ConfigError

_MISSING = object()


def _section(mapping: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = mapping.get(key)
    if value is None:
        raise ConfigError(f"配置缺少段落 {key!r}", component="config")
    if not isinstance(value, Mapping):
        raise ConfigError(f"配置段落 {key!r} 必须是映射", component="config")
    return value


def _number(section: Mapping[str, Any], key: str, *, minimum: float | None = None) -> float:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        raise ConfigError(f"配置缺少字段 {key!r}", component="config")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"配置字段 {key!r} 必须是数值", component="config")
    number = float(value)
    if minimum is not None and number < minimum:
        raise ConfigError(f"配置字段 {key!r} 不得小于 {minimum}", component="config")
    return number


def _integer(section: Mapping[str, Any], key: str, *, minimum: int | None = None) -> int:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        raise ConfigError(f"配置缺少字段 {key!r}", component="config")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"配置字段 {key!r} 必须是整数", component="config")
    if minimum is not None and value < minimum:
        raise ConfigError(f"配置字段 {key!r} 不得小于 {minimum}", component="config")
    return int(value)


def _text(section: Mapping[str, Any], key: str) -> str:
    value = section.get(key, _MISSING)
    if value is _MISSING:
        raise ConfigError(f"配置缺少字段 {key!r}", component="config")
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"配置字段 {key!r} 必须是非空字符串", component="config")
    return value


@dataclass(frozen=True)
class MoldLimits:
    """模具登记约束。"""

    max_cavities: int
    sensor_map_max_age_s: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "MoldLimits":
        return cls(
            max_cavities=_integer(section, "max_cavities", minimum=1),
            sensor_map_max_age_s=_number(section, "sensor_map_max_age_s", minimum=1.0),
        )

    def as_dict(self) -> dict:
        return {
            "max_cavities": self.max_cavities,
            "sensor_map_max_age_s": self.sensor_map_max_age_s,
        }


@dataclass(frozen=True)
class PressLimits:
    """机群与制品约束。"""

    fleet_size: int
    default_thickness_mm: float
    default_product_code: str

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "PressLimits":
        return cls(
            fleet_size=_integer(section, "fleet_size", minimum=1),
            default_thickness_mm=_number(section, "default_thickness_mm", minimum=0.1),
            default_product_code=_text(section, "default_product_code"),
        )

    def as_dict(self) -> dict:
        return {
            "fleet_size": self.fleet_size,
            "default_thickness_mm": self.default_thickness_mm,
            "default_product_code": self.default_product_code,
        }


@dataclass(frozen=True)
class SteamLimits:
    """蒸汽压力窗口与排气／泄压保持时长。"""

    target_pressure_kpa: float
    min_pressure_kpa: float
    max_pressure_kpa: float
    safe_residual_kpa: float
    purge_hold_s: float
    purge_token_max_age_s: float
    vent_hold_s: float
    valve_rated_flow_kg_h: float
    trap_rated_flow_kg_h: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "SteamLimits":
        limits = cls(
            target_pressure_kpa=_number(section, "target_pressure_kpa", minimum=0.0),
            min_pressure_kpa=_number(section, "min_pressure_kpa", minimum=0.0),
            max_pressure_kpa=_number(section, "max_pressure_kpa", minimum=0.0),
            safe_residual_kpa=_number(section, "safe_residual_kpa", minimum=0.0),
            purge_hold_s=_number(section, "purge_hold_s", minimum=0.0),
            purge_token_max_age_s=_number(section, "purge_token_max_age_s", minimum=1.0),
            vent_hold_s=_number(section, "vent_hold_s", minimum=0.0),
            valve_rated_flow_kg_h=_number(section, "valve_rated_flow_kg_h", minimum=1.0),
            trap_rated_flow_kg_h=_number(section, "trap_rated_flow_kg_h", minimum=1.0),
        )
        if not limits.min_pressure_kpa < limits.target_pressure_kpa < limits.max_pressure_kpa:
            raise ConfigError("蒸汽压力必须满足 下限 < 目标 < 上限", component="config")
        if limits.safe_residual_kpa >= limits.target_pressure_kpa:
            raise ConfigError("安全残余压力必须低于工作压力", component="config")
        return limits

    def clamp_pressure(self, target_kpa: float) -> float:
        """把压力目标夹到允许窗口内。"""

        return max(self.min_pressure_kpa, min(self.max_pressure_kpa, float(target_kpa)))

    def as_dict(self) -> dict:
        return {
            "target_pressure_kpa": self.target_pressure_kpa,
            "min_pressure_kpa": self.min_pressure_kpa,
            "max_pressure_kpa": self.max_pressure_kpa,
            "safe_residual_kpa": self.safe_residual_kpa,
            "purge_hold_s": self.purge_hold_s,
            "purge_token_max_age_s": self.purge_token_max_age_s,
            "vent_hold_s": self.vent_hold_s,
            "valve_rated_flow_kg_h": self.valve_rated_flow_kg_h,
            "trap_rated_flow_kg_h": self.trap_rated_flow_kg_h,
        }


@dataclass(frozen=True)
class TempLimits:
    """模温目标、容差与确认保持时长。"""

    setpoint_c: float
    tolerance_c: float
    warmup_rate_c_per_min: float
    confirm_hold_s: float
    max_cavity_spread_c: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "TempLimits":
        limits = cls(
            setpoint_c=_number(section, "setpoint_c"),
            tolerance_c=_number(section, "tolerance_c", minimum=0.0),
            warmup_rate_c_per_min=_number(section, "warmup_rate_c_per_min", minimum=0.1),
            confirm_hold_s=_number(section, "confirm_hold_s", minimum=1.0),
            max_cavity_spread_c=_number(section, "max_cavity_spread_c", minimum=0.0),
        )
        return limits

    def band(self) -> tuple[float, float]:
        return (self.setpoint_c - self.tolerance_c, self.setpoint_c + self.tolerance_c)

    def within_tolerance(self, measured_c: float) -> bool:
        """判定测量值是否落在容差带内。"""

        low, high = self.band()
        return low <= measured_c <= high

    def as_dict(self) -> dict:
        return {
            "setpoint_c": self.setpoint_c,
            "tolerance_c": self.tolerance_c,
            "warmup_rate_c_per_min": self.warmup_rate_c_per_min,
            "confirm_hold_s": self.confirm_hold_s,
            "max_cavity_spread_c": self.max_cavity_spread_c,
        }


@dataclass(frozen=True)
class TimerLimits:
    """硫化时长偏差窗口。"""

    default_nominal_s: float
    shortfall_ratio: float
    overrun_ratio: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "TimerLimits":
        limits = cls(
            default_nominal_s=_number(section, "default_nominal_s", minimum=1.0),
            shortfall_ratio=_number(section, "shortfall_ratio", minimum=0.0),
            overrun_ratio=_number(section, "overrun_ratio", minimum=0.0),
        )
        if limits.shortfall_ratio >= 1.0:
            raise ConfigError("欠硫判定比例必须小于 1", component="config")
        return limits

    def as_dict(self) -> dict:
        return {
            "default_nominal_s": self.default_nominal_s,
            "shortfall_ratio": self.shortfall_ratio,
            "overrun_ratio": self.overrun_ratio,
        }


@dataclass(frozen=True)
class CoolLimits:
    """冷却水流量与温度限值。"""

    rated_flow_m3_h: float
    min_flow_m3_h: float
    alarm_inlet_c: float
    alarm_outlet_c: float
    drain_hold_s: float

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "CoolLimits":
        limits = cls(
            rated_flow_m3_h=_number(section, "rated_flow_m3_h", minimum=0.1),
            min_flow_m3_h=_number(section, "min_flow_m3_h", minimum=0.0),
            alarm_inlet_c=_number(section, "alarm_inlet_c"),
            alarm_outlet_c=_number(section, "alarm_outlet_c"),
            drain_hold_s=_number(section, "drain_hold_s", minimum=0.0),
        )
        if limits.min_flow_m3_h > limits.rated_flow_m3_h:
            raise ConfigError("冷却最小流量不得高于额定流量", component="config")
        if limits.alarm_inlet_c > limits.alarm_outlet_c:
            raise ConfigError("冷却进水告警温度不得高于出水告警温度", component="config")
        return limits

    def as_dict(self) -> dict:
        return {
            "rated_flow_m3_h": self.rated_flow_m3_h,
            "min_flow_m3_h": self.min_flow_m3_h,
            "alarm_inlet_c": self.alarm_inlet_c,
            "alarm_outlet_c": self.alarm_outlet_c,
            "drain_hold_s": self.drain_hold_s,
        }


@dataclass(frozen=True)
class BatchLimits:
    """机群调度约束。"""

    dispatch_window_s: float
    max_queue: int

    @classmethod
    def from_mapping(cls, section: Mapping[str, Any]) -> "BatchLimits":
        return cls(
            dispatch_window_s=_number(section, "dispatch_window_s", minimum=1.0),
            max_queue=_integer(section, "max_queue", minimum=1),
        )

    def as_dict(self) -> dict:
        return {"dispatch_window_s": self.dispatch_window_s, "max_queue": self.max_queue}


@dataclass(frozen=True)
class PlatformConfig:
    """一份完整的平台配置。"""

    name: str
    project_id: str
    storage_root: str
    mold: MoldLimits
    press: PressLimits
    steam: SteamLimits
    temp: TempLimits
    timer: TimerLimits
    cool: CoolLimits
    batch: BatchLimits
    pages: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Any]) -> "PlatformConfig":
        meta = _section(mapping, "platform")
        name = meta.get("name")
        project_id = meta.get("project_id")
        storage_root = meta.get("storage_root")
        if not isinstance(name, str) or not name:
            raise ConfigError("platform.name 必须是非空字符串", component="config")
        if not isinstance(project_id, str) or not project_id:
            raise ConfigError("platform.project_id 必须是非空字符串", component="config")
        if not isinstance(storage_root, str) or not storage_root:
            raise ConfigError("platform.storage_root 必须是非空字符串", component="config")

        limits = _section(mapping, "limits")
        pages = mapping.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ConfigError("pages 必须是非空的页面名列表", component="config")
        for page in pages:
            if not isinstance(page, str) or not page.strip():
                raise ConfigError("pages 中的页面名必须是非空字符串", component="config")

        return cls(
            name=name,
            project_id=project_id,
            storage_root=storage_root,
            mold=MoldLimits.from_mapping(_section(limits, "mold")),
            press=PressLimits.from_mapping(_section(limits, "press")),
            steam=SteamLimits.from_mapping(_section(limits, "steam")),
            temp=TempLimits.from_mapping(_section(limits, "temp")),
            timer=TimerLimits.from_mapping(_section(limits, "timer")),
            cool=CoolLimits.from_mapping(_section(limits, "cool")),
            batch=BatchLimits.from_mapping(_section(limits, "batch")),
            pages=tuple(pages),
        )


def load_config(path: str) -> PlatformConfig:
    """从 YAML 文件读取配置。"""

    if not os.path.isfile(path):
        raise ConfigError(f"配置文件不存在: {path}", component="config")
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, Mapping):
        raise ConfigError("配置文件顶层必须是映射", component="config")
    return PlatformConfig.from_mapping(raw)


def default_config() -> PlatformConfig:
    """内置默认配置，供未指定配置文件时装配使用。"""

    return PlatformConfig(
        name="RubberCure",
        project_id="zxy-73-py",
        storage_root="var/state",
        mold=MoldLimits(max_cavities=8, sensor_map_max_age_s=172_800.0),
        press=PressLimits(
            fleet_size=3, default_thickness_mm=6.0, default_product_code="RB-215"
        ),
        steam=SteamLimits(
            target_pressure_kpa=640.0,
            min_pressure_kpa=80.0,
            max_pressure_kpa=760.0,
            safe_residual_kpa=12.0,
            purge_hold_s=20.0,
            purge_token_max_age_s=180.0,
            vent_hold_s=8.0,
            valve_rated_flow_kg_h=960.0,
            trap_rated_flow_kg_h=240.0,
        ),
        temp=TempLimits(
            setpoint_c=151.0,
            tolerance_c=2.0,
            warmup_rate_c_per_min=6.0,
            confirm_hold_s=60.0,
            max_cavity_spread_c=3.0,
        ),
        timer=TimerLimits(default_nominal_s=600.0, shortfall_ratio=0.05, overrun_ratio=0.08),
        cool=CoolLimits(
            rated_flow_m3_h=18.0,
            min_flow_m3_h=6.0,
            alarm_inlet_c=32.0,
            alarm_outlet_c=48.0,
            drain_hold_s=30.0,
        ),
        batch=BatchLimits(dispatch_window_s=180.0, max_queue=12),
        pages=("press", "steam", "timer", "alarms"),
    )


def config_to_mapping(cfg: PlatformConfig) -> dict:
    """把配置还原成可打印／可回写的映射。"""

    return {
        "platform": {
            "name": cfg.name,
            "project_id": cfg.project_id,
            "storage_root": cfg.storage_root,
        },
        "limits": {
            "mold": cfg.mold.as_dict(),
            "press": cfg.press.as_dict(),
            "steam": cfg.steam.as_dict(),
            "temp": cfg.temp.as_dict(),
            "timer": cfg.timer.as_dict(),
            "cool": cfg.cool.as_dict(),
            "batch": cfg.batch.as_dict(),
        },
        "pages": list(cfg.pages),
    }
