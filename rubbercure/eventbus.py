"""进程内事件总线。

审计、趋势采样和告警分发订阅同一条总线；组件只发布事件，不直接依赖审计
包，避免组件之间互相导入形成环。控制循环会在持有模具锁或阀位锁的情况下
发布事件，因此总线本身必须可重入安全。
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

WILDCARD = "*"


@dataclass(frozen=True)
class Event:
    """一条组件事件。"""

    topic: str
    component: str
    action: str
    payload: dict = field(default_factory=dict)

    @classmethod
    def of(cls, topic: str, component: str, action: str, **payload: object) -> "Event":
        return cls(topic=topic, component=component, action=action, payload=dict(payload))


class EventBus:
    """极简发布／订阅总线，保留历史事件供审计回放。"""

    def __init__(self, history_limit: int = 4096) -> None:
        self._handlers: dict[str, list[Callable[[Event], None]]] = defaultdict(list)
        self._history: list[Event] = []
        self._history_limit = history_limit
        self._lock = threading.RLock()

    def subscribe(self, topic: str, handler: Callable[[Event], None]) -> Callable[[], None]:
        """订阅主题（``"*"`` 表示全量），返回退订回调。"""

        with self._lock:
            self._handlers[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers.get(topic, []):
                    self._handlers[topic].remove(handler)

        return unsubscribe

    def publish(self, event: Event) -> int:
        """把事件投递给订阅者，返回被调用的处理器数量。"""

        with self._lock:
            handlers = list(self._handlers.get(event.topic, ())) + list(
                self._handlers.get(WILDCARD, ())
            )
            self._history.append(event)
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]
        for handler in handlers:
            handler(event)
        return len(handlers)

    def history(self, topic: str | None = None) -> list[Event]:
        """返回历史事件，可按主题过滤。"""

        with self._lock:
            events = list(self._history)
        if topic is None:
            return events
        return [event for event in events if event.topic == topic]
