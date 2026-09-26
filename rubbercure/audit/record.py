"""审计记录。

每条记录都带组件、动作、对象与时间戳；硫化相关动作额外带测量值，便于事后
按时间段复盘"当时是按哪一份曲线基线和哪一代模具映射判的"。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from ..eventbus import Event

SEVERITY_ORDER = {"info": 0, "notice": 1, "warning": 2, "alarm": 3}


@dataclass(frozen=True)
class AuditRecord:
    """一条审计记录。"""

    record_id: str
    timestamp: float
    component: str
    action: str
    subject: str
    severity: str = "info"
    detail: dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        timestamp: float,
        component: str,
        action: str,
        subject: str,
        severity: str = "info",
        **detail: object,
    ) -> "AuditRecord":
        if severity not in SEVERITY_ORDER:
            severity = "info"
        return cls(
            record_id=str(uuid.uuid4()),
            timestamp=float(timestamp),
            component=component,
            action=action,
            subject=subject,
            severity=severity,
            detail=dict(detail),
        )

    @classmethod
    def from_event(cls, timestamp: float, event: Event) -> "AuditRecord":
        """把总线事件转成审计记录。"""

        payload = dict(event.payload)
        severity = str(payload.pop("severity", "info"))
        subject = str(payload.pop("subject", event.component))
        return cls.create(
            timestamp,
            event.component,
            event.action,
            subject,
            severity=severity,
            **payload,
        )

    def as_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "ts": round(self.timestamp, 3),
            "component": self.component,
            "action": self.action,
            "subject": self.subject,
            "severity": self.severity,
            "detail": self.detail,
        }

    def is_alarm(self) -> bool:
        return SEVERITY_ORDER.get(self.severity, 0) >= SEVERITY_ORDER["warning"]
