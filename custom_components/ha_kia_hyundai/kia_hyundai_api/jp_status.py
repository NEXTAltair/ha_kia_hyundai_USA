"""Normalize CCS vehicle snapshots for the shared US-format coordinator.

CCS2 field paths follow hyundai_kia_connect_api's ApiImplType1 and were
verified against a Japan live response. Missing fields stay missing, including
unknown enum values, so an incomplete snapshot cannot imply a vehicle state.
The coordinator's distance and temperature sensors expect miles/Fahrenheit.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from math import isfinite

_JST = timezone(timedelta(hours=9))
_KM_PER_MILE = 1.609344


def _get(data: dict, path: str):
    for key in path.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _put(data: dict, path: str, value) -> None:
    if value is None:
        return
    keys = path.split(".")
    for key in keys[:-1]:
        data = data.setdefault(key, {})
    data[keys[-1]] = value


def _flag(value):
    if value in (False, 0, "0", "false"):
        return False
    if value in (True, 1, "1", "true"):
        return True
    return None


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (ValueError, TypeError):
        return None


def _distance(value, unit):
    value = _number(value)
    if value is None:
        return None
    if unit in (1, "1", "km"):
        return value / _KM_PER_MILE
    if unit in (3, "3", "mi", "mile"):
        return value
    return None


def _timestamp(value, source_timezone):
    if not isinstance(value, str):
        return None
    try:
        compact, separator, _fraction = value.partition(".")
        if len(compact) == 14 and compact.isdigit():
            date_format = "%Y%m%d%H%M%S.%f" if separator else "%Y%m%d%H%M%S"
            result = datetime.strptime(value, date_format)
        else:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=source_timezone)
        return result.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")
    except ValueError:
        return None


def _ccs2(state: dict) -> dict:
    result = {}
    mappings = {
        "DrivingReady": "engine",
        "Body.Hood.Open": "doorStatus.hood",
        "Body.Trunk.Open": "doorStatus.trunk",
        "Chassis.Axle.Tire.PressureLow": "tirePressure.all",
    }
    doors = ("Row1.Driver", "Row1.Passenger", "Row2.Left", "Row2.Right")
    names = ("frontLeft", "frontRight", "backLeft", "backRight")
    for door, name in zip(doors, names):
        mappings[f"Cabin.Door.{door}.Open"] = f"doorStatus.{name}"
    for source, target in mappings.items():
        _put(result, target, _flag(_get(state, source)))
    locks = [_flag(_get(state, f"Cabin.Door.{door}.Lock")) for door in doors]
    # CCS2 uses 0 for locked and 1 for unlocked. A partial response cannot
    # establish that all doors are locked; one unlocked door is sufficient.
    if any(value is True for value in locks):
        result["doorLock"] = False
    elif all(value is False for value in locks):
        result["doorLock"] = True

    soc = _number(_get(state, "Electronics.Battery.Level"))
    reliable = _get(state, "Electronics.Battery.SensorReliability")
    # CCS2's flag is 1 for an unreliable sensor, 0 for a valid reading.
    if soc is not None and 0 <= soc <= 100 and reliable not in (1, "1"):
        _put(result, "batteryStatus.stateOfCharge", soc)
    ev_soc = _number(_get(state, "Green.BatteryManagement.BatteryRemain.Ratio"))
    if ev_soc is not None and 0 <= ev_soc <= 100:
        _put(result, "evStatus.batteryStatus", ev_soc)
    _put(
        result,
        "evStatus.batteryPlugin",
        _flag(_get(state, "Green.ChargingInformation.ConnectorFastening.State")),
    )
    minutes = _number(_get(state, "Green.ChargingInformation.Charging.RemainTime"))
    if minutes is not None and minutes >= 0:
        # Matches upstream CCS2's derivation from reported remaining time.
        _put(result, "evStatus.batteryCharge", minutes > 0)
        _put(
            result, "evStatus.remainChargeTime", [{"timeInterval": {"value": minutes}}]
        )
    limits = []
    for plug, key in ((0, "Quick"), (1, "Standard")):
        limit = _number(_get(state, f"Green.ChargingInformation.TargetSoC.{key}"))
        if limit is not None:
            limits.append({"plugType": plug, "targetSOClevel": limit})
    # Coordinator indexes both entries; do not turn a missing limit into 0.
    if len(limits) == 2:
        _put(result, "evStatus.targetSOC", limits)
    total_range = _distance(
        _get(state, "Drivetrain.FuelSystem.DTE.Total"),
        _get(state, "Drivetrain.FuelSystem.DTE.Unit"),
    )
    if total_range is not None:
        _put(result, "distanceToEmpty.value", total_range)
    date = _timestamp(_get(state, "Date"), timezone.utc)
    _put(result, "syncDate.utc", date)
    return {"lastVehicleInfo": {"vehicleStatusRpt": {"vehicleStatus": result}}}


def normalize_status(body: dict, *, is_ev: bool = False) -> dict | None:
    """Return a coordinator snapshot, or None for an unrecognized envelope."""
    if not isinstance(body, dict):
        return None
    if isinstance(body.get("lastVehicleInfo"), dict):
        return deepcopy(body)
    ccs2 = _get(body, "resMsg.state.Vehicle")
    if isinstance(ccs2, dict):
        result = _ccs2(ccs2)
        _put(
            result,
            "vehicleConfig.vehicleDetail.vehicle.mileage",
            _distance(_get(ccs2, "Drivetrain.Odometer"), 1),
        )
        lat = _number(_get(ccs2, "Location.GeoCoord.Latitude"))
        lon = _number(_get(ccs2, "Location.GeoCoord.Longitude"))
        if (
            lat is not None
            and lon is not None
            and -90 <= lat <= 90
            and -180 <= lon <= 180
        ):
            _put(result, "lastVehicleInfo.location.coord", {"lat": lat, "lon": lon})
        if is_ev:
            total = _get(
                result,
                "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.distanceToEmpty.value",
            )
            if total is not None:
                _put(
                    result,
                    "lastVehicleInfo.vehicleStatusRpt.vehicleStatus.evStatus.drvDistance",
                    [
                        {
                            "rangeByFuel": {
                                "evModeRange": {"value": total},
                                "totalAvailableRange": {"value": total},
                            }
                        }
                    ],
                )
        return result
    info = _get(body, "resMsg.vehicleStatusInfo")
    if not isinstance(info, dict) or not isinstance(info.get("vehicleStatus"), dict):
        return None
    status = deepcopy(info["vehicleStatus"])
    mappings = {
        "hoodOpen": "doorStatus.hood",
        "trunkOpen": "doorStatus.trunk",
        "doorOpen.frontLeft": "doorStatus.frontLeft",
        "doorOpen.frontRight": "doorStatus.frontRight",
        "doorOpen.backLeft": "doorStatus.backLeft",
        "doorOpen.backRight": "doorStatus.backRight",
        "tirePressureLamp.tirePressureLampAll": "tirePressure.all",
    }
    for source, target in mappings.items():
        _put(status, target, _flag(_get(status, source)))
    dte = _get(status, "distanceToEmpty")
    if isinstance(dte, dict):
        dte["value"] = _distance(dte.get("value"), dte.get("unit"))
    # Legacy CCS reports `battery.batSoc`, not US `batteryStatus`.
    soc = _number(_get(status, "battery.batSoc"))
    if soc is not None and 0 <= soc <= 100:
        _put(status, "batteryStatus.stateOfCharge", soc)
    _put(status, "syncDate.utc", _timestamp(status.get("time"), _JST))
    result = {"lastVehicleInfo": {"vehicleStatusRpt": {"vehicleStatus": status}}}
    _put(
        result,
        "vehicleConfig.vehicleDetail.vehicle.mileage",
        _distance(_get(info, "odometer.value"), _get(info, "odometer.unit")),
    )
    location = info.get("vehicleLocation")
    if isinstance(location, dict):
        _put(result, "lastVehicleInfo.location", deepcopy(location))
    # Legacy range objects include their own unit. Convert each range before
    # the coordinator exposes it using the existing mile-based sensor units.
    for item in _get(status, "evStatus.drvDistance") or []:
        for distance in item.get("rangeByFuel", {}).values():
            if isinstance(distance, dict):
                distance["value"] = _distance(
                    distance.get("value"), distance.get("unit")
                )
    return result
