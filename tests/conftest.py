"""Shared fixtures for the Dolby IMS3000 tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT, CONF_SCAN_INTERVAL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dolby_ims3000.const import (
    CONF_ALLOW_CONTROL,
    CONF_CATALOG_INTERVAL,
    CONF_TIMEOUT,
    DOMAIN,
)

from .const import (
    COMMAND_REPLIES,
    CPL_ID,
    HOST,
    PORT,
    PRODUCT_INFO,
    SERIAL,
    SPL_ID,
    STATUS,
    TITLE,
)


def _command(name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Answer the raw commands the coordinator sends."""
    return dict(COMMAND_REPLIES.get(name, {}))


def _make_client() -> MagicMock:
    """Build a stand-in for IMSClient that answers like a playing server."""
    client = MagicMock()
    client.host = HOST
    client.port = PORT
    client.status = AsyncMock(return_value=dict(STATUS))
    client.product_info = AsyncMock(return_value=dict(PRODUCT_INFO))
    client.scheduler_enabled = AsyncMock(return_value=True)
    client.set_scheduler = AsyncMock()
    client.command = AsyncMock(side_effect=_command)
    client.spl_list = AsyncMock(return_value=[SPL_ID])
    client.cpl_list = AsyncMock(return_value=[CPL_ID])
    client.kdm_list = AsyncMock(return_value=[])
    client.cpl_info = AsyncMock(
        return_value={"content_title_text": TITLE, "content_kind_text": "feature"}
    )
    client.play = AsyncMock(return_value={})
    client.pause = AsyncMock(return_value={})
    client.schedule_spl = AsyncMock(return_value=42)
    client.disconnect = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the integration from custom_components."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured server."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Dolby IMS3000",
        unique_id=SERIAL,
        data={CONF_HOST: HOST, CONF_PORT: PORT},
        options={
            CONF_ALLOW_CONTROL: True,
            CONF_SCAN_INTERVAL: 10,
            CONF_CATALOG_INTERVAL: 30,
            CONF_TIMEOUT: 10,
        },
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Skip setting up the entry itself while testing the config flow."""
    with patch(
        "custom_components.dolby_ims3000.async_setup_entry", return_value=True
    ) as setup_entry:
        yield setup_entry


@pytest.fixture
def mock_flow_client() -> Generator[MagicMock]:
    """The client the config flow uses to probe a server."""
    client = _make_client()
    with patch(
        "custom_components.dolby_ims3000.config_flow.IMSClient", return_value=client
    ):
        yield client


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """The client a loaded config entry polls."""
    client = _make_client()
    with patch("custom_components.dolby_ims3000.IMSClient", return_value=client):
        yield client
