"""Tests for setting up and unloading the Dolby IMS3000 integration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dolby_ims3000.api import IMSConnectionError
from custom_components.dolby_ims3000.const import (
    DOMAIN,
    SERVICE_INGEST_JOB,
    SERVICE_PLAY_SPL,
    SERVICE_RAW_COMMAND,
    SERVICE_VALIDATE_CPL,
)

from .const import TITLE


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    """Look an entity up by its unique id rather than guessing its name."""
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, f"no {platform} entity for key {key}"
    return entity_id


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A reachable server loads, reports the title on screen and unloads cleanly."""
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    player = hass.states.get(_entity_id(hass, mock_config_entry, "media_player", "media_player"))
    assert player is not None
    assert player.state == "playing"
    assert player.attributes["media_title"] == TITLE
    # Position and duration describe the film on screen, not the whole playlist.
    assert player.attributes["media_position"] == 5124
    assert player.attributes["media_duration"] == 5138

    timecode = hass.states.get(_entity_id(hass, mock_config_entry, "sensor", "title_timecode"))
    assert timecode is not None
    assert timecode.state == "01:25:24 / 01:25:38"
    assert timecode.attributes["playlist"] == "01:25:24 / 23:03:19"

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.disconnect.assert_called()


async def test_setup_while_server_is_off(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """An unreachable server still loads, as switched off rather than failed."""
    mock_client.status.side_effect = IMSConnectionError("connection refused")

    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    player = hass.states.get(_entity_id(hass, mock_config_entry, "media_player", "media_player"))
    assert player is not None
    assert player.state == STATE_OFF

    online = hass.states.get(_entity_id(hass, mock_config_entry, "binary_sensor", "online"))
    assert online is not None
    assert online.state == STATE_OFF

    timecode = hass.states.get(_entity_id(hass, mock_config_entry, "sensor", "title_timecode"))
    assert timecode is not None
    assert timecode.state == STATE_UNAVAILABLE

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()


async def test_actions_registered_without_entry(hass: HomeAssistant) -> None:
    """Actions exist as soon as the integration loads, before any server is added."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    for service in (
        SERVICE_PLAY_SPL,
        SERVICE_VALIDATE_CPL,
        SERVICE_INGEST_JOB,
        SERVICE_RAW_COMMAND,
    ):
        assert hass.services.has_service(DOMAIN, service)


async def test_action_with_unknown_entry(hass: HomeAssistant) -> None:
    """Calling an action for a server that does not exist is a validation error."""
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_VALIDATE_CPL,
            {"entry_id": "does-not-exist", "cpl_id": "abc"},
            blocking=True,
            return_response=True,
        )
