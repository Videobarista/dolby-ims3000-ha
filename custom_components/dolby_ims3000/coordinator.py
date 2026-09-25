"""Polling coordinator for the Dolby IMS3000."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import IMSClient, IMSCommandError, IMSConnectionError
from .const import (
    CONF_CATALOG_INTERVAL,
    CONF_POSITION_UNIT,
    DEFAULT_CATALOG_INTERVAL,
    DEFAULT_POSITION_UNIT,
    DOMAIN,
    POSITION_UNIT_EDIT_UNITS,
    POSITION_UNIT_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class IMSData:
    """Everything the entities read from."""

    available: bool = False
    last_seen: datetime | None = None
    # Wall-clock time of the last status round trip, in milliseconds.  None
    # while offline, since a timeout is not a response time.
    response_time_ms: float | None = None
    # Playlist counters normalised to whole seconds, plus the edit rate they
    # were derived from (None when the counters were already in seconds).
    position_seconds: int | None = None
    duration_seconds: int | None = None
    edit_rate: float | None = None
    offline_since: datetime | None = None
    status: dict[str, Any] = field(default_factory=dict)
    product: dict[str, Any] = field(default_factory=dict)
    api_version: dict[str, Any] = field(default_factory=dict)
    timezone: str | None = None
    scheduler_enabled: bool | None = None
    current_schedule: int | None = None
    current_schedule_info: dict[str, Any] = field(default_factory=dict)
    next_schedule: int | None = None
    next_schedule_info: dict[str, Any] = field(default_factory=dict)
    # Every schedule entry we've looked up this session, keyed by schedule id.
    # Static once fetched (a schedule's annotation and SPL don't change), so
    # this is a plain cache, never invalidated.
    schedule_info: dict[int, dict[str, Any]] = field(default_factory=dict)
    # SPL uuid -> name, learned opportunistically from schedule_info above.
    # The protocol has no "get SPL name" command; a schedule entry's
    # annotation_text is the only place a show name ever appears, so this
    # fills in as shows get scheduled and observed as current or next.
    spl_names: dict[str, str] = field(default_factory=dict)
    spl_ids: list[str] = field(default_factory=list)
    cpl_ids: list[str] = field(default_factory=list)
    kdm_ids: list[str] = field(default_factory=list)
    # cpl uuid -> GetCPLInfo2 payload, filled lazily and cached
    cpl_info: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_error: str | None = None

    @property
    def current_title(self) -> str | None:
        cpl_id = self.status.get("current_cpl_id")
        if not cpl_id:
            return None
        info = self.cpl_info.get(cpl_id)
        if not info:
            return None
        return info.get("content_title_text") or None


class IMSCoordinator(DataUpdateCoordinator[IMSData]):
    """Fast-polls playback state, slow-polls the catalogue."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: IMSClient,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
            config_entry=entry,
        )
        self.client = client
        self.entry = entry
        self.data = IMSData()
        # SPL selected in the UI but not yet started.  Purely local state.
        self.armed_spl: str | None = None
        self._tick = 0
        # The config flow already probes the host before an entry can be
        # created or reconfigured (see _async_probe in config_flow.py), so by
        # the time this coordinator exists the host is known to be a real
        # IMS3000.  A connection failure here therefore always means "the
        # server is currently unreachable" (most often: switched off outside
        # show hours), never "this was never a valid host" - including on the
        # very first poll after a Home Assistant restart, which is exactly
        # when a cinema server is most likely to still be powered down.  So
        # every connection failure is treated the same way, from the first
        # poll onward: the entry sets up successfully and the device reports
        # "off" instead of setup retrying with an escalating backoff and a
        # full traceback in the log every few seconds.
        self._first_update = True
        self._catalog_every: int = entry.options.get(
            CONF_CATALOG_INTERVAL, DEFAULT_CATALOG_INTERVAL
        )

    async def _async_update_data(self) -> IMSData:
        data = self.data or IMSData()
        catalogue_due = self._tick % max(self._catalog_every, 1) == 0
        self._tick += 1

        is_first_update, self._first_update = self._first_update, False

        poll_start = time.monotonic()
        try:
            data.status = await self.client.status()
        except IMSCommandError as err:
            # A command-level failure still means the box is reachable, and
            # the round trip to get that rejection is a genuine response time.
            data.response_time_ms = round((time.monotonic() - poll_start) * 1000, 1)
            data.last_error = str(err)
            _LOGGER.debug("Status command rejected: %s", err)
        except IMSConnectionError as err:
            return self._mark_offline(data, err, is_first_update)
        else:
            data.response_time_ms = round((time.monotonic() - poll_start) * 1000, 1)

        if not data.available:
            _LOGGER.info(
                "%s is reachable at %s", self.entry.title, self.client.host
            )
        data.available = True
        data.offline_since = None
        data.last_seen = dt_util.utcnow()
        data.last_error = None
        self._scale_counters(data)

        # Cheap per-poll extras.  Failures here must not mark the device down.
        data.scheduler_enabled = await self._try(self.client.scheduler_enabled)
        data.current_schedule = await self._try_field(
            "GetCurrentSchedule", "schedule_id"
        )
        data.next_schedule = await self._try_field("GetNextSchedule", "schedule_id")
        data.current_schedule_info = await self._resolve_schedule(
            data.current_schedule, data
        )
        data.next_schedule_info = await self._resolve_schedule(
            data.next_schedule, data
        )

        if catalogue_due:
            await self._refresh_catalogue(data)

        # Resolve the title of whatever is on screen, cached by CPL uuid.
        cpl_id = data.status.get("current_cpl_id")
        if cpl_id and cpl_id not in data.cpl_info:
            info = await self._try(self.client.cpl_info, cpl_id)
            if info:
                data.cpl_info[cpl_id] = info

        return data

    def _scale_counters(self, data: IMSData) -> None:
        """Normalise the playlist counters to seconds.

        The server reports position and duration in edit units (frames), not
        seconds, and tells us the edit rate of the element on screen.  In
        "auto" mode we divide by that rate when the server supplies one; the
        explicit settings exist for servers that report something else.
        """
        status = data.status
        raw_pos = status.get("show_playlist_position")
        raw_dur = status.get("show_playlist_duration")

        unit = self.entry.options.get(CONF_POSITION_UNIT, DEFAULT_POSITION_UNIT)
        num = status.get("current_element_edit_rate_num") or 0
        den = status.get("current_element_edit_rate_den") or 0
        rate = (num / den) if num and den else None

        if unit == POSITION_UNIT_SECONDS:
            rate = None
        elif unit == POSITION_UNIT_EDIT_UNITS and rate is None:
            # Told to expect frames but the server did not report a rate.
            _LOGGER.debug("Edit units requested but no edit rate reported")

        data.edit_rate = rate
        divisor = rate if rate and rate > 1 else 1
        data.position_seconds = (
            int(raw_pos / divisor) if isinstance(raw_pos, (int, float)) else None
        )
        data.duration_seconds = (
            int(raw_dur / divisor) if isinstance(raw_dur, (int, float)) else None
        )

    def _mark_offline(
        self, data: IMSData, err: Exception, is_first_update: bool = False
    ) -> IMSData:
        """Treat an unreachable server as powered down, not as an error.

        Cinema servers are switched off outside show hours - including, quite
        often, at the moment Home Assistant itself restarts.  Failing setup
        for that would retry with an escalating backoff and a full traceback
        in the log on every attempt; instead the config entry loads normally,
        the connectivity sensor goes off and the media player reports "off".
        """
        if data.available or is_first_update:
            data.offline_since = dt_util.utcnow()
            _LOGGER.info(
                "%s is unreachable at %s, treating it as powered off (%s)",
                self.entry.title,
                self.client.host,
                err,
            )
        else:
            _LOGGER.debug("%s still unreachable: %s", self.entry.title, err)

        data.available = False
        data.last_error = str(err)
        # Drop volatile state so nothing reports a frozen position or title.
        data.status = {}
        data.response_time_ms = None
        data.position_seconds = None
        data.duration_seconds = None
        data.edit_rate = None
        data.scheduler_enabled = None
        data.current_schedule = None
        data.current_schedule_info = {}
        data.next_schedule = None
        data.next_schedule_info = {}
        return data

    async def _refresh_catalogue(self, data: IMSData) -> None:
        if not data.product:
            data.product = await self._try(self.client.product_info) or {}
        if not data.api_version:
            data.api_version = (
                await self._try(self.client.command, "GetAPIProtocolVersion") or {}
            )
        if data.timezone is None:
            tz = await self._try(self.client.command, "GetTimeZone")
            data.timezone = (tz or {}).get("timezone")

        for attr, coro in (
            ("spl_ids", self.client.spl_list),
            ("cpl_ids", self.client.cpl_list),
            ("kdm_ids", self.client.kdm_list),
        ):
            result = await self._try(coro)
            if result is not None:
                setattr(data, attr, result)

        # Populate titles for any CPL we have not seen yet, bounded per cycle so
        # a large library cannot stall the poll loop.
        unknown = [c for c in data.cpl_ids if c not in data.cpl_info][:10]
        for cpl_id in unknown:
            info = await self._try(self.client.cpl_info, cpl_id)
            if info:
                data.cpl_info[cpl_id] = info

    async def _resolve_schedule(
        self, schedule_id: int | None, data: IMSData
    ) -> dict[str, Any]:
        """Look up a schedule entry and remember its SPL's name.

        Cached per schedule id: a schedule's annotation and SPL don't change
        once created, so this only hits the network for ids not seen before -
        in practice at most two calls per poll (current and next), and
        usually zero once both are cached.
        """
        if not schedule_id:
            return {}
        cached = data.schedule_info.get(schedule_id)
        if cached is not None:
            return cached

        info = (
            await self._try(self.client.command, "GetScheduleInfo2", schedule_id)
            or {}
        )
        data.schedule_info[schedule_id] = info

        spl_id = info.get("spl_id")
        name = info.get("annotation_text")
        if spl_id and name:
            data.spl_names[spl_id] = name
        return info

    async def _try(self, func, *args: Any) -> Any:
        """Run an optional call, swallowing command-level errors."""
        try:
            return await func(*args)
        except IMSCommandError as err:
            _LOGGER.debug("Optional call failed: %s", err)
        except IMSConnectionError as err:
            _LOGGER.debug("Optional call lost the connection: %s", err)
        except Exception as err:  # noqa: BLE001 - never break the poll loop
            _LOGGER.debug("Optional call raised: %s", err)
        return None

    async def _try_field(self, command: str, key: str) -> Any:
        result = await self._try(self.client.command, command)
        return (result or {}).get(key)
