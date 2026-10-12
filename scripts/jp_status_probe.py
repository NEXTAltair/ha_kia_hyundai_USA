# /// script
# requires-python = ">=3.10"
# dependencies = ["aiohttp>=3.8", "certifi", "pytz"]
# ///
"""Read cached JP vehicle status; credentials are entered only in the terminal."""

import argparse
import asyncio
from getpass import getpass
import json
import os
from pathlib import Path
import sys

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "custom_components/ha_kia_hyundai")
)
from kia_hyundai_api.jp_hyundai import JpHyundai  # noqa: E402


def redact(value):
    """Preserve response structure while hiding identity and location."""
    private = {
        "vin",
        "vinkey",
        "id",
        "carid",
        "vehicleid",
        "deviceid",
        "key",
        "latitude",
        "longitude",
        "lat",
        "lon",
        "coord",
        "geocoord",
        "email",
        "username",
        "address",
        "formattedaddress",
        "carname",
        "nickname",
    }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "**REDACTED**"
            if key.lower() in private
            or any(
                word in key.lower() for word in ("token", "secret", "password", "stamp")
            )
            else redact(item)
            for key, item in value.items()
        }
    return value


async def probe(args):
    username = os.environ.get("JP_BLUELINK_USERNAME") or input("Bluelink email: ")
    password = os.environ.get("JP_BLUELINK_PASSWORD") or getpass("Bluelink password: ")
    secret = os.environ.get("JP_BLUELINK_CLIENT_SECRET") or getpass(
        "OAuth client secret: "
    )
    api = JpHyundai(username, password, secret, stamp_url=args.stamp_url)
    try:
        await api.login()
        vehicles = await api.get_vehicles()
        results = []
        for vehicle in vehicles:
            data = await api.get_cached_vehicle_status(vehicle["id"])
            results.append(
                {
                    "fetch": dict(api.status_diagnostics),
                    "raw_status": redact(api.last_status_response),
                    "normalized_status": redact(data),
                }
            )
        output = json.dumps(results, ensure_ascii=False, indent=2)
        args.output.write_text(output + "\n", encoding="utf-8")
        # Only diagnostic codes appear in terminal output.
        sys.stdout.write(json.dumps([item["fetch"] for item in results]) + "\n")
        return (
            0
            if results and all(item["fetch"]["state"] == "ok" for item in results)
            else 1
        )
    except Exception as err:  # noqa: BLE001
        # Some API exceptions contain redirect URLs or server bodies.
        sys.stderr.write(
            f"Probe failed ({type(err).__name__}); no credentials were printed.\n"
        )
        return 1
    finally:
        await api.api_session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp-url", default="http://127.0.0.1:8765/stamp")
    parser.add_argument(
        "--output", type=Path, default=Path("/tmp/jp-status.redacted.json")
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(probe(args)))


if __name__ == "__main__":
    main()
