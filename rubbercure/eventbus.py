"""进程内事件总线。

审计、趋势采样和告警分发订阅同一条总线；组件只发布事件，不直接依赖审计
包，避免组件之间互相导入形成环。每个订阅者拥有独立的有界队列和工作线程：
某个处理器阻塞或抛错时，只占住它自己的线程，不会反向卡住发布方。队列堆满
时丢弃新事件并在订阅者状态里累计，控制循环可通过健康检查发现这种背压。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

WILDCARD = "*"
_STOP = object()


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


@dataclass
class _QueuedEvent:
    event: Event
    queued_at: float


class _Subscription:
    """单个订阅者的有界队列、工作线程和错误状态。"""

    def __init__(
        self,
        subscriber_id: int,
        name: str,
        topic: str,
        handler: Callable[[Event], None],
        queue_size: int,
        stall_after_s: float,
    ) -> None:
        self.id = subscriber_id
        self.name = name
        self.topic = topic
        self.handler = handler
        self.queue_size = queue_size
        self.stall_after_s = stall_after_s
        self._items: deque[_QueuedEvent | object] = deque()
        self._condition = threading.Condition()
        self._active = True
        self._processing = False
        self._processing_started = 0.0
        self._accepted = 0
        self._processed = 0
        self._dropped = 0
        self._errors = 0
        self._consecutive_errors = 0
        self._last_error = ""
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def enqueue(self, event: Event) -> bool:
        """非阻塞投递；队列满或订阅已停时返回 ``False``。"""

        with self._condition:
            if not self._active or self._queue_locked() >= self.queue_size:
                self._dropped += 1
                return False
            self._items.append(_QueuedEvent(event, time.monotonic()))
            self._accepted += 1
            self._condition.notify()
            return True

    def drain(self, timeout: float | None = None) -> bool:
        """等待已接受的事件处理完成。"""

        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while self._active and (self._items or self._processing):
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return not self._items and not self._processing

    def unsubscribe(self) -> None:
        """停止订阅并丢弃尚未处理的事件，不等待处理器退出。"""

        self._stop(discard=True)

    def close(self, timeout: float | None = None) -> bool:
        """在已有事件后投递停止哨兵并等待工作线程退出。"""

        self._request_stop()
        thread_timeout = None if timeout is None else max(0.1, timeout + 0.1)
        self._thread.join(thread_timeout)
        return not self._thread.is_alive()

    def snapshot(self) -> dict:
        """返回可放入状态接口的订阅者健康快照。"""

        now = time.monotonic()
        with self._condition:
            queued = self._queue_locked()
            active = self._active
            processing = self._processing
            oldest_event = next(
                (item for item in self._items if item is not _STOP), None
            )
            oldest_age = (
                now - oldest_event.queued_at if oldest_event is not None else 0.0
            )
            processing_for = now - self._processing_started if processing else 0.0
            thread_alive = self._thread.is_alive()
            accepted = self._accepted
            processed = self._processed
            dropped = self._dropped
            errors = self._errors
            consecutive_errors = self._consecutive_errors
            last_error = self._last_error
        if not active:
            state = "stopped"
        elif not thread_alive:
            state = "dead"
        elif dropped:
            state = "overflow"
        elif consecutive_errors:
            state = "failing"
        elif queued and oldest_age >= self.stall_after_s:
            state = "backlogged"
        elif processing_for >= self.stall_after_s:
            state = "stalled"
        else:
            state = "healthy"
        return {
            "id": self.id,
            "name": self.name,
            "topic": self.topic,
            "state": state,
            "active": active,
            "thread_alive": thread_alive,
            "queue_size": self.queue_size,
            "queued": queued,
            "accepted": accepted,
            "processed": processed,
            "dropped": dropped,
            "errors": errors,
            "consecutive_errors": consecutive_errors,
            "last_error": last_error,
            "oldest_event_age_s": round(oldest_age, 3),
            "processing_for_s": round(processing_for, 3),
        }

    def _stop(self, discard: bool) -> None:
        with self._condition:
            if not self._active:
                return
            self._active = False
            if discard:
                self._items.clear()
            self._items.append(_STOP)
            self._condition.notify_all()

    def _request_stop(self) -> None:
        with self._condition:
            if not self._active:
                return
            self._items.append(_STOP)
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._active and not self._items:
                    self._condition.wait()
                item = self._items.popleft()
                if item is _STOP:
                    self._active = False
                    self._processing = False
                    self._condition.notify_all()
                    return
                if not self._active:
                    self._processing = False
                    self._condition.notify_all()
                    return
                self._processing = True
                self._processing_started = time.monotonic()
                event = item.event  # type: ignore[union-attr]
            try:
                self.handler(event)
            except Exception as error:  # 处理器异常只影响本订阅者
                with self._condition:
                    self._errors += 1
                    self._consecutive_errors += 1
                    self._last_error = f"{type(error).__name__}: {error}"
                logging.getLogger(__name__).warning(
                    "事件订阅者 %s 处理 %s/%s 失败: %s",
                    self.name,
                    event.topic,
                    event.action,
                    error,
                    exc_info=True,
                )
            else:
                with self._condition:
                    self._consecutive_errors = 0
            with self._condition:
                self._processed += 1
                self._processing = False
                self._condition.notify_all()

    def _queue_locked(self) -> int:
        return sum(1 for item in self._items if item is not _STOP)


class EventBus:
    """异步发布／订阅总线，保留历史事件供审计回放。"""

    def __init__(
        self,
        history_limit: int = 4096,
        default_queue_size: int = 1024,
        stall_after_s: float = 1.0,
    ) -> None:
        self._handlers: dict[str, list[_Subscription]] = defaultdict(list)
        self._history: list[Event] = []
        self._history_limit = history_limit
        self._default_queue_size = default_queue_size
        self._stall_after_s = stall_after_s
        self._lock = threading.RLock()
        self._next_subscriber_id = 1
        self._closed = False

    def subscribe(
        self,
        topic: str,
        handler: Callable[[Event], None],
        *,
        name: str | None = None,
        queue_size: int | None = None,
    ) -> Callable[[], None]:
        """订阅主题（``"*"`` 表示全量），返回退订回调。"""

        capacity = self._validate_queue_size(
            self._default_queue_size if queue_size is None else queue_size
        )
        with self._lock:
            if self._closed:
                raise RuntimeError("事件总线已关闭")
            subscriber_id = self._next_subscriber_id
            self._next_subscriber_id += 1
            default_name = getattr(handler, "__name__", None)
            subscriber_name = name or default_name or f"subscriber-{subscriber_id}"
            subscription = _Subscription(
                subscriber_id,
                subscriber_name,
                topic,
                handler,
                capacity,
                self._stall_after_s,
            )
            self._handlers[topic].append(subscription)
            subscription.start()

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(topic, [])
                if subscription in handlers:
                    handlers.remove(subscription)
            subscription.unsubscribe()

        return unsubscribe

    def publish(self, event: Event) -> int:
        """把事件非阻塞投递给订阅者，返回匹配的订阅者数量。"""

        with self._lock:
            if self._closed:
                raise RuntimeError("事件总线已关闭")
            subscriptions = list(self._handlers.get(event.topic, ())) + list(
                self._handlers.get(WILDCARD, ())
            )
            self._history.append(event)
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]
        for subscription in subscriptions:
            accepted = subscription.enqueue(event)
            if not accepted:
                logging.getLogger(__name__).warning(
                    "事件订阅者 %s 队列已满，丢弃 %s/%s 事件",
                    subscription.name,
                    event.topic,
                    event.action,
                )
        return len(subscriptions)

    def history(self, topic: str | None = None) -> list[Event]:
        """返回历史事件，可按主题过滤。"""

        with self._lock:
            events = list(self._history)
        if topic is None:
            return events
        return [event for event in events if event.topic == topic]

    def drain(self, timeout: float = 5.0) -> bool:
        """等待全部现存订阅者处理完已接受的事件。"""

        deadline = time.monotonic() + timeout
        with self._lock:
            subscriptions = self._subscriptions_locked()
        for subscription in subscriptions:
            remaining = deadline - time.monotonic()
            if not subscription.drain(max(0.0, remaining)):
                return False
        return True

    def close(self, timeout: float = 5.0) -> bool:
        """等待现存订阅者处理完队列并关闭总线。"""

        deadline = time.monotonic() + timeout
        with self._lock:
            if self._closed:
                return True
            subscriptions = self._subscriptions_locked()
            self._handlers.clear()
        all_stopped = True
        for subscription in subscriptions:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                subscription.unsubscribe()
                all_stopped = False
            elif not subscription.close(remaining):
                all_stopped = False
        with self._lock:
            self._closed = all_stopped
        return all_stopped

    def subscriber_status(self) -> list[dict]:
        """返回每个订阅者的队列、丢弃与错误状态。"""

        with self._lock:
            subscriptions = self._subscriptions_locked()
        return [subscription.snapshot() for subscription in subscriptions]

    def status(self) -> dict:
        """汇总总线背压与订阅者健康状态。"""

        subscribers = self.subscriber_status()
        return {
            "closed": self._closed,
            "subscribers": len(subscribers),
            "queued": sum(item["queued"] for item in subscribers),
            "accepted": sum(item["accepted"] for item in subscribers),
            "processed": sum(item["processed"] for item in subscribers),
            "dropped": sum(item["dropped"] for item in subscribers),
            "errors": sum(item["errors"] for item in subscribers),
            "healthy": all(item["state"] == "healthy" for item in subscribers),
            "subscriber_status": subscribers,
        }

    def _subscriptions_locked(self) -> list[_Subscription]:
        subscriptions: list[_Subscription] = []
        for handlers in self._handlers.values():
            subscriptions.extend(handlers)
        return sorted(subscriptions, key=lambda item: item.id)

    @staticmethod
    def _validate_queue_size(queue_size: int) -> int:
        if queue_size < 1:
            raise ValueError("订阅队列长度必须为正")
        return queue_size
