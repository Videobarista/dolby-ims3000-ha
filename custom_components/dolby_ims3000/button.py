"""Button for the Dolby IMS3000: delete the composition that is on screen."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IMSConfigEntry
from .api import IMSCommandError, IMSError
from .coordinator import IMSCoordinator
from .entity import IMSEntity

_LOGGER = logging.getLogger(__name__)

# Raw playback_state value for "playing" (see STATE_MAP in media_player.py).
_PLAYING = 2

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([IMSDeleteCurrentButton(entry.runtime_data)])


class IMSDeleteCurrentButton(IMSEntity, ButtonEntity):
    """Delete the composition currently loaded in the show.

    Guarded three ways, because a deleted composition cannot be brought back:
    the "Allow deleting content" option must be on, the show must not be
    playing, and the composition to delete is read from the server at the
    moment of the press rather than from the last poll, so a show that has
    moved on to the next film can never take the wrong one with it.

    As a configuration entity it stays off automatically generated dashboards;
    putting it on a dashboard is a deliberate choice, ideally with a
    confirmation dialog (see the README).
    """

    _attr_translation_key = "delete_current_cpl"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: IMSCoordinator) -> None:
        super().__init__(coordinator, "delete_current_cpl")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "cpl_id": data.status.get("current_cpl_id") or None,
            "title": data.current_title,
            "delete_allowed": self.coordinator.delete_allowed,
        }

    async def async_press(self) -> None:
        coordinator = self.coordinator
        if not coordinator.delete_allowed:
            raise ServiceValidationError(
                "Deleting content is disabled for this server. Enable "
                "\"Allow deleting content\" in the integration options first."
            )

        try:
            status = await coordinator.client.status()
        except IMSError as err:
            raise HomeAssistantError(f"Could not read the server status: {err}") from err

        if status.get("playback_state") == _PLAYING:
            raise ServiceValidationError("The show is playing. Pause it before deleting.")
        cpl_id = status.get("current_cpl_id")
        if not cpl_id:
            raise ServiceValidationError(
                "No composition is loaded, so there is nothing to delete."
            )

        info = coordinator.data.cpl_info.get(cpl_id, {})
        title = info.get("content_title_text") or cpl_id
        try:
            await coordinator.client.delete_cpl(cpl_id)
        except IMSCommandError as err:
            raise HomeAssistantError(
                f"The server refused to delete {title} (return code {err.return_code}). "
                "It may still be part of the loaded show."
            ) from err
        except IMSError as err:
            raise HomeAssistantError(f"Could not delete {title}: {err}") from err

        # Warning level on purpose: a deletion cannot be undone, so it belongs in
        # the log even when only warnings are kept.
        _LOGGER.warning(
            "Deleted composition %s (%s) from %s", title, cpl_id, coordinator.entry.title
        )
        coordinator.data.cpl_info.pop(cpl_id, None)
        if cpl_id in coordinator.data.cpl_ids:
            coordinator.data.cpl_ids.remove(cpl_id)
        await coordinator.async_request_refresh()
