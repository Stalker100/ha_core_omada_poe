"""Support for TPLink Omada binary sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from tplink_omada_client.definitions import (
    DeviceStatus,
    DeviceStatusCategory,
    PortType,
)
from tplink_omada_client.devices import (
    OmadaDevice,
    OmadaListDevice,
    OmadaSwitch,
    OmadaSwitchPortDetails,
    OmadaSwitchPortStatus
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import OmadaConfigEntry
from .const import OmadaDeviceStatus
from .controller import OmadaSwitchPortCoordinator
from .coordinator import OmadaDevicesCoordinator, OmadaCoordinator
from .entity import OmadaDeviceEntity

TPort = TypeVar("TPort")
TPortS = TypeVar("TPortS")
TDevice = TypeVar("TDevice", bound="OmadaDevice")
TCoordinator = TypeVar("TCoordinator", bound="OmadaCoordinator[Any]")

PARALLEL_UPDATES = 0

# Useful low level status categories, mapped to a more descriptive status.
DEVICE_STATUS_MAP = {
    DeviceStatus.PROVISIONING: OmadaDeviceStatus.PENDING,
    DeviceStatus.CONFIGURING: OmadaDeviceStatus.PENDING,
    DeviceStatus.UPGRADING: OmadaDeviceStatus.PENDING,
    DeviceStatus.REBOOTING: OmadaDeviceStatus.PENDING,
    DeviceStatus.ADOPT_FAILED: OmadaDeviceStatus.ADOPT_FAILED,
    DeviceStatus.ADOPT_FAILED_WIRELESS: OmadaDeviceStatus.ADOPT_FAILED,
    DeviceStatus.MANAGED_EXTERNALLY: OmadaDeviceStatus.MANAGED_EXTERNALLY,
    DeviceStatus.MANAGED_EXTERNALLY_WIRELESS: OmadaDeviceStatus.MANAGED_EXTERNALLY,
}

# High level status categories, suitable for most device statuses.
DEVICE_STATUS_CATEGORY_MAP = {
    DeviceStatusCategory.DISCONNECTED: OmadaDeviceStatus.DISCONNECTED,
    DeviceStatusCategory.CONNECTED: OmadaDeviceStatus.CONNECTED,
    DeviceStatusCategory.PENDING: OmadaDeviceStatus.PENDING,
    DeviceStatusCategory.HEARTBEAT_MISSED: OmadaDeviceStatus.HEARTBEAT_MISSED,
    DeviceStatusCategory.ISOLATED: OmadaDeviceStatus.ISOLATED,
}


def _map_device_status(device: OmadaListDevice) -> str | None:
    """Map the API device status to the best available descriptive device status."""
    display_status = DEVICE_STATUS_MAP.get(
        device.status
    ) or DEVICE_STATUS_CATEGORY_MAP.get(device.status_category)
    return display_status.value if display_status else None


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: OmadaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    controller = config_entry.runtime_data

    devices_coordinator = controller.devices_coordinator

    async def _create_device_sensor_entities(
        device: OmadaListDevice,
    ) -> None:
        """Create sensor entities for a device."""
        async_add_entities(
            [
                OmadaDeviceSensor(devices_coordinator, device, desc)
                for desc in OMADA_DEVICE_SENSORS
                if desc.exists_func(device)
            ]
        )

    await controller.async_register_device_entities(
        lambda _: True,
        _create_device_sensor_entities,
    )

    async def _create_switch_port_entities(
        device: OmadaListDevice,
    ) -> None:
        """Create entities for a switch's ports."""
        omada_client = controller.omada_client
        switch = await omada_client.get_switch(device)
        coordinator = controller.get_switch_port_coordinator(switch)
        await coordinator.async_request_refresh()

        entities: list[Entity] = []
        entities.extend(
            OmadaDevicePortSensorEntity[
                OmadaSwitchPortCoordinator, OmadaSwitch, OmadaSwitchPortDetails
            ](
                coordinator,
                switch,
                port,
                port.port_id,
                desc,
                port_name=_get_switch_port_base_name(port),
            )
            for port in coordinator.data.values()
            for desc in SWITCH_PORT_DETAILS_SENSORS
            if desc.exists_func(switch, port)
        )
        async_add_entities(entities)

    # Register switch port entities for switches that are connected, such that we can determine the port information
    await controller.async_register_device_entities(
        device_filter=lambda d: (
            d.type == "switch" and d.status_category == DeviceStatusCategory.CONNECTED
        ),
        entity_callback=_create_switch_port_entities,
    )


def _get_switch_port_base_name(port: OmadaSwitchPortDetails) -> str:
    """Get display name for a switch port."""

    if port.name == f"Port{port.port}":
        return str(port.port)
    return f"{port.port} ({port.name})"


