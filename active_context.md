# Active Context: MarcusTaz/ha_kia_hyundai_USA

## Repository
- **GitHub:** https://github.com/MarcusTaz/ha_kia_hyundai_USA
- **Current Version:** v3.1.3
- **Domain:** `ha_kia_hyundai`
- **Home Assistant Min Version:** 2023.1.0

## Current Status
- **Kia:** Fully working (OTP authentication)
- **Hyundai:** Fully working (PIN authentication)
- **Genesis:** Fully working (PIN authentication)
- **HACS Submission:** Pending - validation fixes pushed, need to rerun and resubmit PR

## HACS Submission Status
**Previous PR rejected due to:**
1. Old cached hacs.json with invalid keys (`domains`, `iot_class`, `filename`)
2. Missing `country` key

**Fixes applied (already pushed to main):**
- hacs.json now contains only valid keys:
```json
{
  "name": "Kia/Hyundai/Genesis (USA)",
  "content_in_root": false,
  "render_readme": true,
  "homeassistant": "2023.1.0",
  "country": ["US"]
}
```

**Next steps to complete HACS submission:**
1. Run validation workflow: Actions → Validate → Run workflow
2. Sync fork at https://github.com/MarcusTaz/default
3. Create new branch (not master)
4. Ensure `"MarcusTaz/ha_kia_hyundai_USA",` is alphabetically sorted (after `MarcoGos/kleenex_pollenradar`, before `marcolivierarsenault/moonraker-home-assistant`)
5. Submit PR with description containing 3 links:
   - Repository: https://github.com/MarcusTaz/ha_kia_hyundai_USA
   - Latest Release: https://github.com/MarcusTaz/ha_kia_hyundai_USA/releases/tag/v3.1.2
   - CI Actions: https://github.com/MarcusTaz/ha_kia_hyundai_USA/actions/workflows/validate.yml

## Technical Architecture

### Directory Structure
```
custom_components/ha_kia_hyundai/
├── __init__.py              # Entry point, coordinator setup
├── kia_hyundai_api/         # Embedded API clients
│   ├── __init__.py
│   ├── us_kia.py            # Kia Connect API (OTP auth)
│   ├── us_hyundai.py        # Hyundai BlueLink API (PIN auth)
│   ├── us_genesis.py        # Genesis Connected API (PIN auth)
│   ├── errors.py            # Custom exceptions (AuthError, PINLockedError, TokenExpiredError)
│   └── util_http.py         # HTTP decorators, request handling
├── vehicle_coordinator.py   # DataUpdateCoordinator for vehicle data
├── config_flow.py           # Setup wizard (brand selection, auth)
├── sensor.py                # Sensor entities
├── binary_sensor.py         # Binary sensor entities
├── switch.py                # Switch entities (charging, defrost)
├── climate.py               # Climate entity
├── lock.py                  # Lock entity
├── button.py                # Button entities (remote start/stop)
├── select.py                # Select entities (seat heat/cool, steering wheel)
├── number.py                # Number entities (charge limits)
├── device_tracker.py        # GPS location
├── services.py              # Custom services
├── services.yaml            # Service definitions
├── const.py                 # Constants
├── util.py                  # Utilities
├── manifest.json
├── strings.json
└── translations/en.json
```

### API Client Architecture

#### Authentication Flow
- **Kia (us_kia.py):** Email/password → OTP sent via email/SMS → OTP verification → Session ID
- **Hyundai (us_hyundai.py):** Email/password/PIN → Token with expiration → Proactive refresh
- **Genesis (us_genesis.py):** Email/password/PIN → Token with expiration → Proactive refresh

#### Token Refresh (Hyundai/Genesis)
```python
TOKEN_REFRESH_BUFFER_SECONDS = 300  # Refresh 5 min before expiry

async def _ensure_token_valid(self):
    if not self._is_token_valid():
        await self.login()

def _is_token_valid(self) -> bool:
    if self.token_expires_at is None:
        return False
    return datetime.now(timezone.utc) < self.token_expires_at - timedelta(seconds=TOKEN_REFRESH_BUFFER_SECONDS)
```

