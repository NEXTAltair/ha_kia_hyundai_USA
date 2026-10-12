"""Verify JP entities, configuration and diagnostics in Home Assistant."""

import json
from unittest.mock import patch

from aiohttp.resolver import ThreadedResolver
from homeassistant.config_entries import ConfigEntryState, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.ha_kia_hyundai.const import (
    DOMAIN,
    BRAND_JP_HYUNDAI,
    CONF_BRAND,
    CONF_CLIENT_SECRET,
    CONF_STAMP_URL,
)
from custom_components.ha_kia_hyundai import get_coordinator
from custom_components.ha_kia_hyundai.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ha_kia_hyundai.kia_hyundai_api.jp_hyundai import JpHyundai
from custom_components.ha_kia_hyundai.kia_hyundai_api.jp_status import normalize_status


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations, monkeypatch):
    # pycares 5 starts a permanent global shutdown thread. Mocked HA tests
    # need no asynchronous DNS; use the standard resolver so the harness can
    # continue enforcing cleanup of threads created by the integration.
    monkeypatch.setattr(
        "homeassistant.helpers.aiohttp_client.AsyncResolver", ThreadedResolver
    )
    yield


@pytest.mark.parametrize("with_stamp", [False, True])
async def test_jp_flow_entities_and_stamp_options(hass, ccs2_body, with_stamp):
    async def login(client):
        client.access_token = "Bearer test-token"

    async def vehicles(client):
        client.vehicles = [
            {
                "id": "test-car",
                "nickName": "IONIQ 5",
                "modelName": "IONIQ 5",
                "is_ev": True,
            }
        ]
        return client.vehicles

    async def status(client, vehicle_id):
        if client.stamp_url:
            return normalize_status(ccs2_body, is_ev=True)
        return {}

    with (
        patch.object(JpHyundai, "login", login),
        patch.object(JpHyundai, "get_vehicles", vehicles),
        patch.object(JpHyundai, "get_cached_vehicle_status", status),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_BRAND: BRAND_JP_HYUNDAI}
        )
        assert result["step_id"] == "jp_credentials"
        credentials = {
            "username": "test@example.invalid",
            "password": "test-password",
            CONF_CLIENT_SECRET: "test-secret",
        }
        if with_stamp:
            credentials[CONF_STAMP_URL] = "http://127.0.0.1:8765/stamp"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], credentials
        )
        assert result["step_id"] == "confirm_vehicles"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == FlowResultType.CREATE_ENTRY
        entry = result["result"]
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        assert devices[0].name == "IONIQ 5"
        assert devices[0].model == "IONIQ 5"
        entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        battery = next(
            entity
            for entity in entities
            if entity.unique_id.endswith("-ev_battery_level")
        )
        assert hass.states.get(battery.entity_id).state == (
            "73" if with_stamp else "unavailable"
        )
        assert not any(
            entity.domain in ("switch", "number", "button", "lock", "climate")
            for entity in entities
        )
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert "test-secret" not in json.dumps(diagnostics, default=str)
        assert "test-password" not in json.dumps(diagnostics, default=str)
        assert "test@example.invalid" not in json.dumps(diagnostics, default=str)
        # Existing identity-only entries can add a signer through Options,
        # without deleting the account or entering its credentials again.
        options = await hass.config_entries.options.async_init(entry.entry_id)
        options = await hass.config_entries.options.async_configure(
            options["flow_id"], {CONF_STAMP_URL: "http://127.0.0.1:8765/stamp"}
        )
        assert options["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        assert hass.states.get(battery.entity_id).state == "73"
        ccs2_body["resMsg"]["state"]["Vehicle"]["Green"]["BatteryManagement"][
            "BatteryRemain"
        ]["Ratio"] = 0
        await get_coordinator(hass, "test-car").async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(battery.entity_id).state == "0"
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
