import asyncio
import inspect
import logging
import math
import os
import time
from collections import deque

import aiohttp
from aiohttp.client_exceptions import ContentTypeError

from gear.limits import WeightedLimiter


logger = logging.getLogger("gear")

_session = None
_session_lock = None
_backend_runtime = None
_controller_task = None


class BackendBusyError(Exception):
    pass


def _positive_int(name, default):
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError("{} must be positive".format(name))
    return value


def _positive_float(name, default):
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError("{} must be positive".format(name))
    return value


def recommend_backend_limit(
    current,
    minimum,
    maximum,
    utilization,
    p95_latency,
    error_rate,
    rejected,
    event_loop_lag,
    target_p95,
    error_threshold,
    lag_threshold,
):
    """Return an AIMD-style backend limit recommendation."""
    severe = (
        error_rate >= max(0.05, error_threshold * 2)
        or p95_latency >= target_p95 * 4
        or event_loop_lag >= lag_threshold * 2
    )
    unhealthy = (
        error_rate >= error_threshold
        or p95_latency >= target_p95 * 2
        or event_loop_lag >= lag_threshold
    )

    if severe:
        return max(minimum, min(maximum, int(math.floor(current * 0.5))))
    if unhealthy:
        return max(minimum, min(maximum, int(math.floor(current * 0.75))))

    healthy = (
        error_rate < error_threshold / 2
        and p95_latency <= target_p95
        and event_loop_lag < lag_threshold / 2
    )
    if healthy and (utilization >= 0.8 or rejected > 0):
        increase = max(1, int(math.ceil(current * 0.1)))
        return min(maximum, current + increase)
    return current


class BackendRuntime:
    def __init__(
        self,
        initial,
        minimum,
        maximum,
        adaptive,
        on_limit_change=None,
    ):
        self.initial = int(initial)
        self.minimum = int(minimum)
        self.maximum = int(maximum)
        self.adaptive = bool(adaptive)
        self.on_limit_change = on_limit_change
        self.limiter = WeightedLimiter(self.initial)
        self.samples = deque(maxlen=_positive_int("GEAR_AUTOTUNE_SAMPLE_LIMIT", 4096))
        self.rejected = 0
        self.interval = _positive_float("GEAR_AUTOTUNE_INTERVAL", 10)
        self.window = _positive_float("GEAR_AUTOTUNE_WINDOW", 60)
        self.target_p95 = _positive_float("GEAR_BACKEND_TARGET_P95", 0.5)
        self.error_threshold = _positive_float(
            "GEAR_BACKEND_ERROR_THRESHOLD",
            0.02,
        )
        self.lag_threshold = _positive_float(
            "GEAR_EVENT_LOOP_LAG_THRESHOLD",
            0.2,
        )
        self.minimum_samples = _positive_int("GEAR_AUTOTUNE_MIN_SAMPLES", 20)
        self.queue_timeout = _positive_float(
            "GEAR_BACKEND_QUEUE_TIMEOUT",
            0.25,
        )

    def record(self, latency, success):
        self.samples.append((time.monotonic(), float(latency), bool(success)))

    def record_rejection(self):
        self.rejected += 1

    def _window_samples(self, now):
        cutoff = now - self.window
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()
        return list(self.samples)

    async def tune_once(self, event_loop_lag=0.0):
        now = time.monotonic()
        samples = self._window_samples(now)
        snapshot = await self.limiter.snapshot(reset_peak=True)
        rejected = self.rejected
        self.rejected = 0

        if len(samples) < self.minimum_samples:
            return snapshot["capacity"]

        latencies = sorted(sample[1] for sample in samples)
        p95_index = max(0, int(math.ceil(len(latencies) * 0.95)) - 1)
        p95_latency = latencies[p95_index]
        errors = sum(1 for sample in samples if not sample[2])
        error_rate = errors / float(len(samples))
        utilization = snapshot["peak_in_use"] / float(snapshot["capacity"])
        current = snapshot["capacity"]
        recommended = recommend_backend_limit(
            current=current,
            minimum=self.minimum,
            maximum=self.maximum,
            utilization=utilization,
            p95_latency=p95_latency,
            error_rate=error_rate,
            rejected=rejected,
            event_loop_lag=event_loop_lag,
            target_p95=self.target_p95,
            error_threshold=self.error_threshold,
            lag_threshold=self.lag_threshold,
        )

        if recommended != current:
            await self.limiter.set_capacity(recommended)
            if self.on_limit_change is not None:
                result = self.on_limit_change(recommended)
                if inspect.isawaitable(result):
                    await result
            logger.info(
                "Auto-tuned Meter REST concurrency %d -> %d "
                "(utilization=%.0f%% p95=%.3fs errors=%.1f%% "
                "rejected=%d loop_lag=%.3fs)",
                current,
                recommended,
                utilization * 100,
                p95_latency,
                error_rate * 100,
                rejected,
                event_loop_lag,
            )
        return recommended

    async def run_controller(self):
        expected = time.monotonic() + self.interval
        while True:
            await asyncio.sleep(self.interval)
            now = time.monotonic()
            lag = max(0.0, now - expected)
            expected = now + self.interval
            await self.tune_once(event_loop_lag=lag)


