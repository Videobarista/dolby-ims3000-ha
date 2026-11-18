"""Tests for the Dolby IMS3000 actions and their input validation."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol

from custom_components.dolby_ims3000.api import IMSInvalidRequest
from custom_components.dolby_ims3000.const import (
    DOMAIN,
    SERVICE_INGEST_JOB,
    SERVICE_PLAY_SPL,
    SERVICE_RAW_COMMAND,
    SERVICE_VALIDATE_CPL,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import CPL_ID, SPL_ID

# Home Assistant reports a schema violation as one of these, depending on version.
INVALID_INPUT = (vol.Invalid, ServiceValidationError)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _call(
    hass: HomeAssistant, service: str, entry: MockConfigEntry, **data: Any
) -> dict[str, Any] | None:
    return await hass.services.async_call(
        DOMAIN,
        service,
        {"entry_id": entry.entry_id, **data},
        blocking=True,
        return_response=True,
    )


async def test_play_spl(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A show is scheduled, with the UUID normalised to its canonical form."""
    await _setup(hass, mock_config_entry)

    result = await _call(
        hass, SERVICE_PLAY_SPL, mock_config_entry, spl_id=SPL_ID.upper().replace("-", "")
    )

    assert result is not None
    assert result["schedule_id"] == 42
    kwargs = mock_client.schedule_spl.await_args.kwargs
    assert kwargs["spl_id"] == SPL_ID
    assert kwargs["annotation"] == "Home Assistant"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"spl_id": "not-a-uuid"}, id="bad_uuid"),
        pytest.param({"spl_id": SPL_ID, "annotation": "x" * 128}, id="annotation_too_long"),
        pytest.param({"spl_id": SPL_ID, "annotation": "two\nlines"}, id="annotation_line_break"),
        pytest.param({"spl_id": SPL_ID, "delay": 99999}, id="delay_out_of_range"),
    ],
)
async def test_play_spl_rejects_bad_input(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    data: dict[str, Any],
) -> None:
    """Bad input is refused before anything reaches the server."""
    await _setup(hass, mock_config_entry)

    with pytest.raises(INVALID_INPUT):
        await _call(hass, SERVICE_PLAY_SPL, mock_config_entry, **data)

    mock_client.schedule_spl.assert_not_awaited()


async def test_write_actions_refused_when_control_is_off(
    hass: HomeAssistant, mock_client: MagicMock, read_only_config_entry: MockConfigEntry
) -> None:
    """With control off, nothing that changes the server gets through."""
    await _setup(hass, read_only_config_entry)
    mock_client.command.reset_mock()

    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_PLAY_SPL, read_only_config_entry, spl_id=SPL_ID)
    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_INGEST_JOB, read_only_config_entry, xml="<job/>")

    mock_client.schedule_spl.assert_not_awaited()
    sent = [call.args[0] for call in mock_client.command.await_args_list]
    assert "IngestAddJob" not in sent


async def test_read_actions_work_when_control_is_off(
    hass: HomeAssistant, mock_client: MagicMock, read_only_config_entry: MockConfigEntry
) -> None:
    """Monitoring-only servers can still be queried."""
    await _setup(hass, read_only_config_entry)

    await _call(hass, SERVICE_VALIDATE_CPL, read_only_config_entry, cpl_id=CPL_ID)
    result = await _call(
        hass, SERVICE_RAW_COMMAND, read_only_config_entry, command="GetTimeZone"
    )

    assert result == {"timezone": "Europe/Amsterdam"}


async def test_ingest_job(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An ingest job is passed on when control is allowed."""
    await _setup(hass, mock_config_entry)

    await _call(hass, SERVICE_INGEST_JOB, mock_config_entry, xml="<job/>")

    mock_client.command.assert_any_await("IngestAddJob", xml="<job/>")


async def test_ingest_job_rejects_oversized_xml(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An absurdly large job description is refused."""
    await _setup(hass, mock_config_entry)

    with pytest.raises(INVALID_INPUT):
        await _call(hass, SERVICE_INGEST_JOB, mock_config_entry, xml="x" * (64 * 1024 + 1))


@pytest.mark.parametrize(
    "command", ["PlaySPL", "PauseSPL", "AddSchedule2", "IngestAddJob", "DeleteCPL", "Bogus"]
)
async def test_raw_command_is_read_only(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    command: str,
) -> None:
    """raw_command cannot change the server, even with control allowed."""
    await _setup(hass, mock_config_entry)
    mock_client.command.reset_mock()

    with pytest.raises(INVALID_INPUT):
        await _call(hass, SERVICE_RAW_COMMAND, mock_config_entry, command=command)

    mock_client.command.assert_not_awaited()


async def test_raw_command_passes_known_parameters(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Parameters that belong to the command are passed on."""
    await _setup(hass, mock_config_entry)

    await _call(
        hass,
        SERVICE_RAW_COMMAND,
        mock_config_entry,
        command="GetCPLInfo",
        params={"uuid": CPL_ID},
    )

    mock_client.command.assert_any_await("GetCPLInfo", _check_return_code=False, uuid=CPL_ID)


@pytest.mark.parametrize(
    "params",
    [
        pytest.param({"bogus": 1}, id="unknown_parameter"),
        pytest.param({"_check_return_code": True}, id="internal_parameter"),
        pytest.param({"name": "GetCPLList"}, id="name_collision"),
    ],
)
async def test_raw_command_rejects_unknown_parameters(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    params: dict[str, Any],
) -> None:
    """Parameters the command does not take are refused."""
    await _setup(hass, mock_config_entry)
    mock_client.command.reset_mock()

    with pytest.raises(INVALID_INPUT):
        await _call(
            hass, SERVICE_RAW_COMMAND, mock_config_entry, command="GetCPLInfo", params=params
        )

    mock_client.command.assert_not_awaited()


async def test_raw_command_shortens_long_values(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A reply full of raw data does not flood the response."""
    await _setup(hass, mock_config_entry)
    mock_client.command.side_effect = None
    mock_client.command.return_value = {"_raw": "ab" * 10000}

    result = await _call(hass, SERVICE_RAW_COMMAND, mock_config_entry, command="GetLog")

    assert result is not None
    assert len(result["_raw"]) < 2200
    assert result["_raw"].endswith("more characters)")


async def test_invalid_request_is_a_validation_error(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A value the protocol cannot encode is reported as bad input."""
    await _setup(hass, mock_config_entry)
    mock_client.command.side_effect = IMSInvalidRequest("GetLog: bad value for 'idmin'")

    with pytest.raises(ServiceValidationError):
        await _call(
            hass,
            SERVICE_RAW_COMMAND,
            mock_config_entry,
            command="GetLog",
            params={"database": "x", "idmin": -1, "idmax": 1},
        )