#### Data Transformation
Hyundai/Genesis API responses are transformed to match Kia format for coordinator compatibility. Key transformation in `get_cached_vehicle_status()`:
- Vehicle status from `/rcs/rvs/vehicleStatus`
- Vehicle details from `/enrollment/details/{username}`
- Location from `/rcs/rfc/findMyCar`

### Vehicle Coordinator (vehicle_coordinator.py)

#### Key Properties
- `is_ev` - True if evStatus exists in data (used to hide EV entities for ICE vehicles)
- `ev_battery_level` - EV battery percentage
- `can_remote_climate` - True if vehicle supports remote start
- `front_seat_options` / `rear_seat_options` - Seat heat/cool capabilities from API
- `has_climate_seats` - True if any seat has heat/cool

#### Desired Climate Settings (stored in coordinator)
```python
climate_desired_defrost: bool = False
climate_desired_heating_acc: bool = False
desired_temperature: int = 72
desired_steering_wheel_heat: int = 0  # 0=off, 1=low, 2=high
desired_driver_seat_comfort: SeatSettings | None = None
desired_passenger_seat_comfort: SeatSettings | None = None
desired_left_rear_seat_comfort: SeatSettings | None = None
desired_right_rear_seat_comfort: SeatSettings | None = None
```

### Entity Creation Logic

#### EV Entity Gating (sensor.py, switch.py)
```python
# sensor.py - EV sensors only created if is_ev=True
KiaSensorEntityDescription(
    key="ev_battery_level",
    exists_fn=lambda c: c.is_ev,
    ...
)

# switch.py - Charging switch only for EVs
if coordinator.is_ev:
    switches.append(ChargingSwitch(coordinator=coordinator))
```

#### Seat Entity Gating
Rear seats only created if API reports they exist:
```python
exists_fn=lambda seat: bool(seat.rear_seat_options.get(HEAT_VENT_TYPE, 0))
```

### Services (services.py, services.yaml)

#### start_climate Service
```yaml
start_climate:
  fields:
    duration:
      required: false
      selector:
        number:
          min: 1
          max: 60
          unit_of_measurement: minutes
```

**Duration parameter behavior:**
- If provided: Passed to API
- If not provided: API uses vehicle's stored "Custom Climate" setting
- **Kia EV limitation:** Duration and temperature are ignored by Kia EV API; uses Custom Climate settings from official Kia app

### Known Limitations

#### Kia EV Climate Control
- **What works:** Starting/stopping climate, seat heat/cool, steering wheel heat
- **What doesn't work:** Temperature and duration (controlled by official Kia app's "Custom Climate" profile)
- Documented in README under "Known Limitations"

### Release Management
- **One visible release:** Only latest version visible in HACS
- **Archived releases:** Previous versions kept as drafts for rollback
- Current releases:
  - v3.1.2 (Latest, visible)
  - v3.1.1, v3.1.0, v3.0.0 (Drafts, archived)

### GitHub Actions (.github/workflows/validate.yml)
```yaml
jobs:
  hassfest:
    uses: "home-assistant/actions/hassfest@master"
  hacs:
    uses: "hacs/action@main"
    with:
      category: "integration"
```

## User Preferences
- ALWAYS discuss before making code changes
- No unnecessary branches or betas
- Keep one release visible in HACS dropdown
- No emojis in code or communication
- Minimize token usage

## Files Modified in Recent Sessions
1. `hacs.json` - Removed invalid keys, added `country`
2. `manifest.json` - Version bumps
3. `vehicle_coordinator.py` - Added `is_ev` property
4. `sensor.py` - Added `exists_fn` for EV sensors
5. `switch.py` - Added `is_ev` check for ChargingSwitch
6. `us_hyundai.py` / `us_genesis.py` - Token refresh logic
7. `us_kia.py` - Duration parameter (removed hardcoded value)
8. `services.py` / `services.yaml` - Duration parameter for climate service

## Immediate Next Actions
1. User runs validation workflow on repo
2. User syncs fork of hacs/default
3. User creates new branch and submits HACS PR
4. Wait for HACS maintainer review

## JP continuation (2026-10-12): native Stamp registration verified

Supersedes earlier statements that current JP stamps cannot be generated.
Issue: https://github.com/NEXTAltair/ha_kia_hyundai_USA/issues/1
Branch: `issue-1-jp-status`; worktree:
`/home/altair/src/ha_kia_hyundai_USA-issue-1-jp-status`.

