"""Async client for the Dolby/Doremi cinema server KLV API (TCP 11730)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .klv import (
    HEADER,
    HEADER_LEN,
    ID_LEN,
    KEY_LEN,
    ProtocolError,
    build_request,
    decode_ber,
    parse_payload,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 11730
DEFAULT_TIMEOUT = 10.0
# The server does not tolerate overlapping requests on one socket; every call is
# serialised behind a lock and the connection is torn down on any framing error.
MAX_FRAME = 8 * 1024 * 1024


class IMSError(Exception):
    """Base error."""


class IMSConnectionError(IMSError):
    """Socket could not be opened or was lost mid-exchange."""


class IMSCommandError(IMSError):
    """The server answered, but with a non-zero return code."""

    def __init__(self, command: str, return_code: int, payload: dict[str, Any]) -> None:
        super().__init__(f"{command} failed with return code {return_code}")
        self.command = command
        self.return_code = return_code
        self.payload = payload


class IMSClient:
    """Minimal, serialised KLV client.

    One TCP connection is held open and reused.  A lock guarantees that a
    request and its response are never interleaved with another exchange, which
    the protocol does not support (there is no correlation beyond ordering plus
    the echoed request id).
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0

    # -- connection ------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        if self.connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=self.timeout
            )
        except (OSError, asyncio.TimeoutError) as err:
            self._reader = self._writer = None
            raise IMSConnectionError(
                f"cannot connect to {self.host}:{self.port}: {err}"
            ) from err
        _LOGGER.debug("Connected to %s:%s", self.host, self.port)

    async def disconnect(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        if writer is None:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except (OSError, asyncio.TimeoutError):  # pragma: no cover - best effort
            pass

    def _next_id(self) -> int:
        self._request_id = (self._request_id + 1) % 60000
        return self._request_id

    # -- exchange --------------------------------------------------------

    async def command(self, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Send a request and return the parsed response.

        Raises IMSConnectionError on transport trouble and IMSCommandError when
        the server reports a non-zero return code.
        """
        check_rc = kwargs.pop("_check_return_code", True)
        async with self._lock:
            try:
                return await self._exchange(name, check_rc, *args, **kwargs)
            except (IMSConnectionError, ProtocolError):
                # A desynchronised stream can never recover; drop it so the next
                # call starts from a clean socket.
                await self.disconnect()
                raise

    async def _exchange(
        self, name: str, check_rc: bool, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        await self.connect()
        assert self._reader is not None and self._writer is not None

        frame = build_request(name, self._next_id(), *args, **kwargs)
        try:
            self._writer.write(frame)
            await asyncio.wait_for(self._writer.drain(), timeout=self.timeout)
            key, payload = await asyncio.wait_for(
                self._read_frame(), timeout=self.timeout
            )
        except asyncio.TimeoutError as err:
            raise IMSConnectionError(f"{name}: timed out after {self.timeout}s") from err
        except (OSError, asyncio.IncompleteReadError) as err:
            raise IMSConnectionError(f"{name}: connection lost: {err}") from err

        result = parse_payload(key, payload)
        rc = result.get("return_code")
        if check_rc and isinstance(rc, int) and rc != 0:
            raise IMSCommandError(name, rc, result)
        return result

    async def _read_frame(self) -> tuple[bytes, bytes]:
        assert self._reader is not None
        reader = self._reader

        header = await reader.readexactly(HEADER_LEN)
        if header != HEADER:
            raise ProtocolError(f"unexpected frame header {header.hex()}")

        key = await reader.readexactly(KEY_LEN)

        first = await reader.readexactly(1)
        if first[0] < 128:
            length = first[0]
        else:
            extra = await reader.readexactly(first[0] & 0x7F)
            length, _ = decode_ber(first + extra)

        if length < ID_LEN or length > MAX_FRAME:
            raise ProtocolError(f"implausible payload length {length}")

        body = await reader.readexactly(length)
        return key, body[ID_LEN:]

    # -- convenience wrappers -------------------------------------------

    async def status(self) -> dict[str, Any]:
        """Playback status.  Falls back to v1 if the server predates v2."""
        try:
            return await self.command("StatusSPL2", flags=0)
        except IMSCommandError:
            return await self.command("StatusSPL")

    async def product_info(self) -> dict[str, Any]:
        # GetProductInfo carries no trailing return code byte.
        return await self.command("GetProductInfo", _check_return_code=False)

    async def play(self) -> dict[str, Any]:
        return await self.command("PlaySPL")

    async def pause(self) -> dict[str, Any]:
        return await self.command("PauseSPL")

    async def scheduler_enabled(self) -> bool:
        result = await self.command("GetSchedulerEnable")
        return bool(result.get("enabled", False))

    async def set_scheduler(self, enable: bool) -> None:
        await self.command("SetSchedulerEnable", enable=enable)

    async def spl_list(self) -> list[str]:
        return list((await self.command("GetSPLList")).get("list", []))

    async def cpl_list(self) -> list[str]:
        return list((await self.command("GetCPLList")).get("list", []))

    async def kdm_list(self) -> list[str]:
        return list((await self.command("GetKDMList")).get("list", []))

    async def cpl_info(self, cpl_uuid: str) -> dict[str, Any]:
        try:
            return await self.command("GetCPLInfo2", uuid=cpl_uuid)
        except IMSCommandError:
            return await self.command("GetCPLInfo", uuid=cpl_uuid)

    async def schedule_spl(
        self,
        spl_id: str,
        when: str,
        duration: int = 0,
        flags: int = 0,
        annotation: str = "Home Assistant",
    ) -> int:
        """Queue an SPL.  ``when`` is an ISO-8601 timestamp string."""
        result = await self.command(
            "AddSchedule2",
            spl_id=spl_id,
            time=when,
            duration=duration,
            flags=flags,
            annotation_text=annotation,
        )
        return int(result.get("schedule_id", 0))
