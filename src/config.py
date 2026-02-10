"""Configuration loader for LinkedIn Tracker."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class Config:
    """Application configuration pulled from environment variables."""

    CLIENT_ID: str = os.getenv("LINKEDIN_CLIENT_ID", "")
    CLIENT_SECRET: str = os.getenv("LINKEDIN_CLIENT_SECRET", "")
    REDIRECT_URI: str = os.getenv("LINKEDIN_REDIRECT_URI", "http://localhost:8000/callback")
    SCOPES: list[str] = os.getenv("LINKEDIN_SCOPES", "openid,profile,email,w_member_social,r_member_social").split(",")

    # File paths
    TOKEN_FILE: Path = _PROJECT_ROOT / "tokens.json"
    DB_FILE: Path = _PROJECT_ROOT / "tracker.db"

    # API base URLs
    AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
    TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
    API_BASE = "https://api.linkedin.com"

    @classmethod
    def validate(cls) -> list[str]:
        """Return list of missing required config values."""
        issues = []
        if not cls.CLIENT_ID or cls.CLIENT_ID == "your_client_id_here":
            issues.append("LINKEDIN_CLIENT_ID is not set in .env")
        if not cls.CLIENT_SECRET or cls.CLIENT_SECRET == "your_client_secret_here":
            issues.append("LINKEDIN_CLIENT_SECRET is not set in .env")
        return issues