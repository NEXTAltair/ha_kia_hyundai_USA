"""Verify JP entities, configuration and diagnostics in Home Assistant."""

import base64
import json
from unittest.mock import patch

from aiohttp.resolver import ThreadedResolver
from homeassistant.config_entries import ConfigEntryState, SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from custom_components.ha_kia_hyundai.const import (
    DOMAIN,
    BRAND_JP_HYUNDAI,
    CONF_BRAND,
    CONF_CLIENT_SECRET,
    CONF_CFB,
    CONF_STAMP_URL,
)
from custom_components.ha_kia_hyundai import get_coordinator
from custom_components.ha_kia_hyundai.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ha_kia_hyundai.kia_hyundai_api.jp_hyundai import JpHyundai
from custom_components.ha_kia_hyundai.kia_hyundai_api.jp_status import normalize_status

CFB = base64.b64encode(bytes(range(47))).decode()


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations, monkeypatch):
    # pycares 5 starts a permanent global shutdown thread. Mocked HA tests
    # need no asynchronous DNS; use the standard resolver so the harness can
    # continue enforcing cleanup of threads created by the integration.
    monkeypatch.setattr(
        "homeassistant.helpers.aiohttp_client.AsyncResolver", ThreadedResolver
    )
    yield


@pytest.mark.parametrize("signing", [None, "provider", "local"])
async def test_jp_flow_entities_and_stamp_options(hass, ccs2_body, signing):
    with_stamp = signing is not None
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
        if client.spa_enabled:
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
        if signing == "provider":
            credentials[CONF_STAMP_URL] = "http://127.0.0.1:8765/stamp"
        elif signing == "local":
            credentials[CONF_CFB] = CFB
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
            entity.unique_id.endswith("-fuel_remaining_range_value")
            for entity in entities
        )
        for key in ("ev_remaining_range_value", "total_remaining_range_value"):
            range_entity = next(
                entity for entity in entities if entity.unique_id.endswith(f"-{key}")
            )
            # HA's default metric display converts the normalized 200 miles.
            assert hass.states.get(range_entity.entity_id).state == (
                "322" if with_stamp else "unavailable"
            )
        assert not any(
            entity.domain in ("switch", "number", "button", "lock", "climate")
            for entity in entities
        )
        sensitive_fields = {
            key: f"private-value-{key}"
            for key in (
                "vehicleId",
                "deviceId",
                "carId",
                "VIN",
                "Stamp",
                "username",
                "password",
                "client_secret",
                "access_token",
                "refresh_token",
                "accessToken",
                "refreshToken",
                "controlToken",
                "token",
                "Authorization",
                "email",
            )
        }
        get_coordinator(hass, "test-car").api_connection.vehicle_status_responses[
            "test-car"
        ] = {
            "retCode": "S",
            "resMsg": {
                "nested": [sensitive_fields],
                "state": ccs2_body["resMsg"]["state"],
            },
        }
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        serialized = json.dumps(diagnostics, default=str)
        assert "test-secret" not in serialized
        assert "test-password" not in serialized
        assert "test@example.invalid" not in serialized
        assert CFB not in serialized
        assert all(value not in serialized for value in sensitive_fields.values())
        raw = diagnostics["vehicles"]["test-car"]["jp_status_response"]
        assert raw["retCode"] == "S"
        assert (
            raw["resMsg"]["state"]["Vehicle"]["Green"]["BatteryManagement"][
                "BatteryRemain"
            ]["Ratio"]
            == 73
        )
        assert (
            raw["resMsg"]["state"]["Vehicle"]["Location"]["GeoCoord"] == "**REDACTED**"
        )
        # A new password must not clear the signer URL from initial setup.
        reauth = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
            data=dict(entry.data),
        )
        reauth = await hass.config_entries.flow.async_configure(
            reauth["flow_id"], {CONF_BRAND: BRAND_JP_HYUNDAI}
        )
        reauth = await hass.config_entries.flow.async_configure(
            reauth["flow_id"],
            {
                "username": "test@example.invalid",
                "password": "updated-password",
                CONF_CLIENT_SECRET: "test-secret",
            },
        )
        assert reauth["step_id"] == "confirm_vehicles"
        reauth = await hass.config_entries.flow.async_configure(reauth["flow_id"], {})
        assert reauth["type"] == FlowResultType.ABORT
        assert reauth["reason"] == "reauth_successful"
        await hass.async_block_till_done()
        assert entry.data["password"] == "updated-password"
        assert entry.data[CONF_STAMP_URL] == (
            "http://127.0.0.1:8765/stamp" if signing == "provider" else ""
        )
        assert entry.data[CONF_CFB] == (CFB if signing == "local" else "")
        assert entry.state == ConfigEntryState.LOADED
        assert hass.states.get(battery.entity_id).state == (
            "73" if with_stamp else "unavailable"
        )
        # Existing entries can add local signing through Options,
        # without deleting the account or entering its credentials again.
        options = await hass.config_entries.options.async_init(entry.entry_id)
        invalid = await hass.config_entries.options.async_configure(
            options["flow_id"], {CONF_CFB: "not base64"}
        )
        assert invalid["type"] == FlowResultType.FORM
        assert invalid["errors"] == {CONF_CFB: "invalid_cfb"}
        options = await hass.config_entries.options.async_configure(
            options["flow_id"], {CONF_CFB: CFB, CONF_STAMP_URL: ""}
        )
        assert options["type"] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.state == ConfigEntryState.LOADED
        assert hass.states.get(battery.entity_id).state == "73"
        client = get_coordinator(hass, "test-car").api_connection
        assert client.cfb == CFB
        assert client.stamp_url is None
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert CFB not in json.dumps(diagnostics, default=str)
        ccs2_body["resMsg"]["state"]["Vehicle"]["Green"]["BatteryManagement"][
            "BatteryRemain"
        ]["Ratio"] = 0
        await get_coordinator(hass, "test-car").async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(battery.entity_id).state == "0"
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
