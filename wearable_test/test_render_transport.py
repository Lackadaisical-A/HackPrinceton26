"""Latest-pose backpressure and renderer failure tests, with no serial hardware."""
import time
import unittest

from wearable_test.render_transport import RenderClient


def slow_worker(connection):
    connection.send(("ready",))
    try:
        while True:
            request = connection.recv()
            time.sleep(.12)
            connection.send(("frame",1,1,bytes((request["value"],0,0,255))))
    except (EOFError,OSError): pass


def stalled_worker(connection):
    connection.send(("ready",))
    connection.recv()
    time.sleep(30)


class TransportTests(unittest.TestCase):
    def wait_for(self,predicate,seconds=8):
        deadline = time.monotonic()+seconds
        while time.monotonic()<deadline:
            if predicate(): return
            time.sleep(.01)
        self.fail("Renderer condition did not complete before deadline")

    def test_slow_renderer_keeps_latest_pose_without_blocking_submit(self):
        client = RenderClient(slow_worker)
        try:
            client.submit({"value":0})
            self.wait_for(lambda: client.snapshot()["number"]>=1)
            start = time.monotonic()
            for i in range(10000):
                client.submit({"value":i%256})
                client.snapshot()
            self.assertLess(time.monotonic()-start,1.)
            client.submit({"value":255})
            self.wait_for(lambda: client.snapshot()["frame"][3][0]==255)
            self.assertLess(client.snapshot()["number"],20,"Old poses were queued instead of replaced")
        finally:
            client.close()
        self.assertFalse(client.thread.is_alive())

    def test_stall_restarts_then_exposes_failure_and_accepts_manual_retry(self):
        client = RenderClient(stalled_worker,timeout=.15,max_restarts=1)
        try:
            client.submit({"value":0})
            self.wait_for(lambda: client.snapshot()["state"]=="failed")
            self.assertEqual(client.snapshot()["restarts"],2)
            before = time.monotonic()
            client.submit({"value":1})
            self.assertEqual(client.snapshot()["state"],"failed")
            self.assertLess(time.monotonic()-before,.1)
            client.restart.set()
            self.wait_for(lambda: client.snapshot()["restarts"]>2)
        finally: client.close()
        self.assertFalse(client.thread.is_alive())

    def test_close_interrupts_wait_for_stalled_worker(self):
        client = RenderClient(stalled_worker,timeout=30)
        client.submit({"value":0})
        self.wait_for(lambda: client.snapshot()["state"]=="ready")
        before = time.monotonic()
        client.close()
        self.assertLess(time.monotonic()-before,1.6)
        self.assertFalse(client.thread.is_alive())


if __name__ == "__main__": unittest.main()
