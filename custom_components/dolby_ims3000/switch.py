"""Switch platform: the show scheduler."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IMSConfigEntry
from .api import IMSError
from .const import CONF_ALLOW_CONTROL
from .coordinator import IMSCoordinator
from .entity import IMSEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([IMSSchedulerSwitch(entry.runtime_data)])


class IMSSchedulerSwitch(IMSEntity, SwitchEntity):
    """Enable or disable the server's automatic show scheduler.

    This is the one clean 1:1 mapping in the whole protocol:
    GetSchedulerEnable / SetSchedulerEnable.
    """

    _attr_translation_key = "scheduler"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator: IMSCoordinator) -> None:
        super().__init__(coordinator, "scheduler")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.scheduler_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)

    async def _set(self, enable: bool) -> None:
        if not self.coordinator.entry.options.get(CONF_ALLOW_CONTROL, True):
            raise HomeAssistantError(
                "Control is disabled for this server. Enable it in the "
                "integration options first."
            )
        try:
            await self.coordinator.client.set_scheduler(enable)
        except IMSError as err:
            raise HomeAssistantError(f"Could not change scheduler: {err}") from err
        await self.coordinator.async_request_refresh()
