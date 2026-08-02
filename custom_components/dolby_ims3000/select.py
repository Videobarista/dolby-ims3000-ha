"""Select platform: arm a show playlist without starting it."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IMSConfigEntry
from .coordinator import IMSCoordinator
from .entity import IMSEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([IMSShowSelect(entry.runtime_data)])


class IMSShowSelect(IMSEntity, SelectEntity):
    """Pick which SPL the play action and the play_spl service will target.

    Selecting is deliberately inert: it changes nothing on the server.  The
    protocol has no 'load show' verb, so arming locally and then acting is the
    only honest model.

    Note the server returns show playlists as bare UUIDs with no names, so the
    options are labelled by their first octets.
    """

    _attr_translation_key = "armed_show"

    def __init__(self, coordinator: IMSCoordinator) -> None:
        super().__init__(coordinator, "armed_show")

    @staticmethod
    def _label(spl_id: str) -> str:
        return f"SPL {spl_id[:8]}"

    @property
    def options(self) -> list[str]:
        return [self._label(s) for s in self.coordinator.data.spl_ids]

    @property
    def current_option(self) -> str | None:
        armed = self.coordinator.armed_spl
        return self._label(armed) if armed else None

    async def async_select_option(self, option: str) -> None:
        for spl_id in self.coordinator.data.spl_ids:
            if self._label(spl_id) == option:
                self.coordinator.armed_spl = spl_id
                self.async_write_ha_state()
                return
        raise HomeAssistantError(f"Unknown show playlist: {option}")

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        return {"armed_spl_id": self.coordinator.armed_spl}
