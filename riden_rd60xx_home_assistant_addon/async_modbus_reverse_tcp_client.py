from collections.abc import Callable
import asyncio
import logging
import platform
import socket

from pymodbus.client.base import ModbusBaseClient
from pymodbus.framer.base import FramerType
from pymodbus.framer.rtu import FramerRTU
from pymodbus.pdu import ModbusPDU
from pymodbus.transport import CommParams, CommType

class AsyncModbusReverseTcpClient(ModbusBaseClient, asyncio.Protocol):
    """**AsyncModbusTcpClient**.

    Fixed parameters:

    :param client_connected_cb: Callback on client connection (new client arriving)
    :param client_disconnected_cb: Callback for client disconnection (old client leaving)

    Common optional parameters:

    :param framer: Framer enum name
    :param timeout: Timeout for connecting and receiving data, in seconds (use decimals for milliseconds).
    :param retries: Max number of retries per request.
    :param trace_packet: Called with bytestream received/to be sent
    :param trace_pdu: Called with PDU received/to be sent

    Example::

        from reverse_tcp import AsyncModbusReverseTcpClient

        def modbus_client_connected(client, transport):

            # Handle new client connection
            peername = transport.get_extra_info('peername')
            print('Connection from {}'.format(peername))

        def modbus_client_disconnected(client):

            # Handle client disconnection
            print("Client disconnected")

        async def main():
            # Retrieve IOLoop
            loop = asyncio.get_running_loop()

            # Create TCP server, waiting for clients to connect
            server = await loop.create_server(
                lambda: AsyncModbusReverseTcpClient(modbus_client_connected, modbus_client_disconnected),
                '0.0.0.0', 8080
            )

            async with server:
                await server.serve_forever()

            asyncio.run(main())

    Please refer to :ref:`Pymodbus internals` for advanced usage.
    """

    # RTU allows at most 256 bytes per ADU. Allow a few duplicate/late replies,
    # but never keep parsing an unbounded stream for a single request.
    MAX_RTU_FRAME = 256
    MAX_RTU_RECEIVE = 4 * MAX_RTU_FRAME

    def __init__(
        self,
        client_connected_cb,
        client_disconnected_cb,
        framer: FramerType = FramerType.SOCKET,
        timeout: float = 1.0,
        retries: int = 3,
        trace_packet: Callable[[bool, bytes], bytes] | None = None,
        trace_pdu: Callable[[bool, ModbusPDU], ModbusPDU] | None = None,
    ) -> None:
        """Initialize Asyncio Modbus Reverse TCP Client."""

        # Store callbacks
        self._client_connected_cb = client_connected_cb
        self._client_disconnected_cb = client_disconnected_cb
        self._disconnect_notified = False
        self._logger = logging.getLogger("AsyncModbusReverseTcpClient")
        self._rtu = framer == FramerType.RTU
        self._rtu_buffer = bytearray()
        self._rtu_received = 0
        self._rtu_request = None
        self._user_trace_pdu = trace_pdu

        # Force comms type and clear reset delay, preventing "client" from attempting to reconnect on connection loss
        comm_params = CommParams(
            comm_type=CommType.TCP,
            reconnect_delay=0, # Clear reset delay, preventing "client" from attempting to reconnect on connection loss
            timeout_connect=timeout # Connection timeout (used for communication retries in this case)
        )

        # Init parents
        asyncio.Protocol.__init__(self)
        ModbusBaseClient.__init__(
            self,
            framer=framer,
            retries=retries,
            comm_params=comm_params,
            trace_packet=trace_packet,
            trace_pdu=self._trace_pdu if self._rtu else trace_pdu,
            trace_connect=None
        )

    def connection_made(self, transport):
        """Handle new connection"""

        # Update our conn_name and transaction manager with peer info
        host, port = transport.get_extra_info('peername')
        self.comm_params.comm_name = f"{host}:{port}"
        self.ctx.comm_params.comm_name = self.comm_params.comm_name

        # Provide transport to transaction manager
        self.ctx.transport = transport

        # Retrieve socket
        sock = transport.get_extra_info("socket")

        # Enable keepalive, to poke remote device periodically when idle
        if platform.system() == "Linux":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) # enable keepalive
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5) # secs idle time before starting probes
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2) # secs between probes
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 10) # number of probes

            # Set user timeout to handle device disconnect
            # This option limits how long transmitted data may remain unacknowledged
            # before the connection is closed
            # Set as recommended by cloudflare: https://blog.cloudflare.com/when-tcp-sockets-refuse-to-die
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, 15000) # ms

        elif platform.system() == "Darwin":
            TCP_KEEPALIVE = 0x10
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, 5)

        elif platform.system() == "Windows":
            # asyncio's TransportSocket has setsockopt, but no ioctl method.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 5)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 2)

        # Pass to transaction manager
        self.ctx.callback_connected()

        # Notify via callback
        self._client_connected_cb(self, transport)

    def data_received(self, data):
        """Assemble bounded RTU replies before invoking pymodbus's decoder.

        The upstream RTU garbage-recovery scan can do cubic work on arbitrary
        TCP input. Only pass complete, CRC-checked frames to it, so that it
        always succeeds at the first candidate. Bad streams are disconnected.
        """

        if not self.connected:
            return
        if not self._rtu:
            self.ctx.data_received(data)
            return
        if self._rtu_received + len(data) > self.MAX_RTU_RECEIVE:
            self._invalid_rtu("receive limit exceeded")
            return
        self._rtu_received += len(data)
        self._rtu_buffer.extend(data)

        while len(self._rtu_buffer) >= 2:
            function = self._rtu_buffer[1]
            if function in (3, 6, 16):
                if function == 3:
                    if len(self._rtu_buffer) < 3:
                        return
                    byte_count = self._rtu_buffer[2]
                    if not byte_count or byte_count % 2 or byte_count > 250:
                        self._invalid_rtu("invalid register byte count")
                        return
                    size = byte_count + 5
                else:
                    size = 8
            elif function in (0x83, 0x86, 0x90):
                size = 5
            else:
                self._invalid_rtu("unsupported response function")
                return
            if len(self._rtu_buffer) < size:
                return
            frame = bytes(self._rtu_buffer[:size])
            del self._rtu_buffer[:size]
            if not FramerRTU.check_CRC(frame[:-2], int.from_bytes(frame[-2:], "big")):
                self._invalid_rtu("CRC mismatch")
                return

            # Ignore bounded duplicate/late replies rather than allowing them to
            # satisfy another operation (pymodbus checks device ID, but not the
            # function, register count, or write acknowledgement).
            if self._matches_rtu_request(frame):
                self.ctx.callback_data(data=frame)

    def _trace_pdu(self, sending, pdu):
        """Track requests inside the transaction lock, including each retry."""

        if self._user_trace_pdu is not None:
            pdu = self._user_trace_pdu(sending, pdu)
        if sending:
            self._rtu_buffer.clear()
            self._rtu_received = 0
            self._rtu_request = (pdu.dev_id, pdu.function_code, pdu.address,
                                 pdu.count, tuple(pdu.registers))
        return pdu

    def _matches_rtu_request(self, frame):
        if self._rtu_request is None or self.ctx.response_future.done():
            return False
        device, function, address, count, registers = self._rtu_request
        if frame[0] != device:
            return False
        if frame[1] == (function | 0x80):
            return True
        if frame[1] != function:
            return False
        if function == 3:
            return frame[2] == count * 2
        if int.from_bytes(frame[2:4], "big") != address:
            return False
        value = int.from_bytes(frame[4:6], "big")
        return value == (registers[0] if function == 6 else count)

    def _invalid_rtu(self, reason):
        # close() is idempotent, and later transport callbacks are ignored. Log
        # one short message, without dumping untrusted input or flooding logs.
        self._logger.warning("Closing invalid RTU connection %s: %s",
                             self.comm_params.comm_name, reason)
        self.close()

    def connection_lost(self, exc):
        """Handle closed connection"""

        # Pass to transaction manager
        try:
            self.ctx.connection_lost(exc)
        finally:
            self._notify_disconnected()

    def close(self):
        """Close the socket and notify the service, even after an internal close."""

        try:
            super().close()
        finally:
            self._notify_disconnected()

    def _notify_disconnected(self):
        """Report disconnection once across explicit close and transport callbacks."""

        if not self._disconnect_notified:
            self._disconnect_notified = True
            self._rtu_buffer.clear()
            # Wake an in-flight probe immediately. Otherwise the onboarding
            # queue waits through all retries of an already closed socket.
            self.ctx.response_future.cancel()
            self._client_disconnected_cb(self)
