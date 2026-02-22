"""Support for TPLink Omada binary sensors."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from tplink_omada_client.definitions import DeviceStatus, DeviceStatusCategory, PortType
from tplink_omada_client.devices import (
    OmadaDevice,
    OmadaListDevice,
    OmadaSwitch,
    OmadaSwitchPortDetails,
)

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfDataRate, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from . import OmadaConfigEntry
from .const import CONF_PORT_POE, CONF_PORT_SPEED, OmadaDeviceStatus
from .controller import OmadaSwitchPortCoordinator
from .coordinator import OmadaCoordinator, OmadaDevicesCoordinator
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


def _map_port_speed_id_to_speed(speed_id: int) -> int | None:
    """Map the API port speed ID to the actual speed in Megabits per second."""
    return {
        -1: None,
        0: None,
        1: 10,
        2: 100,
        3: 1_000,
        4: 2_500,
        5: 10_000,
    }.get(speed_id)


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
        descriptions = []
        if config_entry.data.get(CONF_PORT_POE):
            descriptions.extend(SWITCH_PORT_POE_DETAILS_SENSORS)
        if config_entry.data.get(CONF_PORT_SPEED):
            descriptions.extend(SWITCH_PORT_SPEED_DETAILS_SENSORS)

        if not descriptions:
            return
        omada_client = controller.omada_client
        switch = await omada_client.get_switch(device)
        coordinator = controller.get_switch_port_coordinator(switch)
        await coordinator.async_request_refresh()

        while not coordinator.data:
            await asyncio.sleep(1)
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
            for desc in descriptions
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
        OmadaSwitchPortDetails | None,
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


SWITCH_PORT_POE_DETAILS_SENSORS: list[OmadaSwitchPortSensorEntityDescription] = [
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
        update_func=lambda p: p.port_status.poe_power if p else None,
    )
]

SWITCH_PORT_SPEED_DETAILS_SENSORS: list[OmadaSwitchPortSensorEntityDescription] = [
    # OmadaSwitchPortSensorEntityDescription(
    #     key="received",
    #     translation_key="bytes_received",
    #     exists_func=(lambda d, p: True),
    #     entity_category=EntityCategory.DIAGNOSTIC,
    #     state_class=SensorStateClass.MEASUREMENT,
    #     native_unit_of_measurement=UnitOfInformation.BYTES,
    #     update_func=lambda p: p.port_status.bytes_rx if p else None,
    # ),
    # OmadaSwitchPortSensorEntityDescription(
    #     key="transmitted",
    #     translation_key="bytes_transmitted",
    #     exists_func=(lambda d, p: True),
    #     entity_category=EntityCategory.DIAGNOSTIC,
    #     state_class=SensorStateClass.MEASUREMENT,
    #     native_unit_of_measurement=UnitOfInformation.BYTES,
    #     update_func=lambda p: p.port_status.bytes_tx if p else None,
    # ),
    OmadaSwitchPortSensorEntityDescription(
        key="max_speed",
        translation_key="speed_defined",
        exists_func=(lambda d, p: True),
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        update_func=lambda p: (
            _map_port_speed_id_to_speed(p.port_status.link_speed)
            if p and p.port_status.link_status == 1
            else None
        ),
    ),
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
        self._do_update()

    def _do_update(self) -> None:
        """Update the entity's state from the coordinator."""
        latest_port_update = self.entity_description.coordinator_update_func(
            self.coordinator, self._device, self._port_details
        )
        if latest_port_update:
            self._port_details = latest_port_update

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
        """Return the state of the sensor."""
        latest_port_update = self.entity_description.coordinator_update_func(
            self.coordinator, self._device, self._port_details
        )
        if latest_port_update:
            self._port_details = latest_port_update

        return self.entity_description.update_func(latest_port_update)

    # @callback
    # def _handle_coordinator_update(self) -> None:
    #     """Handle updated data from the coordinator."""
    #     self._do_update()
    #     self.async_write_ha_state()
