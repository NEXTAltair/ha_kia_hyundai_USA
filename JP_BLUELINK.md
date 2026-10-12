# Hyundai (Japan) / Bluelink JP

This fork supports login and vehicle discovery on Hyundai Mobility Japan's
Connected Car Services backend (`ccs-apigw.hyundaijapan.com`). Cached vehicle
status uses **Stamps generated inside Home Assistant** from signing data
configured once. Regular operation does not require an Android emulator,
Frida, or an external Stamp server.

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
- In-process signing: eight different ten-digit timestamps produced exactly
  the same results as the official app's native signer. Device registration
  using a Python-generated Stamp also returned HTTP 200 / S / 0000 on
  2026-10-12, without calling a Stamp service.
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
**CFB signing data** under the integration's **Options**; saving reloads the
entry. Use the `cfb` value from the private setup file described below. Leave
the Stamp service URL empty. If an old URL remains configured, local CFB
signing takes precedence and the URL is not contacted. CFB signing data is
preserved across reauthentication and redacted from diagnostics.

The login session is explicitly set to Japanese before sign-in, matching the
JP official login page. This also runs on re-login after token expiry, in
addition to the `lang=ja` authorize parameter. If
the language endpoint fails, sign-in stops rather than using an unspecified
session language. The official app's display after login still needs a user
runtime check.

## One-time signing setup

For app **1.0.17**, a fixed application UUID and a ten-digit Unix timestamp
produce a 47-byte Stamp. The same 47-byte XOR mask reproduces the native
output at different timestamps. Home Assistant stores this mask as CFB
signing data and creates a fresh Stamp for each request locally. This is the
same kind of local signing used by the existing EU/AU client implementations.

Provisioning calls the user's installed official app once to obtain and
cross-check signing samples. This initial step needs a compatible Android
emulator/device, the official app open, and a matching Frida server. It does
not read the account password or issue any vehicle commands. The app's
obfuscated field can change with app versions; the setup fails if the app ID
or signing format is unsupported.

Forward the Frida port and determine the running app's PID:

```bash
adb -s emulator-5584 forward tcp:27042 tcp:27042
adb -s emulator-5584 shell pidof com.hyundai.bluelink.jp
```

Save the configuration using the returned PID (replace `1234`):

```bash
mkdir -p local-private
uv run scripts/jp_stamp_setup.py --pid 1234 --output local-private/jp-stamp-config.json
```

The script verifies eight native samples, creates a mode-0600 JSON file, and
refuses to overwrite an existing file. Signing values are never printed.
Enter the file's `cfb` value in the integration's **Options → CFB signing
data**. Keep the file private; `local-private/` is excluded from Git. Once
configured, the Android environment, Frida and Stamp server can be stopped.

Device registration is cached until restart or a backend rejection
(`resCode: 4002`). A rejected
device registration is discarded and status is retried once after registration.
A second rejection returns an API error and leaves the cache empty so the next
scheduled refresh can register again. Registration uses the signing headers
without account Authorization; status requests add the account Bearer token,
device ID and CCS2 protocol header. Account headers and cookies are never
sent to an external Stamp provider.

## Existing external Stamp installations

The optional `scripts/jp_stamp_server.py` remains available for earlier
development setups. Its URL is used only when CFB signing data is absent.
It binds to `http://127.0.0.1:8765/stamp` by default; existing installations
still need an address reachable from Home Assistant. This fallback server
has no authentication and must remain restricted to trusted hosts.

## Read-only real-vehicle probe

With local signing data configured in the private file, run:

```bash
uv run scripts/jp_status_probe.py --cfb-file local-private/jp-stamp-config.json
```

It prompts locally for the email, password and client secret, logs in, and
reads **cached** status for the account's vehicles. It does not wake the car
or send control commands. Credentials are not saved. The terminal prints
only fetch status codes, and `/tmp/jp-status.redacted.json` contains redacted
raw/normalized response data to check the JP schema. Inspect the file before
sharing it, since unexpected backend fields may carry additional information.
`--stamp-url` explicitly selects the older external provider instead;
`--output` changes the diagnostic output path.

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
