"""Media player entity for the Dolby IMS3000."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import IMSConfigEntry
from .api import IMSError
from .const import (
    CONF_ALLOW_CONTROL,
    CONF_POSITION_UNIT,
    POSITION_UNIT_EDIT_UNITS,
    POSITION_UNIT_SECONDS,
)
from .coordinator import IMSCoordinator
from .entity import IMSEntity

_LOGGER = logging.getLogger(__name__)

STATE_MAP = {
    0: MediaPlayerState.IDLE,
    1: MediaPlayerState.IDLE,
    2: MediaPlayerState.PLAYING,
    3: MediaPlayerState.PAUSED,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: IMSConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([IMSMediaPlayer(entry.runtime_data)])


class IMSMediaPlayer(IMSEntity, MediaPlayerEntity):
    """Playback state and transport control for the show playlist."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_media_content_type = MediaType.MOVIE

    def __init__(self, coordinator: IMSCoordinator) -> None:
        super().__init__(coordinator, "media_player")

    # -- capabilities ----------------------------------------------------

    @property
    def _control_allowed(self) -> bool:
        return bool(self.coordinator.entry.options.get(CONF_ALLOW_CONTROL, True))

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        # No STOP: the protocol exposes no stop verb, only play and pause.
        # No volume: the fader lives in the CP750/CP850, a separate device.
        if not self._control_allowed:
            return MediaPlayerEntityFeature(0)
        return (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )

    # -- state -----------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState:
        if not self.available:
            return MediaPlayerState.OFF
        raw = self.coordinator.data.status.get("playback_state")
        return STATE_MAP.get(raw, MediaPlayerState.IDLE)

    @property
    def media_title(self) -> str | None:
        return self.coordinator.data.current_title

    @property
    def media_content_id(self) -> str | None:
        return self.coordinator.data.status.get("current_cpl_id") or None

    def _scale(self, value: int | None) -> int | None:
        """Convert a playlist counter to seconds if it is in edit units."""
        if value is None:
            return None
        unit = self.coordinator.entry.options.get(
            CONF_POSITION_UNIT, POSITION_UNIT_SECONDS
        )
        if unit != POSITION_UNIT_EDIT_UNITS:
            return int(value)
        status = self.coordinator.data.status
        num = status.get("current_element_edit_rate_num") or 0
        den = status.get("current_element_edit_rate_den") or 0
        if num and den:
            return int(value * den / num)
        return int(value)

    @property
    def media_duration(self) -> int | None:
        return self._scale(self.coordinator.data.status.get("show_playlist_duration"))

    @property
    def media_position(self) -> int | None:
        return self._scale(self.coordinator.data.status.get("show_playlist_position"))

    @property
    def media_position_updated_at(self) -> datetime | None:
        if self.state is MediaPlayerState.PLAYING:
            return dt_util.utcnow()
        return None

    # -- source (show playlist) -----------------------------------------

    def _label(self, spl_id: str) -> str:
        return f"SPL {spl_id[:8]}"

    @property
    def source_list(self) -> list[str]:
        return [self._label(s) for s in self.coordinator.data.spl_ids]

    @property
    def source(self) -> str | None:
        armed = self.coordinator.armed_spl
        if armed:
            return self._label(armed)
        running = self.coordinator.data.status.get("spl_id")
        return self._label(running) if running else None

    async def async_select_source(self, source: str) -> None:
        """Arm a show playlist.  Does not start it — press play for that."""
        for spl_id in self.coordinator.data.spl_ids:
            if self._label(spl_id) == source:
                self.coordinator.armed_spl = spl_id
                self.async_write_ha_state()
                return
        raise HomeAssistantError(f"Unknown show playlist: {source}")

    # -- transport -------------------------------------------------------

    async def async_media_play(self) -> None:
        """Resume a paused show, or start the armed one.

        The server has no 'load and play' verb: it only plays content that is
        already loaded and paused.  When nothing is loaded we fall back to
        scheduling the armed SPL a few seconds out, which is how the scheduler
        is meant to be driven.
        """
        self._assert_control()
        state = self.state
        try:
            if state is MediaPlayerState.PAUSED:
                await self.coordinator.client.play()
            elif self.coordinator.armed_spl:
                start = datetime.now(timezone.utc) + timedelta(seconds=5)
                await self.coordinator.client.schedule_spl(
                    spl_id=self.coordinator.armed_spl,
                    when=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    annotation="Home Assistant",
                )
            else:
                await self.coordinator.client.play()
        except IMSError as err:
            raise HomeAssistantError(f"Play failed: {err}") from err
        await self.coordinator.async_request_refresh()

    async def async_media_pause(self) -> None:
        self._assert_control()
        try:
            await self.coordinator.client.pause()
        except IMSError as err:
            raise HomeAssistantError(f"Pause failed: {err}") from err
        await self.coordinator.async_request_refresh()

    def _assert_control(self) -> None:
        if not self._control_allowed:
            raise HomeAssistantError(
                "Control is disabled for this server. Enable it in the "
                "integration options if you intend to drive playback."
            )

    # -- extras ----------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self.coordinator.data.status
        return {
            "spl_id": status.get("spl_id") or None,
            "armed_spl_id": self.coordinator.armed_spl,
            "current_cpl_id": status.get("current_cpl_id") or None,
            "current_element_id": status.get("current_element_id") or None,
            "current_element_position": status.get("current_element_position"),
            "current_element_duration": status.get("current_element_duration"),
            "playback_state_text": status.get("playback_state_text"),
            "control_allowed": self._control_allowed,
        }
