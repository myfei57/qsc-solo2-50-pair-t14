"""进程内事件总线。

审计、趋势采样和告警分发订阅同一条总线；组件只发布事件，不直接依赖审计
包，避免组件之间互相导入形成环。控制循环会在持有模具锁或阀位锁的情况下
发布事件，因此总线本身必须可重入安全。

订阅者之间必须互相隔离：审计落盘慢、告警处理器抛错，都不能把发布事件的
控制循环拖住。因此每个订阅者拥有一条独立的有界队列和一个守护工作线程，
发布方只负责入队：

* 处理器抛错只记录在它自己的订阅状态上，并通过 ``on_handler_error`` 钩子
  上报，不会影响其他订阅者或发布方；
* 队列堆满时先做一次很短的限时等待，仍然放不下就按策略丢弃
  （默认丢最旧事件、保留最新事件），丢弃计入状态并通过
  ``on_subscriber_overflow`` 钩子上报，避免内存被慢订阅者拖垮；
* 确实需要与发布同线程同步执行的订阅（例如测试断言），可用
  ``queue_size=0`` 退回直调模式，异常同样被隔离。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

WILDCARD = "*"

DROP_OLDEST = "drop_oldest"
DROP_NEWEST = "drop_newest"
_VALID_POLICIES = (DROP_OLDEST, DROP_NEWEST)

DEFAULT_QUEUE_SIZE = 256
DEFAULT_ENQUEUE_TIMEOUT_S = 0.005
# 溢出上报本身限频，避免慢订阅者刷爆审计日志。
_OVERFLOW_REPORT_EVERY = 32

Handler = Callable[["Event"], None]
ProblemHook = Callable[[dict], None]


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
class _Subscription:
    """一个订阅者的邮箱、工作线程与运行状态。"""

    name: str
    topic: str
    handler: Handler
    queue_size: int
    enqueue_timeout_s: float
    policy: str
    synchronous: bool
    queue: deque[Event]
    lock: threading.Lock = field(default_factory=threading.Lock)
    not_empty: threading.Condition = field(init=False)
    not_full: threading.Condition = field(init=False)
    idle: threading.Condition = field(init=False)
    thread: threading.Thread | None = None
    active: bool = True
    offered: int = 0
    processed: int = 0
    delivered: int = 0
    dropped: int = 0
    errors: int = 0
    last_error: str = ""
    overflow_report_at: int = 0

    def __post_init__(self) -> None:
        self.not_empty = threading.Condition(self.lock)
        self.not_full = threading.Condition(self.lock)
        self.idle = threading.Condition(self.lock)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "name": self.name,
                "topic": self.topic,
                "mode": "sync" if self.synchronous else "async",
                "queue_size": 0 if self.synchronous else self.queue_size,
                "queued": len(self.queue),
                "inflight": self.offered - self.processed,
                "delivered": self.delivered,
                "dropped": self.dropped,
                "errors": self.errors,
                "last_error": self.last_error,
                "active": self.active,
                "stalled": not self.synchronous
                and self.active
                and len(self.queue) >= self.queue_size,
            }


class EventBus:
    """按订阅者隔离的发布／订阅总线，保留历史事件供审计回放。"""

    def __init__(
        self,
        history_limit: int = 4096,
        queue_size: int = DEFAULT_QUEUE_SIZE,
        enqueue_timeout_s: float = DEFAULT_ENQUEUE_TIMEOUT_S,
        overflow_policy: str = DROP_OLDEST,
    ) -> None:
        if queue_size < 0:
            raise ValueError("队列容量不能为负")
        if enqueue_timeout_s < 0:
            raise ValueError("入队等待时间不能为负")
        if overflow_policy not in _VALID_POLICIES:
            raise ValueError(f"未知溢出策略: {overflow_policy}")
        self._handlers: dict[str, list[_Subscription]] = defaultdict(list)
        self._history: list[Event] = []
        self._history_limit = history_limit
        self._default_queue_size = queue_size
        self._default_timeout = enqueue_timeout_s
        self._default_policy = overflow_policy
        self._lock = threading.RLock()
        self._error_hooks: list[ProblemHook] = []
        self._overflow_hooks: list[ProblemHook] = []
        self._closed = False

    def subscribe(
        self,
        topic: str,
        handler: Handler,
        *,
        name: str | None = None,
        queue_size: int | None = None,
        enqueue_timeout_s: float | None = None,
        overflow_policy: str | None = None,
    ) -> Callable[[], None]:
        """订阅主题（``"*"`` 表示全量），返回退订回调。

        ``queue_size=0`` 时该订阅在发布线程上同步执行；否则事件进入该订阅
        私有的有界队列，由专用工作线程顺序处理。
        """

        size = self._default_queue_size if queue_size is None else int(queue_size)
        if size < 0:
            raise ValueError("队列容量不能为负")
        timeout = self._default_timeout if enqueue_timeout_s is None else float(enqueue_timeout_s)
        policy = self._default_policy if overflow_policy is None else overflow_policy
        if policy not in _VALID_POLICIES:
            raise ValueError(f"未知溢出策略: {policy}")
        subscription = _Subscription(
            name=name or getattr(handler, "__name__", handler.__class__.__name__),
            topic=topic,
            handler=handler,
            queue_size=size,
            enqueue_timeout_s=timeout,
            policy=policy,
            synchronous=size == 0,
            queue=deque(),
        )
        if not subscription.synchronous:
            worker = threading.Thread(
                target=self._run_worker,
                args=(subscription,),
                name=f"eventbus-{subscription.name}",
                daemon=True,
            )
            subscription.thread = worker
        with self._lock:
            if self._closed:
                raise RuntimeError("总线已关闭，不能再订阅")
            self._handlers[topic].append(subscription)
        if subscription.thread is not None:
            subscription.thread.start()

        def unsubscribe() -> None:
            self._remove(subscription)

        return unsubscribe

    def on_handler_error(self, hook: ProblemHook) -> Callable[[], None]:
        """登记处理器异常钩子，返回注销回调。"""

        with self._lock:
            self._error_hooks.append(hook)

        def off() -> None:
            with self._lock:
                if hook in self._error_hooks:
                    self._error_hooks.remove(hook)

        return off

    def on_subscriber_overflow(self, hook: ProblemHook) -> Callable[[], None]:
        """登记订阅者溢出（发生丢弃）钩子，返回注销回调。"""

        with self._lock:
            self._overflow_hooks.append(hook)

        def off() -> None:
            with self._lock:
                if hook in self._overflow_hooks:
                    self._overflow_hooks.remove(hook)

        return off

    def publish(self, event: Event) -> int:
        """把事件投递给订阅者，返回匹配到的订阅者数量。

        投递过程中的异常、阻塞和溢出都被限制在单个订阅者范围内，本方法不
        会因订阅者问题抛出异常（总线已关闭时仅继续保留历史）。
        """

        with self._lock:
            subscriptions = list(self._handlers.get(event.topic, ())) + list(
                self._handlers.get(WILDCARD, ())
            )
            self._history.append(event)
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]
        for subscription in subscriptions:
            if subscription.synchronous:
                self._deliver_sync(subscription, event)
            else:
                self._enqueue(subscription, event)
        return len(subscriptions)

    def drain(self, timeout_s: float = 1.0) -> bool:
        """等待所有异步订阅者把已入队的事件处理完，返回是否全部排空。"""

        deadline = time.monotonic() + max(0.0, float(timeout_s))
        with self._lock:
            subscriptions = [
                subscription
                for topic_subs in self._handlers.values()
                for subscription in topic_subs
                if not subscription.synchronous
            ]
        for subscription in subscriptions:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            with subscription.lock:
                while (
                    subscription.queue or subscription.offered > subscription.processed
                ) and subscription.active:
                    if not subscription.idle.wait(timeout=max(0.0, deadline - time.monotonic())):
                        break
                    if time.monotonic() >= deadline:
                        break
        with self._lock:
            left = sum(
                len(subscription.queue) + (subscription.offered - subscription.processed)
                for subscription in subscriptions
            )
        return left == 0

    def close(self, timeout_s: float = 2.0) -> None:
        """关闭总线：停止接受新订阅，排空在途事件后退出工作线程。"""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            subscriptions = [
                subscription
                for topic_subs in self._handlers.values()
                for subscription in topic_subs
            ]
        # 先让在途事件处理完（handlers 仍在，drain 才能等到它们）。
        self.drain(timeout_s)
        with self._lock:
            self._handlers.clear()
        for subscription in subscriptions:
            if subscription.thread is None:
                continue
            with subscription.lock:
                subscription.active = False
                subscription.not_empty.notify_all()
                subscription.not_full.notify_all()
            subscription.thread.join(timeout=0.5)

    def health(self) -> list[dict]:
        """返回每个订阅者的投递健康状况（队列深度、丢弃数、错误数等）。"""

        with self._lock:
            subscriptions = [
                subscription
                for topic_subs in self._handlers.values()
                for subscription in topic_subs
            ]
        return [subscription.snapshot() for subscription in subscriptions]

    def subscriber_health(self) -> dict:
        """聚合成平台状态用的摘要，含是否存在报错或堆积的订阅者。"""

        items = self.health()
        return {
            "subscribers": items,
            "unhealthy": [
                item["name"]
                for item in items
                if item["errors"] or item["dropped"] or item["stalled"]
            ],
        }

    def history(self, topic: str | None = None) -> list[Event]:
        """返回历史事件，可按主题过滤。"""

        with self._lock:
            events = list(self._history)
        if topic is None:
            return events
        return [event for event in events if event.topic == topic]

    # ------------------------------------------------------------------

    def _remove(self, subscription: _Subscription) -> None:
        with self._lock:
            subs = self._handlers.get(subscription.topic)
            if not subs or subscription not in subs:
                return
            subs.remove(subscription)
        if subscription.thread is None:
            return
        with subscription.lock:
            subscription.active = False
            subscription.not_empty.notify_all()
            subscription.not_full.notify_all()
        # 卡住的处理器没法安全强杀（Python 没有线程取消原语），守护线程会
        # 在进程退出时回收；退订只保证新事件不再投递给它。
        subscription.thread.join(timeout=0.5)

    def _run_worker(self, subscription: _Subscription) -> None:
        while True:
            with subscription.lock:
                while subscription.active and not subscription.queue:
                    subscription.not_empty.wait()
                if not subscription.active and not subscription.queue:
                    return
                event = subscription.queue.popleft()
                # 腾出一个槽位，唤醒在队列满时等待入队的发布者。
                subscription.not_full.notify()
            self._invoke(subscription, event)
            with subscription.lock:
                subscription.delivered += 1
                subscription.processed += 1
                if not subscription.queue and subscription.offered == subscription.processed:
                    subscription.idle.notify_all()

    def _deliver_sync(self, subscription: _Subscription, event: Event) -> None:
        with subscription.lock:
            if not subscription.active:
                return
            subscription.offered += 1
        try:
            self._invoke(subscription, event)
        finally:
            with subscription.lock:
                subscription.delivered += 1
                subscription.processed += 1
                if not subscription.queue and subscription.offered == subscription.processed:
                    subscription.idle.notify_all()

    def _enqueue(self, subscription: _Subscription, event: Event) -> None:
        with subscription.lock:
            if not subscription.active:
                return
            if len(subscription.queue) < subscription.queue_size:
                subscription.queue.append(event)
                subscription.offered += 1
                subscription.not_empty.notify()
                return
        # 队列已满：做一次有界等待，给慢订阅者一点追赶时间，但绝不死等。
        if subscription.enqueue_timeout_s:
            with subscription.lock:
                if subscription.active and len(subscription.queue) >= subscription.queue_size:
                    subscription.not_full.wait(timeout=subscription.enqueue_timeout_s)
        dropped_event, due = self._overflow_locked(subscription, event)
        if due:
            self._report_overflow(subscription, dropped_event)

    def _overflow_locked(
        self, subscription: _Subscription, event: Event
    ) -> tuple[Event | None, bool]:
        """队列仍满时执行丢弃策略，返回被丢弃事件与本次是否该上报。"""

        with subscription.lock:
            if not subscription.active:
                return None, False
            if len(subscription.queue) < subscription.queue_size:
                subscription.queue.append(event)
                subscription.offered += 1
                subscription.not_empty.notify()
                return None, False
            subscription.dropped += 1
            if subscription.policy == DROP_NEWEST:
                # 保旧弃新：新事件自己被丢，不进 offered，worker 无需处理。
                dropped_event: Event | None = event
            else:
                # 保新弃旧：挤出队首（它已经 offered 过且会由 worker 结掉），
                # 新事件顶替其槽位；offered 不变，processed 随 worker 推进。
                dropped_event = subscription.queue.popleft()
                subscription.queue.append(event)
                subscription.not_empty.notify()
            due = (
                subscription.overflow_report_at == 0
                or subscription.dropped - subscription.overflow_report_at
                >= _OVERFLOW_REPORT_EVERY
            )
            if due:
                subscription.overflow_report_at = subscription.dropped
            if not subscription.queue and subscription.offered == subscription.processed:
                subscription.idle.notify_all()
            return dropped_event, due

    def _invoke(self, subscription: _Subscription, event: Event) -> None:
        try:
            subscription.handler(event)
        except Exception as error:  # 订阅者故障隔离在它自己的线程里
            message = f"{type(error).__name__}: {error}"
            with subscription.lock:
                subscription.errors += 1
                subscription.last_error = message
            self._notify(
                self._error_hooks,
                {
                    "subscriber": subscription.name,
                    "topic": subscription.topic,
                    "event": f"{event.component}.{event.action}",
                    "error": message,
                },
            )

    def _report_overflow(self, subscription: _Subscription, dropped_event: Event | None) -> None:
        detail = {
            "subscriber": subscription.name,
            "topic": subscription.topic,
            "queue_size": subscription.queue_size,
            "dropped_total": subscription.dropped,
            "policy": subscription.policy,
        }
        if dropped_event is not None:
            detail["dropped_event"] = f"{dropped_event.component}.{dropped_event.action}"
        self._notify(self._overflow_hooks, detail)

    @staticmethod
    def _notify(hooks: list[ProblemHook], detail: dict) -> None:
        for hook in tuple(hooks):
            try:
                hook(detail)
            except Exception:
                # 上报钩子本身故障不能反向影响总线。
                pass
