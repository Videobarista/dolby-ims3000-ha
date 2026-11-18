"""Tests for the "Delete current composition" button."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dolby_ims3000.api import IMSCommandError
from custom_components.dolby_ims3000.const import DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import CPL_ID, STATUS

PAUSED = {**STATUS, "playback_state": 3, "playback_state_text": "pause"}


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{entry.entry_id}_delete_current_cpl"
    )
    assert entity_id is not None
    return entity_id


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )


async def test_deletes_the_paused_composition(
    hass: HomeAssistant, mock_client: MagicMock, delete_config_entry: MockConfigEntry
) -> None:
    """With deleting allowed and the show paused, the film on screen is deleted."""
    entity_id = await _setup(hass, delete_config_entry)
    mock_client.status.return_value = dict(PAUSED)

    await _press(hass, entity_id)

    mock_client.delete_cpl.assert_awaited_once_with(CPL_ID)


async def test_refused_when_deleting_is_not_allowed(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Without the option, the button deletes nothing."""
    entity_id = await _setup(hass, mock_config_entry)
    mock_client.status.return_value = dict(PAUSED)

    with pytest.raises(HomeAssistantError, match="disabled"):
        await _press(hass, entity_id)

    mock_client.delete_cpl.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "message"),
    [
        pytest.param(dict(STATUS), "playing", id="playing"),
        pytest.param({**PAUSED, "current_cpl_id": None}, "nothing to delete", id="nothing_loaded"),
    ],
)
async def test_refused_while_playing_or_empty(
    hass: HomeAssistant,
    mock_client: MagicMock,
    delete_config_entry: MockConfigEntry,
    status: dict[str, Any],
    message: str,
) -> None:
    """A playing show, or no loaded film, deletes nothing."""
    entity_id = await _setup(hass, delete_config_entry)
    mock_client.status.return_value = status

    with pytest.raises(HomeAssistantError, match=message):
        await _press(hass, entity_id)

    mock_client.delete_cpl.assert_not_awaited()


async def test_uses_the_status_at_the_moment_of_the_press(
    hass: HomeAssistant, mock_client: MagicMock, delete_config_entry: MockConfigEntry
) -> None:
    """If the show moved on since the last poll, the film now loaded is the one deleted."""
    entity_id = await _setup(hass, delete_config_entry)
    other = "0f0e0d0c-0b0a-4908-8706-050403020100"
    mock_client.status.return_value = {**PAUSED, "current_cpl_id": other}

    await _press(hass, entity_id)

    mock_client.delete_cpl.assert_awaited_once_with(other)


async def test_server_refusal_is_reported(
    hass: HomeAssistant, mock_client: MagicMock, delete_config_entry: MockConfigEntry
) -> None:
    """A refusal from the server becomes a readable error."""
    entity_id = await _setup(hass, delete_config_entry)
    mock_client.status.return_value = dict(PAUSED)
    mock_client.delete_cpl.side_effect = IMSCommandError("DeleteCPL", 3, {})

    with pytest.raises(HomeAssistantError, match="refused"):
        await _press(hass, entity_id)
