"""事件总线：订阅者故障隔离、有界背压与健康上报。

场景对应现场真实风险：审计落盘卡住、告警处理器抛错，都不能让发布事件的
控制循环跟着停；慢订阅者堆积必须被挡住并产生可观测的丢弃告警。
"""

from __future__ import annotations

import threading
import time

import pytest

from rubbercure.eventbus import DROP_NEWEST, DROP_OLDEST, Event, EventBus


def _event(index: int = 0) -> Event:
    return Event.of("t", "comp", f"act-{index}", seq=index)


def test_handler_error_does_not_block_publisher_or_other_subscribers():
    bus = EventBus(queue_size=8, enqueue_timeout_s=0.02)
    good: list[int] = []

    def boom(event: Event) -> None:
        raise RuntimeError("订阅者炸了")

    errors: list[dict] = []
    bus.on_handler_error(errors.append)
    unsub_bad = bus.subscribe("t", boom, name="boom")
    unsub_good = bus.subscribe("t", lambda event: good.append(event.payload["seq"]), name="good")

    for index in range(10):
        bus.publish(_event(index))

    assert bus.drain(timeout_s=2.0)
    assert good == list(range(10))
    health = {item["name"]: item for item in bus.health()}
    assert health["boom"]["errors"] == 10
    assert "RuntimeError" in health["boom"]["last_error"]
    assert health["good"]["errors"] == 0
    assert errors and errors[0]["subscriber"] == "boom"
    assert errors[0]["event"] == "comp.act-0"
    unsub_bad()
    unsub_good()


def test_stuck_subscriber_is_bounded_and_reports_overflow_without_blocking():
    bus = EventBus(queue_size=4, enqueue_timeout_s=0.0)
    release = threading.Event()
    received = threading.Event()
    overflow: list[dict] = []
    bus.on_subscriber_overflow(overflow.append)

    def slow(event: Event) -> None:
        received.set()
        release.wait(timeout=5.0)

    unsub = bus.subscribe("t", slow, name="slow")

    # 第一条被工作线程取走并卡在处理器里，后续只能堆满 4 格然后丢弃。
    bus.publish(_event(0))
    received.wait(timeout=2.0)
    start = time.monotonic()
    for index in range(1, 40):
        bus.publish(_event(index))
    elapsed = time.monotonic() - start
    # 发布方绝不被慢订阅者拖死（无入队等待，39 条应几乎瞬时返回）。
    assert elapsed < 1.0

    health = {item["name"]: item for item in bus.health()}["slow"]
    assert health["queued"] == 4
    assert health["dropped"] == 35
    assert health["stalled"] is True
    assert overflow, "堆积丢弃必须产生溢出上报"
    assert overflow[0]["subscriber"] == "slow"
    assert overflow[0]["dropped_total"] >= 1
    assert overflow[0]["policy"] == DROP_OLDEST

    release.set()
    assert bus.drain(timeout_s=2.0)
    unsub()


def test_drop_oldest_keeps_latest_events():
    bus = EventBus(queue_size=4, enqueue_timeout_s=0.0)
    release = threading.Event()
    started = threading.Event()
    seen: list[int] = []

    def worker(event: Event) -> None:
        # 只卡第一条，让队列堆满；放行后全部快速处理。
        if not started.is_set():
            started.set()
            release.wait(timeout=5.0)
        seen.append(event.payload["seq"])

    unsub = bus.subscribe("t", worker, name="w", overflow_policy=DROP_OLDEST)
    bus.publish(_event(0))
    started.wait(timeout=2.0)
    for index in range(1, 10):
        bus.publish(_event(index))
    release.set()
    assert bus.drain(timeout_s=2.0)
    # 0 被处理器取走在跑；队列保下最后 4 条，中间的被丢掉。
    assert seen[0] == 0
    assert seen[-4:] == [6, 7, 8, 9]
    unsub()


def test_drop_newest_keeps_oldest_events():
    bus = EventBus(queue_size=4, enqueue_timeout_s=0.0)
    release = threading.Event()
    started = threading.Event()
    seen: list[int] = []

    def worker(event: Event) -> None:
        if not started.is_set():
            started.set()
            release.wait(timeout=5.0)
        seen.append(event.payload["seq"])

    unsub = bus.subscribe("t", worker, name="w", overflow_policy=DROP_NEWEST)
    bus.publish(_event(0))
    started.wait(timeout=2.0)
    for index in range(1, 10):
        bus.publish(_event(index))
    release.set()
    assert bus.drain(timeout_s=2.0)
    # 0 在处理；队里保 [1,2,3,4]，新来的全丢。
    assert seen[:5] == [0, 1, 2, 3, 4]
    unsub()


def test_synchronous_subscription_still_isolates_errors():
    bus = EventBus()
    good: list[int] = []
    errors: list[dict] = []
    bus.on_handler_error(errors.append)

    bus.subscribe("t", lambda event: (_ for _ in ()).throw(RuntimeError("x")), queue_size=0)
    bus.subscribe("t", lambda event: good.append(event.payload["seq"]), queue_size=0)

    bus.publish(_event(7))  # 第一个处理器抛错不得影响第二个与发布方
    assert good == [7]
    assert errors and errors[0]["error"].startswith("RuntimeError")


def test_enqueue_timeout_bounds_publisher_wait():
    bus = EventBus(queue_size=2, enqueue_timeout_s=0.02)
    release = threading.Event()
    received = threading.Event()

    def slow(event: Event) -> None:
        received.set()
        release.wait(timeout=5.0)

    unsub = bus.subscribe("t", slow, name="slow")
    bus.publish(_event(0))
    received.wait(timeout=2.0)
    start = time.monotonic()
    for index in range(1, 8):
        bus.publish(_event(index))
    elapsed = time.monotonic() - start
    # 每条最多等 20ms，7 条总等待必须远小于无界阻塞。
    assert elapsed < 0.5
    release.set()
    unsub()


def test_wildcard_and_topic_subscribers_both_receive():
    bus = EventBus()
    topic_seen: list[str] = []
    wildcard_seen: list[str] = []
    unsub_topic = bus.subscribe("t", lambda event: topic_seen.append(event.action), queue_size=0)
    unsub_all = bus.subscribe("*", lambda event: wildcard_seen.append(event.action), queue_size=0)

    assert bus.publish(_event(0)) == 2
    assert topic_seen == ["act-0"]
    assert wildcard_seen == ["act-0"]
    unsub_topic()
    unsub_all()


def test_invalid_construction_arguments_are_rejected():
    with pytest.raises(ValueError):
        EventBus(queue_size=-1)
    with pytest.raises(ValueError):
        EventBus(enqueue_timeout_s=-1.0)
    with pytest.raises(ValueError):
        EventBus(overflow_policy="bogus")
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.subscribe("t", lambda event: None, queue_size=-1)
