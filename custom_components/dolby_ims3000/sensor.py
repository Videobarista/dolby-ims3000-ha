"""Sensors for the Dolby IMS3000."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IMSConfigEntry
from .coordinator import IMSCoordinator, IMSData
from .entity import IMSEntity


@dataclass(frozen=True, kw_only=True)
class IMSSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[IMSData], Any]
    attrs_fn: Callable[[IMSData], dict[str, Any]] | None = None


def _remaining(data: IMSData) -> int | None:
    pos = data.status.get("show_playlist_position")
    dur = data.status.get("show_playlist_duration")
    if pos is None or dur is None:
        return None
    return max(int(dur) - int(pos), 0)


def _sw_version(data: IMSData) -> str | None:
    parts = [
        data.product.get("software_version_major"),
        data.product.get("software_version_minor"),
        data.product.get("software_version_revision"),
        data.product.get("software_version_build"),
    ]
    if any(p is None for p in parts):
        return None
    return ".".join(str(p) for p in parts)


def _api_version(data: IMSData) -> str | None:
    v = data.api_version
    parts = [v.get("version_major"), v.get("version_minor"), v.get("version_build")]
    if any(p is None for p in parts):
        return None
    return ".".join(str(p) for p in parts)


SENSORS: tuple[IMSSensorDescription, ...] = (
    IMSSensorDescription(
        key="playback_state",
        translation_key="playback_state",
        device_class=SensorDeviceClass.ENUM,
        options=["unknown", "stop", "play", "pause"],
        value_fn=lambda d: d.status.get("playback_state_text"),
    ),
    IMSSensorDescription(
        key="current_title",
        translation_key="current_title",
        value_fn=lambda d: d.current_title,
        attrs_fn=lambda d: {
            "cpl_id": d.status.get("current_cpl_id") or None,
            "content_kind": (
                d.cpl_info.get(d.status.get("current_cpl_id", ""), {})
            ).get("content_kind_text"),
        },
    ),
    IMSSensorDescription(
        key="position",
        translation_key="position",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.status.get("show_playlist_position"),
    ),
    IMSSensorDescription(
        key="duration",
        translation_key="duration",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda d: d.status.get("show_playlist_duration"),
    ),
    IMSSensorDescription(
        key="remaining",
        translation_key="remaining",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_remaining,
    ),
    IMSSensorDescription(
        key="spl_count",
        translation_key="spl_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: len(d.spl_ids),
        attrs_fn=lambda d: {"spl_ids": d.spl_ids[:50]},
    ),
    IMSSensorDescription(
        key="cpl_count",
        translation_key="cpl_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: len(d.cpl_ids),
        attrs_fn=lambda d: {
            "titles": [
                info.get("content_title_text")
                for info in list(d.cpl_info.values())[:50]
                if info.get("content_title_text")
            ]
        },
    ),
    IMSSensorDescription(
        key="kdm_count",
        translation_key="kdm_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: len(d.kdm_ids),
    ),
    IMSSensorDescription(
        key="next_schedule",
        translation_key="next_schedule",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.next_schedule,
        attrs_fn=lambda d: {
            "annotation": d.next_schedule_info.get("annotation_text"),
            "spl_id": d.next_schedule_info.get("spl_id"),
            "status": d.next_schedule_info.get("status_text"),
        },
    ),
    IMSSensorDescription(
        key="timezone",
        translation_key="timezone",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.timezone,
    ),
    IMSSensorDescription(
        key="software_version",
        translation_key="software_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_sw_version,
    ),
    IMSSensorDescription(
        key="api_version",
        translation_key="api_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_api_version,
    ),
    IMSSensorDescription(
        key="serial_number",
        translation_key="serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.product.get("product_serial"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(IMSSensor(coordinator, desc) for desc in SENSORS)


class IMSSensor(IMSEntity, SensorEntity):
    """A single value read from the coordinator snapshot."""

    entity_description: IMSSensorDescription

    def __init__(
        self, coordinator: IMSCoordinator, description: IMSSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        try:
            return self.entity_description.value_fn(self.coordinator.data)
        except Exception:  # noqa: BLE001 - a missing field is not an error
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.attrs_fn is None:
            return None
        try:
            return {
                k: v
                for k, v in self.entity_description.attrs_fn(
                    self.coordinator.data
                ).items()
                if v is not None
            }
        except Exception:  # noqa: BLE001
            return None
