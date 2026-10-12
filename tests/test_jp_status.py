"""Regression tests for protocol translation and unknown data."""

from copy import deepcopy

import pytest

from custom_components.ha_kia_hyundai.kia_hyundai_api.jp_status import normalize_status


def test_ccs2_units_and_state(ccs2_body):
    original = deepcopy(ccs2_body)
    data = normalize_status(ccs2_body, is_ev=True)
    status = data["lastVehicleInfo"]["vehicleStatusRpt"]["vehicleStatus"]
    assert status["evStatus"]["batteryStatus"] == 73
    assert status["evStatus"]["batteryCharge"] is True
    assert status["evStatus"]["batteryPlugin"] is True
    assert status["doorLock"] is True
    assert status["doorStatus"]["trunk"] is True
    assert status["engine"] is False
    assert data["lastVehicleInfo"]["location"]["coord"] == {"lat": 0.0, "lon": 0.0}
    assert data["vehicleConfig"]["vehicleDetail"]["vehicle"][
        "mileage"
    ] == pytest.approx(10000)
    assert status["evStatus"]["drvDistance"][0]["rangeByFuel"]["evModeRange"][
        "value"
    ] == pytest.approx(200)
    assert status["syncDate"]["utc"] == "20261012010000"
    assert ccs2_body == original


def test_missing_and_sentinel_values_stay_unknown(ccs2_body):
    state = ccs2_body["resMsg"]["state"]["Vehicle"]
    state["Green"] = {}
    state["Electronics"]["Battery"]["Level"] = 255
    del state["Cabin"]["Door"]["Row2"]["Right"]["Lock"]
    data = normalize_status(ccs2_body)
    status = data["lastVehicleInfo"]["vehicleStatusRpt"]["vehicleStatus"]
    assert "doorLock" not in status
    assert "batteryStatus" not in status
    assert "evStatus" not in status
    state["Cabin"]["Door"]["Row1"]["Driver"]["Lock"] = 1
    assert (
        normalize_status(ccs2_body)["lastVehicleInfo"]["vehicleStatusRpt"][
            "vehicleStatus"
        ]["doorLock"]
        is False
    )


def test_legacy_jst_and_distance_units():
    body = {
        "resMsg": {
            "vehicleStatusInfo": {
                "odometer": {"value": 160.9344, "unit": 1},
                "vehicleStatus": {
                    "time": "20261012100000",
                    "doorLock": False,
                    "battery": {"batSoc": 80},
                    "evStatus": {
                        "batteryStatus": 0,
                        "batteryCharge": False,
                        "drvDistance": [
                            {
                                "rangeByFuel": {
                                    "evModeRange": {"value": 160.9344, "unit": 1}
                                }
                            }
                        ],
                    },
                },
            }
        }
    }
    data = normalize_status(body)
    status = data["lastVehicleInfo"]["vehicleStatusRpt"]["vehicleStatus"]
    assert status["syncDate"]["utc"] == "20261012010000"
    assert status["doorLock"] is False
    assert status["evStatus"]["batteryStatus"] == 0
    assert status["evStatus"]["drvDistance"][0]["rangeByFuel"]["evModeRange"][
        "value"
    ] == pytest.approx(100)


@pytest.mark.parametrize("body", [None, [], {}, {"resMsg": {}}, {"retCode": "F"}])
def test_unknown_envelope(body):
    assert normalize_status(body) is None
