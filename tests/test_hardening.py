import asyncio
import json
import os
import unittest
from unittest import mock

from aiohttp import web

from gear import cli
from gear.limits import WeightedLimiter
from gear.meter import request as meter_request
from gear.resources import GIB, SystemResources, calculate_runtime_limits


class SharedSessionTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        await meter_request.close_session()
        self.runners = []
        self.environment = mock.patch.dict(
            os.environ,
            {
                "GEAR_BACKEND_CONNECTION_LIMIT": "4",
                "GEAR_BACKEND_TOTAL_TIMEOUT": "2",
                "GEAR_BACKEND_CONNECT_TIMEOUT": "1",
                "GEAR_BACKEND_READ_TIMEOUT": "1",
            },
        )
        self.environment.start()

    async def asyncTearDown(self):
        await meter_request.close_session()
        for runner in self.runners:
            await runner.cleanup()
        self.environment.stop()

    async def start_test_server(self, handler):
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.runners.append(runner)
        port = site._server.sockets[0].getsockname()[1]
        return "http://127.0.0.1:{}".format(port)

    async def test_session_is_reused_and_bounded(self):
        first = await meter_request.get_session()
        second = await meter_request.get_session()

        self.assertIs(first, second)
        self.assertEqual(first.connector.limit, 4)
        self.assertEqual(first.connector.limit_per_host, 4)

    async def test_restful_returns_decoded_result_directly(self):
        expected = {"number": 123}

        async def fake_method(endpoint, params=None, data=None, **kwargs):
            return expected

        restful = meter_request.Restful("http://127.0.0.1:8669/blocks/best")
        result = await restful.make_request(fake_method)

        self.assertEqual(result, expected)

    async def test_backend_calls_are_bounded_before_connector_queue(self):
        active = 0
        maximum = 0

        async def handler(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.05)
            active -= 1
            return web.json_response({"ok": True})

        endpoint = await self.start_test_server(handler)
        meter_request.configure_backend(
            initial=2,
            minimum=2,
            maximum=2,
            adaptive=False,
        )

        results = await asyncio.gather(*[
            meter_request.get(endpoint) for _ in range(6)
        ])

        self.assertEqual(maximum, 2)
        self.assertEqual(results, [{"ok": True}] * 6)


class RPCConcurrencyTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.previous_slots = cli.rpc_slots
        self.previous_client_counts = cli.rpc_client_counts
        self.previous_client_lock = cli.rpc_client_lock
        self.previous_per_ip_limit = cli.RPC_PER_IP_CONCURRENCY
        self.previous_timeout = cli.RPC_QUEUE_TIMEOUT
        cli.rpc_slots = WeightedLimiter(2)
        cli.rpc_client_counts = {}
        cli.rpc_client_lock = asyncio.Lock()
        cli.RPC_PER_IP_CONCURRENCY = 10
        cli.RPC_QUEUE_TIMEOUT = 0.01

    async def asyncTearDown(self):
        cli.rpc_slots = self.previous_slots
        cli.rpc_client_counts = self.previous_client_counts
        cli.rpc_client_lock = self.previous_client_lock
        cli.RPC_PER_IP_CONCURRENCY = self.previous_per_ip_limit
        cli.RPC_QUEUE_TIMEOUT = self.previous_timeout

    async def test_middleware_rejects_work_above_limit(self):
        active = 0
        maximum = 0

        async def handler(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.05)
            active -= 1
            return web.Response(status=200)

        requests = []
        for index in range(10):
            request = mock.Mock()
            request.headers = {}
            request.remote = "192.0.2.{}".format(index)
            requests.append(request)

        responses = await asyncio.gather(*[
            cli.rpc_concurrency_middleware(request, handler)
            for request in requests
        ])

        statuses = [response.status for response in responses]
        self.assertEqual(maximum, 2)
        self.assertEqual(statuses.count(200), 2)
        self.assertEqual(statuses.count(503), 8)

    async def test_per_ip_limit_preserves_capacity_for_other_clients(self):
        cli.rpc_slots = WeightedLimiter(10)
        cli.RPC_PER_IP_CONCURRENCY = 2

        async def handler(request):
            await asyncio.sleep(0.05)
            return web.Response(status=200)

        abusive_requests = []
        for _ in range(6):
            request = mock.Mock()
            request.headers = {"X-Forwarded-For": "198.51.100.10"}
            request.remote = "127.0.0.1"
            abusive_requests.append(request)

        legitimate_request = mock.Mock()
        legitimate_request.headers = {
            "X-Forwarded-For": "198.51.100.20"
        }
        legitimate_request.remote = "127.0.0.1"

        responses = await asyncio.gather(
            *[
                cli.rpc_concurrency_middleware(request, handler)
                for request in abusive_requests
            ],
            cli.rpc_concurrency_middleware(legitimate_request, handler),
        )

        statuses = [response.status for response in responses]
        self.assertEqual(statuses.count(200), 3)
        self.assertEqual(statuses.count(429), 4)


