"""Polling coordinator for the Dolby IMS3000."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IMSClient, IMSCommandError, IMSConnectionError
from .const import (
    CONF_CATALOG_INTERVAL,
    DEFAULT_CATALOG_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class IMSData:
    """Everything the entities read from."""

    available: bool = False
    status: dict[str, Any] = field(default_factory=dict)
    product: dict[str, Any] = field(default_factory=dict)
    api_version: dict[str, Any] = field(default_factory=dict)
    timezone: str | None = None
    scheduler_enabled: bool | None = None
    current_schedule: int | None = None
    next_schedule: int | None = None
    next_schedule_info: dict[str, Any] = field(default_factory=dict)
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
        self._catalog_every: int = entry.options.get(
            CONF_CATALOG_INTERVAL, DEFAULT_CATALOG_INTERVAL
        )

    async def _async_update_data(self) -> IMSData:
        data = self.data or IMSData()
        catalogue_due = self._tick % max(self._catalog_every, 1) == 0
        self._tick += 1

        try:
            data.status = await self.client.status()
        except IMSCommandError as err:
            # A command-level failure still means the box is reachable.
            data.last_error = str(err)
            _LOGGER.debug("Status command rejected: %s", err)
        except IMSConnectionError as err:
            data.available = False
            data.last_error = str(err)
            raise UpdateFailed(str(err)) from err

        data.available = True
        data.last_error = None

        # Cheap per-poll extras.  Failures here must not mark the device down.
        data.scheduler_enabled = await self._try(self.client.scheduler_enabled)
        data.current_schedule = await self._try_field(
            "GetCurrentSchedule", "schedule_id"
        )
        data.next_schedule = await self._try_field("GetNextSchedule", "schedule_id")

        if catalogue_due:
            await self._refresh_catalogue(data)

        # Resolve the title of whatever is on screen, cached by CPL uuid.
        cpl_id = data.status.get("current_cpl_id")
        if cpl_id and cpl_id not in data.cpl_info:
            info = await self._try(self.client.cpl_info, cpl_id)
            if info:
                data.cpl_info[cpl_id] = info

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

        if data.next_schedule:
            info = await self._try(
                self.client.command, "GetScheduleInfo2", data.next_schedule
            )
            data.next_schedule_info = info or {}

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
