"""Binary sensors for the Dolby IMS3000."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IMSConfigEntry
from .coordinator import IMSCoordinator, IMSData
from .entity import IMSEntity


@dataclass(frozen=True, kw_only=True)
class IMSBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[IMSData], bool | None]
    # True for entities that must report even while the server is unreachable.
    always_available: bool = False


BINARY_SENSORS: tuple[IMSBinarySensorDescription, ...] = (
    IMSBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.available,
        always_available=True,
    ),
    IMSBinarySensorDescription(
        key="show_running",
        translation_key="show_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda d: d.status.get("playback_state") == 2,
    ),
    IMSBinarySensorDescription(
        key="show_loaded",
        translation_key="show_loaded",
        value_fn=lambda d: bool(d.status.get("spl_id")),
    ),
    IMSBinarySensorDescription(
        key="content_encrypted",
        translation_key="content_encrypted",
        entity_registry_enabled_default=False,
        value_fn=lambda d: (
            None
            if not d.current_title
            else bool(
                d.cpl_info.get(d.status.get("current_cpl_id", ""), {}).get(
                    "picture_encryption"
                )
            )
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        IMSBinarySensor(coordinator, desc) for desc in BINARY_SENSORS
    )


class IMSBinarySensor(IMSEntity, BinarySensorEntity):
    entity_description: IMSBinarySensorDescription

    def __init__(
        self, coordinator: IMSCoordinator, description: IMSBinarySensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.always_available:
            return bool(self.coordinator.last_update_success)
        return super().available

    @property
    def is_on(self) -> bool | None:
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except Exception:  # noqa: BLE001
            return None
