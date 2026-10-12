"""Fixtures for JP cached status and Home Assistant setup."""

import pytest


@pytest.fixture
def ccs2_body():
    """Synthetic CCS2 snapshot using the documented shared CCS schema."""
    return {
        "retCode": "S",
        "resCode": "0000",
        "resMsg": {
            "state": {
                "Vehicle": {
                    "Date": "20261012010000",
                    "DrivingReady": 0,
                    "Drivetrain": {
                        "Odometer": 16093.44,
                        "FuelSystem": {"DTE": {"Total": 321.8688, "Unit": 1}},
                    },
                    "Electronics": {"Battery": {"Level": 90, "SensorReliability": 1}},
                    "Green": {
                        "BatteryManagement": {"BatteryRemain": {"Ratio": 73}},
                        "ChargingInformation": {
                            "ConnectorFastening": {"State": 1},
                            "Charging": {"RemainTime": 30},
                            "TargetSoC": {"Quick": 80, "Standard": 90},
                        },
                    },
                    "Cabin": {
                        "Door": {
                            "Row1": {
                                "Driver": {"Lock": 0, "Open": 0},
                                "Passenger": {"Lock": 0, "Open": 0},
                            },
                            "Row2": {
                                "Left": {"Lock": 0, "Open": 0},
                                "Right": {"Lock": 0, "Open": 0},
                            },
                        }
                    },
                    "Body": {"Hood": {"Open": 0}, "Trunk": {"Open": 1}},
                    "Location": {"GeoCoord": {"Latitude": 0.0, "Longitude": 0.0}},
                }
            }
        },
    }
