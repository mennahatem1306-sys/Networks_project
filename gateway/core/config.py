"""Milestone 1 limits and configuration validation."""

QUEUE_CAPACITY = 5
UINT32_MAX = 2**32 - 1
MAX_DURATION_MS = 3_600_000
ALLOWED_MODES = {"PRODUCTION", "BENCHMARKING"}
ALLOWED_RESULTS = {"PASS", "FAIL"}
DEFAULTS = dict(target_parts=10, cycle_time_ms=500, inspection_time_ms=1000, rule_n=3, rule_k=0)
LIMITS = {
    "target_parts": (1, 1000),
    "cycle_time_ms": (1, MAX_DURATION_MS),
    "inspection_time_ms": (1, MAX_DURATION_MS),
    "rule_n": (1, 2**31 - 1),
    "rule_k": (-(2**31), 2**31 - 1),
}


def integer(value, low=1, high=UINT32_MAX):
    return type(value) is int and low <= value <= high


def invalid_field(values):
    return next((key for key, bounds in LIMITS.items()
                 if not integer(values.get(key), *bounds)), None)
