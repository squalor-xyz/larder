# SPDX-License-Identifier: AGPL-3.0-only
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from pathlib import Path

from larder.scheduler import Scheduler

THREAD_NAME = "larder-scheduler"


class FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def scheduler_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == THREAD_NAME]


class SchedulerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        self.clock = FakeClock()

    def make(self, clock: Callable[[], float] | None = None) -> Scheduler:
        scheduler = Scheduler(self.data_dir, clock=clock or self.clock)
        self.addCleanup(scheduler.stop)
        return scheduler


class RunDueTest(SchedulerTestCase):
    def test_explicit_first_run_delay(self) -> None:
        scheduler = self.make()
        output = self.data_dir / "output"
        scheduler.register(
            "immediate", 10, lambda: output.write_text("ran"), first_run_delay=0
        )
        scheduler.run_due()
        self.assertEqual(output.read_text(), "ran")
        output.unlink()
        self.clock.now = 9.9
        scheduler.run_due()
        self.assertFalse(output.exists())
        self.clock.now = 10
        scheduler.run_due()
        self.assertEqual(output.read_text(), "ran")

    def test_rejects_negative_first_run_delay(self) -> None:
        with self.assertRaises(ValueError):
            self.make().register("invalid", 10, lambda: None, first_run_delay=-1)

    def test_job_runs_when_its_interval_has_elapsed_and_not_before(self) -> None:
        calls: list[float] = []
        scheduler = self.make()
        scheduler.register("job", 10, lambda: calls.append(self.clock.now))

        # The first run is registration time plus one interval, not registration.
        scheduler.run_due()
        self.assertEqual(calls, [])
        self.clock.now = 9.9
        scheduler.run_due()
        self.assertEqual(calls, [])

        self.clock.now = 10.0
        scheduler.run_due()
        self.assertEqual(calls, [10.0])

    def test_due_jobs_run_one_at_a_time_in_registration_order(self) -> None:
        events: list[str] = []
        scheduler = self.make()

        def job(name: str) -> Callable[[], None]:
            def run() -> None:
                events.append(f"{name} start")
                events.append(f"{name} end")

            return run

        # The longer interval is registered first; order is registration, not
        # interval.
        scheduler.register("first", 20, job("first"))
        scheduler.register("second", 5, job("second"))
        scheduler.register("third", 10, job("third"))
        self.clock.now = 30.0

        scheduler.run_due()

        self.assertEqual(
            events,
            [
                "first start",
                "first end",
                "second start",
                "second end",
                "third start",
                "third end",
            ],
        )

    def test_next_run_is_one_interval_after_the_job_finishes(self) -> None:
        calls: list[float] = []
        scheduler = self.make()

        def slow() -> None:
            calls.append(self.clock.now)
            self.clock.now += 3.0  # the job takes 3 seconds

        scheduler.register("slow", 10, slow)
        self.clock.now = 10.0
        scheduler.run_due()
        self.assertEqual(calls, [10.0])

        # It started at 10 and finished at 13, so the next run is at 23.
        self.clock.now = 22.9
        scheduler.run_due()
        self.assertEqual(calls, [10.0])
        self.clock.now = 23.0
        scheduler.run_due()
        self.assertEqual(calls, [10.0, 23.0])

    def test_failing_job_is_logged_and_does_not_stop_the_others(self) -> None:
        calls: list[str] = []
        scheduler = self.make()

        def boom() -> None:
            calls.append("boom")
            raise RuntimeError("kaboom")

        scheduler.register("boom", 10, boom)
        scheduler.register("fine", 10, lambda: calls.append("fine"))
        self.clock.now = 10.0

        with self.assertLogs("larder.scheduler", "ERROR") as logs:
            scheduler.run_due()

        self.assertEqual(calls, ["boom", "fine"])
        output = "\n".join(logs.output)
        self.assertIn("boom", output)
        self.assertIn("kaboom", output)

        # The failed job is rescheduled one interval later like any other.
        self.clock.now = 19.9
        with self.assertNoLogs("larder.scheduler"):
            scheduler.run_due()
        self.assertEqual(calls, ["boom", "fine"])
        self.clock.now = 20.0
        with self.assertLogs("larder.scheduler", "ERROR"):
            scheduler.run_due()
        self.assertEqual(calls, ["boom", "fine", "boom", "fine"])

    def test_register_rejects_duplicate_names_and_non_positive_intervals(self) -> None:
        scheduler = self.make()
        scheduler.register("job", 10, lambda: None)

        with self.assertRaises(ValueError):
            scheduler.register("job", 20, lambda: None)
        with self.assertRaises(ValueError):
            scheduler.register("zero", 0, lambda: None)
        with self.assertRaises(ValueError):
            scheduler.register("negative", -1, lambda: None)

        # A rejected registration leaves nothing behind: the name is still free.
        calls: list[str] = []
        scheduler.register("zero", 1, lambda: calls.append("zero"))
        self.clock.now = 1.0
        scheduler.run_due()
        self.assertEqual(calls, ["zero"])


class ThreadTest(SchedulerTestCase):
    def test_start_runs_jobs_on_a_thread_and_stop_joins_it(self) -> None:
        ran = threading.Event()
        finished = threading.Event()
        names: list[str] = []

        def job() -> None:
            names.append(threading.current_thread().name)
            ran.set()
            time.sleep(0.2)  # stop() must wait for a running job
            finished.set()

        scheduler = Scheduler(self.data_dir)  # real clock
        self.addCleanup(scheduler.stop)
        scheduler.register("job", 0.01, job)
        self.assertFalse(scheduler.running)

        scheduler.start()

        self.assertTrue(scheduler.running)
        self.assertTrue(ran.wait(timeout=10))
        self.assertEqual(names[0], THREAD_NAME)
        (thread,) = scheduler_threads()
        self.assertTrue(thread.daemon)

        with self.assertNoLogs("larder.scheduler"):
            scheduler.start()  # a second start is a no-op
        self.assertEqual(scheduler_threads(), [thread])

        scheduler.stop()

        self.assertTrue(finished.is_set())
        self.assertFalse(scheduler.running)
        self.assertFalse(thread.is_alive())
        self.assertEqual(scheduler_threads(), [])

    def test_second_scheduler_on_the_same_data_dir_runs_no_jobs(self) -> None:
        first = self.make()
        first.start()
        self.assertTrue(first.running)

        calls: list[str] = []
        late_clock = FakeClock(100.0)
        second = self.make(late_clock)
        second.register("job", 1, lambda: calls.append("job"))
        late_clock.now = 200.0  # the job is already due

        with self.assertLogs("larder.scheduler", "WARNING") as logs:
            second.start()

        self.assertEqual(len(logs.records), 1)
        self.assertFalse(second.running)
        self.assertEqual(len(scheduler_threads()), 1)  # only the first one's
        self.assertEqual(calls, [])

    def test_stop_releases_the_lock_so_another_scheduler_can_run(self) -> None:
        first = self.make()
        first.start()
        first.stop()

        second = self.make()
        second.start()

        self.assertTrue(second.running)
        self.assertEqual(len(scheduler_threads()), 1)

    def test_stop_without_start_is_a_no_op(self) -> None:
        scheduler = self.make()

        scheduler.stop()

        self.assertFalse(scheduler.running)


if __name__ == "__main__":
    unittest.main()
