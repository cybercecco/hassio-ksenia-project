"""Constants for the Ksenia Lares 4.0 Gateway integration."""

from __future__ import annotations

from enum import StrEnum

DOMAIN = "ksenia_lares4"
MANUFACTURER = "Ksenia Security"
MODEL = "Lares 4.0"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_SSL = "ssl"
CONF_USERNAME = "username"
CONF_PIN = "pin"

DEFAULT_PORT_SSL = 443
DEFAULT_PORT_PLAIN = 80
DEFAULT_SSL = True

PLATFORMS: list[str] = [
    "alarm_control_panel",
    "binary_sensor",
    "sensor",
    "button",
]

SIGNAL_UPDATE = f"{DOMAIN}_update"

SETUP_TIMEOUT = 45

READ_TYPES = [
    "SCENARIOS",
    "PARTITIONS",
    "ZONES",
    "STATUS_PARTITIONS",
    "STATUS_ZONES",
    "STATUS_SYSTEM",
]

REALTIME_TYPES = [
    "STATUS_PARTITIONS",
    "STATUS_ZONES",
    "STATUS_SYSTEM",
]


class ArmCode(StrEnum):
    """ARM.S / partition ARM codes from the panel."""

    DISARMED = "D"
    FULLY_ARMED = "T"
    PARTIALLY_ARMED = "P"
    FULLY_ARMED_EXIT_DELAY = "T_OUT"
    PARTIALLY_ARMED_EXIT_DELAY = "P_OUT"
    FULLY_ARMED_ENTRY_DELAY = "T_IN"
    PARTIALLY_ARMED_ENTRY_DELAY = "P_IN"
    IMMEDIATE_ARMING = "IA"
    DELAYED_ARMING = "DA"
    EXIT_DELAY_ACTIVE = "OT"
    ENTRY_DELAY_ACTIVE = "IT"


class AlarmFlag(StrEnum):
    """AST (Alarm Status) values for partitions."""

    NO_ALARM = "OK"
    ONGOING = "AL"
    MEMORY = "AM"


SCENARIO_CATEGORIES = {
    "DISARM": "disarm",
    "ARM": "arm_away",
    "PARTIAL": "arm_home",
}
