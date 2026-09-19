"""One worker for every plugin call. Completion runs after the task returns."""

from concurrent.futures import Future
from queue import Queue
import threading


class Worker:
    def __init__(self):
        self.queue = Queue()
        self.thread = threading.Thread(
            target=self._loop, name="gear-worker", daemon=False
        )
        self.thread.start()

    def submit(self, action, after=None):
        future = Future()
        self.queue.put((action, after, future))
        return future

    def _loop(self):
        while True:
            item = self.queue.get()
            if item is None:
                return
            action, after, future = item
            try:
                value = action()
                if after:
                    after()
                future.set_result(value)
            except BaseException as exc:
                future.set_exception(exc)

    def flush(self):
        if threading.current_thread() is self.thread:
            raise RuntimeError("Worker cannot wait for itself")
        self.submit(lambda: None).result()

    def close(self):
        self.queue.put(None)
        self.thread.join()
