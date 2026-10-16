"""Google sign-in (OAuth 2.0 authorization-code flow, handled entirely server-side)."""
from urllib.parse import urlencode

import httpx

import config

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def configured() -> bool:
    return bool(config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET)


def redirect_uri() -> str:
    return f"{config.APP_URL}/api/auth/google/callback"


def authorize_url(state: str) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": config.GOOGLE_CLIENT_ID, "redirect_uri": redirect_uri(), "response_type": "code",
        "scope": "openid email profile", "state": state, "prompt": "select_account"})


def fetch_userinfo(code: str) -> dict:
    """Exchange the one-time code for an access token, then read the profile ({sub, email, email_verified, name}).
    The token comes straight from Google over TLS, so no id_token signature checking is needed."""
    with httpx.Client(timeout=15) as h:
        t = h.post(TOKEN_URL, data={"code": code, "client_id": config.GOOGLE_CLIENT_ID, "client_secret": config.GOOGLE_CLIENT_SECRET,
                                    "redirect_uri": redirect_uri(), "grant_type": "authorization_code"})
        t.raise_for_status()
        u = h.get(USERINFO_URL, headers={"Authorization": "Bearer " + t.json()["access_token"]})
        u.raise_for_status()
        return u.json()
