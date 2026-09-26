"""审计日志。

订阅总线的全量事件并落盘成 JSONL；同时保留内存尾部供控制台直接读取。
告警条目按严重度过滤，控制台 alarms 页面与停机复盘都从这里取数。
"""

from __future__ import annotations

import threading

from ..clock import Clock
from ..eventbus import Event, EventBus
from ..store.journal import JsonlJournal
from .record import SEVERITY_ORDER, AuditRecord

_MEMORY_LIMIT = 2000


class AuditLog:
    """事件总线的持久化订阅者。"""

    def __init__(self, clock: Clock, bus: EventBus, journal: JsonlJournal) -> None:
        self._clock = clock
        self._journal = journal
        self._records: list[AuditRecord] = []
        self._counters: dict[str, int] = {}
        self._lock = threading.RLock()
        bus.subscribe("*", self._on_event)

    def _on_event(self, event: Event) -> None:
        self.record(AuditRecord.from_event(self._clock.now(), event))

    def record(self, record: AuditRecord) -> AuditRecord:
        """写入一条审计记录（内存 + 磁盘）。"""

        with self._lock:
            self._records.append(record)
            if len(self._records) > _MEMORY_LIMIT:
                del self._records[: len(self._records) - _MEMORY_LIMIT]
            self._counters[record.severity] = self._counters.get(record.severity, 0) + 1
        self._journal.append(record.as_dict())
        return record

    def entries(self, limit: int = 100) -> list[dict]:
        """返回最近 ``limit`` 条记录，按时间正序。"""

        with self._lock:
            records = self._records[-limit:]
        return [record.as_dict() for record in records]

    def alarms(self) -> list[dict]:
        """返回内存中全部告警级别以上的记录。"""

        with self._lock:
            records = [record for record in self._records if record.is_alarm()]
        return [record.as_dict() for record in records]

    def summary(self) -> dict:
        """返回按严重度统计的计数与落盘段数。"""

        with self._lock:
            counters = dict(self._counters)
            total = len(self._records)
        return {
            "total": total,
            "by_severity": {
                name: counters.get(name, 0)
                for name in sorted(SEVERITY_ORDER, key=lambda item: SEVERITY_ORDER[item])
            },
            "segments": self._journal.segment_count(),
        }

    def tail(self, count: int = 20) -> list[dict]:
        """返回最近 ``count`` 条记录，按时间倒序，供控制台折叠展示。"""

        with self._lock:
            records = list(reversed(self._records[-count:]))
        return [record.as_dict() for record in records]

    def replay_from_journal(self, limit: int = 200) -> list[dict]:
        """从落盘日志回放整段历史（跨段拼接），供控制台对照内存视图。"""

        records = self._journal.read_all()
        return records[-limit:]

    def replay_segment(self, index: int) -> list[dict]:
        """读取指定落盘段。"""

        return self._journal.read_segment(index)
