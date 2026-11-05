"""Real loopback RTU/TCP traffic; no PSU, MQTT broker or Home Assistant needed."""

import asyncio
import json
from pathlib import Path
import struct
import subprocess
import sys
import textwrap
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

APP = Path(__file__).resolve().parents[1] / "riden_rd60xx_home_assistant_addon"
sys.path.insert(0, str(APP))

from pymodbus.framer.base import FramerType
from pymodbus.exceptions import ModbusException

from async_modbus_reverse_tcp_client import AsyncModbusReverseTcpClient
from rd60xx import RD60xx, RD60xxStateSet
from rd60xx_to_mqtt import RD60xxToMQTT


def adu(payload):
    # Independent, straightforward Modbus CRC for the wire-level PSU simulator.
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return payload + crc.to_bytes(2, "little")


def read_reply(values, device=1):
    return adu(bytes([device, 3, len(values) * 2]) + struct.pack(f">{len(values)}H", *values))


class SimulatedPSU:
    """A stock-style PSU that dials out and responds to real RTU requests."""

    def __init__(self, reader, writer, device=1, fragmented=False):
        self.reader, self.writer = reader, writer
        self.device, self.fragmented = device, fragmented
        self.registers = {
            0: 60062, 1: 0, 2: 41798, 3: 142,
            8: 420, 9: 1000, 10: 420, 11: 100,
            18: 1, 80: 420, 81: 1000, 82: 430, 83: 1100,
        }
        self.requests = []

    async def run(self):
        try:
            while True:
                header = await self.reader.readexactly(2)
                device, function = header
                if function in (3, 6):
                    packet = header + await self.reader.readexactly(6)
                elif function == 16:
                    header += await self.reader.readexactly(5)
                    packet = header + await self.reader.readexactly(header[6] + 2)
                else:
                    raise AssertionError(f"Unexpected request {function}")
                if adu(packet[:-2]) != packet:
                    raise AssertionError("Invalid request CRC")
                self.requests.append(packet)
                if device != self.device:
                    continue
                address, value = struct.unpack(">HH", packet[2:6])
                if function == 3:
                    reply = read_reply([self.registers.get(address + i, 0) for i in range(value)], device)
                elif function == 6:
                    self.registers[address] = value
                    reply = packet
                else:
                    values = struct.unpack(f">{value}H", packet[7:-2])
                    self.registers.update({address + i: v for i, v in enumerate(values)})
                    reply = adu(packet[:6])
                if self.fragmented:
                    for chunk in (reply[:1], reply[1:3], reply[3:]):
                        self.writer.write(chunk)
                        await self.writer.drain()
                        await asyncio.sleep(0.001)
                else:
                    self.writer.write(reply)
                    await self.writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass


class RTUProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = None
        self.clients, self.writers, self.tasks = [], [], []
        self.arrivals = asyncio.Queue()
        self.framer = FramerType.RTU
        self.trace_pdu = None

        def connected(client, transport):
            self.arrivals.put_nowait(client)
            if self.service is not None:
                self.service.psu_connected(client, transport)

        def disconnected(client):
            if self.service is not None:
                self.service.psu_disconnected(client)

        def factory():
            if self.framer == FramerType.RTU:
                return RD60xx(connected, disconnected, timeout=0.1, retries=0, trace_pdu=self.trace_pdu)
            return AsyncModbusReverseTcpClient(connected, disconnected, timeout=0.1, retries=0)

        self.server = await asyncio.get_running_loop().create_server(factory, "127.0.0.1", 0)

    async def asyncTearDown(self):
        for client in self.clients:
            client.close()
        if self.service is not None:
            self.tasks.extend(worker._psu_task for worker in list(self.service._psus.values()))
        for writer in self.writers:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.server.close()
        await self.server.wait_closed()
        await asyncio.sleep(0)

    async def connect(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.server.sockets[0].getsockname()[1])
        client = await asyncio.wait_for(self.arrivals.get(), 1)
        self.clients.append(client)
        self.writers.append(writer)
        return client, reader, writer

    def start(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.append(task)
        return task

    async def start_read(self, count=1):
        client, reader, writer = await self.connect()
        request = self.start(client.read_holding_registers(device_id=1, address=0, count=count))
        await asyncio.wait_for(reader.readexactly(8), 1)
        return client, reader, writer, request

    async def send(self, writer, data):
        writer.write(data)
        await writer.drain()
        # Ensure a data_received callback between intentional TCP fragments.
        await asyncio.sleep(0.002)

    async def until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 1)

    def start_service(self, addresses=None):
        self.service = RD60xxToMQTT("unused", 1883, psu_addresses=addresses,
                                   set_clock_on_connection=False, default_update_period=0)
        self.service._mqtt_client = Mock(publish=AsyncMock())
        self.start(self.service._psu_task())

    async def simulated_psu(self, device=1, fragmented=False):
        client, reader, writer = await self.connect()
        simulator = SimulatedPSU(reader, writer, device, fragmented)
        self.start(simulator.run())
        return client, simulator

    async def test_every_two_part_split_of_a_valid_reply(self):
        client, reader, writer = await self.connect()
        frame = read_reply([60062, 41798])
        for split in range(1, len(frame)):
            with self.subTest(split=split):
                task = self.start(client.read_holding_registers(device_id=1, address=0, count=2))
                await asyncio.wait_for(reader.readexactly(8), 1)
                await self.send(writer, frame[:split])
                self.assertFalse(task.done())
                await self.send(writer, frame[split:])
                self.assertEqual((await asyncio.wait_for(task, 1)).registers, [60062, 41798])

    async def test_largest_read_reply_fragmented_one_byte_at_a_time(self):
        client, _, _, task = await self.start_read(125)
        frame = read_reply(list(range(125)))
        for byte in frame:
            client.data_received(bytes([byte]))
        self.assertEqual((await asyncio.wait_for(task, 1)).registers, list(range(125)))

    async def test_invalid_streams_close_once_without_parser_scan(self):
        good = read_reply([1])
        streams = [b"GET / HTTP/1.1\r\n\r\n", b"\x16\x03\x03\x00",
                   (b"\x01\x03\x00" * 171)[:512], b"\0" * 1024,
                   b"\x01\x03\xfc", b"\x01\x03\x01", b"\x01\x03\x00",
                   good[:-1] + bytes([good[-1] ^ 1]), b"\x01\x7f"]
        for data in streams:
            with self.subTest(size=len(data), header=data[:3]):
                client, _, _ = await self.connect()
                disconnected = Mock()
                client._client_disconnected_cb = disconnected
                with self.assertLogs("AsyncModbusReverseTcpClient", level="WARNING") as logs, \
                        patch.object(client.ctx, "callback_data") as decode:
                    client.data_received(data)
                    client.data_received(data)
                self.assertFalse(client.connected)
                disconnected.assert_called_once_with(client)
                decode.assert_not_called()
                self.assertEqual(len(logs.records), 1)

    async def test_unexpected_frames_do_not_satisfy_a_read(self):
        _, _, writer, task = await self.start_read(2)
        wrong = read_reply([1, 2], device=2) + adu(b"\x01\x06\x00\x00\x00\x01") + read_reply([1])
        await self.send(writer, wrong)
        self.assertFalse(task.done())
        await self.send(writer, read_reply([60062, 41798]))
        self.assertEqual((await asyncio.wait_for(task, 1)).registers, [60062, 41798])

    async def test_write_acknowledgements_match_address_value_and_count(self):
        client, reader, writer = await self.connect()
        single = self.start(client.write_register(device_id=1, address=18, value=1))
        await asyncio.wait_for(reader.readexactly(8), 1)
        await self.send(writer, adu(b"\x01\x06\x00\x13\x00\x01") + adu(b"\x01\x06\x00\x12\x00\x00"))
        self.assertFalse(single.done())
        await self.send(writer, adu(b"\x01\x06\x00\x12\x00\x01"))
        self.assertEqual((await asyncio.wait_for(single, 1)).registers, [1])

        multiple = self.start(client.write_registers(device_id=1, address=80, values=[420, 1000]))
        await asyncio.wait_for(reader.readexactly(13), 1)
        await self.send(writer, adu(b"\x01\x10\x00\x51\x00\x02") + adu(b"\x01\x10\x00\x50\x00\x01"))
        self.assertFalse(multiple.done())
        await self.send(writer, adu(b"\x01\x10\x00\x50\x00\x02"))
        self.assertEqual((await asyncio.wait_for(multiple, 1)).count, 2)

    async def test_modbus_exception_reply_reaches_caller(self):
        _, _, writer, task = await self.start_read()
        await self.send(writer, adu(b"\x01\x83\x02"))
        response = await asyncio.wait_for(task, 1)
        self.assertTrue(response.isError())
        self.assertEqual(response.exception_code, 2)

    async def test_coalesced_duplicates_do_not_leak_into_next_request(self):
        client, reader, writer, task = await self.start_read()
        await self.send(writer, read_reply([11]) * 3)
        self.assertEqual((await asyncio.wait_for(task, 1)).registers, [11])
        next_task = self.start(client.read_holding_registers(device_id=1, address=0, count=1))
        await asyncio.wait_for(reader.readexactly(8), 1)
        self.assertFalse(next_task.done())
        await self.send(writer, read_reply([22]))
        self.assertEqual((await asyncio.wait_for(next_task, 1)).registers, [22])

    async def test_valid_junk_is_limited_even_across_callbacks(self):
        client, _, _, task = await self.start_read()
        with self.assertLogs("AsyncModbusReverseTcpClient", level="WARNING") as logs:
            for _ in range(200):
                client.data_received(read_reply([1], device=2))
        self.assertFalse(client.connected)
        self.assertEqual(len(logs.records), 1)
        self.assertLessEqual(len(client._rtu_buffer), client.MAX_RTU_RECEIVE)
        self.assertFalse(task.done())

    async def test_partial_frame_is_discarded_before_a_retry(self):
        client, reader, writer = await self.connect()
        client.ctx.retries = 1
        task = self.start(client.read_holding_registers(device_id=1, address=0, count=1))
        await asyncio.wait_for(reader.readexactly(8), 1)
        await self.send(writer, read_reply([11])[:3])
        await asyncio.wait_for(reader.readexactly(8), 1)
        await self.send(writer, read_reply([22]))
        response = await asyncio.wait_for(task, 1)
        self.assertEqual(response.registers, [22])
        self.assertEqual(response.retries, 1)

    async def test_remote_disconnect_wakes_pending_request_without_retries(self):
        client, _, writer, task = await self.start_read()
        client.ctx.retries = 3
        client.ctx.comm_params.timeout_connect = 10
        writer.close()
        await writer.wait_closed()
        with self.assertRaises(ModbusException):
            await asyncio.wait_for(task, 0.5)
        self.assertFalse(client.connected)

    async def test_generic_modbus_tcp_still_buffers_fragments(self):
        self.framer = FramerType.SOCKET
        client, reader, writer = await self.connect()
        task = self.start(client.read_holding_registers(device_id=1, address=0, count=1))
        request = await asyncio.wait_for(reader.readexactly(12), 1)
        response = request[:2] + b"\x00\x00\x00\x05\x01\x03\x02\xea\x9e"
        await self.send(writer, response[:5])
        self.assertFalse(task.done())
        await self.send(writer, response[5:])
        self.assertEqual((await asyncio.wait_for(task, 1)).registers, [60062])

    async def test_trace_hooks_preserved(self):
        self.trace_pdu = Mock(side_effect=lambda sending, pdu: pdu)
        _, _, writer, task = await self.start_read()
        await self.send(writer, read_reply([60062]))
        self.assertEqual((await asyncio.wait_for(task, 1)).registers, [60062])
        self.assertEqual([c.args[0] for c in self.trace_pdu.call_args_list], [True, False])

    async def test_simulated_psu_reads_writes_clock_and_fragmented_replies(self):
        client, psu = await self.simulated_psu(fragmented=True)
        state = await asyncio.wait_for(client.get_state(), 1)
        self.assertEqual((state.model, state.serial_no, state.firmware_version), (60062, 41798, "1.42"))
        self.assertEqual(len(state.presets), 9)
        await asyncio.wait_for(client.set_clock(2026, 11, 5, 12, 30, 0), 1)
        self.assertEqual([psu.registers[i] for i in range(48, 54)], [2026, 11, 5, 12, 30, 0])
        await asyncio.wait_for(client.set_state(RD60xxStateSet(output_enable=False,
                               output_voltage_set=4.1, output_current_set=0.5, ovp=4.3, ocp=0.6)), 1)
        regs = RD60xx.RD60xxRegisters
        self.assertEqual(psu.registers[regs.OUTPUT_ENABLE.value], 0)
        self.assertEqual(psu.registers[regs.M0_V.value], 410)
        self.assertEqual(psu.registers[regs.M0_C.value], 500)
        state = await asyncio.wait_for(client.get_state(), 1)
        self.assertFalse(state.output_enable)
        self.assertAlmostEqual(state.ovp, 4.3)
        self.assertAlmostEqual(state.ocp, 0.6)

    async def test_service_reconnect_remembers_nondefault_modbus_address(self):
        self.start_service([1, 7])
        first, _ = await self.simulated_psu(device=7)
        identity = "60062_41798"
        await self.until(lambda: identity in self.service._psus)
        old = self.service._psus[identity]
        second, _ = await self.simulated_psu(device=7)
        await self.until(lambda: self.service._psus.get(identity) is not old)
        self.assertFalse(first.connected)
        self.assertEqual(second.PSU_ADDR, 7)
        state = await asyncio.wait_for(second.get_state(), 1)
        self.assertEqual(state.serial_no, 41798)
        self.assertIs(self.service._psus[identity].client, second)

    async def test_decimal_setpoints_round_to_the_correct_register(self):
        client, psu = await self.simulated_psu()
        regs = RD60xx.RD60xxRegisters
        for state in [RD60xxStateSet(output_voltage_set=4.1),
                      RD60xxStateSet(output_current_set=1.001),
                      RD60xxStateSet(ovp=4.1, ocp=1.001)]:
            await asyncio.wait_for(client.set_state(state), 1)
        self.assertEqual(psu.registers[regs.M0_V.value], 410)
        self.assertEqual(psu.registers[regs.M0_C.value], 1001)
        self.assertEqual(psu.registers[regs.M0_OVP.value], 410)
        self.assertEqual(psu.registers[regs.M0_OCP.value], 1001)

    async def test_invalid_client_does_not_prevent_healthy_onboarding(self):
        self.start_service()
        _, _, bad_writer = await self.connect()
        with self.assertLogs("AsyncModbusReverseTcpClient", level="WARNING"):
            await self.send(bad_writer, (b"\x01\x03\x00" * 171)[:512])
        healthy, _ = await self.simulated_psu(fragmented=True)
        await self.until(lambda: "60062_41798" in self.service._psus)
        self.assertIs(self.service._psus["60062_41798"].client, healthy)
        await self.until(lambda: any('"connected": true' in call.kwargs.get("payload", "")
                                    for call in self.service._mqtt_client.publish.call_args_list))

    async def test_invalid_client_does_not_stall_an_existing_psu_or_timers(self):
        healthy, _ = await self.simulated_psu()
        _, _, bad_writer = await self.connect()
        timer = asyncio.get_running_loop().create_future()
        started = time.perf_counter()
        asyncio.get_running_loop().call_later(0.02, lambda: timer.set_result(time.perf_counter() - started))
        with self.assertLogs("AsyncModbusReverseTcpClient", level="WARNING"):
            await self.send(bad_writer, (b"\x01\x03\x00" * 171)[:512])
        state = await asyncio.wait_for(healthy.get_state(), 0.5)
        self.assertEqual(state.model, 60062)
        self.assertLess(await asyncio.wait_for(timer, 0.5), 0.25)

    async def test_disconnected_queued_clients_are_not_probed(self):
        self.start_service()
        dead = Mock(connected=False, get_state=AsyncMock())
        self.service._new_psu_queue.put_nowait((dead, "dead", 8080))
        await self.until(self.service._new_psu_queue.empty)
        dead.get_state.assert_not_awaited()
        self.assertFalse(self.service._psus)

    async def test_disconnect_during_discovery_does_not_create_a_stale_bridge(self):
        entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def discovery(*args):
            entered.set()
            await release.wait()
            finished.set()

        self.start_service()
        self.service._mqtt_discovery_enabled = True
        with patch("rd60xx_to_mqtt.publish_discovery_config", side_effect=discovery):
            client, _ = await self.simulated_psu()
            await asyncio.wait_for(entered.wait(), 1)
            client.close()
            release.set()
            await asyncio.wait_for(finished.wait(), 1)
            self.assertFalse(self.service._psus)
            self.assertFalse(self.tasks[0].done())


