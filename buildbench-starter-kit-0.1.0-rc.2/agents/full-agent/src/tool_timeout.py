from contextlib import contextmanager
import signal
import threading


@contextmanager
def tool_time_limit(seconds: float):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("tool timeout requires the main thread")

    if not hasattr(signal, "setitimer"):
        raise RuntimeError("tool timeout is unsupported on this platform")

    if signal.getitimer(signal.ITIMER_REAL) != (0.0, 0.0):
        raise RuntimeError("another real-time timer is already active")

    previous_handler = signal.getsignal(signal.SIGALRM)

    def timed_out(signum, frame):
        raise TimeoutError("tool execution time limit reached")

    signal.signal(signal.SIGALRM, timed_out)

    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)