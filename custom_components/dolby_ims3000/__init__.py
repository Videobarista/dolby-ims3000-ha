"""The Dolby IMS3000 integration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .api import IMSClient, IMSError, IMSInvalidRequest
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
from .klv import READ_ONLY_REQUESTS, REQUEST_BY_NAME

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.MEDIA_PLAYER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Runtime data is the coordinator; see async_setup_entry.
IMSConfigEntry = ConfigEntry[IMSCoordinator]

# Set up from the UI only; there is no YAML configuration.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# The annotation goes into a 128-byte NUL-padded field; one byte is kept free so
# the server always finds a terminator.
ANNOTATION_MAX_BYTES = 127
# An ingest job description is a few kilobytes of XML at most.
XML_MAX_BYTES = 64 * 1024
# Bounds for the raw_command escape hatch.
RAW_MAX_PARAMS = 8
RAW_MAX_PARAM_LENGTH = 256
RAW_MAX_RESPONSE_VALUE = 2048


def _uuid(value: Any) -> str:
    """Accept a UUID in any common spelling and return its canonical form."""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as err:
        raise vol.Invalid(
            "must be a UUID, such as 851cc838-022e-43b7-9fee-18656bdfc995"
        ) from err


def _text(max_bytes: int, *, single_line: bool) -> Any:
    """Text that fits ``max_bytes`` once encoded, optionally on one line."""

    def validate(value: Any) -> str:
        text = cv.string(value)
        if single_line and any(ord(char) < 0x20 for char in text):
            raise vol.Invalid("must not contain line breaks or control characters")
        if len(text.encode("utf-8")) > max_bytes:
            raise vol.Invalid(f"must be at most {max_bytes} bytes long")
        return text

    return validate


_ENTRY_SELECTOR = {vol.Required("entry_id"): cv.string}

PLAY_SPL_SCHEMA = vol.Schema(
    {
        **_ENTRY_SELECTOR,
        vol.Required(ATTR_SPL_ID): _uuid,
        vol.Optional(ATTR_DELAY, default=5): vol.All(int, vol.Range(min=0, max=3600)),
        vol.Optional(ATTR_ANNOTATION, default="Home Assistant"): _text(
            ANNOTATION_MAX_BYTES, single_line=True
        ),
    }
)

VALIDATE_CPL_SCHEMA = vol.Schema({**_ENTRY_SELECTOR, vol.Required(ATTR_CPL_ID): _uuid})

INGEST_JOB_SCHEMA = vol.Schema(
    {
        **_ENTRY_SELECTOR,
        vol.Required(ATTR_XML): _text(XML_MAX_BYTES, single_line=False),
    }
)

RAW_COMMAND_SCHEMA = vol.Schema(
    {
        **_ENTRY_SELECTOR,
        # Read-only commands only: anything that changes the server has its
        # own entity or action, guarded by the "allow playback control" option.
        vol.Required(ATTR_COMMAND): vol.In(sorted(READ_ONLY_REQUESTS)),
        vol.Optional(ATTR_PARAMS, default={}): vol.All(
            vol.Schema(
                {
                    cv.string: vol.Any(
                        bool,
                        int,
                        float,
                        vol.All(cv.string, vol.Length(max=RAW_MAX_PARAM_LENGTH)),
                    )
                }
            ),
            vol.Length(max=RAW_MAX_PARAMS),
        ),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain actions once, independent of any config entry."""
    _async_register_services(hass)
    return True


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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IMSConfigEntry) -> bool:
    """Tear down."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_entry(hass: HomeAssistant, entry: IMSConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _coordinator_for(hass: HomeAssistant, call: ServiceCall) -> IMSCoordinator:
    entry_id = call.data["entry_id"]
    entry: IMSConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(f"No Dolby IMS3000 entry with id {entry_id}")
    if entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(f"Dolby IMS3000 entry {entry.title} is not loaded")
    return entry.runtime_data


def _require_control(coordinator: IMSCoordinator) -> None:
    """Refuse an action that changes the server when control is switched off."""
    if not coordinator.control_allowed:
        raise ServiceValidationError(
            f"Control is disabled for {coordinator.entry.title}. Enable "
            "\"Allow playback control\" in the integration options first."
        )


def _shorten(value: Any) -> str:
    """Keep action responses readable, even for a reply full of raw hex."""
    text = str(value)
    if len(text) <= RAW_MAX_RESPONSE_VALUE:
        return text
    hidden = len(text) - RAW_MAX_RESPONSE_VALUE
    return f"{text[:RAW_MAX_RESPONSE_VALUE]}... ({hidden} more characters)"


def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain actions."""

    async def handle_play_spl(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call)
        _require_control(coordinator)
        start = datetime.now(UTC) + timedelta(seconds=call.data[ATTR_DELAY])
        stamp = start.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            schedule_id = await coordinator.client.schedule_spl(
                spl_id=call.data[ATTR_SPL_ID],
                when=stamp,
                annotation=call.data[ATTR_ANNOTATION],
            )
        except IMSInvalidRequest as err:
            raise ServiceValidationError(str(err)) from err
        except IMSError as err:
            raise HomeAssistantError(f"Could not schedule show: {err}") from err
        await coordinator.async_request_refresh()
        return {"schedule_id": schedule_id, "start_time": stamp}

    async def handle_validate_cpl(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call)
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            result = await coordinator.client.command(
                "ValidateCPL",
                uuid=call.data[ATTR_CPL_ID],
                time=stamp,
                level=0,
                _check_return_code=False,
            )
        except IMSInvalidRequest as err:
            raise ServiceValidationError(str(err)) from err
        except IMSError as err:
            raise HomeAssistantError(f"Could not validate CPL: {err}") from err
        return {k: v for k, v in result.items() if not k.startswith("_")}

    async def handle_ingest_job(call: ServiceCall) -> dict:
        coordinator = _coordinator_for(hass, call)
        _require_control(coordinator)
        try:
            result = await coordinator.client.command(
                "IngestAddJob", xml=call.data[ATTR_XML]
            )
        except IMSInvalidRequest as err:
            raise ServiceValidationError(str(err)) from err
        except IMSError as err:
            raise HomeAssistantError(f"Could not queue ingest job: {err}") from err
        return {"job_id": result.get("job_id")}

    async def handle_raw_command(call: ServiceCall) -> dict:
        """Escape hatch: issue a read-only command and return the parsed reply."""
        coordinator = _coordinator_for(hass, call)
        command = call.data[ATTR_COMMAND]
        params = dict(call.data[ATTR_PARAMS])
        allowed = {arg.name for arg in REQUEST_BY_NAME[command].args}
        unknown = set(params) - allowed
        if unknown:
            takes = ", ".join(sorted(allowed)) or "no parameters"
            raise ServiceValidationError(
                f"{command} takes {takes}; not {', '.join(sorted(unknown))}"
            )
        try:
            result = await coordinator.client.command(
                command, _check_return_code=False, **params
            )
        except IMSInvalidRequest as err:
            raise ServiceValidationError(str(err)) from err
        except IMSError as err:
            raise HomeAssistantError(f"Command failed: {err}") from err
        return {key: _shorten(value) for key, value in result.items()}

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
