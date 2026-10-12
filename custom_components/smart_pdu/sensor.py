"""Sensor platform: electrical metrics for Smart PDU."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NUM_OUTLETS
from . import PduHub

# (metrics 键, 名称, device_class, 单位, state_class)
MAIN_SENSORS = [
    ("p", "Power", SensorDeviceClass.POWER, "W", SensorStateClass.MEASUREMENT),
    ("v", "Voltage", SensorDeviceClass.VOLTAGE, "V", SensorStateClass.MEASUREMENT),
    ("c", "Total Current", SensorDeviceClass.CURRENT, "A", SensorStateClass.MEASUREMENT),
    ("e", "Energy", SensorDeviceClass.ENERGY, "kWh", SensorStateClass.TOTAL_INCREASING),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    hub: PduHub = hass.data[DOMAIN][entry.entry_id]
    entities = [PduSensor(hub, entry, *spec) for spec in MAIN_SENSORS]
    entities.extend(
        PduOutletCurrentSensor(hub, entry, outlet)
        for outlet in range(1, NUM_OUTLETS + 1)
    )
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Smart PDU",
        manufacturer="Generic",
        model="SmartPDU-8P",
        sw_version="2.0.0",
    )


class PduSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(
        self,
        hub: PduHub,
        entry: ConfigEntry,
        key: str,
        name: str,
        device_class: SensorDeviceClass,
        unit: str,
        state_class: SensorStateClass,
    ) -> None:
        super().__init__(hub.coordinator)
        self.hub = hub
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}-{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = state_class
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data["metrics"].get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self.hub.has_clients

    @property
    def extra_state_attributes(self) -> dict:
        return {"pdu_clients": self.hub.client_peers}


class PduOutletCurrentSensor(PduSensor):
    """插座 N 电流，对应 PVC_Info 中的 c0..c7（Node-RED pdu-1 对应 c0）。"""

    def __init__(self, hub: PduHub, entry: ConfigEntry, outlet: int) -> None:
        super().__init__(
            hub,
            entry,
            f"c{outlet - 1}",
            f"Outlet {outlet} Current",
            SensorDeviceClass.CURRENT,
            "A",
            SensorStateClass.MEASUREMENT,
        )
        self._attr_unique_id = f"{entry.entry_id}-outlet-{outlet}-current"
