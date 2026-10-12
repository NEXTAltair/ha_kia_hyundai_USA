"""JpHyundai - Hyundai Bluelink API for Japan (Hyundai Mobility Japan Co., Ltd.).

STATUS: work in progress, UNVERIFIED end-to-end. See active_context.md.

Reverse-engineered from network captures of the official "Hyundai Bluelink"
iOS app (bundle id com.hyundai.bluelink.jp) and its own web login bundle
(served by the gateway itself at /web/v1/user/static/js/index.*.js), not
from any private Hyundai documentation.

Login is a three-step OAuth2 authorization-code dance, structurally the same
family as this project's EU Hyundai/Kia/Genesis implementations:

1. GET  {USER_API_URL}oauth2/authorize?response_type=code&client_id=...
        -> sets a session cookie, redirects to the web login UI.
2. POST {USER_API_URL}signin  {"email", "password", "mobileNum"}
        (cookie from step 1 required)
        -> {"redirectUrl": "...&code=..."} on success, or
           {"step": N, ...} if an extra step (agreement/identity
           verification) is required, or an error body with "errCode"
           (confirmed: 4003 = wrong email/password, 4006 = account
           temporarily locked after 5 failed attempts).
3. POST {USER_API_URL}oauth2/token
        grant_type=authorization_code&redirect_uri=...&code=...
        Authorization: Basic base64(client_id:client_secret)
        -> {"access_token", "refresh_token", "token_type", "expires_in"}

Step 3's client_secret is deliberately NOT hardcoded here (see const.py).
Pass it in via Home Assistant's config entry / secrets.yaml.

Vehicle list/status/control endpoint *paths* below were read out of the
gateway's own web bundle (so the paths themselves are public, served to any
browser that loads the login page) and are believed correct, but no field
in a status/vehicle-list response has actually been observed yet — nothing
in this module beyond login has been exercised against a real account.
Treat the parsing below as a first draft to adjust once real responses are
seen, not as confirmed behavior.
"""

import base64
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientSession

from .errors import AuthError, PINLockedError
from .const import (
    JP_HYUNDAI_USER_API_URL,
    JP_HYUNDAI_SPA_API_URL,
    JP_HYUNDAI_SPA_API_URL_V2,
    JP_HYUNDAI_CLIENT_ID,
    JP_HYUNDAI_APP_ID_CANDIDATES,
)

_LOGGER = logging.getLogger(__name__)

USER_AGENT = "okhttp/3.12.0"

_JP_CONTROL_UNSUPPORTED = (
    "Remote control/status is not available for JP Hyundai yet: the CCS API "
    "gateway rejects the /spa/vehicles endpoints without the native app's "
    "device-identity headers (ccsp-application-id + Stamp). Login and the "
    "vehicle list work; see active_context.md for the remaining work."
)


