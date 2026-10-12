# Hyundai (Japan) / Bluelink JP brand

This fork adds a **Hyundai (Japan)** brand to the integration, targeting the
Hyundai Mobility Japan Connected Car Services backend
(`ccs-apigw.hyundaijapan.com`). It is additive: the existing US
Kia/Hyundai/Genesis paths are untouched.

## Install

Via HACS as a custom repository:

1. HACS -> three-dot menu -> Custom repositories
2. Repository: `https://github.com/NEXTAltair/ha_kia_hyundai_USA`, category
   **Integration** -> Add
3. Install "Kia/Hyundai/Genesis (USA)" and restart Home Assistant

Or copy `custom_components/ha_kia_hyundai/` into `<config>/custom_components/`
and restart.

Then: Settings -> Devices & Services -> Add Integration -> pick brand
**Hyundai (Japan)** and enter your account email, password, and the app's
OAuth client secret. (PIN and a "CFB" field are optional; see Limitations.)

## What works today

- **Login** against the real JP backend (OAuth2 authorize -> signin ->
  token), verified live.
- **Vehicle list** (`GET /api/v1/user/cars`) -> your vehicle is created as a
  Home Assistant device (e.g. an IONIQ 5).
- The device and its entities register and the integration loads cleanly.

## Limitations (live status and remote control)

Live vehicle status and remote control use the backend's `/spa/` and `/ccs2/`
endpoints, which the gateway only accepts with a valid per-request `Stamp`
header. In current JP app versions that `Stamp` is produced by white-box
cryptography inside the app's native library, so this integration cannot
generate it on its own. Without a valid `Stamp` the gateway returns
`403 Access to this API has been disallowed`, and status-derived entities stay
unavailable.

This is the same class of problem the wider community hits: mature projects
(bluelinky, hyundai_kia_connect_api) do not generate these stamps locally
either; for the EU region they rely on externally pre-generated stamps. No
such source exists for Japan, and the CCS2 protocol used by E-GMP cars
(IONIQ 5/6) is still largely unimplemented across these projects.

Practical consequence: out of the box, the JP brand provides **login +
vehicle presence**, not live telematics. The optional "CFB" config field
reflects an older signing scheme and does not enable live data on current app
versions.

## Status

- Core: login + vehicle device in Home Assistant — working, verified against a
  real account.
- Live status/control — blocked by the white-box `Stamp` requirement described
  above.

No application secrets are shipped in this repository; the OAuth client secret
(and any optional values) are supplied locally by the user via the config
entry and are never committed to source.