class WeightedOperationLimitTests(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.previous_slots = cli.rpc_operation_slots
        self.previous_counts = cli.rpc_operation_client_counts
        self.previous_lock = cli.rpc_operation_client_lock
        self.previous_per_ip_limit = cli.RPC_PER_IP_OPERATION_LIMIT
        self.previous_timeout = cli.RPC_QUEUE_TIMEOUT
        cli.rpc_operation_slots = WeightedLimiter(4)
        cli.rpc_operation_client_counts = {}
        cli.rpc_operation_client_lock = asyncio.Lock()
        cli.RPC_PER_IP_OPERATION_LIMIT = 4
        cli.RPC_QUEUE_TIMEOUT = 0.01

    async def asyncTearDown(self):
        cli.rpc_operation_slots = self.previous_slots
        cli.rpc_operation_client_counts = self.previous_counts
        cli.rpc_operation_client_lock = self.previous_lock
        cli.RPC_PER_IP_OPERATION_LIMIT = self.previous_per_ip_limit
        cli.RPC_QUEUE_TIMEOUT = self.previous_timeout

    async def test_batch_weight_consumes_actual_operation_capacity(self):
        acquired, limit = await cli.acquire_operation_slots("192.0.2.1", 3)
        self.assertTrue(acquired)
        self.assertIsNone(limit)

        acquired, limit = await cli.acquire_operation_slots("192.0.2.2", 2)
        self.assertFalse(acquired)
        self.assertEqual(limit, "global")

        await cli.release_operation_slots("192.0.2.1", 3)
        acquired, limit = await cli.acquire_operation_slots("192.0.2.2", 2)
        self.assertTrue(acquired)
        self.assertIsNone(limit)
        await cli.release_operation_slots("192.0.2.2", 2)

    async def test_per_ip_operation_limit_preserves_other_clients(self):
        cli.rpc_operation_slots = WeightedLimiter(10)
        acquired, _ = await cli.acquire_operation_slots("198.51.100.1", 4)
        self.assertTrue(acquired)

        acquired, limit = await cli.acquire_operation_slots("198.51.100.1", 1)
        self.assertFalse(acquired)
        self.assertEqual(limit, "client")

        acquired, limit = await cli.acquire_operation_slots("198.51.100.2", 1)
        self.assertTrue(acquired)
        self.assertIsNone(limit)

        await cli.release_operation_slots("198.51.100.1", 4)
        await cli.release_operation_slots("198.51.100.2", 1)


class ResourceSizingTests(unittest.TestCase):

    def test_powerful_container_gets_bounded_dynamic_headroom(self):
        resources = SystemResources(
            cpus=16,
            memory_bytes=64 * GIB,
            nofile_soft=65536,
            nofile_hard=524288,
        )
        limits = calculate_runtime_limits(resources, environment={})

        self.assertTrue(limits.auto_tune)
        self.assertEqual(limits.backend_initial, 128)
        self.assertEqual(limits.backend_min, 64)
        self.assertEqual(limits.backend_max, 512)
        self.assertEqual(limits.rpc_concurrency, 256)
        self.assertEqual(limits.rpc_operation_limit, 256)
        self.assertTrue(limits.rpc_concurrency_dynamic)
        self.assertTrue(limits.rpc_operation_limit_dynamic)

    def test_small_container_is_capped_by_available_memory(self):
        resources = SystemResources(
            cpus=1,
            memory_bytes=GIB,
            nofile_soft=1024,
            nofile_hard=1024,
        )
        limits = calculate_runtime_limits(resources, environment={})

        self.assertEqual(limits.backend_initial, 16)
        self.assertEqual(limits.backend_max, 16)
        self.assertEqual(limits.rpc_concurrency, 32)
        self.assertEqual(limits.rpc_operation_limit, 32)

    def test_disabling_autotune_honors_fixed_overrides(self):
        resources = SystemResources(
            cpus=1,
            memory_bytes=GIB,
            nofile_soft=1024,
            nofile_hard=1024,
        )
        limits = calculate_runtime_limits(
            resources,
            environment={
                "GEAR_AUTOTUNE": "0",
                "GEAR_BACKEND_CONNECTION_LIMIT": "300",
                "GEAR_RPC_CONCURRENCY": "700",
                "GEAR_RPC_OPERATION_LIMIT": "800",
            },
        )

        self.assertFalse(limits.auto_tune)
        self.assertEqual(limits.backend_min, 300)
        self.assertEqual(limits.backend_initial, 300)
        self.assertEqual(limits.backend_max, 300)
        self.assertEqual(limits.rpc_concurrency, 700)
        self.assertEqual(limits.rpc_operation_limit, 800)

    def test_explicit_small_initial_limit_is_not_raised(self):
        resources = SystemResources(
            cpus=16,
            memory_bytes=64 * GIB,
            nofile_soft=65536,
            nofile_hard=524288,
        )
        limits = calculate_runtime_limits(
            resources,
            environment={"GEAR_BACKEND_CONNECTION_LIMIT": "4"},
        )

        self.assertEqual(limits.backend_min, 4)
        self.assertEqual(limits.backend_initial, 4)
        self.assertEqual(limits.backend_max, 512)


class AdaptiveBackendTests(unittest.TestCase):

    def test_healthy_saturated_backend_increases_gradually(self):
        recommended = meter_request.recommend_backend_limit(
            current=100,
            minimum=64,
            maximum=512,
            utilization=0.9,
            p95_latency=0.2,
            error_rate=0,
            rejected=0,
            event_loop_lag=0.01,
            target_p95=0.5,
            error_threshold=0.02,
            lag_threshold=0.2,
        )
        self.assertEqual(recommended, 110)

    def test_unhealthy_backend_reduces_quickly(self):
        recommended = meter_request.recommend_backend_limit(
            current=200,
            minimum=64,
            maximum=512,
            utilization=1,
            p95_latency=3,
            error_rate=0.1,
            rejected=10,
            event_loop_lag=0.5,
            target_p95=0.5,
            error_threshold=0.02,
            lag_threshold=0.2,
        )
        self.assertEqual(recommended, 100)


class BatchLimitTests(unittest.IsolatedAsyncioTestCase):

    async def test_oversized_batch_is_rejected_before_dispatch(self):
        payload = [
            {
                "jsonrpc": "2.0",
                "method": "eth_getBalance",
                "params": ["0x" + "0" * 40, "latest"],
                "id": index,
            }
            for index in range(cli.MAX_BATCH_SIZE + 1)
        ]

        request = mock.Mock()
        request.text = mock.AsyncMock(return_value=json.dumps(payload))
        request.headers = {}
        request.remote = "127.0.0.1"

        with mock.patch.object(
            cli,
            "handleTextRequest",
            new=mock.AsyncMock(),
        ) as dispatch:
            response = await cli.http_handler(request)

        self.assertEqual(response.status, 429)
        dispatch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
