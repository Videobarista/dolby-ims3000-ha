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
    # True for entities that must keep reporting while the server is down.
    always_available: bool = False


def _next_schedule(data: IMSData) -> str | None:
    """Name of the next scheduled show, or None when nothing is queued.

    The server returns schedule id 0 to mean "no schedule", which would
    otherwise surface as a meaningless "0" in the UI.
    """
    schedule_id = data.next_schedule
    if not schedule_id:
        return None
    annotation = data.next_schedule_info.get("annotation_text")
    return annotation or f"Schedule {schedule_id}"


def _left(position: int | None, duration: int | None) -> int | None:
    if position is None or duration is None:
        return None
    return max(duration - position, 0)


def _timecode(seconds: int | None) -> str | None:
    """Format whole seconds as HH:MM:SS, the way the server's own UI does."""
    if seconds is None:
        return None
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _title_timecode(data: IMSData) -> str | None:
    """Position of the current title out of its length, e.g. 01:25:24 / 01:25:38."""
    position = _timecode(data.title_position)
    duration = _timecode(data.title_duration)
    if position is None or duration is None:
        return None
    return f"{position} / {duration}"


def _title_timecode_attrs(data: IMSData) -> dict[str, str | None]:
    playlist_position = _timecode(data.playlist_position)
    playlist_duration = _timecode(data.playlist_duration)
    playlist = None
    if playlist_position is not None and playlist_duration is not None:
        playlist = f"{playlist_position} / {playlist_duration}"
    return {
        "position": _timecode(data.title_position),
        "duration": _timecode(data.title_duration),
        "remaining": _timecode(_left(data.title_position, data.title_duration)),
        "playlist": playlist,
    }


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
        key="title_timecode",
        translation_key="title_timecode",
        value_fn=_title_timecode,
        attrs_fn=_title_timecode_attrs,
    ),
    IMSSensorDescription(
        key="title_position",
        translation_key="title_position",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.title_position,
    ),
    IMSSensorDescription(
        key="title_duration",
        translation_key="title_duration",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        value_fn=lambda d: d.title_duration,
    ),
    IMSSensorDescription(
        key="title_remaining",
        translation_key="title_remaining",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _left(d.title_position, d.title_duration),
    ),
    IMSSensorDescription(
        key="position",
        translation_key="position",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.playlist_position,
    ),
    IMSSensorDescription(
        key="duration",
        translation_key="duration",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        suggested_display_precision=0,
        value_fn=lambda d: d.playlist_duration,
    ),
    IMSSensorDescription(
        key="remaining",
        translation_key="remaining",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _left(d.playlist_position, d.playlist_duration),
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
        value_fn=_next_schedule,
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
    IMSSensorDescription(
        key="response_time",
        translation_key="response_time",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
        value_fn=lambda d: d.response_time_ms,
    ),
    IMSSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.last_seen,
        always_available=True,
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
    def available(self) -> bool:
        if self.entity_description.always_available:
            return bool(self.coordinator.last_update_success)
        return super().available

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
