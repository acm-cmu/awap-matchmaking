from threading import Lock


class AtomicCounter:
    next_val: int
    lock: Lock

    def __init__(self, first_val: int) -> None:
        self.next_val = first_val
        self.lock = Lock()

    def __iter__(self):
        return self

    def __next__(self):
        with self.lock:
            val = self.next_val
            self.next_val += 1
        return val
