"""The Dolby IMS3000 integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import IMSClient, IMSError
from .const import (
    ATTR_ANNOTATION,
    ATTR_COMMAND,
    ATTR_CPL_ID,
    ATTR_DELAY,
    ATTR_PARAMS,
    ATTR_SPL_ID,
    ATTR_XML,
    CONF_TIMEOUT,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_TIMEOUT,
    DOMAIN,
    SERVICE_INGEST_JOB,
    SERVICE_PLAY_SPL,
    SERVICE_RAW_COMMAND,
    SERVICE_VALIDATE_CPL,
)
from .coordinator import IMSCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Runtime data is the coordinator; see async_setup_entry.
IMSConfigEntry = ConfigEntry[IMSCoordinator]

_ENTRY_SELECTOR = {vol.Required("entry_id"): cv.string}

PLAY_SPL_SCHEMA = vol.Schema(
    {
        **_ENTRY_SELECTOR,
        vol.Required(ATTR_SPL_ID): cv.string,
        vol.Optional(ATTR_DELAY, default=5): vol.All(int, vol.Range(min=0, max=3600)),
        vol.Optional(ATTR_ANNOTATION, default="Home Assistant"): cv.string,
    }
)

VALIDATE_CPL_SCHEMA = vol.Schema(
    {**_ENTRY_SELECTOR, vol.Required(ATTR_CPL_ID): cv.string}
)

INGEST_JOB_SCHEMA = vol.Schema({**_ENTRY_SELECTOR, vol.Required(ATTR_XML): cv.string})

RAW_COMMAND_SCHEMA = vol.Schema(
    {
        **_ENTRY_SELECTOR,
        vol.Required(ATTR_COMMAND): cv.string,
        vol.Optional(ATTR_PARAMS, default={}): dict,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: IMSConfigEntry) -> bool:
    """Set up a configured server."""
    client = IMSClient(
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        timeout=entry.options.get(CONF_TIMEOUT, DEFAULT_TIMEOUT),
    )

    coordinator = IMSCoordinator(
        hass,
        entry,
        client,
        scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    entry.async_on_unload(client.disconnect)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IMSConfigEntry) -> bool:
    """Tear down."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: IMSConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _coordinator_for(hass: HomeAssistant, call: ServiceCall) -> IMSCoordinator:
    entry_id = call.data["entry_id"]
    entry: IMSConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        raise HomeAssistantError(f"No loaded Dolby IMS3000 entry with id {entry_id}")
    return entry.runtime_data


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services once."""
    if hass.services.has_service(DOMAIN, SERVICE_PLAY_SPL):
        return

    async def handle_play_spl(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call)
        start = datetime.now(timezone.utc) + timedelta(seconds=call.data[ATTR_DELAY])
        stamp = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            schedule_id = await coordinator.client.schedule_spl(
                spl_id=call.data[ATTR_SPL_ID],
                when=stamp,
                annotation=call.data[ATTR_ANNOTATION],
            )
        except IMSError as err:
            raise HomeAssistantError(f"Could not schedule show: {err}") from err
        await coordinator.async_request_refresh()
        return {"schedule_id": schedule_id, "start_time": stamp}

    async def handle_validate_cpl(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            result = await coordinator.client.command(
                "ValidateCPL",
                uuid=call.data[ATTR_CPL_ID],
                time=stamp,
                level=0,
                _check_return_code=False,
            )
        except IMSError as err:
            raise HomeAssistantError(f"Could not validate CPL: {err}") from err
        return {k: v for k, v in result.items() if not k.startswith("_")}

    async def handle_ingest_job(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call)
        try:
            result = await coordinator.client.command(
                "IngestAddJob", xml=call.data[ATTR_XML]
            )
        except IMSError as err:
            raise HomeAssistantError(f"Could not queue ingest job: {err}") from err
        return {"job_id": result.get("job_id")}

    async def handle_raw_command(call: ServiceCall) -> dict:
        """Escape hatch: issue any known command and return the parsed reply.

        Intended for validating this integration against real hardware.
        """
        coordinator = _coordinator_for(hass, call)
        params = dict(call.data[ATTR_PARAMS])
        params["_check_return_code"] = False
        try:
            result = await coordinator.client.command(call.data[ATTR_COMMAND], **params)
        except IMSError as err:
            raise HomeAssistantError(f"Command failed: {err}") from err
        return {k: str(v) for k, v in result.items()}

    hass.services.async_register(
        DOMAIN, SERVICE_PLAY_SPL, handle_play_spl,
        schema=PLAY_SPL_SCHEMA, supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_VALIDATE_CPL, handle_validate_cpl,
        schema=VALIDATE_CPL_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_INGEST_JOB, handle_ingest_job,
        schema=INGEST_JOB_SCHEMA, supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RAW_COMMAND, handle_raw_command,
        schema=RAW_COMMAND_SCHEMA, supports_response=SupportsResponse.ONLY,
    )