class JpHyundai:
    """Hyundai Bluelink Japan API client."""

    access_token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    vehicles: list[dict] | None = None
    # Coordinator compatibility: the shared VehicleCoordinator tracks an
    # in-flight remote command here. JP remote control is not wired up yet
    # (see get_cached_vehicle_status), so this always stays None.
    last_action = None

    # Refresh the token this many seconds before it actually expires.
    TOKEN_REFRESH_BUFFER_SECONDS = 300

    # Resolved during device registration (only when cfb is supplied).
    spa_device_id: str | None = None
    resolved_app_id: str | None = None

    def __init__(
        self,
        username: str,
        password: str,
        client_secret: str,
        pin: str | None = None,
        client_session: ClientSession | None = None,
        cfb: str | None = None,
    ):
        """Initialize the JP Hyundai Bluelink API client.

        Parameters
        ----------
        username : str
            Bluelink account email address.
        password : str
            Bluelink account password.
        client_secret : str
            The OAuth client secret matching JP_HYUNDAI_CLIENT_ID. Not
            shipped with this codebase — supply it via your own config
            entry / secrets.yaml.
        pin : str, optional
            4-digit remote-control PIN, needed for vehicle commands
            (lock/unlock/climate). Not yet wired up to any request.
        """
        if not client_secret:
            raise ValueError(
                "JpHyundai requires a client_secret; see const.py / "
                "active_context.md for why it isn't hardcoded."
            )
        self.username = username
        self.password = password
        self.client_secret = client_secret
        self.pin = pin
        self.device_id = str(uuid.uuid4())
        # Optional app-embedded CFB key (base64), user-supplied. When present
        # it unlocks the /spa/* device-identity flow (status + control). When
        # None, those stay disabled and degrade gracefully.
        self.cfb = cfb
        if client_session is None:
            self.api_session = ClientSession(raise_for_status=False)
        else:
            self.api_session = client_session

    def _redirect_uri(self) -> str:
        return JP_HYUNDAI_USER_API_URL + "oauth2/redirect"

    async def login(self):
        """Run the authorize -> signin -> token-exchange dance."""
        _LOGGER.info("JP Hyundai login attempt for user: %s", self.username)

        # Step 1: GET oauth2/authorize to obtain the session cookie that
        # /signin needs. aiohttp's ClientSession keeps cookies automatically
        # across requests on the same session as long as redirects are
        # followed (default), matching what the real app/browser does.
        authorize_url = (
            JP_HYUNDAI_USER_API_URL
            + "oauth2/authorize"
            + f"?response_type=code&state=test&client_id={JP_HYUNDAI_CLIENT_ID}"
            + f"&redirect_uri={self._redirect_uri()}&lang=ja"
        )
        await self.api_session.get(authorize_url, headers={"User-Agent": USER_AGENT})

        # Step 2: POST signin (matches the official web client's
        # account.signin({email,password,mobileNum}) call to this same path).
        signin_response = await self.api_session.post(
            JP_HYUNDAI_USER_API_URL + "signin",
            json={
                "email": self.username,
                "password": self.password,
                "mobileNum": None,
            },
            headers={
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        signin_body = await signin_response.json()

        if signin_response.status not in range(200, 304):
            err_code = signin_body.get("errCode")
            if err_code == "4006":
                raise PINLockedError(
                    "JP Hyundai account temporarily locked after repeated "
                    "failed sign-ins. Wait before retrying."
                )
            if err_code == "4003":
                raise AuthError("JP Hyundai sign-in failed: wrong email or password.")
            raise AuthError(f"JP Hyundai sign-in failed: {signin_body}")

        if "redirectUrl" not in signin_body:
            raise AuthError(
                "JP Hyundai sign-in requires an additional step not yet "
                f"implemented (step={signin_body.get('step')!r}): {signin_body}"
            )

        parsed = urlparse(signin_body["redirectUrl"])
        codes = parse_qs(parsed.query).get("code")
        if not codes:
            raise AuthError(
                f"JP Hyundai sign-in redirectUrl had no code: {signin_body['redirectUrl']}"
            )
        authorization_code = codes[0]

        # Step 3: exchange the authorization code for an access token.
        await self._exchange_code_for_token(authorization_code)
        _LOGGER.info("JP Hyundai login succeeded for user: %s", self.username)

    async def _exchange_code_for_token(self, authorization_code: str):
        import base64

        basic = base64.b64encode(
            f"{JP_HYUNDAI_CLIENT_ID}:{self.client_secret}".encode()
        ).decode()
        data = (
            "grant_type=authorization_code"
            f"&redirect_uri={self._redirect_uri()}"
            f"&code={authorization_code}"
        )
        response = await self.api_session.post(
            JP_HYUNDAI_USER_API_URL + "oauth2/token",
            data=data,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
            },
        )
        body = await response.json()
        if response.status not in range(200, 304) or "access_token" not in body:
            raise AuthError(f"JP Hyundai token exchange failed: {body}")

        self.access_token = body["token_type"] + " " + body["access_token"]
        self.refresh_token = body.get("refresh_token")
        expires_in = body.get("expires_in")
        self.token_expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            if expires_in
            else None
        )

    def _auth_headers(self) -> dict:
        if not self.access_token:
            raise AuthError("Not logged in.")
        return {
            "Authorization": self.access_token,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }

    def _is_token_valid(self) -> bool:
        if self.access_token is None:
            return False
        if self.token_expires_at is None:
            return True
        buffer = timedelta(seconds=self.TOKEN_REFRESH_BUFFER_SECONDS)
        return datetime.now(timezone.utc) < self.token_expires_at - buffer

    async def _ensure_token_valid(self) -> None:
        """Re-login if the access token is missing or about to expire.

        Login is fully headless (username + password + client_secret), so
        the coordinator can keep refreshing indefinitely without any
        interactive step. The ~1h token lifetime is handled here.
        """
        if self._is_token_valid():
            return
        _LOGGER.info("JP Hyundai token missing or expiring soon; re-logging in")
        self.access_token = None
        await self.login()

    async def get_vehicles(self) -> list[dict]:
        """Get list of vehicles for the account.

        CONFIRMED against a real account (2026-10-11): matches the official
        web client's own account.cars('GET')(), which hits GET
        /api/v1/user/cars and reads response.data.cars (an array).
        (/api/v1/profile/vehicles, tried first, 404s on this gateway — that
        path list was apparently frontend routes, not API endpoints.)

        Real response shape observed:
            {"cars": [{"carId": "<uuid>", "carName": "IONIQ 5",
                       "vin": "...", "carType": "EV", "pinchecker": false,
                       "createDate": "20250704T045401.312",
                       "vehicleName": "IONIQ 5"}]}
        "carId" is the vehicleId used in the /spa/vehicles/{vehicleId}/...
        endpoints (unconfirmed — not yet exercised against a real account).
        """
        await self._ensure_token_valid()
        response = await self.api_session.get(
            JP_HYUNDAI_USER_API_URL + "cars",
            headers=self._auth_headers(),
        )
        body = await response.json()
        _LOGGER.debug("JP Hyundai get_vehicles raw response: %s", body)
        self.vehicles = [
            {
                "id": car.get("carId"),
                "vin": car.get("vin"),
                "VIN": car.get("vin"),
                "nickName": car.get("carName") or car.get("vehicleName", ""),
                "carType": car.get("carType", ""),
                "is_ev": car.get("carType") == "EV",
            }
            for car in body.get("cars", [])
        ]
        return self.vehicles

    # ------------------------------------------------------------------
    # /spa/* device-identity flow (Stamp + device registration).
    #
    # Mirrors the upstream hyundai_kia_connect_api EU implementation, which
    # this JP backend matches (same CCS platform, same ccsp-* headers and
    # /spa/ structure seen in the app). The Stamp is
    #   base64( XOR( cfb_bytes, f"{app_id}:{unix_seconds}" ) )
    # with an app-embedded CFB key. The CFB value is user-supplied (never
    # shipped); when absent, all of this is skipped and status/control
    # degrade gracefully, preserving the "vehicle appears, no live data"
    # behavior. UNVERIFIED end-to-end: not yet exercised against the real
    # backend with a real CFB — see active_context.md and the probe
    # scratchpad/jp_probe_spa_access.py.
    # ------------------------------------------------------------------

    @property
    def spa_enabled(self) -> bool:
        """True if a CFB key was supplied, enabling the /spa/* flow."""
        return bool(self.cfb)

    def _get_stamp(self, app_id: str) -> str:
        cfb_bytes = base64.b64decode(self.cfb)
        raw = f"{app_id}:{int(time.time())}".encode()
        xored = bytes(b ^ cfb_bytes[i % len(cfb_bytes)] for i, b in enumerate(raw))
        return base64.b64encode(xored).decode()

    def _spa_headers(self, app_id: str, device_id: str) -> dict:
        return {
            "Authorization": self.access_token,
            "ccsp-service-id": JP_HYUNDAI_CLIENT_ID,
            "ccsp-application-id": app_id,
            "ccsp-device-id": device_id,
            "Stamp": self._get_stamp(app_id),
            "Accept": "application/json",
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": USER_AGENT,
        }

    async def _ensure_device_registered(self) -> bool:
        """Register a device to unlock /spa/* access. Returns success.

        Tries each candidate APP_ID until registration succeeds (only the
        APP_ID matching the supplied CFB produces a valid Stamp). Caches the
        resolved APP_ID + device id for the session.
        """
        if not self.spa_enabled:
            return False
        if self.spa_device_id and self.resolved_app_id:
            return True
        await self._ensure_token_valid()

        for app_id in JP_HYUNDAI_APP_ID_CANDIDATES:
            device_id = str(uuid.uuid4())
            payload = {
                "pushRegId": f"{uuid.uuid4().hex}{uuid.uuid4().hex}",
                "pushType": "GCM",
                "uuid": device_id,
            }
            try:
                resp = await self.api_session.post(
                    JP_HYUNDAI_SPA_API_URL + "notifications/register",
                    json=payload,
                    headers=self._spa_headers(app_id, device_id),
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("JP device register error for app_id %s: %s", app_id, err)
                continue
            if resp.status not in range(200, 304):
                _LOGGER.debug(
                    "JP device register rejected app_id %s (%s)", app_id, resp.status
                )
                continue
            body = await resp.json()
            resolved = (
                (body.get("resMsg") or {}).get("deviceId")
                if isinstance(body.get("resMsg"), dict)
                else body.get("deviceId")
            )
            if resolved:
                self.spa_device_id = resolved
                self.resolved_app_id = app_id
                _LOGGER.info("JP /spa/ device registered (app_id resolved)")
                return True
        _LOGGER.warning(
            "JP device registration failed for all candidate app_ids; the CFB "
            "key may be wrong or the Stamp mechanism differs. Live status/"
            "control stays disabled."
        )
        return False

    async def get_cached_vehicle_status(self, vehicle_id: str) -> dict:
        """Get vehicle status.

        If a CFB key is configured, register a device and call the /spa/
        status endpoint with the device-identity headers. Otherwise (or on
        any failure) return {} so the Home Assistant integration still loads
        and the vehicle appears as a device; status-derived entities just
        report unknown. The empty dict is shaped for the shared coordinator,
        which reads it with safely_get_json_value.
        """
        await self._ensure_token_valid()

        if not self.spa_enabled:
            return {}
        if not await self._ensure_device_registered():
            return {}

        response = await self.api_session.get(
            JP_HYUNDAI_SPA_API_URL_V2 + f"vehicles/{vehicle_id}/status",
            headers=self._spa_headers(self.resolved_app_id, self.spa_device_id),
        )
        if response.status not in range(200, 304):
            _LOGGER.warning(
                "JP Hyundai vehicle status request failed (%s); returning empty "
                "status.", response.status
            )
            return {}
        body = await response.json()
        # IMPORTANT: the real JP /spa/status structure has never been observed
        # (the 403 blocked it until a CFB was supplied). The shared
        # VehicleCoordinator reads US-BlueLink-shaped paths
        # (lastVehicleInfo.vehicleStatusRpt.vehicleStatus...), which the JP
        # body may NOT match — so entities can stay "unknown" even on a 200.
        # This logs the raw body at INFO once so the real shape can be
        # captured and the field mapping written. Remove/lower to DEBUG after
        # the mapping exists. See active_context.md.
        _LOGGER.info(
            "JP Hyundai raw /spa/ status (capture this to build the field "
            "mapping): %s", body
        )
        return body

    async def check_last_action_finished(self, vehicle_id: str) -> bool:
        """No remote commands are issued yet, so nothing is ever in flight."""
        return True

    async def request_vehicle_data_sync(self, vehicle_id: str):
        """Force-refresh from the vehicle. Needs the /spa/ flow (CFB)."""
        raise NotImplementedError(_JP_CONTROL_UNSUPPORTED)

    async def _control_door(self, vehicle_id: str, action: str):
        if not self.spa_enabled:
            raise NotImplementedError(_JP_CONTROL_UNSUPPORTED)
        if not await self._ensure_device_registered():
            raise NotImplementedError(_JP_CONTROL_UNSUPPORTED)
        payload = {"action": action, "deviceId": self.spa_device_id}
        resp = await self.api_session.post(
            JP_HYUNDAI_SPA_API_URL_V2 + f"vehicles/{vehicle_id}/control/door",
            json=payload,
            headers=self._spa_headers(self.resolved_app_id, self.spa_device_id),
        )
        if resp.status not in range(200, 304):
            body = await resp.text()
            raise AuthError(f"JP door control failed ({resp.status}): {body[:200]}")
        return await resp.json()

    async def lock(self, vehicle_id: str):
        """Lock the vehicle (needs a configured CFB key; else NotImplemented)."""
        return await self._control_door(vehicle_id, "close")

    async def unlock(self, vehicle_id: str):
        """Unlock the vehicle (needs a configured CFB key; else NotImplemented)."""
        return await self._control_door(vehicle_id, "open")
