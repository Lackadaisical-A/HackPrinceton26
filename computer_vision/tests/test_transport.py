import time

import pytest

from paul_vision.config import WatchdogConfig
from paul_vision.messages import build_vision_message, validate_vision
from paul_vision.summary import TEXT_DEGRADED
from paul_vision.transport import Subscriber, VisionPublisher, Watchdog

CFG = WatchdogConfig(stall_timeout_s=0.5, heartbeat_s=0.5)
FAST = WatchdogConfig(stall_timeout_s=0.15, heartbeat_s=0.1)
ANY_PORT = "tcp://127.0.0.1:0"


def message(frame_id: int) -> dict:
    return build_vision_message(t=time.time(), frame_id=frame_id, status="no_target", phase="search")


def collect(sub: Subscriber, seconds: float) -> list[dict]:
    out, deadline = [], time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        received = sub.recv(left)
        if received:
            out.append(received[1])
    return out


def wait_until_connected(pub: VisionPublisher, sub: Subscriber) -> None:
    """PUB/SUB drops messages until the subscription has reached the publisher."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        pub.publish(message(0))
        if sub.recv(0.05):
            while sub.recv(0.05):  # drain
                pass
            return
    pytest.fail("subscriber never connected")


# --- watchdog (pure) ---

def test_watchdog_is_quiet_while_messages_flow():
    dog = Watchdog(CFG, start_t=0.0)
    for i in range(1, 100):
        dog.feed(i * 0.03)
        assert dog.poll(i * 0.03 + 0.02) == 0
    assert not dog.stalled


def test_watchdog_fires_after_the_stall_timeout_then_at_the_heartbeat_period():
    dog = Watchdog(CFG, start_t=0.0)
    dog.feed(1.0)
    assert dog.poll(1.49) == 0
    assert dog.poll(1.50) == 1   # first heartbeat of the stall
    assert dog.stalled
    assert dog.poll(1.90) == 0
    assert dog.poll(2.00) == 2
    assert dog.poll(2.50) == 3


def test_watchdog_fires_if_nothing_is_ever_published():
    dog = Watchdog(CFG, start_t=10.0)
    assert dog.poll(10.4) == 0
    assert dog.poll(10.5) == 1


def test_watchdog_recovers_and_can_stall_again():
    dog = Watchdog(CFG, start_t=0.0)
    assert dog.poll(0.6) == 1
    dog.feed(0.7)
    assert not dog.stalled
    assert dog.poll(1.1) == 0
    assert dog.poll(1.2) == 1   # numbering restarts for the new stall


def test_watchdog_time_to_next():
    dog = Watchdog(CFG, start_t=0.0)
    assert dog.time_to_next(0.2) == pytest.approx(0.3)
    assert dog.time_to_next(0.9) == 0.0
    dog.poll(0.9)
    assert dog.time_to_next(1.0) == pytest.approx(0.4)


# --- publisher and subscriber over real sockets ---

def test_published_messages_arrive_in_order():
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=CFG) as pub, Subscriber(pub.endpoint) as sub:
        assert not pub.endpoint.endswith(":0")  # the real port is reported
        wait_until_connected(pub, sub)
        for i in range(1, 11):
            pub.publish(message(i))
        received = collect(sub, 0.3)
    assert [m["frame_id"] for m in received] == list(range(1, 11))


def test_subscriber_filters_by_topic():
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=CFG) as pub, \
            Subscriber(pub.endpoint) as vision, Subscriber(pub.endpoint, topic="pressure") as other:
        wait_until_connected(pub, vision)
        pub.publish(message(1))
        assert vision.recv(1.0) is not None
        assert other.recv(0.2) is None


def test_degraded_heartbeats_when_publishing_stops_and_recovery_after():
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=FAST) as pub, Subscriber(pub.endpoint) as sub:
        wait_until_connected(pub, sub)
        pub.publish(message(41))
        stalled = collect(sub, 0.6)
        pub.publish(message(42))
        recovered = collect(sub, 0.1)

    assert stalled[0]["frame_id"] == 41 and stalled[0]["status"] == "no_target"
    heartbeats = stalled[1:]
    assert len(heartbeats) >= 3
    for beat in heartbeats:
        validate_vision(beat)
        assert beat["status"] == "degraded" and beat["target"] is None and beat["guidance"] is None
        assert beat["frame_id"] == 41  # the last frame that was published
    assert [b["summary"] for b in heartbeats] == [TEXT_DEGRADED] + [None] * (len(heartbeats) - 1)
    assert heartbeats[-1]["t"] > heartbeats[0]["t"]
    # A last heartbeat may still be in flight when the collection window closes, so only fresh messages count.
    assert [m["frame_id"] for m in recovered if m["status"] != "degraded"] == [42]
    assert recovered[-1]["frame_id"] == 42


def test_heartbeats_carry_the_last_published_phase_and_no_alert():
    # A camera stall during a carry is not the end of the carry, and the alert belongs to the frame that saw a drop.
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=FAST) as pub, Subscriber(pub.endpoint) as sub:
        wait_until_connected(pub, sub)
        pub.publish(build_vision_message(t=time.time(), frame_id=7, status="no_target", phase="carry",
                                         summary="Grip lost. Object may have dropped.", alert="dropped"))
        stalled = collect(sub, 0.45)
    heartbeats = [m for m in stalled if m["status"] == "degraded"]
    assert len(heartbeats) >= 2
    for beat in heartbeats:
        validate_vision(beat)
        assert beat["phase"] == "carry" and beat["alert"] is None and beat["frame_id"] == 7


def test_heartbeats_before_any_message_say_search():
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=FAST) as pub, Subscriber(pub.endpoint) as sub:
        beats = collect(sub, 0.6)
    assert beats, "no heartbeat arrived"
    assert all(b["status"] == "degraded" and b["phase"] == "search" and b["alert"] is None for b in beats)


def test_one_subscriber_can_listen_to_several_publishers():
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=CFG) as a, \
            VisionPublisher(ANY_PORT, send_hwm=20, watchdog=CFG) as b, \
            Subscriber([a.endpoint, b.endpoint]) as sub:
        assert sub.endpoints == [a.endpoint, b.endpoint]
        for pub in (a, b):
            wait_until_connected(pub, sub)
        a.publish(message(1))
        b.publish(message(2))
        received = collect(sub, 0.3)
    assert sorted(m["frame_id"] for m in received if m["status"] != "degraded") == [1, 2]


def test_a_subscriber_needs_an_endpoint():
    with pytest.raises(ValueError, match="at least one endpoint"):
        Subscriber([])


def test_conflate_keeps_only_the_newest_message():
    with VisionPublisher(ANY_PORT, send_hwm=100, watchdog=CFG) as pub, \
            Subscriber(pub.endpoint, conflate=True) as sub:
        wait_until_connected(pub, sub)
        for i in range(1, 51):
            pub.publish(message(i))
        time.sleep(0.3)  # let all 50 arrive while the consumer is "busy"
        newest = sub.recv(1.0)
        assert newest[1]["frame_id"] == 50
        assert sub.recv(0.1) is None


def test_binding_a_port_twice_fails_clearly():
    with VisionPublisher(ANY_PORT, send_hwm=20, watchdog=CFG) as pub:
        with pytest.raises(RuntimeError, match="could not bind"):
            VisionPublisher(pub.endpoint, send_hwm=20, watchdog=CFG).start()


def test_publish_never_blocks_when_the_queue_is_full():
    pub = VisionPublisher(ANY_PORT, send_hwm=5, watchdog=CFG).start()
    try:
        started = time.monotonic()
        for i in range(10_000):
            pub.publish(message(i))
        assert time.monotonic() - started < 2.0
    finally:
        pub.close()
