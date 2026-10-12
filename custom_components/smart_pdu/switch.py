"""Switch platform: 8 outlet switches for Smart PDU."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NUM_OUTLETS
from . import PduHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: PduHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        PduOutletSwitch(hub, entry, outlet) for outlet in range(1, NUM_OUTLETS + 1)
    )


class PduOutletSwitch(CoordinatorEntity, SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, hub: PduHub, entry: ConfigEntry, outlet: int) -> None:
        super().__init__(hub.coordinator)
        self.hub = hub
        self._outlet = outlet
        self._attr_unique_id = f"{entry.entry_id}-outlet-{outlet}"
        self._attr_name = f"Outlet {outlet}"
        self._attr_icon = "mdi:power-socket-de" if outlet <= 8 else "mdi:power-socket"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Smart PDU",
            manufacturer="Generic",
            model="SmartPDU-8P",
            sw_version="2.0.0",
        )

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data["outlets"].get(self._outlet, False))

    @property
    def available(self) -> bool:
        return super().available and self.hub.has_clients

    @property
    def extra_state_attributes(self) -> dict:
        return {"pdu_clients": self.hub.client_peers}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_set(False)

    async def _async_set(self, on: bool) -> None:
        try:
            await self.hub.async_set_outlet(self._outlet, on)
        except ConnectionError as err:
            raise HomeAssistantError(str(err)) from err
