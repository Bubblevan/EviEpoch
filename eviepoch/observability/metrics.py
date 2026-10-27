"""Bounded, content-free operation counters. No user IDs, prompts or source text."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from functools import wraps


class Metrics:
    def __init__(self):
        self.lock = threading.Lock()
        self.counts = defaultdict(int)
        self.durations = defaultdict(lambda: deque(maxlen=512))

    def observe(self, operation, seconds, success):
        with self.lock:
            self.counts[operation + ("_success" if success else "_error")] += 1
            self.durations[operation].append(seconds)

    def snapshot(self):
        with self.lock:
            latencies = {}
            for operation, values in self.durations.items():
                ordered = sorted(values)
                latencies[operation] = {"samples": len(ordered), "p50_seconds": ordered[len(ordered)//2],
                                        "p95_seconds": ordered[min(len(ordered)-1, int(len(ordered)*0.95))]}
            return {"counts": dict(self.counts), "latencies": latencies}


def measured(operation):
    def decorate(function):
        @wraps(function)
        def run(self, *args, **kwargs):
            start, success = time.perf_counter(), False
            try:
                result = function(self, *args, **kwargs)
                success = True
                return result
            finally:
                self.metrics.observe(operation, time.perf_counter() - start, success)
        return run
    return decorate
