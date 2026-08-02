"""Constants for the Dolby IMS3000 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "dolby_ims3000"
MANUFACTURER: Final = "Dolby"
DEFAULT_NAME: Final = "Dolby IMS3000"
DEFAULT_PORT: Final = 11730
DEFAULT_SCAN_INTERVAL: Final = 10
DEFAULT_TIMEOUT: Final = 10

CONF_ALLOW_CONTROL: Final = "allow_control"
CONF_CATALOG_INTERVAL: Final = "catalog_interval"
CONF_POSITION_UNIT: Final = "position_unit"
CONF_SPL_NAMES: Final = "spl_names"
CONF_TIMEOUT: Final = "timeout"

POSITION_UNIT_SECONDS: Final = "seconds"
POSITION_UNIT_EDIT_UNITS: Final = "edit_units"
POSITION_UNITS: Final = [POSITION_UNIT_SECONDS, POSITION_UNIT_EDIT_UNITS]

# How often the slow-moving catalogue (SPL/CPL/KDM lists, product info) is
# refreshed, in multiples of the fast poll.
DEFAULT_CATALOG_INTERVAL: Final = 30

SERVICE_PLAY_SPL: Final = "play_spl"
SERVICE_VALIDATE_CPL: Final = "validate_cpl"
SERVICE_RAW_COMMAND: Final = "raw_command"
SERVICE_INGEST_JOB: Final = "ingest_job"

ATTR_SPL_ID: Final = "spl_id"
ATTR_CPL_ID: Final = "cpl_id"
ATTR_DELAY: Final = "delay"
ATTR_ANNOTATION: Final = "annotation"
ATTR_COMMAND: Final = "command"
ATTR_PARAMS: Final = "params"
ATTR_XML: Final = "xml"
