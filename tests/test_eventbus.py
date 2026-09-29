"""事件总线：订阅者隔离、有界背压与错误状态。"""

from __future__ import annotations

import threading

import pytest

from rubbercure.eventbus import Event, EventBus


def test_one_failing_subscriber_does_not_stop_others():
    bus = EventBus()
    failed_started = threading.Event()
    release_failure = threading.Event()
    seen_by_other: list[str] = []

    def failing(_event: Event) -> None:
        failed_started.set()
        release_failure.wait(5.0)
        raise RuntimeError("audit disk unavailable")

    bus.subscribe("steam", failing, name="failing", queue_size=4)
    bus.subscribe(
        "steam", lambda event: seen_by_other.append(event.action), name="healthy"
    )

    bus.publish(Event.of("steam", "steam", "first"))
    assert failed_started.wait(1.0)
    bus.publish(Event.of("steam", "steam", "second"))

    assert bus.drain(timeout=0.1) is False
    assert seen_by_other == ["first", "second"]

    release_failure.set()
    assert bus.drain(timeout=5.0)
    status = bus.status()
    assert status["errors"] == 2
    failing_status = next(
        item
        for item in status["subscriber_status"]
        if item["name"] == "failing"
    )
    assert failing_status["last_error"].startswith("RuntimeError")
    bus.close()


def test_bounded_queue_does_not_block_publisher_and_reports_drops():
    bus = EventBus(default_queue_size=1, stall_after_s=0.01)
    handler_started = threading.Event()
    release_handler = threading.Event()
    handled: list[str] = []

    def slow(event: Event) -> None:
        handled.append(event.action)
        handler_started.set()
        release_handler.wait(5.0)

    bus.subscribe("cool", slow, name="slow-alarm", queue_size=1)

    bus.publish(Event.of("cool", "cool", "first"))
    assert handler_started.wait(1.0)
    bus.publish(Event.of("cool", "cool", "queued"))

    for index in range(3):
        subscribers = bus.publish(Event.of("cool", "cool", f"extra-{index}"))
        assert subscribers == 1

    status = bus.status()
    assert status["dropped"] == 3
    assert status["healthy"] is False
    slow_status = status["subscriber_status"][0]
    assert slow_status["state"] in {"stalled", "overflow"}
    assert slow_status["queue_size"] == 1

    release_handler.set()
    assert bus.close(timeout=5.0)
    assert handled == ["first", "queued"]


def test_subscriber_recovers_after_successful_event():
    bus = EventBus()

    def sometimes_fails(event: Event) -> None:
        if event.action == "bad":
            raise RuntimeError("temporary")

    bus.subscribe("temp", sometimes_fails, name="recovering")
    bus.publish(Event.of("temp", "temp", "bad"))
    bus.publish(Event.of("temp", "temp", "good"))

    assert bus.drain(timeout=5.0)
    status = bus.status()
    assert status["errors"] == 1
    assert status["healthy"] is True
    bus.close()


def test_close_processes_accepted_events():
    bus = EventBus()
    seen: list[str] = []
    release = threading.Event()

    def handler(event: Event) -> None:
        release.wait(5.0)
        seen.append(event.action)

    bus.subscribe("batch", handler, name="graceful-audit", queue_size=4)
    bus.publish(Event.of("batch", "batch", "first"))
    bus.publish(Event.of("batch", "batch", "second"))

    release.set()
    assert bus.close(timeout=5.0)
    assert seen == ["first", "second"]
    with pytest.raises(RuntimeError):
        bus.publish(Event.of("batch", "batch", "after-close"))


def test_unsubscribe_removes_handler():
    bus = EventBus()
    seen: list[str] = []
    unsubscribe = bus.subscribe("steam", lambda event: seen.append(event.action))

    bus.publish(Event.of("steam", "steam", "first"))
    assert bus.drain(timeout=5.0)
    unsubscribe()
    bus.publish(Event.of("steam", "steam", "second"))
    assert bus.drain(timeout=5.0)

    assert seen == ["first"]
    assert bus.history("steam")[1].action == "second"
    bus.close()


def test_subscribe_rejects_invalid_queue_size():
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.subscribe("steam", lambda _event: None, queue_size=0)
    bus.close()
    with pytest.raises(RuntimeError):
        bus.publish(Event.of("steam", "steam", "after-close"))
