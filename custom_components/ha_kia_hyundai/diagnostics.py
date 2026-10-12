import attr
import logging

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_UNIQUE_ID, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from . import get_all_coordinators
from .const import DOMAIN, CONF_CLIENT_SECRET, CONF_CFB, CONF_PIN, CONF_REFRESH_TOKEN, CONF_STAMP_URL

TO_REDACT = {
    CONF_USERNAME, CONF_PASSWORD, CONF_UNIQUE_ID, "vehicle_identifier",
    CONF_CLIENT_SECRET, CONF_CFB, CONF_PIN, CONF_REFRESH_TOKEN,
    CONF_STAMP_URL,
    "vin", "VIN", "key", "device_id", "id", "title",
}
TO_REDACT_MAPPED = {
    "identifier",
    "vin",
    "VIN",
    "carId",
    "vehicleId",
    "deviceId",
    "access_token",
    "refresh_token",
    "controlToken",
    "Stamp",
    "key",
    "latitude",
    "longitude",
    "location_name",
}
TO_REDACT_RAW = {
    "vehicle_identifier",
    "vinKey",
    "vin",
    "meid",
    "mdn",
    "iccid",
    "preferredDealer",
    "lat",
    "lon",
    "latitude",
    "longitude",
    "Latitude",
    "Longitude",
    "GeoCoord",
    "invDealerCode",
}
TO_REDACT_DEVICE = {"identifiers"}
TO_REDACT_ENTITIES = {"latitude", "longitude"}

_LOGGER = logging.getLogger(__name__)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, dict[str, any]]:
    """Return diagnostics for a config entry."""
    coordinators = get_all_coordinators(hass)

    data = {
        "entry": async_redact_data(config_entry.as_dict(), TO_REDACT),
        "vehicles": {},
    }

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)

    for vehicle_id, coordinator in coordinators.items():
        vehicle_data = {
            "vehicle_raw_response": async_redact_data(coordinator.data, TO_REDACT_RAW),
        }
        status_diagnostics = getattr(coordinator.api_connection, "status_diagnostics", None)
        if status_diagnostics is not None:
            status_diagnostics = coordinator.api_connection.vehicle_status_diagnostics.get(
                vehicle_id, {"state": "not_requested"}
            )
            vehicle_data["status_fetch"] = dict(status_diagnostics)
            raw = coordinator.api_connection.vehicle_status_responses.get(vehicle_id)
            if raw is not None:
                vehicle_data["jp_status_response"] = async_redact_data(raw, TO_REDACT_RAW)

        hass_device = device_registry.async_get_device(
            identifiers={(DOMAIN, str(coordinator.vehicle_id))}
        )
        if hass_device:
            vehicle_data["device"] = {
                **async_redact_data(attr.asdict(hass_device), TO_REDACT_DEVICE),
                "entities": {},
            }

            hass_entities = er.async_entries_for_device(
                entity_registry,
                device_id=hass_device.id,
                include_disabled_entities=True,
            )

            for entity_entry in hass_entities:
                state = hass.states.get(entity_entry.entity_id)
                state_dict = None
                if state:
                    state_dict = dict(state.as_dict())
                    # The entity_id is already provided at root level.
                    state_dict.pop("entity_id", None)
                    # The context doesn't provide useful information in this case.
                    state_dict.pop("context", None)

                vehicle_data["device"]["entities"][entity_entry.entity_id] = {
                    **async_redact_data(
                        attr.asdict(
                            entity_entry, filter=lambda attr, value: attr.name != "entity_id"
                        ),
                        TO_REDACT_ENTITIES,
                    ),
                    "state": state_dict,
                }

        data["vehicles"][vehicle_id] = vehicle_data

    return data
