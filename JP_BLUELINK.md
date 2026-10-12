# Hyundai (Japan) / Bluelink JP

This fork supports login and vehicle discovery on Hyundai Mobility Japan's
Connected Car Services backend (`ccs-apigw.hyundaijapan.com`). Cached vehicle
status can now use a **local Stamp service** backed by the user's installed
official Android app.

Live validation and the current JP work scope are limited to **IONIQ 5 EV**.
Other models, including ICE, HEV and PHEV vehicles, are unverified and outside
the current scope. Shared protocol fixtures do not establish live support for
those vehicles.

## Verification status

- OAuth login and `/api/v1/user/cars`: verified with a real JP account.
- Native Stamp generation and `/api/v1/spa/notifications/register`: verified
  against the real JP backend with official Android Bluelink **1.0.17**
  (version code 10260), including registration through this integration.
  The response was HTTP 200, `retCode: S`, `resCode: 0000`.
- CCS2 cached status: verified with a real JP IONIQ 5 on 2026-10-12 using
  `scripts/jp_status_probe.py`. The integration retrieved battery SOC,
  location, door/charging state and odometer through the native Stamp service.
  JP timestamps include fractional seconds (`YYYYMMDDHHmmss.SSS`).
- CCS2 and legacy parsing and Home Assistant entities: tested with synthetic
  fixtures in Home Assistant 2025.1.4. Legacy status and display in the user's
  running Home Assistant instance have not been verified with a live car.
- Remote commands: unverified; JP wake, charging and charge-limit controls
  are not offered in Home Assistant.

No application binary, OAuth secret, or signing key is shipped in this repo.

## Install

Use this fork as a HACS custom repository, or copy
`custom_components/ha_kia_hyundai/` into your Home Assistant configuration and
restart. Add the integration, select **Hyundai (Japan)**, and enter your
Bluelink email, password and OAuth client secret.

Existing identity-only entries can keep their credentials. Configure the
Stamp service URL under the integration's **Options**; saving reloads the
entry. The optional CFB field is retained for older signing schemes and is
not the signing method verified with app 1.0.17.

## Local Stamp service

The service calls the installed official app's native signing function for
each request. It does not read account passwords or export the signing key.
It requires the official app to remain open in a compatible Android test
emulator/device with a matching Frida server already running. This is an
experimental development setup; it is not a self-contained HACS installation.
The obfuscated app field used by the adapter can change in other versions.

Forward the Frida port and determine the running app's PID:

```bash
adb -s emulator-5584 forward tcp:27042 tcp:27042
adb -s emulator-5584 shell pidof com.hyundai.bluelink.jp
```

Start the service using the returned PID (replace `1234`):

```bash
uv run scripts/jp_stamp_server.py --pid 1234
```

It listens at `http://127.0.0.1:8765/stamp` by default and returns
`{"app_id": "...", "stamp": "..."}` with `Cache-Control: no-store`.
Configure this URL in the integration. It must be reachable **from Home
Assistant**: localhost inside a container or another machine is a different
host. Use a private network or an SSH tunnel to the signer. `--host` and
`--port` can change its listen address; the service has no authentication and
must remain restricted to trusted hosts.

Each snapshot obtains a fresh Stamp. The signer reconnects once if its Frida
agent is unloaded; other signing failures return HTTP 503 without raw errors.
The app PID and Frida port forward must remain valid. Device registration is
cached until restart or a backend rejection (`resCode: 4002`). A rejected
device registration is discarded and status is retried once after registration.
A second rejection returns an API error and leaves the cache empty so the next
scheduled refresh can register again. Registration uses the signing headers
without account Authorization; status requests add the account Bearer token,
device ID and CCS2 protocol header. Account headers and cookies are never
sent to the Stamp provider.

## Read-only real-vehicle probe

With the Stamp service running, run:

```bash
uv run scripts/jp_status_probe.py
```

It prompts locally for the email, password and client secret, logs in, and
reads **cached** status for the account's vehicles. It does not wake the car
or send control commands. Credentials are not saved. The terminal prints
only fetch status codes, and `/tmp/jp-status.redacted.json` contains redacted
raw/normalized response data to check the JP schema. Inspect the file before
sharing it, since unexpected backend fields may carry additional information.
`--stamp-url` and `--output` override these defaults.

## Vehicle state and diagnostics

- CCS2 `/api/v1/spa/vehicles/{id}/ccs2/carstatus/latest` is tried first.
- Only HTTP 404 triggers the legacy `/api/v1/spa/vehicles/{id}/status/latest`
  fallback. Authentication errors are not treated as a different protocol.
- Battery SOC, charging, door/hood/trunk state, range, odometer, location and
  timestamps are mapped to the existing coordinator. Unknown values remain
  unknown; unreported distance units are not assumed. JP kilometer readings
  are converted before being exposed by the existing mile-based sensors.
- EV metadata from the vehicle list prepares the EV battery/range sensors
  even before authenticated telemetry is available.
- Downloaded diagnostics distinguish `stamp_not_configured`,
  `stamp_provider_unavailable`, `device_registration_failed`,
  `gateway_rejected`, `api_error`, `unsupported_response` and `ok`.
  Credentials, entry titles containing emails, and coordinates are redacted.

## Tests

Use Python 3.12:

```bash
python3 -m pip install -r requirements-test.txt
python3 -m pytest -q
python3 -m ruff check .
```

The test runtime is pinned to Home Assistant 2025.1.4. Protocol tests use a
localhost mock server; runtime tests drive configuration, EV entities,
diagnostics, Options reload and unloading. They do not contact a live car.