class CPURegressionTests(unittest.TestCase):
    def test_large_hostile_payloads_are_bounded_in_a_killable_process(self):
        # An asyncio timeout cannot stop a decoder blocking the event loop.
        # A separate process prevents an old/regressed parser from hanging CI.
        script = textwrap.dedent("""
            import asyncio, json, logging, random, sys, time
            from unittest.mock import Mock
            sys.path.insert(0, sys.argv[1])
            from rd60xx import RD60xx
            async def run():
                elapsed = []
                for size in (512, 1024, 16384, 1048576):
                    client = RD60xx(lambda *_: None, lambda *_: None)
                    client.ctx.transport = Mock()
                    payload = (b'\\x01\\x03\\x00' * ((size + 2) // 3))[:size]
                    start = time.process_time()
                    client.data_received(payload)
                    elapsed.append(time.process_time() - start)
                    assert not client.connected
                logging.disable(logging.CRITICAL)
                rng = random.Random(7)
                started = time.process_time()
                for _ in range(256):
                    size = rng.choice((0, 1, 2, 3, 5, 8, 16, 64, 128, 255, 256, 512, 1024, 1025))
                    client = RD60xx(lambda *_: None, lambda *_: None)
                    client.ctx.transport = Mock()
                    payload = rng.randbytes(size)
                    for offset in range(0, size, 7):
                        client.data_received(payload[offset:offset + 7])
                    assert len(client._rtu_buffer) <= client.MAX_RTU_RECEIVE
                    client.close()
                print(json.dumps({'hostile': elapsed, 'fuzz_cpu': time.process_time() - started}))
            asyncio.run(run())
        """)
        result = subprocess.run([sys.executable, "-B", "-c", script, str(APP)],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        timings = json.loads(result.stdout)
        self.assertLess(max(timings["hostile"]), 0.25)
        self.assertLess(timings["fuzz_cpu"], 1)


if __name__ == "__main__":
    unittest.main()
