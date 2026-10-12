import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "riden_rd60xx_home_assistant_addon"))

from pymodbus.exceptions import ConnectionException, ModbusException

from bridge import Bridge
from psu_state import PSUState
from rd60xx import RD60xx, RD60xxStateGet
from rd60xx_to_mqtt import RD60xxToMQTT


def sample_state():
    return RD60xxStateGet(
        60062, 41798, "1.42", 25, 77, 0, 4.2, 1.0, 4.3, 1.1,
        4.2, 0.1, 0.42, 12, 0, 0, True, True, 4.2, 25, 77,
        0.1, 0.42, [(4.2, 1.0, 4.3, 1.1)],
    )


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = []
        self.state = PSUState()
        self.state.update_period = 5
        self.publish = AsyncMock()

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def make_bridge(self, psu, *, clock=False):
        worker = Bridge("127.0.0.1", 8080, "60062_41798", 60062, 41798,
                        psu, self.state, self.publish, clock)
        self.tasks.append(worker._psu_task)
        return worker

    def fake_psu(self):
        return Mock(connected=True, get_state=AsyncMock(return_value=sample_state()),
                    set_state=AsyncMock(), set_clock=AsyncMock())

    async def test_disconnected_error_stops_after_first_attempt(self):
        psu = self.fake_psu()
        psu.connected = False
        # Bound the old implementation's runaway loop so the regression fails safely.
        psu.get_state.side_effect = [ConnectionException("Not connected")] * 10 + [RuntimeError("runaway")]
        worker = self.make_bridge(psu)
        worker.queue_state_get()
        with self.assertLogs("Bridge", level="WARNING") as logs:
            await asyncio.wait_for(worker._psu_task, 1)
        self.assertEqual(psu.get_state.await_count, 1)
        self.assertEqual(len(logs.records), 1)
        psu.close.assert_called_once()
        self.publish.assert_not_awaited()

    async def test_transient_errors_back_off_for_all_poll_periods(self):
        for period, expected_delay in [(0, 5), (0.1, 5), (5, 5), (20, 20)]:
            with self.subTest(period=period):
                self.state.update_period = period
                psu = self.fake_psu()
                psu.get_state.side_effect = ModbusException("temporary failure")
                sleeping = asyncio.Event()
                blocker = asyncio.Event()

                async def backoff(delay):
                    self.assertEqual(delay, expected_delay)
                    sleeping.set()
                    await blocker.wait()

                with patch("bridge.asyncio.sleep", side_effect=backoff) as sleep, \
                        self.assertLogs("Bridge", level="WARNING"):
                    worker = self.make_bridge(psu)
                    worker.queue_state_get()
                    await asyncio.wait_for(sleeping.wait(), 1)
                    self.assertEqual(psu.get_state.await_count, 1)
                    sleep.assert_awaited_once()
                    psu.close.assert_not_called()
                    worker.cancel()
                    await asyncio.wait_for(asyncio.gather(worker._psu_task, return_exceptions=True), 1)

    async def test_polling_recovers_after_transient_error(self):
        psu = self.fake_psu()
        psu.get_state.side_effect = [ModbusException("temporary failure"), sample_state()]
        self.state.update_period = 0
        retried = asyncio.Event()
        self.publish.side_effect = lambda *_: retried.set()
        with patch("bridge.asyncio.sleep", new=AsyncMock()), self.assertLogs("Bridge", level="WARNING"):
            worker = self.make_bridge(psu)
            worker.queue_state_get()
            worker.queue_state_get()
            await asyncio.wait_for(retried.wait(), 1)
        self.assertEqual(psu.get_state.await_count, 2)
        self.assertTrue(self.publish.call_args.args[1]["connected"])
        psu.close.assert_not_called()

    async def test_clock_error_also_backs_off(self):
        psu = self.fake_psu()
        psu.set_clock.side_effect = [ModbusException("clock failure"), asyncio.CancelledError()]
        with patch("bridge.asyncio.sleep", new=AsyncMock()) as sleep, \
                self.assertLogs("Bridge", level="WARNING"):
            worker = self.make_bridge(psu, clock=True)
            await asyncio.wait_for(worker._psu_task, 1)
        sleep.assert_awaited_once_with(5)
        self.assertEqual(psu.set_clock.await_count, 2)
        psu.get_state.assert_not_awaited()

    async def test_wrapped_cancellation_is_not_retried_or_warned(self):
        psu = self.fake_psu()
        error = ModbusException("request cancelled")
        error.__cause__ = asyncio.CancelledError()
        psu.get_state.side_effect = error
        with patch("bridge.asyncio.sleep", new=AsyncMock()) as sleep, \
                patch("bridge.logging.getLogger", return_value=Mock()) as logger:
            worker = self.make_bridge(psu)
            worker.queue_state_get()
            await asyncio.wait_for(worker._psu_task, 1)
        psu.get_state.assert_awaited_once()
        sleep.assert_not_awaited()
        logger.return_value.warning.assert_not_called()

    async def test_unexpected_failure_closes_actual_client(self):
        psu = self.fake_psu()
        psu.get_state.side_effect = ValueError("bad response")
        with self.assertLogs("Bridge", level="ERROR"):
            worker = self.make_bridge(psu)
            worker.queue_state_get()
            await asyncio.wait_for(worker._psu_task, 1)
        psu.close.assert_called_once()

    async def test_healthy_commands_and_polling_publish_readback(self):
        psu = self.fake_psu()
        self.state.update_period = 0
        published = asyncio.Event()
        self.publish.side_effect = lambda *_: published.set()
        worker = self.make_bridge(psu)
        worker.queue_state_set({"output_enable": False})
        worker.queue_state_get()
        await asyncio.wait_for(published.wait(), 1)
        psu.set_state.assert_awaited_once()
        self.assertFalse(psu.set_state.call_args.args[0].output_enable)
        # Publication follows device readback, rather than the requested value.
        self.assertTrue(self.publish.call_args.args[1]["output_enable"])


class ReverseConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = RD60xxToMQTT("unused", 1883, default_update_period=0, set_clock_on_connection=False)
        if sys.platform == "win32":
            # Windows asyncio wraps sockets without ioctl. These loopback tests
            # exercise lifecycle handling; Linux CI also exercises socket tuning.
            keepalive = patch("async_modbus_reverse_tcp_client.platform.system", return_value="Test")
            keepalive.start()
            self.addCleanup(keepalive.stop)
        self.service._mqtt_client = Mock(publish=AsyncMock())
        self.clients = []
        self.writers = []
        self.workers = []
        self.background_tasks = []
        self.connected = asyncio.Queue()

        def on_connect(client, transport):
            self.connected.put_nowait(client)

        loop = asyncio.get_running_loop()
        self.server = await loop.create_server(
            lambda: RD60xx(on_connect, self.service.psu_disconnected, timeout=0.02, retries=0),
            "127.0.0.1", 0,
        )

    async def asyncTearDown(self):
        for client in self.clients:
            client.close()
        for writer in self.writers:
            writer.close()
            await writer.wait_closed()
        for worker in self.workers:
            worker.cancel()
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*(worker._psu_task for worker in self.workers),
                             *self.background_tasks, return_exceptions=True)
        self.server.close()
        await self.server.wait_closed()
        await asyncio.sleep(0)

    async def connect(self):
        _, writer = await asyncio.open_connection("127.0.0.1", self.server.sockets[0].getsockname()[1])
        client = await asyncio.wait_for(self.connected.get(), 1)
        self.clients.append(client)
        self.writers.append(writer)
        return client, writer

    def register(self, client):
        state = self.service._psu_states.get_state("60062_41798")
        state.update_period = 0
        worker = Bridge("127.0.0.1", 8080, "60062_41798", 60062, 41798,
                        client, state, self.service._mqtt_outbound)
        self.service._psus[worker.identity] = worker
        self.workers.append(worker)
        return worker

    async def wait_for_offline(self):
        for _ in range(100):
            messages = self.service._mqtt_client.publish.call_args_list
            if any(json.loads(call.kwargs["payload"]) == {"connected": False, "period": 0}
                   for call in messages):
                return
            await asyncio.sleep(0.001)
        self.fail("No MQTT offline state published")

    async def test_internal_pymodbus_close_removes_stale_bridge(self):
        client, _ = await self.connect()
        worker = self.register(client)
        # Reproduce pymodbus's internal close, bypassing the protocol callback.
        client.ctx.close()
        worker.queue_state_get()
        await asyncio.wait_for(asyncio.gather(worker._psu_task, return_exceptions=True), 1)
        await self.wait_for_offline()
        self.assertNotIn(worker.identity, self.service._psus)
        self.assertFalse(client.connected)

    async def test_explicit_close_notifies_once_even_after_connection_lost(self):
        client, _ = await self.connect()
        disconnected = Mock()
        client._client_disconnected_cb = disconnected
        client.close()
        client.close()
        client.connection_lost(None)
        await asyncio.sleep(0)
        disconnected.assert_called_once_with(client)
        self.assertFalse(client.connected)

    async def test_remote_disconnect_publishes_offline(self):
        client, writer = await self.connect()
        worker = self.register(client)
        writer.close()
        await writer.wait_closed()
        await self.wait_for_offline()
        self.assertNotIn(worker.identity, self.service._psus)
        await asyncio.wait_for(asyncio.gather(worker._psu_task, return_exceptions=True), 1)

    async def test_real_modbus_timeout_closes_and_drops_pending_commands(self):
        client, _ = await self.connect()
        worker = self.register(client)
        client.ctx.count_until_disconnect = -1
        client.set_state = AsyncMock()
        worker.queue_state_get()
        worker.queue_state_set({"output_enable": False})
        with self.assertLogs("Bridge", level="WARNING") as logs:
            await asyncio.wait_for(asyncio.gather(worker._psu_task, return_exceptions=True), 1)
        await self.wait_for_offline()
        self.assertEqual(len(logs.records), 1)
        client.set_state.assert_not_awaited()
        self.assertFalse(client.connected)
        self.assertNotIn(worker.identity, self.service._psus)

    async def test_cached_reconnect_preserves_identity_and_retires_old_worker(self):
        onboard = asyncio.create_task(self.service._psu_task())
        self.background_tasks.append(onboard)

        async def add_client(host, state):
            client, _ = await self.connect()
            client.get_state = AsyncMock(return_value=state)
            self.service._new_psu_queue.put_nowait((client, host, 8080))
            identity = f"{state.model}_{state.serial_no}"
            for _ in range(100):
                worker = self.service._psus.get(identity)
                if worker is not None and worker.client is client:
                    self.workers.append(worker)
                    return worker
                await asyncio.sleep(0.001)
            self.fail("PSU was not onboarded")

        old_worker = await add_client("first", sample_state())
        other_state = sample_state()
        other_state._model = 60125
        other_state._serial_no = 123
        other_worker = await add_client("second", other_state)
        new_worker = await add_client("first", sample_state())
        await asyncio.wait_for(asyncio.gather(old_worker._psu_task, return_exceptions=True), 1)
        self.assertFalse(old_worker.client.connected)
        self.assertEqual(new_worker.model, 60062)
        self.assertEqual(new_worker.serial_no, 41798)
        self.assertEqual(other_worker.model, 60125)
        self.assertEqual(other_worker.serial_no, 123)
        self.assertIs(self.service._psus[new_worker.identity], new_worker)

    async def test_old_disconnect_does_not_remove_reconnected_psu(self):
        old_client, _ = await self.connect()
        old_worker = self.register(old_client)
        new_client, _ = await self.connect()
        new_worker = self.register(new_client)
        old_client.close()
        await asyncio.sleep(0.01)
        self.assertIs(self.service._psus[new_worker.identity], new_worker)
        old_worker.cancel()
        new_client.close()
        await self.wait_for_offline()


if __name__ == "__main__":
    unittest.main()
