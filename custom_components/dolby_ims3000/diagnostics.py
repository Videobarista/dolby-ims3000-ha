"""Diagnostics for the Dolby IMS3000 integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import IMSConfigEntry

# Certificates and serials identify a specific site.  Film titles, show names
# and schedule annotations can be confidential before a release or a festival
# screening, so they are left out of a report that may be posted publicly.
TO_REDACT = {
    "annotation_text",
    "certificate",
    "content_title_text",
    "host",
    "product_id",
    "product_serial",
    "spl_names",
    "title",
    "username",
    "x509_subject_name",
}

REDACTED = "**REDACTED**"


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
    data["spl_names_count"] = len(data.get("spl_names", {}))

    # Redaction works on keys, but the address also appears inside error text
    # ("cannot connect to 10.0.0.50:11730").
    host = str(entry.data.get(CONF_HOST, ""))
    last_error = data.get("last_error")
    if host and isinstance(last_error, str):
        data["last_error"] = last_error.replace(host, REDACTED)

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
