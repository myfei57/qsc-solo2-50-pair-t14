"""控制台 JSON 接口。

接口只做参数校验收敛与转发，业务判断全部留在组件与顺序执行器里：状态来自
``Platform`` 的状态快照，趋势走趋势缓冲，硫化／开模／调度／停运走四条工艺
顺序。控制台因此不会绕开联锁，也不会自己拼一条工艺链。
"""

from __future__ import annotations

from ..app import CycleInputs, Platform
from ..errors import RubberCureError

MAX_TREND_WINDOW_S = 86_400.0
MAX_AUDIT_LIMIT = 500


class ConsoleAPI:
    """把 HTTP 请求转成平台动作。"""

    def __init__(self, platform: Platform) -> None:
        self.platform = platform

    def status(self) -> dict:
        payload = self.platform.status()
        payload["topology"] = self.platform.topology_summary()
        payload["namespaces"] = self.platform.namespaces.describe()
        return payload

    def trend(self, series: str | None = None, window_s: float = 600.0) -> dict:
        """返回趋势窗口：采样点、均值与最新值。"""

        window = max(1.0, min(MAX_TREND_WINDOW_S, float(window_s)))
        names = [series] if series else self.platform.trend.series_names()
        known = self.platform.trend.series_names()
        unknown = [name for name in names if name not in known]
        if unknown:
            raise ValueError(f"未知的趋势分量: {', '.join(unknown)}")
        end = self.platform.clock.now()
        start = end - window
        result: dict[str, dict] = {}
        for name in names:
            filled = self.platform.trend.fill_gaps(name)
            samples = self.platform.trend.window(name, start, end)
            latest = self.platform.trend.latest(name)
            result[name] = {
                "filled_points": filled,
                "samples": [sample.as_dict() for sample in samples],
                "average": round(self.platform.trend.average(name, start, end), 4),
                "latest": latest.as_dict() if latest else None,
            }
        return {"window_s": window, "start": round(start, 3), "end": round(end, 3), "series": result}

    def alarms(self) -> dict:
        alarms = self.platform.audit.alarms()
        return {"count": len(alarms), "alarms": alarms}

    def audit(self, limit: int = 100) -> dict:
        bounded = max(1, min(MAX_AUDIT_LIMIT, int(limit)))
        return {
            "entries": self.platform.audit.entries(bounded),
            "recent": self.platform.audit.tail(bounded // 4 + 1),
        }

    def journal(self, segment: int | None = None, limit: int = 200) -> dict:
        """读落盘日志；给段号时只读该段。"""

        if segment is None:
            return {"segment": None, "entries": self.platform.audit.replay_from_journal(limit)}
        return {"segment": segment, "entries": self.platform.audit.replay_segment(segment)}

    def readiness(self) -> dict:
        """投产前体检：把机群与模具的关键前置条件摊开给控制台。"""

        platform = self.platform
        return {
            "idle_presses": [unit.press_id for unit in platform.fleet.idle_presses()],
            "loaded_molds": {
                unit.press_id: unit.mold_id for unit in platform.fleet.all() if unit.mold_id
            },
            "molds": summarize_count(platform),
            "latched": platform.latch.set_molds(),
            "queued": platform.scheduler.queue.entries(),
            "curves": sorted(platform.curves.snapshot()),
            "steam_target_kpa": platform.config.steam.target_pressure_kpa,
            "temp_setpoint_c": platform.config.temp.setpoint_c,
        }

    def cure(self, payload: dict) -> dict:
        """在一台硫化机上跑硫化顺序。"""

        mold_id = str(payload.get("mold_id", "mold-01"))
        try:
            return {
                "ok": True,
                "report": self.platform.run_cure(
                    mold_id,
                    product_code=payload.get("product_code"),
                    cavity_count=int(payload.get("cavity_count", 4)),
                    thickness_mm=payload.get("thickness_mm"),
                    press_id=str(payload.get("press_id", "press-01")),
                ),
            }
        except RubberCureError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def open_mold(self, payload: dict) -> dict:
        try:
            return {
                "ok": True,
                "report": self.platform.run_open(str(payload.get("press_id", "press-01"))),
            }
        except RubberCureError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def dispatch(self, payload: dict) -> dict:
        try:
            return {
                "ok": True,
                "report": self.platform.run_dispatch(str(payload.get("press_id", "press-01"))),
            }
        except RubberCureError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def stop(self, payload: dict) -> dict:
        try:
            return {
                "ok": True,
                "report": self.platform.run_stop(str(payload.get("press_id", "press-01"))),
            }
        except RubberCureError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def step(self, payload: dict) -> dict:
        """推进一个机群控制周期。"""

        inputs = CycleInputs(
            inlet_c=float(payload.get("inlet_c", 26.0)),
            steam_target_kpa=float(
                payload.get("steam_target_kpa", self.platform.config.steam.target_pressure_kpa)
            ),
            dt=float(payload.get("dt", 1.0)),
        )
        try:
            return {"ok": True, "cycle": self.platform.cycle(inputs)}
        except RubberCureError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def enqueue(self, payload: dict) -> dict:
        mold_id = str(payload.get("mold_id", "mold-01"))
        try:
            return {
                "ok": True,
                "queued": self.platform.enqueue_mold(
                    mold_id,
                    thickness_mm=payload.get("thickness_mm"),
                    priority=int(payload.get("priority", 50)),
                ),
            }
        except RubberCureError as error:
            return {"ok": False, "error": self.platform.note_error(error)}

    def remap(self, payload: dict) -> dict:
        """重新登记传感器映射。"""

        mold_id = str(payload.get("mold_id", "mold-01"))
        raw = payload.get("sensors") or {}
        try:
            sensors = {int(cavity): str(sensor) for cavity, sensor in raw.items()}
            return {"ok": True, "mapping": self.platform.remap_sensor(mold_id, sensors)}
        except (RubberCureError, ValueError) as error:
            if isinstance(error, RubberCureError):
                return {"ok": False, "error": self.platform.note_error(error)}
            return {"ok": False, "error": {"type": "ValueError", "message": str(error)}}

    def retire(self, payload: dict) -> dict:
        """把一台模具标记为报废。"""

        mold_id = str(payload.get("mold_id", "mold-01"))
        reason = str(payload.get("reason", "控制台触发的检修报废"))
        try:
            return {"ok": True, "mold": self.platform.retire_mold(mold_id, reason)}
        except RubberCureError as error:
            return {"ok": False, "error": self.platform.note_error(error)}


def summarize_count(platform: Platform) -> dict:
    """统计台账里的模具数量，供体检接口使用。"""

    records = platform.registry.describe()
    return {
        "total": len(records),
        "active": sum(1 for record in records if record["status"] == "active"),
    }
