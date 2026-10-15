"""Tests for the battery lifetime elapsed binary sensor."""

from datetime import timedelta
from types import MappingProxyType

import pytest
from custom_components.battery_notes.const import (
    ATTR_BATTERY_LIFETIME,
    ATTR_BATTERY_LIFETIME_EXPIRES,
    ATTR_DEVICE_ID,
    ATTR_SOURCE_ENTITY_ID,
    CONF_BATTERY_LIFETIME,
    CONF_SOURCE_ENTITY_ID,
    DOMAIN,
    LAST_REPLACED,
    SERVICE_BATTERY_REPLACED,
)
from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.const import CONF_DEVICE_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from . import setup_integration

pytestmark = pytest.mark.usefixtures("mock_library_updater", "_mock_library_file")


@pytest.fixture(autouse=True)
def freeze_setup_time(freezer: FrozenDateTimeFactory) -> None:
    """Keep source creation and replacement timestamps stable."""
    freezer.move_to("2026-01-01T12:00:00+00:00")


@pytest.fixture
def lifetime_config_entry(
    battery_note_config_entry: MockConfigEntry,
) -> MockConfigEntry:
    """Return a config entry with a 2-day battery lifetime configured."""
    subentry = next(iter(battery_note_config_entry.subentries.values()))
    updated_subentry = subentry.__class__(
        data=MappingProxyType({**subentry.data, CONF_BATTERY_LIFETIME: 2}),
        subentry_id=subentry.subentry_id,
        subentry_type=subentry.subentry_type,
        title=subentry.title,
        unique_id=subentry.unique_id,
    )
    return MockConfigEntry(
        domain=battery_note_config_entry.domain,
        version=battery_note_config_entry.version,
        title=battery_note_config_entry.title,
        data=battery_note_config_entry.data,
        options=battery_note_config_entry.options,
        subentries_data=[updated_subentry.as_dict()],
    )


async def test_lifetime_sensor_tracks_time_and_resets_on_replacement(
    hass: HomeAssistant,
    lifetime_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test lifetime elapsed state flips with time and resets after replacement."""
    await setup_integration(hass, lifetime_config_entry)
    subentry = next(iter(lifetime_config_entry.subentries.values()))
    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{subentry.unique_id}_battery_lifetime_expired"
    )
    assert entity_id is not None

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"
    assert state.attributes[ATTR_BATTERY_LIFETIME] == 2
    expires_at = state.attributes[ATTR_BATTERY_LIFETIME_EXPIRES]
    assert expires_at is not None

    freezer.tick(timedelta(days=2) - timedelta(seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"

    freezer.tick(timedelta(seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"

    replacement_target = {
        field: subentry.data[field]
        for field in (ATTR_DEVICE_ID, ATTR_SOURCE_ENTITY_ID)
        if field in subentry.data
    }
    await hass.services.async_call(
        DOMAIN,
        SERVICE_BATTERY_REPLACED,
        replacement_target,
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "off"
    assert state.attributes[ATTR_BATTERY_LIFETIME_EXPIRES] > expires_at

    freezer.tick(timedelta(days=2))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


async def test_lifetime_sensor_not_created_without_lifetime(
    hass: HomeAssistant,
    battery_note_config_entry: MockConfigEntry,
) -> None:
    """Test notes without a lifetime do not create a lifetime elapsed sensor."""
    await setup_integration(hass, battery_note_config_entry)
    subentry = next(iter(battery_note_config_entry.subentries.values()))
    entity_registry = er.async_get(hass)
    assert (
        entity_registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{subentry.unique_id}_battery_lifetime_expired"
        )
        is None
    )


async def test_lifetime_sensor_unavailable_without_last_replaced(
    hass: HomeAssistant,
    lifetime_config_entry: MockConfigEntry,
) -> None:
    """Test sensor is unavailable when lifetime is set but no replacement date exists."""
    await setup_integration(hass, lifetime_config_entry)
    subentry = next(iter(lifetime_config_entry.subentries.values()))
    assert lifetime_config_entry.runtime_data.subentry_coordinators is not None
    coordinator = lifetime_config_entry.runtime_data.subentry_coordinators[
        subentry.subentry_id
    ]

    source_id = subentry.data.get(CONF_SOURCE_ENTITY_ID)
    if source_id:
        coordinator.async_update_entity_config(source_id, {LAST_REPLACED: None})
    else:
        coordinator.async_update_device_config(
            subentry.data[CONF_DEVICE_ID], {LAST_REPLACED: None}
        )
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{subentry.unique_id}_battery_lifetime_expired"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "unavailable"
