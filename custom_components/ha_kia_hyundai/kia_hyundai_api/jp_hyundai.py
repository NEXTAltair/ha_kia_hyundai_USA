"""JpHyundai - Hyundai Bluelink API for Japan (Hyundai Mobility Japan Co., Ltd.).

Login and vehicle discovery have been verified against a real JP account.
The current official Android app can generate a valid Stamp through its
native signing function; the optional local Stamp service connects it to
this client. Device registration and CCS2 cached status have been verified
live on IONIQ 5 EV. Other models and remote control are not verified.

The OAuth client secret and any legacy CFB value are user-supplied. No
application binary, credential, or signing key is distributed with this
integration. See JP_BLUELINK.md for setup and validation steps.
"""

import base64
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from aiohttp import ClientError, ClientSession, ClientTimeout

from .errors import AuthError, PINLockedError
from .jp_status import normalize_status
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
        stamp_url: str | None = None,
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
        self.stamp_url = stamp_url
        if stamp_url:
            parsed_url = urlparse(stamp_url)
            if parsed_url.scheme not in ("http", "https") or not parsed_url.hostname or parsed_url.username:
                raise ValueError("Stamp provider URL must be an HTTP(S) URL without embedded credentials")
        self.status_diagnostics = {"state": "not_requested"}
        self.last_status_response = None
        self.vehicle_status_diagnostics = {}
        self.vehicle_status_responses = {}
        if client_session is None:
            self.api_session = ClientSession(raise_for_status=False)
        else:
            self.api_session = client_session

    def _redirect_uri(self) -> str:
        return JP_HYUNDAI_USER_API_URL + "oauth2/redirect"

    async def login(self):
        """Run the authorize -> signin -> token-exchange dance."""
        _LOGGER.info("JP Hyundai login attempt")

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

        # Step 2: The JP web client sets the session language before signin.
        # This is separate from the lang=ja authorize parameter.
        async with self.api_session.post(
            JP_HYUNDAI_USER_API_URL + "language",
            json={"lang": "ja"},
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        ) as language_response:
            if language_response.status not in range(200, 300):
                raise AuthError(
                    "JP Hyundai session language setup failed "
                    f"(HTTP {language_response.status})."
                )
            # The gateway normally returns 204 with no JSON body.
            await language_response.read()

        # Step 3: POST signin (matches the official web client's
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

        # Step 4: exchange the authorization code for an access token.
        await self._exchange_code_for_token(authorization_code)
        _LOGGER.info("JP Hyundai login succeeded")

    async def _exchange_code_for_token(self, authorization_code: str):
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
        _LOGGER.debug("JP Hyundai received %d vehicles", len(body.get("cars", [])))
        self.vehicles = [
            {
                "id": car.get("carId"),
                "vin": car.get("vin"),
                "VIN": car.get("vin"),
                "nickName": car.get("carName") or car.get("vehicleName", ""),
                "modelName": car.get("vehicleName") or car.get("carName", ""),
                "carType": car.get("carType", ""),
                "is_ev": car.get("carType") == "EV",
            }
            for car in body.get("cars", [])
        ]
        return self.vehicles

    # The local provider uses the official app's native signer. The older
    # XOR/CFB implementation is kept as an optional compatibility path.

    @property
    def spa_enabled(self) -> bool:
        """True if a Stamp service or legacy CFB key was configured."""
        return bool(self.cfb or self.stamp_url)

    def _get_stamp(self, app_id: str) -> str:
        try:
            cfb_bytes = base64.b64decode(self.cfb, validate=True)
        except (TypeError, ValueError) as err:
            raise ClientError("Invalid legacy CFB signing key") from err
        if not cfb_bytes:
            raise ClientError("Invalid legacy CFB signing key")
        raw = f"{app_id}:{int(time.time())}".encode()
        xored = bytes(b ^ cfb_bytes[i % len(cfb_bytes)] for i, b in enumerate(raw))
        return base64.b64encode(xored).decode()

    async def _spa_headers(self, app_id: str, device_id: str) -> dict:
        stamp = self._get_stamp(app_id) if not self.stamp_url else None
        if self.stamp_url:
            # Never forward account headers or cookies to the signing service.
            # A short-lived isolated session also avoids absorbing its cookies
            # into the account session. Redirects are deliberately rejected.
            async with (
                ClientSession(timeout=ClientTimeout(total=10)) as session,
                session.get(self.stamp_url, allow_redirects=False) as response,
            ):
                if response.status != 200:
                    raise ClientError("JP Stamp provider did not return HTTP 200")
                try:
                    body = await response.json()
                except ValueError as err:
                    raise ClientError("JP Stamp provider returned invalid JSON") from err
            if not isinstance(body, dict):
                raise ClientError("JP Stamp provider returned an invalid response")
            app_id, stamp = body.get("app_id"), body.get("stamp")
            if not isinstance(app_id, str) or not isinstance(stamp, str) or not 0 < len(stamp) <= 4096:
                raise ClientError("JP Stamp provider returned invalid signing data")
            try:
                app_id = str(uuid.UUID(app_id))
                decoded = base64.b64decode(stamp, validate=True)
            except (TypeError, ValueError) as err:
                raise ClientError("JP Stamp provider returned invalid signing data") from err
            if not decoded:
                raise ClientError("JP Stamp provider returned invalid signing data")
            if self.spa_device_id and self.resolved_app_id != app_id:
                self.spa_device_id = None
                self.resolved_app_id = None
                raise ClientError("JP Stamp provider changed application ID; registration will retry")
        return {
            "Authorization": self.access_token,
            "ccsp-service-id": JP_HYUNDAI_CLIENT_ID,
            "ccsp-application-id": app_id,
            "ccsp-device-id": device_id,
            "Stamp": stamp,
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

        app_ids = ("",) if self.stamp_url else JP_HYUNDAI_APP_ID_CANDIDATES
        for app_id in app_ids:
            device_id = str(uuid.uuid4())
            payload = {
                "pushRegId": f"{uuid.uuid4().hex}{uuid.uuid4().hex}",
                "pushType": "GCM",
                "uuid": device_id,
            }
            try:
                headers = await self._spa_headers(app_id, device_id)
                # Registration precedes account authentication in the native
                # app. An invalid/expired Bearer header rejects even a valid
                # Stamp; neither account nor device credentials belong here.
                headers.pop("Authorization", None)
                headers.pop("ccsp-device-id", None)
                resp = await self.api_session.post(
                    JP_HYUNDAI_SPA_API_URL + "notifications/register",
                    json=payload,
                    headers=headers,
                )
            except (ClientError, TimeoutError) as err:
                if self.stamp_url:
                    raise
                _LOGGER.debug("JP device register failed: %s", type(err).__name__)
                continue
            if resp.status not in range(200, 304):
                _LOGGER.debug(
                    "JP device register rejected app_id %s (%s)", app_id, resp.status
                )
                continue
            body = await resp.json()
            if body.get("retCode") not in (None, "S"):
                continue
            resolved = (
                (body.get("resMsg") or {}).get("deviceId")
                if isinstance(body.get("resMsg"), dict)
                else body.get("deviceId")
            )
            if resolved:
                self.spa_device_id = resolved
                self.resolved_app_id = headers["ccsp-application-id"]
                _LOGGER.info("JP /spa/ device registered (app_id resolved)")
                return True
        _LOGGER.warning(
            "JP device registration failed for all candidate app_ids; the CFB "
            "key may be wrong or the Stamp mechanism differs. Live status/"
            "control stays disabled."
        )
        return False

    def _record_fetch(self, vehicle_id: str, diagnostics: dict) -> None:
        self.status_diagnostics = diagnostics
        self.vehicle_status_diagnostics[vehicle_id] = diagnostics

    async def get_cached_vehicle_status(self, vehicle_id: str) -> dict:
        """Get vehicle status.

        A Stamp service or legacy CFB key enables device registration and
        cached snapshots. Missing signing data or a rejected response returns
        an empty snapshot so identity and EV sensors can still be set up.
        Diagnostics record the failure without exposing the raw body in logs.
        """
        await self._ensure_token_valid()
        return await self._fetch_cached_vehicle_status(
            vehicle_id, retry_device_registration=True
        )

    async def _fetch_cached_vehicle_status(
        self, vehicle_id: str, *, retry_device_registration: bool
    ) -> dict:
        """Fetch a snapshot, recovering a rejected registration at most once."""
        self.last_status_response = None
        self.vehicle_status_responses.pop(vehicle_id, None)

        if not self.spa_enabled:
            self._record_fetch(vehicle_id, {"state": "stamp_not_configured"})
            return {}
        try:
            registered = await self._ensure_device_registered()
        except (ClientError, TimeoutError):
            self._record_fetch(vehicle_id, {"state": "stamp_provider_unavailable"})
            _LOGGER.warning("JP Stamp provider unavailable; live status is unavailable")
            return {}
        if not registered:
            self._record_fetch(vehicle_id, {"state": "device_registration_failed"})
            return {}

        # E-GMP vehicles (IONIQ 5/6 etc.) are "CCS2" and expose status at a
        # different path than the legacy CCS fleet. The car-list response is
        # too minimal to tell them apart reliably, so try the CCS2 cached-
        # status path first and fall back to the legacy path only on 404
        # (a wrong-generation path). Any other status (e.g. 403) is a real
        # failure, not a path mismatch, so we stop and return {}.
        # Endpoint paths were read out of the app dex; the IONIQ 5 is CCS2.
        status_urls = (
            JP_HYUNDAI_SPA_API_URL + f"vehicles/{vehicle_id}/ccs2/carstatus/latest",
            JP_HYUNDAI_SPA_API_URL + f"vehicles/{vehicle_id}/status/latest",
        )
        response = None
        for url in status_urls:
            try:
                headers = await self._spa_headers(self.resolved_app_id, self.spa_device_id)
            except (ClientError, TimeoutError):
                self._record_fetch(vehicle_id, {"state": "stamp_provider_unavailable"})
                return {}
            headers["Ccuccs2protocolsupport"] = "1" if "/ccs2/" in url else "0"
            response = await self.api_session.get(url, headers=headers)
            if response.status == 404:
                _LOGGER.debug("JP status path 404, trying fallback: %s", url)
                continue
            break
        if response is None or response.status not in range(200, 304):
            self._record_fetch(vehicle_id, {
                "state": "gateway_rejected" if response is not None and response.status == 403 else "http_error",
                "http_status": None if response is None else response.status,
            })
            _LOGGER.warning(
                "JP Hyundai vehicle status request failed (%s); returning empty "
                "status.", None if response is None else response.status
            )
            return {}
        body = await response.json()
        self.last_status_response = body
        self.vehicle_status_responses[vehicle_id] = body
        if isinstance(body, dict) and body.get("retCode") not in (None, "S"):
            if str(body.get("resCode")) == "4002":
                # Even the final rejection must invalidate the cache so the
                # next scheduled refresh can register a fresh device.
                self.spa_device_id = None
                self.resolved_app_id = None
                if retry_device_registration:
                    _LOGGER.info("JP device registration rejected; re-registering once")
                    return await self._fetch_cached_vehicle_status(
                        vehicle_id, retry_device_registration=False
                    )
            self._record_fetch(vehicle_id, {"state": "api_error", "res_code": body.get("resCode")})
            return {}
        is_ev = any(car.get("id") == vehicle_id and car.get("is_ev")
                    for car in self.vehicles or [])
        result = normalize_status(body, is_ev=is_ev)
        if result is None:
            self._record_fetch(vehicle_id, {"state": "unsupported_response"})
            _LOGGER.warning("JP vehicle status has an unsupported response structure")
            return {}
        self._record_fetch(vehicle_id, {"state": "ok"})
        return result

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
        # Like status, CCS2 (IONIQ 5/6) and legacy fleets use different door
        # endpoints. Try the CCS2 path first, fall back to legacy on 404.
        # The exact CCS2 command payload is unverified (no live capture yet);
        # a wrong payload surfaces as a non-404 error with the body, never a
        # physical action, so this is safe to attempt.
        payload = {"action": action, "deviceId": self.spa_device_id}
        control_urls = (
            JP_HYUNDAI_SPA_API_URL_V2 + f"vehicles/{vehicle_id}/ccs2/control/door",
            JP_HYUNDAI_SPA_API_URL_V2 + f"vehicles/{vehicle_id}/control/door",
        )
        resp = None
        for url in control_urls:
            resp = await self.api_session.post(
                url,
                json=payload,
                headers=await self._spa_headers(self.resolved_app_id, self.spa_device_id),
            )
            if resp.status == 404:
                _LOGGER.debug("JP door path 404, trying fallback: %s", url)
                continue
            break
        if resp is None or resp.status not in range(200, 304):
            body = "" if resp is None else await resp.text()
            status = None if resp is None else resp.status
            raise AuthError(f"JP door control failed ({status}): {body[:200]}")
        return await resp.json()

    async def lock(self, vehicle_id: str):
        """Lock the vehicle (needs a configured CFB key; else NotImplemented)."""
        return await self._control_door(vehicle_id, "close")

    async def unlock(self, vehicle_id: str):
        """Unlock the vehicle (needs a configured CFB key; else NotImplemented)."""
        return await self._control_door(vehicle_id, "open")
