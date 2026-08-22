import asyncio


class WeightedLimiter:
    """A resizable asyncio limiter whose acquisitions can have a weight."""

    def __init__(self, capacity):
        capacity = int(capacity)
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._in_use = 0
        self._peak_in_use = 0
        self._condition = asyncio.Condition()

    @property
    def capacity(self):
        return self._capacity

    @property
    def in_use(self):
        return self._in_use

    async def acquire(self, weight=1, timeout=None):
        weight = int(weight)
        if weight <= 0:
            raise ValueError("weight must be positive")

        async def wait_for_capacity():
            async with self._condition:
                while self._in_use + weight > self._capacity:
                    # A resized limiter may no longer be able to admit this
                    # acquisition at all. Let the caller reject it promptly.
                    if weight > self._capacity:
                        return False
                    await self._condition.wait()
                self._in_use += weight
                self._peak_in_use = max(self._peak_in_use, self._in_use)
                return True

        try:
            if timeout is None:
                return await wait_for_capacity()
            return await asyncio.wait_for(wait_for_capacity(), timeout=timeout)
        except asyncio.TimeoutError:
            return False

    async def release(self, weight=1):
        weight = int(weight)
        if weight <= 0:
            raise ValueError("weight must be positive")
        async with self._condition:
            if weight > self._in_use:
                raise ValueError("released more capacity than is in use")
            self._in_use -= weight
            self._condition.notify_all()

    async def set_capacity(self, capacity):
        capacity = int(capacity)
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        async with self._condition:
            self._capacity = capacity
            self._condition.notify_all()

    async def snapshot(self, reset_peak=False):
        async with self._condition:
            result = {
                "capacity": self._capacity,
                "in_use": self._in_use,
                "peak_in_use": self._peak_in_use,
            }
            if reset_peak:
                self._peak_in_use = self._in_use
            return result
