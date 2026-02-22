"""Constants for the TP-Link Omada integration."""

from enum import StrEnum
from typing import Final

DOMAIN = "tplink_omada"
CONF_PORT_POE: Final = "port_poe"
CONF_PORT_SPEED: Final = "port_speed"


class OmadaDeviceStatus(StrEnum):
    """Possible composite status values for Omada devices."""

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    PENDING = "pending"
    HEARTBEAT_MISSED = "heartbeat_missed"
    ISOLATED = "isolated"
    ADOPT_FAILED = "adopt_failed"
    MANAGED_EXTERNALLY = "managed_externally"
