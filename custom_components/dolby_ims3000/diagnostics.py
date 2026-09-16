"""Diagnostics for the Dolby IMS3000 integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import IMSConfigEntry

# Certificates and serials identify a specific site; redact by default.
TO_REDACT = {"product_serial", "product_id", "certificate", "x509_subject_name", "host"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: IMSConfigEntry
) -> dict[str, Any]:
    """Return everything a bug report needs, minus identifying details."""
    coordinator = entry.runtime_data
    data = asdict(coordinator.data) if coordinator.data else {}

    # The full CPL cache can be enormous; keep it representative instead.
    cpl_info = data.get("cpl_info", {})
    data["cpl_info"] = dict(list(cpl_info.items())[:5])
    data["cpl_info_cached_count"] = len(cpl_info)

    return async_redact_data(
        {
            "entry": {
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            "connected": coordinator.client.connected,
            "armed_spl": coordinator.armed_spl,
            "data": data,
        },
        TO_REDACT,
    )
