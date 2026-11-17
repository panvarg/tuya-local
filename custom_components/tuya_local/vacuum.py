"""
Setup for different kinds of Tuya vacuum cleaners
"""

import asyncio
import base64
import binascii
import logging

from homeassistant.components.vacuum import (
    SERVICE_CLEAN_SPOT,
    SERVICE_RETURN_TO_BASE,
    SERVICE_STOP,
    Segment,
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.helpers.storage import Store

from .device import TuyaLocalDevice
from .entity import TuyaLocalEntity
from .helpers.config import async_tuya_setup_platform
from .helpers.device_config import TuyaEntityConfig

_LOGGER = logging.getLogger(__name__)

# Segment (room) cleaning, used by devices that define a "clean_segments" dp.
# Format "ilife" (ILIFE A30 Pro and similar):
#   clean_segments = base64(01 01 <4 byte big-endian bitmask>), bit N = room id N
#   room_list      = base64("<map id>,<map name>,<id>,<name>,<id>,<name>,...")
SEGMENT_STORE_VERSION = 1
DEFAULT_SEGMENT_COUNT = 10


def parse_ilife_room_list(raw):
    """Parse an ILIFE room list into [(id, name), ...]; [] if not parseable."""
    if not raw:
        return []
    try:
        text = base64.b64decode(raw).decode("utf-8")
    except binascii.Error, ValueError, UnicodeDecodeError:
        return []
    parts = [p.strip() for p in text.split(",")]
    rooms = []
    for i in range(2, len(parts) - 1, 2):
        if parts[i].isdigit() and parts[i + 1]:
            rooms.append((parts[i], parts[i + 1]))
    return rooms


def encode_ilife_segments(segment_ids):
    """Encode room ids into the ILIFE clean_segments value."""
    mask = 0
    for seg in segment_ids:
        mask |= 1 << int(seg)
    return base64.b64encode(bytes([1, 1]) + mask.to_bytes(4, "big")).decode()


async def async_setup_entry(hass, config_entry, async_add_entities):
    config = {**config_entry.data, **config_entry.options}
    await async_tuya_setup_platform(
        hass,
        async_add_entities,
        config,
        "vacuum",
        TuyaLocalVacuum,
    )


class TuyaLocalVacuum(TuyaLocalEntity, StateVacuumEntity):
    """Representation of a Tuya Vacuum Cleaner"""

    def __init__(self, device: TuyaLocalDevice, config: TuyaEntityConfig):
        """
        Initialise the sensor.
        Args:
            device (TuyaLocalDevice): the device API instance.
            config (TuyaEntityConfig): the configuration for this entity
        """
        super().__init__()
        dps_map = self._init_begin(device, config)
        self._status_dps = dps_map.get("status")
        self._command_dps = dps_map.get("command")
        self._locate_dps = dps_map.get("locate")
        self._power_dps = dps_map.get("power")
        self._activate_dps = dps_map.get("activate")
        self._direction_dps = dps_map.get("direction_control")
        self._error_dps = dps_map.get("error")
        self._fan_dps = dps_map.pop("fan_speed", None)
        self._segments_dps = dps_map.get("clean_segments")
        self._room_list_dps = dps_map.get("room_list")
        self._segment_store = None
        self._segment_cache = None

        if self._status_dps is None:
            raise AttributeError(f"{config.config_id} is missing a status dps")
        self._init_end(dps_map)

    @property
    def supported_features(self):
        """Return the features supported by this vacuum cleaner."""
        support = (
            VacuumEntityFeature.STATE
            | VacuumEntityFeature.STATUS
            | VacuumEntityFeature.SEND_COMMAND
        )
        if self._fan_dps:
            support |= VacuumEntityFeature.FAN_SPEED
        if self._power_dps:
            support |= VacuumEntityFeature.TURN_ON | VacuumEntityFeature.TURN_OFF
        if self._locate_dps:
            support |= VacuumEntityFeature.LOCATE

        cmd_dps = self._command_dps or self._status_dps
        cmd_support = cmd_dps.values(self._device)
        if SERVICE_RETURN_TO_BASE in cmd_support:
            support |= VacuumEntityFeature.RETURN_HOME
        if SERVICE_CLEAN_SPOT in cmd_support:
            support |= VacuumEntityFeature.CLEAN_SPOT
        if SERVICE_STOP in cmd_support:
            support |= VacuumEntityFeature.STOP

        if self._activate_dps:
            support |= VacuumEntityFeature.START | VacuumEntityFeature.PAUSE
        else:
            if "start" in cmd_support:
                support |= VacuumEntityFeature.START
            if "pause" in cmd_support:
                support |= VacuumEntityFeature.PAUSE

        if self._segments_dps:
            support |= VacuumEntityFeature.CLEAN_AREA

        return support

    @property
    def status(self):
        """Return the status of the vacuum cleaner."""
        return self._status_dps.get_value(self._device)

    @property
    def activity(self):
        """Return the state of the vacuum cleaner."""
        status = self.status
        if self._error_dps and self._error_dps.get_value(self._device):
            return VacuumActivity.ERROR
        elif status in [SERVICE_RETURN_TO_BASE, "returning"]:
            return VacuumActivity.RETURNING
        elif status in ["standby", "sleep"]:
            return VacuumActivity.IDLE
        elif status == "paused":
            return VacuumActivity.PAUSED
        elif status in ["charging", "charged", "docked"]:
            return VacuumActivity.DOCKED
        elif self._power_dps and self._power_dps.get_value(self._device) is False:
            return VacuumActivity.IDLE
        elif self._activate_dps and self._activate_dps.get_value(self._device) is False:
            return VacuumActivity.PAUSED
        else:
            return VacuumActivity.CLEANING

    async def async_turn_on(self, **kwargs):
        """Turn on the vacuum cleaner."""
        if self._power_dps:
            _LOGGER.info("%s turning on", self._config.config_id)
            await self._power_dps.async_set_value(self._device, True)

    async def async_turn_off(self, **kwargs):
        """Turn off the vacuum cleaner."""
        if self._power_dps:
            _LOGGER.info("%s turning off", self._config.config_id)
            await self._power_dps.async_set_value(self._device, False)

    async def async_toggle(self, **kwargs):
        """Toggle the vacuum cleaner."""
        dps = self._power_dps or self._activate_dps
        if dps:
            switch_to = not dps.get_value(self._device)
            _LOGGER.info("%s toggling to %s", self._config.config_id, switch_to)
            await dps.async_set_value(self._device, switch_to)

    async def async_start(self):
        dps = self._command_dps or self._status_dps
        if dps and "start" in dps.values(self._device):
            _LOGGER.info("%s starting by command", self._config.config_id)
            await dps.async_set_value(self._device, "start")
        elif self._activate_dps:
            _LOGGER.info("%s activating", self._config.config_id)
            await self._activate_dps.async_set_value(self._device, True)

    async def async_pause(self):
        """Pause the vacuum cleaner."""
        dps = self._command_dps or self._status_dps
        if dps and "pause" in dps.values(self._device):
            _LOGGER.info("%s pausing by command", self._config.config_id)
            await dps.async_set_value(self._device, "pause")
        elif self._activate_dps:
            _LOGGER.info("%s deactivating", self._config.config_id)
            await self._activate_dps.async_set_value(self._device, False)

    async def async_return_to_base(self, **kwargs):
        """Tell the vacuum cleaner to return to its base."""
        dps = self._command_dps or self._status_dps
        if dps and SERVICE_RETURN_TO_BASE in dps.values(self._device):
            _LOGGER.info("%s returning to base", self._config.config_id)
            await dps.async_set_value(self._device, SERVICE_RETURN_TO_BASE)

    async def async_clean_spot(self, **kwargs):
        """Tell the vacuum cleaner do a spot clean."""
        dps = self._command_dps or self._status_dps
        if dps and SERVICE_CLEAN_SPOT in dps.values(self._device):
            _LOGGER.info("%s doing spot clean", self._config.config_id)
            await dps.async_set_value(self._device, SERVICE_CLEAN_SPOT)

    async def async_stop(self, **kwargs):
        """Tell the vacuum cleaner to stop."""
        dps = self._command_dps or self._status_dps
        if dps and SERVICE_STOP in dps.values(self._device):
            _LOGGER.info("%s stopping", self._config.config_id)
            await dps.async_set_value(self._device, SERVICE_STOP)

    async def async_locate(self, **kwargs):
        """Locate the vacuum cleaner."""
        if self._locate_dps:
            _LOGGER.info("%s locating", self._config.config_id)
            await self._locate_dps.async_set_value(self._device, True)

    async def async_send_command(self, command, params=None, **kwargs):
        """Send a command to the vacuum cleaner."""
        dps = self._command_dps or self._status_dps
        # stop command is often present in both command and direction dps
        # in that case, prefer the direction dp as async_stop will cover
        # the commad dp seperately.
        if (
            command == SERVICE_STOP
            and self._direction_dps
            and SERVICE_STOP in self._direction_dps.values(self._device)
        ):
            dps = self._direction_dps

        if command in dps.values(self._device):
            _LOGGER.info(
                "%s sending %s %s",
                self._config.config_id,
                "direction" if dps is self._direction_dps else "command",
                command,
            )
            await dps.async_set_value(self._device, command)
        elif self._direction_dps and command in self._direction_dps.values(
            self._device
        ):
            _LOGGER.info("%s sending direction %s", self._config.config_id, command)
            await self._direction_dps.async_set_value(self._device, command)

    @property
    def fan_speed_list(self):
        """Return the list of fan speeds supported"""
        if self._fan_dps:
            return self._fan_dps.values(self._device)

    @property
    def fan_speed(self):
        """Return the current fan speed"""
        if self._fan_dps:
            return self._fan_dps.get_value(self._device)

    async def async_set_fan_speed(self, fan_speed, **kwargs):
        """Set the fan speed of the vacuum."""
        if self._fan_dps:
            _LOGGER.info(
                "%s setting fan speed to %s", self._config.config_id, fan_speed
            )
            await self._fan_dps.async_set_value(self._device, fan_speed)

    async def _async_segment_cache(self):
        """Load the persisted room list (rooms are not always reported)."""
        if self._segment_store is None:
            self._segment_store = Store(
                self.hass,
                SEGMENT_STORE_VERSION,
                f"tuya_local.segments.{self.unique_id}",
            )
            self._segment_cache = await self._segment_store.async_load() or []
        return self._segment_cache

    async def async_get_segments(self):
        """Return the rooms that can be cleaned."""
        cache = await self._async_segment_cache()
        rooms = []
        if self._room_list_dps:
            rooms = parse_ilife_room_list(self._room_list_dps.get_value(self._device))
        if rooms:
            rooms = [[rid, name] for rid, name in rooms]
            if rooms != cache:
                self._segment_cache = rooms
                await self._segment_store.async_save(rooms)
        elif cache:
            rooms = cache
        else:
            try:
                seen = self.last_seen_segments
            except RuntimeError:
                seen = None
            if seen:
                rooms = [[s.id, s.name] for s in seen]
            else:
                rooms = [[str(i), f"Room {i}"] for i in range(DEFAULT_SEGMENT_COUNT)]
        return [Segment(id=str(rid), name=name) for rid, name in rooms]

    async def async_clean_segments(self, segment_ids, **kwargs):
        """Clean the given rooms."""
        if not self._segments_dps:
            return
        ids = [str(s) for s in segment_ids if str(s).isdigit() and int(s) < 32]
        if not ids:
            _LOGGER.warning(
                "%s: no valid rooms in %s", self._config.config_id, segment_ids
            )
            return
        # The robot ignores a new room selection while it is cleaning or
        # returning to base, so stop it first.
        if self.activity in (VacuumActivity.CLEANING, VacuumActivity.RETURNING):
            await self.async_pause()
            await asyncio.sleep(3)
        value = encode_ilife_segments(ids)
        _LOGGER.info("%s cleaning rooms %s (%s)", self._config.config_id, ids, value)
        await self._segments_dps.async_set_value(self._device, value)
