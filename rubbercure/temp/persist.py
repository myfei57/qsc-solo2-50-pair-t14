"""模温确认落盘。

硫化计时只能在本机确认模温已进入容差带、并且这份确认已经落盘之后启动。
"确认"是运行期状态，"落盘"是持久事实：控制器重启后按下盘内容恢复，因此
没有落盘记录的确认一律不算数。落盘采用原子替换，避免留下半份 JSON 让
重启后的判定落空。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from ..clock import Clock
from ..config import TempLimits
from ..errors import InterlockError
from ..eventbus import Event, EventBus
from ..store.jsonfile import JsonStore
from .read import MoldTempReading
from .tolerance import band_summary

STATE_KEY = "temp_confirmation"


@dataclass(frozen=True)
class TemperatureConfirmation:
    """一份已经落盘的模温确认。"""

    mold_id: str
    generation: int
    setpoint_c: float
    confirmed_c: float
    confirmed_at: float
    hold_s: float
    digest: str = ""

    def as_dict(self) -> dict:
        return {
            "mold_id": self.mold_id,
            "generation": self.generation,
            "setpoint_c": self.setpoint_c,
            "confirmed_c": round(self.confirmed_c, 3),
            "confirmed_at": round(self.confirmed_at, 3),
            "hold_s": round(self.hold_s, 3),
            "digest": self.digest[:12],
        }


class TempPersister:
    """把模温确认写入原子存储，并对外提供"已落盘"判定。"""

    def __init__(
        self,
        clock: Clock,
        bus: EventBus,
        store: JsonStore,
        limits: TempLimits,
    ) -> None:
        self._clock = clock
        self._bus = bus
        self._store = store
        self._limits = limits
        self._latest: TemperatureConfirmation | None = None
        self._lock = threading.RLock()

    def confirm(self, reading: MoldTempReading, hold_s: float) -> TemperatureConfirmation:
        """在模温进入容差带并保持足够时长后写下一份确认。"""

        measured = reading.average_c()
        if not self._limits.within_tolerance(measured):
            raise InterlockError(
                f"模具 {reading.mold_id} 模温 {measured:.2f}℃ 未进入容差带，禁止确认",
                component="temp",
            )
        if hold_s < self._limits.confirm_hold_s:
            raise InterlockError(
                f"模具 {reading.mold_id} 模温保持 {hold_s:.1f}s 未达 {self._limits.confirm_hold_s:.1f}s",
                component="temp",
            )
        payload = {
            "mold_id": reading.mold_id,
            "generation": reading.generation,
            "setpoint_c": self._limits.setpoint_c,
            "confirmed_c": round(measured, 4),
            "confirmed_at": round(self._clock.now(), 4),
            "hold_s": round(float(hold_s), 4),
        }
        digest = self._store.write(STATE_KEY, payload)
        confirmation = TemperatureConfirmation(
            mold_id=reading.mold_id,
            generation=reading.generation,
            setpoint_c=self._limits.setpoint_c,
            confirmed_c=measured,
            confirmed_at=payload["confirmed_at"],
            hold_s=hold_s,
            digest=digest,
        )
        with self._lock:
            self._latest = confirmation
        detail = confirmation.as_dict()
        detail.update(
            band_summary(measured, self._limits.setpoint_c, self._limits.tolerance_c)
        )
        self._bus.publish(
            Event.of(
                "temp",
                "temp",
                "temp_confirmed",
                subject=STATE_KEY,
                severity="notice",
                **detail,
            )
        )
        return confirmation

    def latest(self) -> TemperatureConfirmation | None:
        with self._lock:
            return self._latest

    def stored(self) -> dict | None:
        """直接读回落盘内容，用于与内存视图对照。"""

        return self._store.read(STATE_KEY)

    def is_durable(self) -> bool:
        """确认已落盘：内存有记录且磁盘上确实存在对应文档。"""

        with self._lock:
            latest = self._latest
        return bool(latest is not None and self._store.exists(STATE_KEY) and self._store.digest(STATE_KEY))

    def require_durable(self) -> TemperatureConfirmation:
        """硫化计时在启动前必须确认模温已落盘。"""

        if not self.is_durable():
            raise InterlockError("模温确认尚未落盘，禁止启动硫化计时", component="temp")
        confirmation = self.latest()
        assert confirmation is not None  # is_durable 已保证非空
        return confirmation

    def restore(self) -> bool:
        """从磁盘恢复上次确认。"""

        payload = self._store.read(STATE_KEY)
        if not payload:
            return False
        try:
            confirmation = TemperatureConfirmation(
                mold_id=str(payload["mold_id"]),
                generation=int(payload["generation"]),
                setpoint_c=float(payload["setpoint_c"]),
                confirmed_c=float(payload["confirmed_c"]),
                confirmed_at=float(payload["confirmed_at"]),
                hold_s=float(payload["hold_s"]),
                digest=self._store.digest(STATE_KEY),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InterlockError(f"模温确认落盘内容不完整: {exc}", component="temp") from exc
        with self._lock:
            self._latest = confirmation
        self._bus.publish(
            Event.of(
                "temp",
                "temp",
                "temp_restored",
                subject=STATE_KEY,
                mold_id=confirmation.mold_id,
                generation=confirmation.generation,
            )
        )
        return True

    def snapshot(self) -> dict:
        with self._lock:
            latest = self._latest
        stored = self.stored()
        return {
            "durable": self.is_durable(),
            "latest": latest.as_dict() if latest else None,
            "stored_mold": stored.get("mold_id") if stored else None,
        }