- Previous scratch files were found at
  `/tmp/claude-1000/-home-altair-src-ha-kia-hyundai-USA/a745a6ee-69dd-49a8-ac64-8195ba77397f/scratchpad`.
- Official Android app 1.0.17 (10260) on `emulator-5584` signs
  `APP_ID:unix_seconds` using `MyApiConfig.stringFromJNI(String)`, returning
  bytes encoded as Android Base64 NO_WRAP. Public APP_ID is read from the
  config instance's `e` field. No signing key was extracted or committed.
- Native Stamp + correct signing headers succeeded against real JP
  `/api/v1/spa/notifications/register`: HTTP 200, retCode S, resCode 0000.
  Also verified through the new integration's Stamp URL flow. Registration
  must omit Authorization and provisional ccsp-device-id headers.
- Added local signer (`scripts/jp_stamp_server.py`), terminal-only read probe
  (`scripts/jp_status_probe.py`), Stamp URL config/Options and reload,
  CCS2/legacy snapshot normalization, JP EV metadata fallback, and diagnostic
  redaction. Unsupported JP charge/limit/wake controls are not offered.
- Local tests: 31 passed on Home Assistant 2025.1.4 / Python 3.12. Ruff passed.
  Test-only ThreadedResolver avoids pycares' global shutdown-thread artifact;
  resource cleanup checks remain enabled.
- User ran `uv run scripts/jp_status_probe.py` with local credentials and got
  `[{"state": "ok"}]`. Real IONIQ 5 CCS2 status and carId-to-spa-ID matching
  are now verified. Battery SOC and valid coordinates were returned; account
  credentials remain unsaved. Redacted output is `/tmp/jp-status.redacted.json`.
  The response's `Vehicle.Date` has fractional seconds; the parser now handles
  this format. No wake/door/charging command was issued. The remaining runtime
  check is entity display in the user's running Home Assistant instance.
- Read JP_BLUELINK.md for the complete setup and limitations. Local signer
  used in this session listens on 127.0.0.1:8765; Frida forwarded on 27042.

## JP P1 continuation and vehicle scope (2026-10-12)

- User requested only P1 (`4244623037`): recover an invalid device ID after
  API resCode 4002. The user owns only IONIQ 5, so live validation and current
  JP work scope are IONIQ 5 EV. ICE/HEV/PHEV and other-model review findings
  are outside scope and must not trigger implementation without new direction.
- A 4002 response clears both cached device ID and resolved app ID, registers
  again with a fresh Stamp, and retries cached status once. A second rejection
  is an API error and clears the cache for a later scheduled refresh.
- HTTP mock regression tests cover successful recovery, bounded repeated
  rejection and subsequent refresh, CCS2/legacy path handling, failed
  re-registration, signer outage, and unchanged non-4002 behavior. All 31
  tests, Ruff and diff checks pass. This failure recovery is mock-tested;
  no device invalidation or control command was issued to the real vehicle.


## JP login language session (2026-10-12, Issue #4)

Context: the user merged PR #2, installed it into production, re-added an
old entry missing client_secret, and authenticated successfully. They
reported the official Bluelink app changed from Japanese to English.
The separate missing-secret repair Issue #3 was cancelled before code
changes when the user chose deleting/re-adding the entry instead.

Decision: match the current JP official login page by POSTing lang=ja to
/api/v1/user/language in the authorize session before /signin. Existing
lang=ja on the authorize URL remains. Stop before sending account credentials
if the language endpoint rejects the request. No other-model/P2 work.

Evidence and rationale: current official login bundles both set the session
language before signin; shared CCS clients implement the same request.
An independent anonymous JP session received HTTP 204 from the language
endpoint. No real account login, language change, or vehicle command was
issued during verification; the official app's resulting language needs a
user runtime check after installing this fix.

Verification: new HTTP tests fail on the prior login order, then pass with
the fix. They cover redirect session cookies, language-before-signin for
200/204, applying Japanese again on re-login, and no credential submission
after 403/503. All 35 Home Assistant tests and Ruff/diff checks pass.
Branch/worktree: issue-4-jp-login-language, based on fork/main 0827ac4.