def configure_backend(
    initial,
    minimum,
    maximum,
    adaptive=True,
    on_limit_change=None,
):
    """Configure the Meter REST limiter before creating the client session."""
    global _backend_runtime
    if _session is not None and not _session.closed:
        raise RuntimeError("cannot reconfigure an active backend session")
    if not (0 < minimum <= initial <= maximum):
        raise ValueError("backend limits must satisfy 0 < min <= initial <= max")
    _backend_runtime = BackendRuntime(
        initial=initial,
        minimum=minimum,
        maximum=maximum,
        adaptive=adaptive,
        on_limit_change=on_limit_change,
    )
    return _backend_runtime


def _get_backend_runtime():
    global _backend_runtime
    if _backend_runtime is None:
        fixed_limit = _positive_int("GEAR_BACKEND_CONNECTION_LIMIT", 128)
        _backend_runtime = BackendRuntime(
            initial=fixed_limit,
            minimum=fixed_limit,
            maximum=fixed_limit,
            adaptive=False,
        )
    return _backend_runtime


def start_backend_controller():
    global _controller_task
    runtime = _get_backend_runtime()
    if runtime.adaptive and (
        _controller_task is None or _controller_task.done()
    ):
        _controller_task = asyncio.create_task(runtime.run_controller())
    return _controller_task


async def get_session():
    """Return the process-wide, bounded Meter REST client session."""
    global _session
    global _session_lock

    if _session is not None and not _session.closed:
        return _session

    if _session_lock is None:
        _session_lock = asyncio.Lock()

    async with _session_lock:
        if _session is None or _session.closed:
            runtime = _get_backend_runtime()
            timeout = aiohttp.ClientTimeout(
                total=_positive_float("GEAR_BACKEND_TOTAL_TIMEOUT", 10),
                connect=_positive_float("GEAR_BACKEND_CONNECT_TIMEOUT", 2),
                sock_read=_positive_float("GEAR_BACKEND_READ_TIMEOUT", 8),
            )
            connector = aiohttp.TCPConnector(
                limit=runtime.maximum,
                limit_per_host=runtime.maximum,
                keepalive_timeout=_positive_float(
                    "GEAR_BACKEND_KEEPALIVE_TIMEOUT",
                    15,
                ),
                enable_cleanup_closed=True,
            )
            _session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
            )
    return _session


async def close_session():
    """Close the Meter REST session and adaptive controller."""
    global _session
    global _session_lock
    global _backend_runtime
    global _controller_task

    if _controller_task is not None:
        _controller_task.cancel()
        try:
            await _controller_task
        except asyncio.CancelledError:
            pass
    _controller_task = None

    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
    _session_lock = None
    _backend_runtime = None


async def _decode_response(response):
    try:
        return await response.json()
    except (ContentTypeError, ValueError):
        text = await response.text()
        return {
            "error": text.strip() or "HTTP {}".format(response.status),
            "code": -2,
        }


async def _request(method, endpoint_uri, params=None, data=None, **kwargs):
    runtime = _get_backend_runtime()
    admitted = await runtime.limiter.acquire(timeout=runtime.queue_timeout)
    if not admitted:
        runtime.record_rejection()
        raise BackendBusyError("Meter REST concurrency limit reached")

    started = time.monotonic()
    try:
        session = await get_session()
        async with method(
            session,
            endpoint_uri,
            params=params,
            json=data,
            **kwargs,
        ) as response:
            result = await _decode_response(response)
            runtime.record(
                time.monotonic() - started,
                success=response.status < 500,
            )
            return result
    except Exception:
        runtime.record(time.monotonic() - started, success=False)
        raise
    finally:
        await runtime.limiter.release()


async def post(endpoint_uri, params=None, data=None, **kwargs):
    return await _request(
        aiohttp.ClientSession.post,
        endpoint_uri,
        params=params,
        data=data,
        **kwargs,
    )


async def get(endpoint_uri, params=None, data=None, **kwargs):
    return await _request(
        aiohttp.ClientSession.get,
        endpoint_uri,
        params=params,
        **kwargs,
    )


class Restful(object):

    def __init__(self, endpoint):
        super(Restful, self).__init__()
        self._endpoint = endpoint

    def __call__(self, parameter):
        if parameter is not None:
            return Restful('%s/%s' % (self._endpoint, parameter))
        return self

    def __getattr__(self, resource):
        return Restful('%s/%s' % (self._endpoint, resource))

    async def make_request(self, method, params=None, data=None, **kwargs):
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
        }
        kwargs.setdefault('headers', headers)
        try:
            return await method(
                self._endpoint,
                params=params,
                data=data,
                **kwargs,
            )
        except BackendBusyError as error:
            logger.warning("Meter-Restful request rejected: %s", error)
            return {"error": "meter node is busy", "code": -32005}
        except asyncio.TimeoutError as error:
            logger.warning("Timed out connecting to Meter-Restful server: %s", error)
            return {"error": "meter node request timed out", "code": -1}
        except aiohttp.ClientConnectionError as error:
            logger.warning("Unable to connect to Meter-Restful server: %s", error)
            return {"error": "meter node is not running", "code": -1}
        except aiohttp.ClientError as error:
            logger.warning("Meter-Restful request failed: %s", error)
            return {"error": str(error), "code": -3}
        except Exception as error:
            logger.exception("Unexpected Meter-Restful request failure")
            return {"error": str(error), "code": -3}
