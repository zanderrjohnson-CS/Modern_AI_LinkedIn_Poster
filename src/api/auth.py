"""
LinkedIn OAuth2 authentication flow and token management.

Handles:
- 3-legged OAuth2 authorization (opens browser, runs local callback server)
- Token storage and retrieval (tokens.json)
- Automatic token refresh when expired
"""

import json
import time
import secrets
import webbrowser
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from pathlib import Path

import requests

from src.config import Config


class _CallbackHandler(BaseHTTPRequestHandler):
    """Handles the OAuth2 redirect callback on localhost."""

    auth_code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if "error" in params:
            _CallbackHandler.error = params["error"][0]
            self._respond("Authorization failed. Check the terminal for details. You can close this tab.")
            return

        _CallbackHandler.auth_code = params.get("code", [None])[0]
        _CallbackHandler.state = params.get("state", [None])[0]
        self._respond("Authorization successful! You can close this tab and return to the terminal.")

    def _respond(self, message: str):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        html = f"""<!DOCTYPE html>
        <html><body style="font-family: system-ui; display: flex; justify-content: center;
        align-items: center; height: 100vh; margin: 0; background: #f8f9fa;">
        <div style="text-align: center; padding: 2rem; background: white; border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <h2>{message}</h2></div></body></html>"""
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        """Suppress default request logging."""
        pass


class TokenManager:
    """Manages OAuth2 tokens: storage, retrieval, and refresh."""

    def __init__(self, token_file: Path = Config.TOKEN_FILE):
        self.token_file = token_file
        self._tokens: dict | None = None

    def load(self) -> dict | None:
        """Load tokens from disk."""
        if self.token_file.exists():
            with open(self.token_file) as f:
                self._tokens = json.load(f)
            return self._tokens
        return None

    def save(self, tokens: dict):
        """Save tokens to disk."""
        self._tokens = tokens
        with open(self.token_file, "w") as f:
            json.dump(tokens, f, indent=2)

    def is_expired(self) -> bool:
        """Check if the access token has expired."""
        if not self._tokens:
            return True
        expires_at = self._tokens.get("expires_at", 0)
        # Consider expired 5 minutes early to avoid edge cases
        return time.time() > (expires_at - 300)

    @property
    def access_token(self) -> str | None:
        if self._tokens:
            return self._tokens.get("access_token")
        return None

    @property
    def refresh_token(self) -> str | None:
        if self._tokens:
            return self._tokens.get("refresh_token")
        return None


def authorize() -> dict:
    """
    Run the full OAuth2 authorization flow:
    1. Start local callback server
    2. Open browser to LinkedIn authorization page
    3. Wait for the redirect with the auth code
    4. Exchange code for tokens
    5. Save and return tokens

    Returns:
        dict with access_token, refresh_token, expires_at, user_info
    """
    issues = Config.validate()
    if issues:
        raise RuntimeError("Configuration errors:\n" + "\n".join(f"  - {i}" for i in issues))

    # Generate state parameter for CSRF protection
    state = secrets.token_urlsafe(32)

    # Reset handler state
    _CallbackHandler.auth_code = None
    _CallbackHandler.state = None
    _CallbackHandler.error = None

    # Parse redirect URI to get the port
    parsed_redirect = urllib.parse.urlparse(Config.REDIRECT_URI)
    port = parsed_redirect.port or 8000

    # Start local server in background thread
    server = HTTPServer(("localhost", port), _CallbackHandler)
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()

    # Build authorization URL
    auth_params = {
        "response_type": "code",
        "client_id": Config.CLIENT_ID,
        "redirect_uri": Config.REDIRECT_URI,
        "state": state,
        "scope": " ".join(Config.SCOPES),
    }
    auth_url = f"{Config.AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    print(f"\nOpening browser for LinkedIn authorization...")
    print(f"If it doesn't open, visit:\n{auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for callback
    print("Waiting for authorization callback...")
    thread.join(timeout=120)
    server.server_close()

    if _CallbackHandler.error:
        raise RuntimeError(f"Authorization failed: {_CallbackHandler.error}")

    if not _CallbackHandler.auth_code:
        raise RuntimeError("No authorization code received. Did you authorize in the browser?")

    if _CallbackHandler.state != state:
        raise RuntimeError("State mismatch — possible CSRF attack. Try again.")

    # Exchange auth code for tokens
    print("Exchanging authorization code for tokens...")
    tokens = _exchange_code(_CallbackHandler.auth_code)

    # Fetch user info to get the person URN
    print("Fetching your LinkedIn profile info...")
    user_info = _fetch_user_info(tokens["access_token"])
    tokens["user_id"] = user_info["sub"]
    tokens["user_name"] = user_info.get("name", "Unknown")
    tokens["person_urn"] = f"urn:li:person:{user_info['sub']}"

    # Save tokens
    token_mgr = TokenManager()
    token_mgr.save(tokens)

    print(f"\n✓ Authenticated as: {tokens['user_name']}")
    print(f"  Person URN: {tokens['person_urn']}")
    print(f"  Token expires: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(tokens['expires_at']))}")
    print(f"  Saved to: {Config.TOKEN_FILE}")

    return tokens


def _exchange_code(code: str) -> dict:
    """Exchange an authorization code for access and refresh tokens."""
    resp = requests.post(
        Config.TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": Config.CLIENT_ID,
            "client_secret": Config.CLIENT_SECRET,
            "redirect_uri": Config.REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in", 5184000),  # default 60 days
        "expires_at": time.time() + data.get("expires_in", 5184000),
        "scope": data.get("scope", ""),
    }


def refresh_access_token(refresh_token: str) -> dict:
    """Use a refresh token to get a new access token."""
    resp = requests.post(
        Config.TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": Config.CLIENT_ID,
            "client_secret": Config.CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", refresh_token),
        "expires_in": data.get("expires_in", 5184000),
        "expires_at": time.time() + data.get("expires_in", 5184000),
        "scope": data.get("scope", ""),
    }


def _fetch_user_info(access_token: str) -> dict:
    """Fetch the authenticated user's profile info via OpenID Connect."""
    resp = requests.get(
        f"{Config.API_BASE}/v2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    resp.raise_for_status()
    return resp.json()


def get_valid_token() -> tuple[str, str]:
    """
    Load tokens, refresh if needed, and return (access_token, person_urn).

    Raises RuntimeError if no tokens exist or refresh fails.
    """
    mgr = TokenManager()
    tokens = mgr.load()

    if not tokens:
        raise RuntimeError(
            "No saved tokens found. Run 'python -m src.cli auth' first."
        )

    if mgr.is_expired():
        if not mgr.refresh_token:
            raise RuntimeError(
                "Access token expired and no refresh token available.\n"
                "Run 'python -m src.cli auth' to re-authorize."
            )
        print("Access token expired, refreshing...")
        try:
            new_tokens = refresh_access_token(mgr.refresh_token)
            # Preserve user info from old tokens
            new_tokens["user_id"] = tokens.get("user_id")
            new_tokens["user_name"] = tokens.get("user_name")
            new_tokens["person_urn"] = tokens.get("person_urn")
            mgr.save(new_tokens)
            tokens = new_tokens
            print("✓ Token refreshed successfully.")
        except requests.HTTPError as e:
            raise RuntimeError(
                f"Token refresh failed ({e}). Run 'python -m src.cli auth' to re-authorize."
            )

    return tokens["access_token"], tokens["person_urn"]