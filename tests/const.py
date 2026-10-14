"""Constants shared by the Dolby IMS3000 tests."""

from __future__ import annotations

from typing import Any

HOST = "10.0.0.50"
PORT = 11730
SERIAL = "IMS3K-123456"
SPL_ID = "851cc838-022e-43b7-9fee-18656bdfc995"
CPL_ID = "2b7f6c3e-6d4a-4c1e-9b1a-3f5e8d2c7a10"
TITLE = "Test Feature"

PRODUCT_INFO: dict[str, Any] = {
    "product_name": "IMS3000",
    "product_serial": SERIAL,
}

# A film at 01:25:24 of 01:25:38, inside a show playlist of 23:03:19.
STATUS: dict[str, Any] = {
    "playback_state": 2,
    "playback_state_text": "play",
    "spl_id": SPL_ID,
    "show_playlist_position": 5124,
    "show_playlist_duration": 82999,
    "current_cpl_id": CPL_ID,
    "current_element_position": 5124,
    "current_element_duration": 5138,
}

COMMAND_REPLIES: dict[str, dict[str, Any]] = {
    "GetCurrentSchedule": {"schedule_id": 0},
    "GetNextSchedule": {"schedule_id": 0},
    "GetAPIProtocolVersion": {"version_major": 1, "version_minor": 2, "version_build": 2},
    "GetTimeZone": {"timezone": "Europe/Amsterdam"},
}
