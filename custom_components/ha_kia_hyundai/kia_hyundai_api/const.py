"""Constants for the Kia Hyundai API."""

from enum import Enum

SENSITIVE_FIELD_NAMES = [
    "username",
    "password",
    "userid",
    "vin",
    "sid",
    "vinkey",
    "lat",
    "lon",
    "blueLinkServicePin",
    "accessToken",
]

# Kia USA API (iOS app headers)
API_URL_HOST = "api.owners.kia.com"
API_URL_BASE = "https://" + API_URL_HOST + "/apigw/v1/"

# Hyundai USA API (BlueLink)
HYUNDAI_API_URL_HOST = "api.telematics.hyundaiusa.com"
HYUNDAI_LOGIN_API_BASE = "https://" + HYUNDAI_API_URL_HOST + "/v2/ac/"
HYUNDAI_API_URL_BASE = "https://" + HYUNDAI_API_URL_HOST + "/ac/v2/"

# Genesis USA API (uses Hyundai infrastructure with different brand indicator)
GENESIS_API_URL_HOST = "api.telematics.hyundaiusa.com"
GENESIS_LOGIN_API_BASE = "https://" + GENESIS_API_URL_HOST + "/v2/ac/"
GENESIS_API_URL_BASE = "https://" + GENESIS_API_URL_HOST + "/ac/v2/"

# Hyundai Bluelink Japan (Hyundai Mobility Japan Co., Ltd.)
# Confirmed by capturing the official "Hyundai Bluelink" iOS app
# (com.hyundai.bluelink.jp) with mitmproxy, cross-checked against the
# gateway's own web login bundle (ccs-apigw.hyundaijapan.com/web/v1/user).
# UNVERIFIED end-to-end: the login flow below has not yet completed a
# real sign-in. See active_context.md before relying on this.
JP_HYUNDAI_API_URL_HOST = "ccs-apigw.hyundaijapan.com:8080"
JP_HYUNDAI_USER_API_URL = "https://" + JP_HYUNDAI_API_URL_HOST + "/api/v1/user/"
JP_HYUNDAI_PROFILE_API_URL = "https://" + JP_HYUNDAI_API_URL_HOST + "/api/v1/profile/"
JP_HYUNDAI_SPA_API_URL = "https://" + JP_HYUNDAI_API_URL_HOST + "/api/v1/spa/"
JP_HYUNDAI_SPA_API_URL_V2 = "https://" + JP_HYUNDAI_API_URL_HOST + "/api/v2/spa/"
# client_id observed in plaintext in the app's own outbound oauth2/authorize
# request (it is a public OAuth client identifier, not a secret).
JP_HYUNDAI_CLIENT_ID = "e084c0e4-fcfe-47e3-99c9-36eb93593737"
# The matching OAuth client secret is NOT included here. Set it locally via
# your Home Assistant config entry / secrets.yaml — never commit it. See
# active_context.md for why this is deliberately left out of source control.
JP_HYUNDAI_CLIENT_SECRET = None

# Candidate application IDs (ccsp-application-id) seen in the app's compiled
# code. These are application identifiers (same class as the EU/US APP_IDs
# that the upstream library hardcodes openly), not secrets. The device
# registration tries each until one is accepted, so the correct one does not
# need to be pinned down in advance.
JP_HYUNDAI_APP_ID_CANDIDATES = [
    # Verified with Japanese Android app 1.0.17 and local CFB signing.
    "8be122e0-b0d9-40b5-a46e-67b25194ba88",
    "29faeaf5-958c-44ad-a2aa-0dad290a1511",
    "b7b049e3-9978-41e4-839a-a8bb929dcafc",
    "c26da3fa-a163-472f-9e28-a380c470a697",
    "d71b3c0a-a2de-4d7c-8536-2f0d88489342",
    "dcac447e-4fd7-41a6-947e-27c31e7b381b",
    "ee90fe1d-812e-4ff4-891c-63910a8bae91",
    "efb053c1-283a-4dca-8bbe-bb81e5e49832",
]
# CFB signing data is provisioned once from the user's installed app. It is
# intentionally not shipped here; supply it through the JP configuration.
JP_HYUNDAI_CFB = None


class SeatSettings(Enum):
    """Class to hold seat settings."""

    NONE = 0
    HeatHigh = 6
    HeatMedium = 5
    HeatLow = 4
    CoolHigh = 3
    CoolMedium = 2
    CoolLow = 1
