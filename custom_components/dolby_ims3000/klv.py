"""KLV codec for the Dolby/Doremi digital cinema server intra-theatre protocol.

Dependency-free.  Implements the SMPTE-style fixed-length-pack KLV framing used
by Doremi (now Dolby) cinema servers on TCP 11730.

Frame layout, both directions::

    +----------------+--------+-----------+--------------+-------------+
    | 13-byte header | 3b key | BER len   | 4b request id| payload ... |
    +----------------+--------+-----------+--------------+-------------+
                              \\________ BER covers id + payload ______/

Protocol knowledge derived from ronhanson/python-dcitools (MIT).  Three
divergences from that reference are deliberate and marked ``FIXED:`` below.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable, NamedTuple

# 06 0E 2B 34  SMPTE universal label
# 02 05 01 0A  fixed-length pack, set/pack dictionary, registry v10
# 0E 10        organizationally registered / Doremi Labs, Inc
# 01 01 01     DCP-2000 messages, version 1, intra-theatre message packs
HEADER = bytes.fromhex("060E2B340205010A0E10010101")
HEADER_LEN = len(HEADER)
KEY_LEN = 3
ID_LEN = 4


class ProtocolError(Exception):
    """Raised when a frame cannot be encoded or decoded."""


# --------------------------------------------------------------------------
# BER length
# --------------------------------------------------------------------------

def encode_ber(value: int) -> bytes:
    """Encode an integer as a BER length field."""
    if value < 0:
        raise ProtocolError("BER length cannot be negative")
    if value < 128:
        return bytes([value])
    payload = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(payload)]) + payload


def decode_ber(data: bytes) -> tuple[int, int]:
    """Decode a BER length.  Returns ``(length, bytes_consumed)``."""
    if not data:
        raise ProtocolError("empty BER field")
    first = data[0]
    if first < 128:
        return first, 1
    n = first & 0x7F
    if len(data) < 1 + n:
        raise ProtocolError("truncated BER field")
    return int.from_bytes(data[1 : 1 + n], "big"), 1 + n


# --------------------------------------------------------------------------
# Scalar encoders (host -> server)
# --------------------------------------------------------------------------

def enc_uuid(value: Any) -> bytes:
    if isinstance(value, uuid.UUID):
        return value.bytes
    return uuid.UUID(str(value)).bytes


def enc_text(value: Any, size: int | None = None) -> bytes:
    raw = str(value).encode("utf-8")
    if size is not None:
        if len(raw) > size:
            raw = raw[:size]
        # FIXED: dcitools right-justifies (pads on the *left* with NULs), which
        # produces a leading-NUL string no server would parse.  Fixed-width text
        # fields in this protocol are NUL-terminated / NUL-padded on the right.
        raw = raw.ljust(size, b"\x00")
    return raw


def enc_int(value: Any, bit: int = 32) -> bytes:
    return int(value).to_bytes(bit // 8, "big")


def enc_bool(value: Any) -> bytes:
    return b"\x01" if value else b"\x00"


# --------------------------------------------------------------------------
# Scalar decoders (server -> host)
# --------------------------------------------------------------------------

def dec_int(raw: bytes) -> int:
    return int.from_bytes(bytes(raw), "big")


def dec_bool(raw: bytes) -> bool:
    return bool(raw and raw[0])


def dec_text(raw: bytes) -> str:
    """Decode a fixed-width text field, dropping NUL padding."""
    return bytes(raw).split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()


def dec_uuid(raw: bytes) -> str:
    raw = bytes(raw)
    if len(raw) != 16 or raw == b"\x00" * 16:
        return ""
    return str(uuid.UUID(bytes=raw))


def dec_uuid_list(raw: bytes) -> list[str]:
    raw = bytes(raw)
    out: list[str] = []
    for i in range(len(raw) // 16):
        chunk = raw[i * 16 : (i + 1) * 16]
        if chunk == b"\x00" * 16:
            continue
        out.append(str(uuid.UUID(bytes=chunk)))
    return out


# --------------------------------------------------------------------------
# Message definitions
# --------------------------------------------------------------------------

class Arg(NamedTuple):
    """One argument of an outbound request."""

    name: str
    encode: Callable[..., bytes]
    kwargs: dict[str, Any] = {}


class Field(NamedTuple):
    """One field of an inbound response, addressed by byte slice."""

    name: str
    start: int
    end: int | None
    decode: Callable[[bytes], Any]
    lookup: dict[int, str] | None = None


class Request(NamedTuple):
    name: str
    key: bytes
    args: tuple[Arg, ...] = ()


class Response(NamedTuple):
    name: str
    key: bytes
    fields: tuple[Field, ...] = ()


def _k(hex_key: str) -> bytes:
    return bytes.fromhex(hex_key)


CONTENT_KIND = {
    0: "Unknown", 1: "Feature", 2: "Trailer", 3: "Test", 4: "Teaser",
    5: "Rating", 6: "Advertisement", 7: "Short", 8: "Transitional",
    9: "PSA", 10: "Policy", 128: "Live CPL",
}
STORAGE = {1: "local", 2: "remote", 3: "local+remote"}
ENCODING = {0: "Unknown", 1: "MPEG2", 2: "JPEG2000", 3: "Audio PCM"}
ENCRYPTION = {0: "No Encryption", 1: "AES 128 CBC"}
PLAYBACK_STATE = {0: "unknown", 1: "stop", 2: "play", 3: "pause"}
SCHEDULE_STATUS = {
    0: "recorded", 1: "success", 2: "failed",
    3: "failed because a show was running",
}
INGEST_STATUS = {
    0: "pending", 1: "paused", 2: "running", 3: "scheduled",
    4: "success", 5: "aborted", 6: "unused", 7: "failed",
}
CPL_VALIDATION = {
    0: "No error nor warning",
    1: "CPL is not registered on this server",
    2: "CPL is partially registered on this server",
    3: "CPL is registered on this server but cannot be loaded",
    4: "CPL requires a KDM to play; no KDM found",
    5: "CPL requires a KDM to play; out-dated KDM found",
    6: "CPL requires a KDM to play; KDM built with a wrong certificate",
    7: "CPL requires a KDM to play; all KDM rejected (RTC no longer secured)",
    8: "CPL requires a KDM to play; all KDM rejected (protected playback forbidden)",
    9: "CPL requires a KDM to play; KDM with invalid content authenticator",
    10: "CPL signature check failed",
    255: "Out of memory",
}

RC_TRAILER = Field("return_code", -1, None, dec_int)

REQUESTS: tuple[Request, ...] = (
    # ---- CPL ----
    Request("GetCPLList", _k("010100")),
    Request("GetCPLInfo", _k("010300"), (Arg("uuid", enc_uuid),)),
    Request("GetCPLInfo2", _k("010301"), (Arg("uuid", enc_uuid),)),
    Request("DeleteCPL", _k("010500"), (Arg("uuid", enc_uuid),)),
    Request("RetrieveCPL", _k("010700"), (Arg("uuid", enc_uuid),)),
    Request("StoreCPL", _k("010900"), (Arg("xml", enc_text),)),
    Request("ValidateCPL", _k("010B00"), (
        Arg("uuid", enc_uuid),
        Arg("time", enc_text, {"size": 32}),
        Arg("level", enc_int, {"bit": 32}),
    )),
    Request("GetCPLSize", _k("010D00"), (Arg("uuid", enc_uuid),)),
    Request("GetCPLMarker", _k("010F00"), (Arg("uuid", enc_uuid),)),
    Request("GetCPLPlayStat", _k("011100"), (Arg("uuid", enc_uuid),)),
    # ---- KDM ----
    Request("GetKDMList", _k("020100")),
    Request("GetKDMInfo", _k("020300"), (Arg("uuid", enc_uuid),)),
    Request("GetKDMInfo2", _k("020301"), (Arg("uuid", enc_uuid),)),
    # ---- SPL / playback ----
    Request("GetSPLList", _k("030100")),
    Request("StoreSPL", _k("031F00"), (Arg("xml", enc_text),)),
    Request("ValidateSPL", _k("032500"), (
        Arg("uuid", enc_uuid),
        Arg("time", enc_text, {"size": 32}),
        Arg("level", enc_int, {"bit": 32}),
    )),
    Request("StatusSPL", _k("031B00")),
    Request("StatusSPL2", _k("031B01"), (Arg("flags", enc_int, {"bit": 32}),)),
    Request("PlaySPL", _k("030B00")),
    Request("PauseSPL", _k("030D00")),
    # ---- Scheduler ----
    # FIXED: dcitools passes size= to int encoders that only accept bit=, so
    # AddSchedule2/GetScheduleInfo2 could never have been executed as written.
    # duration is a 32-bit count, flags a 64-bit mask, schedule ids are uint64.
    Request("AddSchedule2", _k("040101"), (
        Arg("spl_id", enc_uuid),
        Arg("time", enc_text, {"size": 32}),
        Arg("duration", enc_int, {"bit": 32}),
        Arg("flags", enc_int, {"bit": 64}),
        Arg("annotation_text", enc_text, {"size": 128}),
    )),
    Request("GetScheduleInfo2", _k("040701"), (Arg("id", enc_int, {"bit": 64}),)),
    Request("GetCurrentSchedule", _k("040900")),
    Request("GetNextSchedule", _k("040B00")),
    Request("SetSchedulerEnable", _k("040D00"), (Arg("enable", enc_bool),)),
    Request("GetSchedulerEnable", _k("040F00")),
    # ---- Product / misc ----
    Request("GetProductInfo", _k("050100")),
    Request("GetProductCertificate", _k("050300"), (Arg("type", enc_int, {"bit": 8}),)),
    Request("GetAPIProtocolVersion", _k("050500")),
    Request("GetTimeZone", _k("051F00")),
    Request("WhoAmI", _k("0E0B00")),
    # ---- Logs / ingest ----
    Request("GetLog", _k("110100"), (
        Arg("database", enc_text, {"size": 8}),
        Arg("idmin", enc_int, {"bit": 64}),
        Arg("idmax", enc_int, {"bit": 64}),
    )),
    Request("GetLogLastId", _k("110300"), (Arg("database", enc_text, {"size": 8}),)),
    Request("IngestAddJob", _k("070F00"), (Arg("xml", enc_text),)),
    Request("IngestGetJobStatus", _k("071D00"), (Arg("job_id", enc_int, {"bit": 64}),)),
)


def _list_fields() -> tuple[Field, ...]:
    return (
        Field("amount", 0, 4, dec_int),
        Field("item_length", 4, 8, dec_int),
        Field("list", 8, -1, dec_uuid_list),
        RC_TRAILER,
    )


_CPL_COMMON: tuple[Field, ...] = (
    Field("cpl_uuid", 0, 16, dec_uuid),
    Field("storage", 16, 17, dec_int, STORAGE),
    Field("content_title_text", 17, 145, dec_text),
    Field("content_kind", 145, 146, dec_int, CONTENT_KIND),
    Field("duration", 146, 150, dec_int),
    Field("edit_rate_a", 150, 154, dec_int),
    Field("edit_rate_b", 154, 158, dec_int),
    Field("picture_encoding", 158, 159, dec_int, ENCODING),
    Field("picture_width", 159, 161, dec_int),
    Field("picture_height", 161, 163, dec_int),
    Field("picture_encryption", 163, 164, dec_int, ENCRYPTION),
    Field("sound_encoding", 164, 165, dec_int, ENCODING),
    Field("sound_channel_count", 165, 166, dec_int),
    Field("sound_quantization_bits", 166, 167, dec_int),
    Field("sound_encryption", 167, 168, dec_int, ENCRYPTION),
)

_STATUS_COMMON: tuple[Field, ...] = (
    Field("playback_state", 0, 1, dec_int, PLAYBACK_STATE),
    Field("spl_id", 1, 17, dec_uuid),
    Field("show_playlist_position", 17, 21, dec_int),
    Field("show_playlist_duration", 21, 25, dec_int),
    Field("current_cpl_id", 25, 41, dec_uuid),
    Field("current_event_id", 41, 57, dec_uuid),
    Field("current_element_id", 57, 73, dec_uuid),
    Field("current_element_position", 73, 77, dec_int),
    Field("current_element_duration", 77, 81, dec_int),
)

RESPONSES: tuple[Response, ...] = (
    Response("GetCPLList", _k("010200"), _list_fields()),
    Response("GetCPLInfo", _k("010400"), _CPL_COMMON + (
        Field("crypto_key_id_list", 176, -1, dec_uuid_list),
        RC_TRAILER,
    )),
    Response("GetCPLInfo2", _k("010401"), _CPL_COMMON + (
        Field("crypto_key_id_list", 176, -55, dec_uuid_list),
        Field("schemas", -55, -54, dec_int, {0: "Unknown", 1: "Digicine (Interop)", 2: "SMPTE"}),
        Field("stream_type", -54, -53, dec_int, {0: "None", 1: "FTP Stream", 2: "FTP Stream + Ingest"}),
        Field("complete", -53, -52, dec_int),
        Field("frame_per_edit", -52, -51, dec_int),
        Field("frame_rate_a", -49, -45, dec_int),
        Field("frame_rate_b", -45, -41, dec_int),
        Field("sound_sample_rate_a", -41, -37, dec_int),
        Field("sound_sample_rate_b", -37, -33, dec_int),
        Field("content_version_id", -25, -9, dec_uuid),
        RC_TRAILER,
    )),
    Response("DeleteCPL", _k("010600"), (RC_TRAILER,)),
    Response("StoreCPL", _k("010A00"), (RC_TRAILER,)),
    Response("RetrieveCPL", _k("010800"), (
        Field("xml", 0, -1, dec_text), RC_TRAILER,
    )),
    Response("ValidateCPL", _k("010C00"), (
        Field("result", 0, 1, dec_int),
        Field("error_code", 1, 2, dec_int, CPL_VALIDATION),
        Field("error_message", 2, -1, dec_text),
        RC_TRAILER,
    )),
    Response("GetCPLSize", _k("010E00"), (
        Field("size", 0, 8, dec_int), RC_TRAILER,
    )),
    Response("GetCPLMarker", _k("011000"), (RC_TRAILER,)),
    Response("GetCPLPlayStat", _k("011200"), (
        Field("error_code", 0, 4, dec_int),
    )),
    Response("GetKDMList", _k("020200"), _list_fields()),
    Response("GetKDMInfo", _k("020400"), (
        Field("kdm_uuid", 0, 16, dec_uuid),
        Field("cpl_uuid", 16, 32, dec_uuid),
        Field("not_valid_before", 32, 40, dec_int),
        Field("not_valid_after", 40, 48, dec_int),
        Field("key_id_list", 56, -1, dec_uuid_list),
        RC_TRAILER,
    )),
    Response("GetKDMInfo2", _k("020401"), (
        Field("kdm_uuid", 0, 16, dec_uuid),
        Field("cpl_uuid", 16, 32, dec_uuid),
        Field("not_valid_before", 32, 40, dec_int),
        Field("not_valid_after", 40, 48, dec_int),
        Field("key_id_list", 56, -293, dec_uuid_list),
        Field("forensic_picture_disable", -293, -292, dec_int),
        Field("forensic_audio_disable", -292, -291, dec_int),
        Field("content_authenticator", -289, -257, dec_text),
        Field("x509_subject_name", -257, -1, dec_text),
        RC_TRAILER,
    )),
    Response("GetSPLList", _k("030200"), _list_fields()),
    Response("StoreSPL", _k("032000"), (RC_TRAILER,)),
    Response("ValidateSPL", _k("032600"), (
        Field("result", 0, 1, dec_int),
        Field("error_code", 1, 2, dec_int, {
            0: "No error nor warning",
            1: "SPL is not registered on this server",
            2: "SPL is not registered on this server",
            3: "SPL is registered on this server but cannot be loaded",
            255: "Out of memory",
        }),
        Field("cpl_id", 2, 18, dec_uuid),
        Field("error_message", 18, -1, dec_text),
        RC_TRAILER,
    )),
    Response("PlaySPL", _k("030C00"), (RC_TRAILER,)),
    Response("PauseSPL", _k("030E00"), (RC_TRAILER,)),
    Response("StatusSPL", _k("031C00"), _STATUS_COMMON + (RC_TRAILER,)),
    Response("StatusSPL2", _k("031C01"), _STATUS_COMMON + (
        Field("flags", 81, 85, dec_int),
        Field("current_element_edit_rate_num", 85, 87, dec_int),
        Field("current_element_edit_rate_den", 87, 89, dec_int),
        Field("current_element_edit_position", 89, 93, dec_int),
        Field("current_element_edit_duration", 93, 97, dec_int),
        Field("current_element_frames_per_edit", 97, 99, dec_int),
        RC_TRAILER,
    )),
    Response("AddSchedule2", _k("040201"), (
        Field("schedule_id", 0, 8, dec_int), RC_TRAILER,
    )),
    Response("GetScheduleInfo2", _k("040801"), (
        Field("schedule_id", 0, 8, dec_int),
        Field("spl_id", 8, 24, dec_uuid),
        # FIXED: dcitools wires these to *encoder* functions (text_to_bytes /
        # int_to_bytes) on the response side, which cannot decode.  Corrected to
        # the matching decoders.
        Field("time", 24, 28, dec_int),
        Field("duration", 28, 32, dec_int),
        Field("status", 32, 33, dec_int, SCHEDULE_STATUS),
        Field("flags", 33, 41, dec_int),
        Field("annotation_text", 41, -1, dec_text),
        RC_TRAILER,
    )),
    Response("GetCurrentSchedule", _k("040A00"), (
        Field("schedule_id", 0, 8, dec_int), RC_TRAILER,
    )),
    Response("GetNextSchedule", _k("040C00"), (
        Field("schedule_id", 0, 8, dec_int), RC_TRAILER,
    )),
    Response("SetSchedulerEnable", _k("040E00"), (RC_TRAILER,)),
    Response("GetSchedulerEnable", _k("041000"), (
        Field("enabled", 0, 1, dec_bool), RC_TRAILER,
    )),
    Response("GetProductInfo", _k("050200"), (
        Field("product_name", 0, 16, dec_text),
        Field("product_serial", 16, 32, dec_text),
        Field("product_id", 32, 48, dec_uuid),
        Field("software_version_major", 48, 49, dec_int),
        Field("software_version_minor", 49, 50, dec_int),
        Field("software_version_revision", 50, 51, dec_int),
        Field("software_version_build", 51, 52, dec_int),
        Field("hardware_version_major", 52, 53, dec_int),
        Field("hardware_version_minor", 53, 54, dec_int),
        Field("hardware_version_build", 54, 55, dec_int),
        Field("hardware_version_extra", 55, 56, dec_int),
    )),
    Response("GetProductCertificate", _k("050400"), (
        Field("certificate", 0, -1, dec_text), RC_TRAILER,
    )),
    Response("GetAPIProtocolVersion", _k("050600"), (
        Field("version_major", 0, 1, dec_int),
        Field("version_minor", 1, 2, dec_int),
        Field("version_build", 2, 3, dec_int),
    )),
    Response("GetTimeZone", _k("052000"), (
        Field("timezone", 0, -1, dec_text), RC_TRAILER,
    )),
    Response("WhoAmI", _k("0E0C00"), (
        Field("username", 0, 16, dec_text),
        Field("dci_level", 16, -1, dec_int),
        RC_TRAILER,
    )),
    Response("GetLog", _k("110200"), (
        Field("errorcode", 0, 1, dec_int),
        Field("xml", 4, -1, dec_text),
        RC_TRAILER,
    )),
    Response("GetLogLastId", _k("110400"), (
        Field("errorcode", 0, 1, dec_int),
        Field("last_id", 4, -1, dec_int),
        RC_TRAILER,
    )),
    Response("IngestAddJob", _k("071000"), (
        Field("job_id", 0, 8, dec_int), RC_TRAILER,
    )),
    Response("IngestGetJobStatus", _k("071E00"), (
        Field("error_count", 0, 4, dec_int),
        Field("warning_count", 4, 8, dec_int),
        Field("event_count", 8, 12, dec_int),
        Field("status", 12, 16, dec_int, INGEST_STATUS),
        Field("download_progress", 16, 20, dec_int),
        Field("process_progress", 20, 24, dec_int),
        Field("actions", 24, 28, dec_int),
        Field("title", 28, -1, dec_text),
        RC_TRAILER,
    )),
)

REQUEST_BY_NAME: dict[str, Request] = {r.name: r for r in REQUESTS}
RESPONSE_BY_KEY: dict[bytes, Response] = {r.key: r for r in RESPONSES}
RESPONSE_BY_NAME: dict[str, Response] = {r.name: r for r in RESPONSES}


# --------------------------------------------------------------------------
# Frame build / parse
# --------------------------------------------------------------------------

def build_request(name: str, request_id: int, /, *args: Any, **kwargs: Any) -> bytes:
    """Build a complete outbound KLV frame."""
    try:
        definition = REQUEST_BY_NAME[name]
    except KeyError:
        raise ProtocolError(f"unknown request {name!r}") from None

    chunks: list[bytes] = []
    positional = list(args)
    for arg in definition.args:
        if arg.name in kwargs:
            value = kwargs[arg.name]
        elif positional:
            value = positional.pop(0)
        else:
            raise ProtocolError(f"{name}: missing parameter {arg.name!r}")
        try:
            chunks.append(arg.encode(value, **arg.kwargs))
        except ProtocolError:
            raise
        except Exception as err:  # noqa: BLE001 - surfaced as protocol error
            raise ProtocolError(f"{name}: bad value for {arg.name!r}: {err}") from err

    payload = (request_id & 0xFFFFFFFF).to_bytes(ID_LEN, "big") + b"".join(chunks)
    return HEADER + definition.key + encode_ber(len(payload)) + payload


def parse_payload(key: bytes, payload: bytes) -> dict[str, Any]:
    """Decode a response payload (request id already stripped)."""
    definition = RESPONSE_BY_KEY.get(bytes(key))
    if definition is None:
        return {
            "_unknown_key": bytes(key).hex(),
            "_raw": bytes(payload).hex(),
        }

    result: dict[str, Any] = {"_message": definition.name}
    for field in definition.fields:
        try:
            chunk = payload[field.start : field.end] if field.end is not None else payload[field.start :]
            # An empty slice means the frame is shorter than this definition
            # expects.  Reporting nothing is safer than reporting a decoded
            # zero, which for return_code would look like success.
            if not chunk:
                continue
            value = field.decode(chunk)
        except Exception:  # noqa: BLE001 - a short frame should not kill the poll
            continue
        result[field.name] = value
        if field.lookup is not None and isinstance(value, int):
            result[f"{field.name}_text"] = field.lookup.get(value, f"unknown ({value})")
    return result


def explain(frame: bytes) -> str:
    """Human-readable dump of a frame, for diagnostics and the probe tool."""
    if len(frame) < HEADER_LEN + KEY_LEN + 1:
        return f"<short frame {frame.hex()}>"
    key = frame[HEADER_LEN : HEADER_LEN + KEY_LEN]
    length, consumed = decode_ber(frame[HEADER_LEN + KEY_LEN :])
    start = HEADER_LEN + KEY_LEN + consumed
    req_id = int.from_bytes(frame[start : start + ID_LEN], "big")
    body = frame[start + ID_LEN :]
    known = RESPONSE_BY_KEY.get(key)
    if known is not None:
        label = f"{known.name} response"
    else:
        label = next(
            (f"{r.name} request" for r in REQUESTS if r.key == key),
            "unknown message",
        )
    return (
        f"key={key.hex()} ({label}) ber={length} id={req_id} "
        f"payload={body.hex()[:120]}{'...' if len(body) > 60 else ''}"
    )
