#!/usr/bin/env python3
"""Offline self-test for the KLV codec.

No hardware and no Home Assistant required.  Builds frames, serves them back
through a loopback socket and checks they decode to the expected values.

    python3 tools/selftest.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bootstrap import load  # noqa: E402

klv, api = load()
IMSClient = api.IMSClient
IMSCommandError = api.IMSCommandError

FAILURES: list[str] = []


def check(label: str, got, want) -> None:
    if got == want:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}\n         got  {got!r}\n         want {want!r}")
        FAILURES.append(label)


def test_ber() -> None:
    print("BER length coding")
    for value in (0, 1, 126, 127, 128, 255, 256, 65535, 65536, 16777216):
        encoded = klv.encode_ber(value)
        decoded, consumed = klv.decode_ber(encoded)
        check(f"round-trip {value}", (decoded, consumed), (value, len(encoded)))
    check("127 is single byte", klv.encode_ber(127), b"\x7f")
    check("128 is long form", klv.encode_ber(128), b"\x81\x80")


def test_request_framing() -> None:
    print("\nrequest framing")
    frame = klv.build_request("PlaySPL", 1)
    check("header", frame[:13].hex(), "060e2b340205010a0e10010101")
    check("key", frame[13:16].hex(), "030b00")
    check("ber", frame[16], 4)
    check("request id", int.from_bytes(frame[17:21], "big"), 1)
    check("total length", len(frame), 21)

    cpl = "851cc838-022e-43b7-9fee-18656bdfc995"
    frame = klv.build_request("GetCPLInfo2", 7, uuid=cpl)
    check("uuid key", frame[13:16].hex(), "010301")
    check("ber covers id+uuid", frame[16], 20)
    check("uuid payload", frame[21:37], uuid.UUID(cpl).bytes)

    frame = klv.build_request("SetSchedulerEnable", 3, enable=True)
    check("bool payload", frame[-1:], b"\x01")

    # Fixed-width text must be NUL-padded on the right, not the left.
    frame = klv.build_request("GetLogLastId", 4, database="SM")
    check("text padded right", frame[21:29], b"SM\x00\x00\x00\x00\x00\x00")


def test_missing_parameter() -> None:
    print("\nerror handling")
    try:
        klv.build_request("GetCPLInfo2", 1)
    except klv.ProtocolError as err:
        check("missing arg raises", "missing parameter" in str(err), True)
    else:
        check("missing arg raises", False, True)

    try:
        klv.build_request("NoSuchCommand", 1)
    except klv.ProtocolError:
        check("unknown command raises", True, True)
    else:
        check("unknown command raises", False, True)


def build_status_payload() -> bytes:
    """Synthesise a plausible StatusSPL2 payload."""
    spl = uuid.uuid4()
    cpl = uuid.uuid4()
    event = uuid.uuid4()
    element = uuid.uuid4()
    body = bytearray()
    body += bytes([2])                       # playback_state = play
    body += spl.bytes                        # spl_id
    body += (1234).to_bytes(4, "big")        # playlist position
    body += (7200).to_bytes(4, "big")        # playlist duration
    body += cpl.bytes
    body += event.bytes
    body += element.bytes
    body += (600).to_bytes(4, "big")         # element position
    body += (900).to_bytes(4, "big")         # element duration
    body += (0).to_bytes(4, "big")           # flags
    body += (24).to_bytes(2, "big")          # edit rate num
    body += (1).to_bytes(2, "big")           # edit rate den
    body += (14400).to_bytes(4, "big")       # edit position
    body += (21600).to_bytes(4, "big")       # edit duration
    body += (1).to_bytes(2, "big")           # frames per edit
    body += (0).to_bytes(4, "big")           # kdm field
    body += bytes([0])                       # return code
    return bytes(body), str(spl), str(cpl)


def test_response_parsing() -> None:
    print("\nresponse parsing")
    payload, spl, cpl = build_status_payload()
    parsed = klv.parse_payload(bytes.fromhex("031c01"), payload)
    check("message name", parsed["_message"], "StatusSPL2")
    check("state decoded", parsed["playback_state_text"], "play")
    check("spl id", parsed["spl_id"], spl)
    check("cpl id", parsed["current_cpl_id"], cpl)
    check("position", parsed["show_playlist_position"], 1234)
    check("duration", parsed["show_playlist_duration"], 7200)
    check("edit rate num", parsed["current_element_edit_rate_num"], 24)
    check("return code", parsed["return_code"], 0)

    # Product info: fixed-width NUL-padded text must come back clean.
    body = bytearray()
    body += b"IMS3000".ljust(16, b"\x00")
    body += b"340406".ljust(16, b"\x00")
    body += uuid.uuid4().bytes
    body += bytes([3, 5, 20, 0, 4, 6, 10, 0])
    parsed = klv.parse_payload(bytes.fromhex("050200"), bytes(body))
    check("product name trimmed", parsed["product_name"], "IMS3000")
    check("serial trimmed", parsed["product_serial"], "340406")
    check("sw major", parsed["software_version_major"], 3)

    # A uuid list response.
    ids = [uuid.uuid4() for _ in range(3)]
    body = (
        (3).to_bytes(4, "big")
        + (16).to_bytes(4, "big")
        + b"".join(i.bytes for i in ids)
        + bytes([0])
    )
    parsed = klv.parse_payload(bytes.fromhex("030200"), body)
    check("list amount", parsed["amount"], 3)
    check("list contents", parsed["list"], [str(i) for i in ids])

    # An unknown key must degrade, not explode.
    parsed = klv.parse_payload(bytes.fromhex("ffffff"), b"\x01\x02")
    check("unknown key handled", parsed["_unknown_key"], "ffffff")


class FakeServer:
    """Answers one canned response per request, framed exactly like the real thing."""

    def __init__(self, response_key: bytes, payload: bytes) -> None:
        self.response_key = response_key
        self.payload = payload
        self.received: list[bytes] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header = await reader.readexactly(13)
                key = await reader.readexactly(3)
                first = await reader.readexactly(1)
                if first[0] < 128:
                    length = first[0]
                else:
                    extra = await reader.readexactly(first[0] & 0x7F)
                    length, _ = klv.decode_ber(first + extra)
                body = await reader.readexactly(length)
                self.received.append(header + key + first + body)

                req_id = body[:4]
                out = req_id + self.payload
                writer.write(
                    klv.HEADER + self.response_key + klv.encode_ber(len(out)) + out
                )
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()


async def test_client_roundtrip() -> None:
    print("\nclient round-trip over loopback")
    payload, spl, _ = build_status_payload()
    server = FakeServer(bytes.fromhex("031c01"), payload)
    port = await server.start()

    client = IMSClient("127.0.0.1", port, timeout=5)
    try:
        result = await client.status()
        check("status via client", result["spl_id"], spl)
        check("connection reused", client.connected, True)

        # Two calls on one socket must not desynchronise.
        again = await client.status()
        check("second call ok", again["playback_state_text"], "play")
        check("one frame per call", len(server.received), 2)

        ids = [
            int.from_bytes(frame[17:21], "big") for frame in server.received
        ]
        check("request ids advance", ids, [1, 2])
    finally:
        await client.disconnect()
        await server.stop()


async def test_error_return_code() -> None:
    print("\nnon-zero return code")
    server = FakeServer(bytes.fromhex("030c00"), bytes([7]))
    port = await server.start()
    client = IMSClient("127.0.0.1", port, timeout=5)
    try:
        await client.play()
    except IMSCommandError as err:
        check("raises IMSCommandError", err.return_code, 7)
    else:
        check("raises IMSCommandError", False, True)
    finally:
        await client.disconnect()
        await server.stop()


async def test_connection_failure() -> None:
    print("\nunreachable host")
    client = IMSClient("127.0.0.1", 1, timeout=2)
    try:
        await client.command("GetProductInfo")
    except Exception as err:  # noqa: BLE001
        check("raises IMSConnectionError", type(err).__name__, "IMSConnectionError")
    finally:
        await client.disconnect()


def test_definitions_consistent() -> None:
    print("\ndefinition table sanity")
    keys = [r.key for r in klv.REQUESTS]
    check("no duplicate request keys", len(keys), len(set(keys)))
    rkeys = [r.key for r in klv.RESPONSES]
    check("no duplicate response keys", len(rkeys), len(set(rkeys)))
    check("all keys are 3 bytes", all(len(k) == 3 for k in keys + rkeys), True)

    missing = [
        r.name for r in klv.REQUESTS if r.name not in klv.RESPONSE_BY_NAME
    ]
    check("every request has a response definition", missing, [])


def main() -> int:
    test_ber()
    test_request_framing()
    test_missing_parameter()
    test_response_parsing()
    test_definitions_consistent()
    asyncio.run(test_client_roundtrip())
    asyncio.run(test_error_return_code())
    asyncio.run(test_connection_failure())

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
