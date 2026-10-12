"""Keep the JP login session in Japanese before authenticating the account."""

import base64

from aiohttp import ClientSession, CookieJar, web
from aiohttp.resolver import ThreadedResolver
from aiohttp.test_utils import TestServer
import pytest

from custom_components.ha_kia_hyundai.kia_hyundai_api import jp_hyundai as jp
from custom_components.ha_kia_hyundai.kia_hyundai_api import AuthError


@pytest.fixture
async def login_server(monkeypatch, socket_enabled):
    monkeypatch.setattr("aiohttp.connector.DefaultResolver", ThreadedResolver)
    state = {"calls": [], "language_status": 204, "session_language": "en"}

    async def authorize(request):
        assert request.query["lang"] == "ja"
        state["calls"].append("authorize")
        state["session_language"] = "en"
        response = web.HTTPFound("/login-page")
        response.set_cookie("login-session", "synthetic-session")
        raise response

    async def login_page(request):
        assert request.cookies["login-session"] == "synthetic-session"
        return web.Response(text="Synthetic login page")

    async def language(request):
        assert request.cookies["login-session"] == "synthetic-session"
        assert "Authorization" not in request.headers
        assert await request.json() == {"lang": "ja"}
        state["calls"].append("language")
        if state["language_status"] < 300:
            state["session_language"] = "ja"
        return web.Response(status=state["language_status"])

    async def signin(request):
        assert request.cookies["login-session"] == "synthetic-session"
        assert await request.json() == {
            "email": "test@example.invalid",
            "password": "test-password",
            "mobileNum": None,
        }
        state["calls"].append("signin")
        state["signin_language"] = state["session_language"]
        return web.json_response(
            {
                "redirectUrl": str(
                    request.url.with_path("/redirect").with_query(code="test-code")
                )
            }
        )

    async def token(request):
        assert request.cookies["login-session"] == "synthetic-session"
        basic = base64.b64encode(
            f"{jp.JP_HYUNDAI_CLIENT_ID}:test-secret".encode()
        ).decode()
        assert request.headers["Authorization"] == f"Basic {basic}"
        assert (await request.post())["code"] == "test-code"
        state["calls"].append("token")
        return web.json_response(
            {
                "token_type": "Bearer",
                "access_token": "test-token",
                "refresh_token": "test-refresh-token",
                "expires_in": 3600,
            }
        )

    app = web.Application()
    app.router.add_get("/user/oauth2/authorize", authorize)
    app.router.add_get("/login-page", login_page)
    app.router.add_post("/user/language", language)
    app.router.add_post("/user/signin", signin)
    app.router.add_post("/user/oauth2/token", token)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(jp, "JP_HYUNDAI_USER_API_URL", str(server.make_url("/user/")))
    # Only the loopback test server needs IP-address cookies enabled.
    async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as session:
        client = jp.JpHyundai(
            "test@example.invalid",
            "test-password",
            "test-secret",
            client_session=session,
        )
        try:
            yield client, state
        finally:
            await server.close()


@pytest.mark.parametrize("language_status", [200, 204])
async def test_login_sets_japanese_before_signin(login_server, language_status):
    client, state = login_server
    state["language_status"] = language_status
    await client.login()
    assert state["calls"] == ["authorize", "language", "signin", "token"]
    assert state["signin_language"] == "ja"
    assert client.access_token == "Bearer test-token"

    # Expiring authentication must apply the same language on the next login.
    client.access_token = None
    state["calls"].clear()
    await client._ensure_token_valid()
    assert state["calls"] == ["authorize", "language", "signin", "token"]
    assert state["signin_language"] == "ja"


@pytest.mark.parametrize("language_status", [403, 503])
async def test_language_failure_stops_before_sending_credentials(
    login_server, language_status
):
    client, state = login_server
    state["language_status"] = language_status
    with pytest.raises(AuthError, match=f"session language.*{language_status}"):
        await client.login()
    assert state["calls"] == ["authorize", "language"]
    assert client.access_token is None
