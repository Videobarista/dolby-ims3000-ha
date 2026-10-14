"""Tests for the Dolby IMS3000 config, reconfigure and options flows."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dolby_ims3000.api import IMSConnectionError, IMSError
from custom_components.dolby_ims3000.const import (
    CONF_ALLOW_CONTROL,
    CONF_CATALOG_INTERVAL,
    CONF_TIMEOUT,
    DOMAIN,
)

from .const import HOST, PORT, PRODUCT_INFO, SERIAL

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

USER_INPUT: dict[str, Any] = {
    CONF_HOST: HOST,
    CONF_PORT: PORT,
    CONF_NAME: "Screen 1",
    CONF_ALLOW_CONTROL: True,
}

PROBE_FAILURES = [
    pytest.param(IMSConnectionError("refused"), None, "cannot_connect", id="cannot_connect"),
    pytest.param(IMSError("garbage"), None, "invalid_response", id="invalid_response"),
    pytest.param(None, {}, "invalid_response", id="no_product_name"),
    pytest.param(RuntimeError("boom"), None, "unknown", id="unknown"),
]


async def test_user_flow(hass: HomeAssistant, mock_flow_client: MagicMock) -> None:
    """A reachable server is added with its serial as unique id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Screen 1"
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: PORT}
    assert result["options"][CONF_ALLOW_CONTROL] is True
    assert result["result"].unique_id == SERIAL
    mock_flow_client.disconnect.assert_awaited()


async def test_user_flow_without_serial(
    hass: HomeAssistant, mock_flow_client: MagicMock
) -> None:
    """A server that reports no serial is still added, without a unique id."""
    mock_flow_client.product_info.return_value = {"product_name": "IMS3000"}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id is None


@pytest.mark.parametrize(("side_effect", "return_value", "error"), PROBE_FAILURES)
async def test_user_flow_errors_and_recovery(
    hass: HomeAssistant,
    mock_flow_client: MagicMock,
    side_effect: Exception | None,
    return_value: dict[str, Any] | None,
    error: str,
) -> None:
    """A failed probe shows an error, and the flow recovers on the next try."""
    mock_flow_client.product_info.side_effect = side_effect
    if return_value is not None:
        mock_flow_client.product_info.return_value = return_value

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    mock_flow_client.disconnect.assert_awaited()

    mock_flow_client.product_info.side_effect = None
    mock_flow_client.product_info.return_value = dict(PRODUCT_INFO)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_same_host_aborts(
    hass: HomeAssistant,
    mock_flow_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Adding a host and port that are already configured aborts before probing."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_flow_client.product_info.assert_not_awaited()


async def test_user_flow_same_serial_updates_host(
    hass: HomeAssistant,
    mock_flow_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The same server at a new address updates the existing entry and aborts."""
    mock_config_entry.add_to_hass(hass)
    new_input = {**USER_INPUT, CONF_HOST: "10.0.0.51"}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new_input)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_HOST] == "10.0.0.51"


async def _start_reconfigure(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    entry.add_to_hass(hass)
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )


async def test_reconfigure_flow(
    hass: HomeAssistant,
    mock_flow_client: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The host and port can be changed after setup."""
    result = await _start_reconfigure(hass, mock_config_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.60", CONF_PORT: 11731}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == {CONF_HOST: "10.0.0.60", CONF_PORT: 11731}


@pytest.mark.parametrize(("side_effect", "return_value", "error"), PROBE_FAILURES)
async def test_reconfigure_flow_errors_and_recovery(
    hass: HomeAssistant,
    mock_flow_client: MagicMock,
    mock_config_entry: MockConfigEntry,
    side_effect: Exception | None,
    return_value: dict[str, Any] | None,
    error: str,
) -> None:
    """A failed probe during reconfigure shows an error and can be retried."""
    mock_flow_client.product_info.side_effect = side_effect
    if return_value is not None:
        mock_flow_client.product_info.return_value = return_value

    result = await _start_reconfigure(hass, mock_config_entry)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.60", CONF_PORT: PORT}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_flow_client.product_info.side_effect = None
    mock_flow_client.product_info.return_value = dict(PRODUCT_INFO)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "10.0.0.60", CONF_PORT: PORT}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_HOST] == "10.0.0.60"


async def test_options_flow(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Polling and control options can be changed."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            CONF_ALLOW_CONTROL: False,
            CONF_SCAN_INTERVAL: 15,
            CONF_CATALOG_INTERVAL: 60,
            CONF_TIMEOUT: 5,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_ALLOW_CONTROL] is False
    assert mock_config_entry.options[CONF_SCAN_INTERVAL] == 15
    assert mock_config_entry.options[CONF_CATALOG_INTERVAL] == 60
    assert mock_config_entry.options[CONF_TIMEOUT] == 5
