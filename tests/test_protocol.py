"""Tests for the KLV protocol layer: encoding, framing and hostile replies."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.dolby_ims3000 import klv
from custom_components.dolby_ims3000.api import (
    MAX_FRAME,
    IMSClient,
    IMSCommandError,
    IMSConnectionError,
    IMSInvalidRequest,
    IMSUnexpectedResponse,
)

from .const import CPL_ID


def _reply(name: str, body: bytes, request_id: int = 1) -> bytes:
    """A complete response frame as the server would send it."""
    payload = request_id.to_bytes(klv.ID_LEN, "big") + body
    key = klv.RESPONSE_BY_NAME[name].key
    return klv.HEADER + key + klv.encode_ber(len(payload)) + payload


class FakeServer:
    """Stands in for the TCP connection: queued bytes are what the server sends."""

    def __init__(self) -> None:
        self.writer = MagicMock()
        self.writer.is_closing.return_value = False
        self.writer.drain = AsyncMock()
        self.writer.wait_closed = AsyncMock()
        self._data = b""
        self._eof = False

    def feed(self, data: bytes, *, eof: bool = False) -> None:
        self._data += data
        self._eof = eof

    async def open_connection(self, *args: object, **kwargs: object) -> tuple:
        # Created here, inside the running loop, as the real call would.
        reader = asyncio.StreamReader()
        reader.feed_data(self._data)
        if self._eof:
            reader.feed_eof()
        return reader, self.writer


@pytest.fixture
def server() -> Generator[FakeServer]:
    """Replace the TCP connection with an in-memory one."""
    fake = FakeServer()
    with patch(
        "custom_components.dolby_ims3000.api.asyncio.open_connection",
        fake.open_connection,
    ):
        yield fake


def test_store_commands_are_not_in_the_table() -> None:
    """Commands that store content on the server cannot be built at all."""
    for name in ("StoreCPL", "StoreSPL"):
        assert name not in klv.REQUEST_BY_NAME
        with pytest.raises(klv.ProtocolError):
            klv.encode_request(name)


def test_read_only_list_matches_the_table() -> None:
    """Every read-only command exists, and no known write command is listed."""
    assert set(klv.REQUEST_BY_NAME) >= klv.READ_ONLY_REQUESTS
    writes = {
        "AddSchedule2",
        "DeleteCPL",
        "IngestAddJob",
        "PauseSPL",
        "PlaySPL",
        "SetSchedulerEnable",
    }
    assert writes == set(klv.REQUEST_BY_NAME) - klv.READ_ONLY_REQUESTS


def test_text_is_cut_on_a_character_boundary() -> None:
    """Fixed-width text never ends in half a multi-byte character."""
    encoded = klv.enc_text("é" * 100, size=9)
    assert len(encoded) == 9
    assert encoded.rstrip(b"\x00").decode("utf-8") == "éééé"


@pytest.mark.parametrize(
    ("name", "args", "kwargs"),
    [
        pytest.param("GetCPLInfo", (), {"uuid": "not-a-uuid"}, id="bad_uuid"),
        pytest.param("GetCPLInfo", (), {}, id="missing_parameter"),
        pytest.param("GetCPLList", (), {"extra": 1}, id="unexpected_parameter"),
        pytest.param("GetCPLList", (1,), {}, id="too_many_parameters"),
        pytest.param("GetScheduleInfo2", (-1,), {}, id="negative_number"),
        pytest.param("GetScheduleInfo2", (2**64,), {}, id="number_too_large"),
    ],
)
def test_bad_values_are_rejected(name: str, args: tuple, kwargs: dict) -> None:
    """Values that do not fit their field never produce a frame."""
    with pytest.raises(klv.ProtocolError):
        klv.encode_request(name, *args, **kwargs)


async def test_invalid_request_keeps_the_connection(
    server: FakeServer,
) -> None:
    """Bad input is the caller's mistake: nothing is sent, nothing is dropped."""
    client = IMSClient("10.0.0.50")
    await client.connect()

    with pytest.raises(IMSInvalidRequest):
        await client.command("GetCPLInfo", uuid="not-a-uuid")

    assert client.connected
    server.writer.write.assert_not_called()


async def test_command_round_trip(server: FakeServer) -> None:
    """A normal reply is parsed and the request carries the right key."""
    server.feed(_reply("GetSchedulerEnable", b"\x01\x00"))
    client = IMSClient("10.0.0.50")

    result = await client.command("GetSchedulerEnable")

    assert result["enabled"] is True
    sent = server.writer.write.call_args.args[0]
    assert sent.startswith(klv.HEADER + klv.REQUEST_BY_NAME["GetSchedulerEnable"].key)


async def test_non_zero_return_code(server: FakeServer) -> None:
    """A non-zero return code is a command error, not a lost connection."""
    server.feed(_reply("GetSchedulerEnable", b"\x00\x05"))
    client = IMSClient("10.0.0.50")

    with pytest.raises(IMSCommandError) as err:
        await client.command("GetSchedulerEnable")

    assert err.value.return_code == 5
    assert client.connected


async def test_reply_to_another_command(server: FakeServer) -> None:
    """A reply of the wrong type is refused, but the stream stays usable."""
    server.feed(_reply("GetTimeZone", b"Europe/Amsterdam\x00"))
    client = IMSClient("10.0.0.50")

    with pytest.raises(IMSUnexpectedResponse):
        await client.command("GetSchedulerEnable")

    assert client.connected


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(b"HTTP/1.1 200 OK\r\n" + b"\x00" * 32, id="not_klv"),
        pytest.param(
            klv.HEADER
            + klv.RESPONSE_BY_NAME["GetCPLList"].key
            + klv.encode_ber(MAX_FRAME + 1),
            id="oversized_length",
        ),
        pytest.param(
            klv.HEADER + klv.RESPONSE_BY_NAME["GetCPLList"].key + klv.encode_ber(2) + b"\x00\x00",
            id="too_short_for_request_id",
        ),
    ],
)
async def test_malformed_frames_drop_the_connection(
    server: FakeServer, frame: bytes
) -> None:
    """Garbage or an absurd length is never read into memory or trusted."""
    server.feed(frame)
    client = IMSClient("10.0.0.50")

    with pytest.raises(klv.ProtocolError):
        await client.command("GetCPLList")

    assert not client.connected


async def test_truncated_reply(server: FakeServer) -> None:
    """A reply that stops halfway is a lost connection."""
    server.feed(_reply("GetCPLList", b"\x00" * 40)[:-10], eof=True)
    client = IMSClient("10.0.0.50")

    with pytest.raises(IMSConnectionError):
        await client.command("GetCPLList")

    assert not client.connected


async def test_silent_server_times_out(server: FakeServer) -> None:
    """A server that accepts the request and then says nothing cannot hang the poll."""
    client = IMSClient("10.0.0.50", timeout=0.05)

    with pytest.raises(IMSConnectionError):
        await client.command("GetCPLList")

    assert not client.connected


async def test_short_reply_reports_no_fake_values(
    server: FakeServer,
) -> None:
    """Fields missing from a short reply are left out instead of read as zero."""
    server.feed(_reply("GetCPLInfo", bytes.fromhex(CPL_ID.replace("-", ""))))
    client = IMSClient("10.0.0.50")

    result = await client.command("GetCPLInfo", _check_return_code=False, uuid=CPL_ID)

    assert result["cpl_uuid"] == CPL_ID
    assert "content_title_text" not in result
