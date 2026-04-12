"""Cover platform for Axis Gear smart blinds."""

from __future__ import annotations

from homeassistant.components.cover import CoverEntity, CoverEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AxisGearConfigEntry
from .coordinator import AxisGearCoordinator, AxisGearState


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AxisGearConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Axis Gear cover entities."""
    async_add_entities([AxisGearCover(entry.runtime_data, entry)])


class AxisGearCover(CoordinatorEntity[AxisGearCoordinator], CoverEntity):
    """Representation of an Axis Gear shade."""

    _attr_supported_features = (
        CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
    )
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, coordinator: AxisGearCoordinator, entry: AxisGearConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = entry.unique_id
        self._attr_device_info = {
            "identifiers": {("axis_gear", entry.data["address"])},
            "name": entry.title,
            "manufacturer": "AXIS Labs",
            "model": "Gear",
        }

    @property
    def current_cover_position(self) -> int | None:
        """Return current position (0=closed, 100=open in HA convention)."""
        pos = self.coordinator.data.position
        if pos is None:
            return None
        # Axis Gear: 0=open, 100=closed. HA: 0=closed, 100=open.
        return 100 - pos

    @property
    def is_closed(self) -> bool | None:
        pos = self.coordinator.data.position
        if pos is None:
            return None
        return pos >= 100

    async def async_open_cover(self, **kwargs) -> None:
        """Open the cover."""
        await self.coordinator.async_set_position(0)

    async def async_close_cover(self, **kwargs) -> None:
        """Close the cover."""
        await self.coordinator.async_set_position(100)

    async def async_set_cover_position(self, **kwargs) -> None:
        """Set cover position (HA: 0=closed, 100=open)."""
        ha_position = kwargs["position"]
        # Convert HA convention to Axis convention
        await self.coordinator.async_set_position(100 - ha_position)
