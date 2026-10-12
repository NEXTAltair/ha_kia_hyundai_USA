"""Exercise HTTP authentication and status fallback without live credentials."""

import base64

from aiohttp import web
from aiohttp.resolver import ThreadedResolver
from aiohttp.test_utils import TestServer
import pytest

from custom_components.ha_kia_hyundai.kia_hyundai_api import jp_hyundai as jp

APP_ID = "8be122e0-b0d9-40b5-a46e-67b25194ba88"


@pytest.fixture
async def api_server(monkeypatch, ccs2_body, socket_enabled):
    monkeypatch.setattr("aiohttp.connector.DefaultResolver", ThreadedResolver)
    state = {
        "status_code": 200,
        "body": ccs2_body,
        "calls": [],
        "provider_code": 200,
        "register_code": 200,
        "registered_devices": [],
        "status_devices": [],
        "status_bodies": [],
        "provider_code_after_status": None,
    }

    async def stamp(request):
        assert "Authorization" not in request.headers
        assert not request.cookies
        state["calls"].append("stamp")
        return web.json_response(
            {"app_id": APP_ID, "stamp": base64.b64encode(b"synthetic-stamp").decode()},
            status=state["provider_code"],
        )

    async def register(request):
        assert "Authorization" not in request.headers
        assert "ccsp-device-id" not in request.headers
        assert request.headers["ccsp-application-id"] == APP_ID
        state["calls"].append("register")
        if state["register_code"] != 200:
            return web.json_response(
                {"retCode": "F", "resCode": "9999"}, status=state["register_code"]
            )
        device_id = f"test-device-{len(state['registered_devices']) + 1}"
        state["registered_devices"].append(device_id)
        return web.json_response({"retCode": "S", "resMsg": {"deviceId": device_id}})

    async def status(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        assert request.headers["ccsp-device-id"] == state["registered_devices"][-1]
        state["status_devices"].append(request.headers["ccsp-device-id"])
        state["calls"].append(request.path)
        code = state["status_code"] if "/ccs2/" in request.path else 200
        body = state["body"]
        if code == 200 and state["status_bodies"]:
            body = state["status_bodies"].pop(0)
        if state["provider_code_after_status"] is not None:
            state["provider_code"] = state["provider_code_after_status"]
        return web.json_response(body, status=code)

    app = web.Application()
    app.router.add_get("/stamp", stamp)
    app.router.add_post("/spa/notifications/register", register)
    app.router.add_get("/spa/vehicles/{vid}/ccs2/carstatus/latest", status)
    app.router.add_get("/spa/vehicles/{vid}/status/latest", status)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(jp, "JP_HYUNDAI_SPA_API_URL", str(server.make_url("/spa/")))
    client = jp.JpHyundai(
        "dummy", "dummy", "dummy", stamp_url=str(server.make_url("/stamp"))
    )
    client.access_token = "Bearer test-token"
    client.vehicles = [{"id": "test-car", "is_ev": True}]
    client.api_session.cookie_jar.update_cookies({"account": "test-account-cookie"})
    yield client, state
    await client.api_session.close()
    await server.close()


async def test_native_provider_and_registration_cache(api_server):
    client, state = api_server
    data = await client.get_cached_vehicle_status("test-car")
    assert (
        data["lastVehicleInfo"]["vehicleStatusRpt"]["vehicleStatus"]["evStatus"][
            "batteryStatus"
        ]
        == 73
    )
    assert client.status_diagnostics == {"state": "ok"}
    await client.get_cached_vehicle_status("test-car")
    assert state["calls"].count("register") == 1
    assert state["calls"].count("stamp") == 3


@pytest.mark.parametrize("code, legacy", [(404, True), (403, False), (500, False)])
async def test_path_fallback_only_on_404(api_server, code, legacy):
    client, state = api_server
    state["status_code"] = code
    result = await client.get_cached_vehicle_status("test-car")
    assert any(path.endswith("/status/latest") for path in state["calls"]) is legacy
    assert bool(result) is legacy
    if code == 403:
        assert client.status_diagnostics["state"] == "gateway_rejected"


async def test_provider_outage_preserves_vehicle_setup(api_server):
    client, state = api_server
    state["provider_code"] = 503
    assert await client.get_cached_vehicle_status("test-car") == {}
    assert client.status_diagnostics["state"] == "stamp_provider_unavailable"
    assert "register" not in state["calls"]


async def test_application_error_not_reported_as_telemetry(api_server):
    client, state = api_server
    state["body"] = {"retCode": "F", "resCode": "4004"}
    assert await client.get_cached_vehicle_status("test-car") == {}
    assert client.status_diagnostics == {"state": "api_error", "res_code": "4004"}
    assert state["calls"].count("register") == 1
    assert client.spa_device_id == state["registered_devices"][-1]


@pytest.mark.parametrize("status_code", [200, 404])
async def test_invalid_device_registration_recovers_once(api_server, status_code):
    client, state = api_server
    state["status_code"] = status_code
    assert await client.get_cached_vehicle_status("test-car")
    old_device = client.spa_device_id
    state["calls"].clear()
    state["status_devices"].clear()
    state["status_bodies"] = [{"retCode": "F", "resCode": "4002"}, state["body"]]

    data = await client.get_cached_vehicle_status("test-car")
    status = data["lastVehicleInfo"]["vehicleStatusRpt"]["vehicleStatus"]
    assert status["evStatus"]["batteryStatus"] == 73
    assert client.status_diagnostics == {"state": "ok"}
    assert client.spa_device_id != old_device
    assert client.resolved_app_id == APP_ID
    assert state["calls"].count("register") == 1
    assert state["calls"].count("stamp") == (3 if status_code == 200 else 5)
    assert state["status_devices"] == (
        [old_device, client.spa_device_id]
        if status_code == 200
        else [old_device, old_device, client.spa_device_id, client.spa_device_id]
    )
    assert client.vehicle_status_responses["test-car"] == state["body"]
    assert await client.get_cached_vehicle_status("test-car")
    assert state["calls"].count("register") == 1


@pytest.mark.parametrize("status_code", [200, 404])
async def test_repeated_invalid_device_stops_and_discards_cache(
    api_server, ccs2_body, status_code
):
    client, state = api_server
    state["status_code"] = status_code
    state["body"] = {"retCode": "F", "resCode": "4002"}

    assert await client.get_cached_vehicle_status("test-car") == {}
    assert state["calls"].count("register") == 2
    assert len(state["status_devices"]) == (2 if status_code == 200 else 4)
    assert client.spa_device_id is None
    assert client.resolved_app_id is None
    assert client.status_diagnostics == {"state": "api_error", "res_code": "4002"}

    # A later scheduled refresh starts a new registration without a reload.
    state["body"] = ccs2_body
    assert await client.get_cached_vehicle_status("test-car")
    assert state["calls"].count("register") == 3
    assert client.status_diagnostics == {"state": "ok"}


async def test_invalid_device_reregistration_failure_is_diagnosed(api_server):
    client, state = api_server
    assert await client.get_cached_vehicle_status("test-car")
    state["calls"].clear()
    state["status_devices"].clear()
    state["body"] = {"retCode": "F", "resCode": "4002"}
    state["register_code"] = 503

    assert await client.get_cached_vehicle_status("test-car") == {}
    assert client.status_diagnostics == {"state": "device_registration_failed"}
    assert state["calls"].count("register") == 1
    assert len(state["status_devices"]) == 1
    assert client.spa_device_id is None
    assert client.resolved_app_id is None


async def test_invalid_device_recovery_handles_stamp_outage(api_server):
    client, state = api_server
    assert await client.get_cached_vehicle_status("test-car")
    state["calls"].clear()
    state["status_devices"].clear()
    state["body"] = {"retCode": "F", "resCode": "4002"}
    state["provider_code_after_status"] = 503

    assert await client.get_cached_vehicle_status("test-car") == {}
    assert client.status_diagnostics == {"state": "stamp_provider_unavailable"}
    assert "register" not in state["calls"]
    assert len(state["status_devices"]) == 1
    assert client.spa_device_id is None
    assert client.resolved_app_id is None
