"""Shared entity base for the Dolby IMS3000."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import IMSCoordinator


class IMSEntity(CoordinatorEntity[IMSCoordinator]):
    """Base entity: device info, availability, unique ids."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: IMSCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        product = self.coordinator.data.product if self.coordinator.data else {}
        serial = product.get("product_serial") or None
        model = product.get("product_name") or "IMS3000"

        sw_parts = [
            product.get("software_version_major"),
            product.get("software_version_minor"),
            product.get("software_version_revision"),
            product.get("software_version_build"),
        ]
        sw_version = (
            ".".join(str(p) for p in sw_parts) if all(p is not None for p in sw_parts) else None
        )
        hw_parts = [
            product.get("hardware_version_major"),
            product.get("hardware_version_minor"),
            product.get("hardware_version_build"),
        ]
        hw_version = (
            ".".join(str(p) for p in hw_parts) if all(p is not None for p in hw_parts) else None
        )

        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.entry.entry_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=self.coordinator.entry.title,
            serial_number=serial,
            sw_version=sw_version,
            hw_version=hw_version,
            configuration_url=f"http://{self.coordinator.entry.data[CONF_HOST]}/",
        )

    @property
    def available(self) -> bool:
        return bool(
            super().available
            and self.coordinator.data
            and self.coordinator.data.available
        )