@dataclass(frozen=True, kw_only=True)
class OmadaDevicePortSensorEntityDescription(
    SensorEntityDescription, Generic[TCoordinator, TDevice, TPort]
):
    """Entity description for a sensor derived from a network port on an Omada device."""

    exists_func: Callable[[TDevice, TPort], bool] = lambda _, p: True
    coordinator_update_func: Callable[[TCoordinator, TDevice, TPort], TPort | None]
    update_func: Callable[[TPort | None], StateType]

@dataclass(frozen=True, kw_only=True)
class OmadaSwitchPortSensorEntityDescription(
    OmadaDevicePortSensorEntityDescription[
        OmadaSwitchPortCoordinator, OmadaSwitch, OmadaSwitchPortDetails
    ]
):
    """Entity description for a toggle switch for a feature of a Port on an Omada Switch."""

    coordinator_update_func: Callable[
        [OmadaSwitchPortCoordinator, OmadaSwitch, OmadaSwitchPortDetails],
        OmadaSwitchPortDetails | None
    ] = lambda coord, _, port: coord.data.get(port.port_id)

@dataclass(frozen=True, kw_only=True)
class OmadaDeviceSensorEntityDescription(SensorEntityDescription):
    """Entity description for a status derived from an Omada device in the device list."""

    exists_func: Callable[[OmadaListDevice], bool] = lambda _: True
    update_func: Callable[[OmadaListDevice], StateType]


OMADA_DEVICE_SENSORS: list[OmadaDeviceSensorEntityDescription] = [
    OmadaDeviceSensorEntityDescription(
        key="device_status",
        translation_key="device_status",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        update_func=_map_device_status,
        options=[v.value for v in OmadaDeviceStatus],
    ),
    OmadaDeviceSensorEntityDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        update_func=lambda device: device.cpu_usage,
    ),
    OmadaDeviceSensorEntityDescription(
        key="mem_usage",
        translation_key="mem_usage",
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        update_func=lambda device: device.mem_usage,
    ),
]


SWITCH_PORT_DETAILS_SENSORS: list[OmadaSwitchPortSensorEntityDescription] = [
    OmadaSwitchPortSensorEntityDescription(
        key="poe_usage",
        translation_key="poe_usage",
        exists_func=(
            lambda d, p: (
                d.device_capabilities.supports_poe
                and p.supports_poe
                and p.type != PortType.SFP
            )
        ),
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        update_func=lambda p: p.port_status.poe_power if p else None
    )

    # Uncomment below to add more sensors for ports

    #OmadaSwitchPortSensorEntityDescription(
    #    key="",                # name of the sensor after the port name
    #    translation_key="",
    #    exists_func=(
    #        lambda d, p: # condition for whether this sensor should be created for a given port
    #    ),
    #    entity_category=EntityCategory.DIAGNOSTIC,
    #    state_class=SensorStateClass.MEASUREMENT,
    #    native_unit_of_measurement="",  # unit of measurement, if applicable
    #    update_func=lambda p: # value to return for the sensor, given the latest port details
    #)

]


class OmadaDeviceSensor(OmadaDeviceEntity[OmadaDevicesCoordinator], SensorEntity):
    """Sensor for property of a generic Omada device."""

    entity_description: OmadaDeviceSensorEntityDescription

    def __init__(
        self,
        coordinator: OmadaDevicesCoordinator,
        device: OmadaListDevice,
        entity_description: OmadaDeviceSensorEntityDescription,
    ) -> None:
        """Initialize the device sensor."""
        super().__init__(coordinator, device)
        self.entity_description = entity_description
        self._attr_unique_id = f"{device.mac}_{entity_description.key}"

    @property
    def native_value(self) -> StateType:
        """Return the state of the sensor."""
        return self.entity_description.update_func(
            self.coordinator.data[self.device.mac]
        )


class OmadaDevicePortSensorEntity(
    OmadaDeviceEntity[TCoordinator],
    SensorEntity,
    Generic[TCoordinator, TDevice, TPort],
):
    """Generic sensor entity for a Network Port of an Omada Device."""

    entity_description: OmadaDevicePortSensorEntityDescription[
        TCoordinator, TDevice, TPort
    ]

    def __init__(
        self,
        coordinator: TCoordinator,
        device: TDevice,
        port_details: TPort,
        port_id: str,
        entity_description: OmadaDevicePortSensorEntityDescription[
            TCoordinator, TDevice, TPort
        ],
        port_name: str | None = None,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        self.entity_description = entity_description
        self._device = device
        self._port_details = port_details
        self._attr_unique_id = f"{device.mac}_{port_id}_{entity_description.key}"
        self._attr_translation_placeholders = {"port_name": port_name or port_id}

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        self.native_value


    @property
    def available(self) -> bool:
        """Return true if entity is available."""
        return bool(
            super().available
            and self._port_details
            and self.entity_description.exists_func(self._device, self._port_details)
        )

    @property
    def native_value(self) -> StateType:
        latest_port_update = self.entity_description.coordinator_update_func(
            self.coordinator, self._device, self._port_details
        )
        if latest_port_update:
            self._port_details = latest_port_update

        return self.entity_description.update_func(latest_port_update)
