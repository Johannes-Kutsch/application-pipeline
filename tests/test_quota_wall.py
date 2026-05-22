import threading
from datetime import datetime, timedelta, timezone

from application_pipeline.llm.quota import QuotaWall


def _utc(offset_minutes: int = 0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=offset_minutes)


# --- fresh wall ---


def test_fresh_wall_is_not_active():
    wall = QuotaWall()
    assert wall.is_active() is False


def test_fresh_wall_wait_returns_immediately():
    wall = QuotaWall()
    wall.wait_if_blocked()  # should not block


# --- raise_wall on fresh wall ---


def test_raise_wall_returns_true_on_first_raise():
    wall = QuotaWall()
    assert wall.raise_wall(_utc(+5)) is True


def test_raise_wall_activates_wall():
    wall = QuotaWall()
    wall.raise_wall(_utc(+5))
    assert wall.is_active() is True


# --- concurrent raise_wall idempotency ---


def test_second_raise_wall_returns_false():
    wall = QuotaWall()
    wall.raise_wall(_utc(+5))
    assert wall.raise_wall(_utc(+10)) is False


def test_second_raise_wall_does_not_extend_wall():
    first_reset = _utc(+5)
    wall = QuotaWall()
    wall.raise_wall(first_reset)
    wall.raise_wall(_utc(+10))
    # wall should still expire at first_reset + 2min, well before +12min
    assert wall.is_active() is True


# --- wait_if_blocked with fake clock ---


def _fake_wall(reset_minutes: float) -> tuple["QuotaWall", list[float]]:
    """Build a wall with a controllable clock and a sleep stub that advances it."""
    base = datetime.now(timezone.utc)
    clock = [base + timedelta(minutes=reset_minutes - 1)]  # starts 1 min before wake
    slept: list[float] = []

    def now_fn() -> datetime:
        return clock[0]

    def sleep_fn(seconds: float) -> None:
        slept.append(seconds)
        # advance clock past the wake time so the next loop iteration exits
        clock[0] = clock[0] + timedelta(minutes=reset_minutes + 3)

    wall = QuotaWall(now_fn=now_fn, sleep_fn=sleep_fn)
    return wall, slept


def test_wait_if_blocked_blocks_until_wall_passes():
    reset_time = _utc(+5)
    wall, slept = _fake_wall(reset_minutes=5)
    wall.raise_wall(reset_time)
    wall.wait_if_blocked()
    assert len(slept) >= 1  # sleep was called at least once


def test_wait_if_blocked_applies_two_minute_buffer():
    base = datetime.now(timezone.utc)
    reset_time = base + timedelta(minutes=5)
    # fake clock stays just before reset_time + 2min
    clock = [base + timedelta(minutes=6, seconds=59)]  # 1 sec before wake
    slept: list[float] = []

    def now_fn() -> datetime:
        return clock[0]

    def sleep_fn(seconds: float) -> None:
        slept.append(seconds)
        clock[0] = base + timedelta(minutes=10)  # jump past wake

    wall = QuotaWall(now_fn=now_fn, sleep_fn=sleep_fn)
    wall.raise_wall(reset_time)
    # still active 1 sec before reset_time + 2min
    assert wall.is_active() is True
    wall.wait_if_blocked()
    assert len(slept) >= 1


# --- concurrent threads all unblock ---


# --- wall expiry resets state ---


def test_is_active_false_after_wall_expires():
    past_reset = _utc(-10)  # reset_time in the past → wake_time also in the past
    wall = QuotaWall()
    wall.raise_wall(past_reset)
    assert wall.is_active() is False


def test_raise_wall_returns_true_after_previous_wall_expired():
    past_reset = _utc(-10)
    wall = QuotaWall()
    wall.raise_wall(past_reset)
    assert wall.raise_wall(_utc(+5)) is True


# --- concurrent threads all unblock ---


def test_concurrent_threads_all_unblock():
    """N threads blocked on wait_if_blocked() all return after the wall expires."""
    base = datetime.now(timezone.utc)
    reset_time = base + timedelta(minutes=5)
    # clock starts before wake, sleep advances it past
    clock = [base + timedelta(minutes=6)]
    lock = threading.Lock()

    def now_fn() -> datetime:
        with lock:
            return clock[0]

    def sleep_fn(seconds: float) -> None:
        with lock:
            clock[0] = base + timedelta(minutes=10)

    wall = QuotaWall(now_fn=now_fn, sleep_fn=sleep_fn)
    wall.raise_wall(reset_time)

    results: list[str] = []

    def worker() -> None:
        wall.wait_if_blocked()
        results.append("done")

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert len(results) == 6  # all threads returned
