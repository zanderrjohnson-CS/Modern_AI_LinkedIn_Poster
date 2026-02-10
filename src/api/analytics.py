"""
LinkedIn Analytics collection module.

Attempts to fetch analytics from the memberCreatorPostAnalytics API.
Falls back gracefully if the scope isn't available, allowing manual metric entry.

Available metric types from the API:
- IMPRESSION
- REACTION
- COMMENT
- SHARE
- ENGAGEMENT  (total engagement)
- CLICK
- VIDEO_VIEW  (for video posts)
"""

from datetime import datetime, timedelta
from dataclasses import dataclass

import requests

from src.config import Config
from src.api.auth import get_valid_token

API_VERSION = "202504"

METRIC_TYPES = ["IMPRESSION", "REACTION", "COMMENT", "SHARE", "CLICK"]


def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": API_VERSION,
    }


@dataclass
class PostMetrics:
    """Metrics for a single post."""
    impressions: int = 0
    reactions: int = 0
    comments: int = 0
    shares: int = 0
    clicks: int = 0
    source: str = "manual"  # "api" or "manual"


def fetch_post_analytics(post_urn: str, days_back: int = 30) -> PostMetrics | None:
    """
    Try to fetch analytics for a single post from LinkedIn's API.

    Args:
        post_urn: The post URN (e.g., "urn:li:share:12345")
        days_back: How many days of data to aggregate.

    Returns:
        PostMetrics if successful, None if API access is denied.
    """
    access_token, _ = get_valid_token()

    end = datetime.now()
    start = end - timedelta(days=days_back)

    metrics = PostMetrics(source="api")

    for metric_type in METRIC_TYPES:
        params = {
            "q": "entity",
            "entity": f"(share:{post_urn})",
            "queryType": metric_type,
            "aggregation": "DAILY",
            "dateRange": (
                f"(start:(day:{start.day},month:{start.month},year:{start.year}),"
                f"end:(day:{end.day},month:{end.month},year:{end.year}))"
            ),
        }

        try:
            resp = requests.get(
                f"{Config.API_BASE}/rest/memberCreatorPostAnalytics",
                headers=_headers(access_token),
                params=params,
            )

            if resp.status_code == 403:
                return None  # No API access — signal caller to use manual entry
            if resp.status_code == 401:
                return None

            resp.raise_for_status()
            data = resp.json()

            total = sum(elem.get("count", 0) for elem in data.get("elements", []))

            if metric_type == "IMPRESSION":
                metrics.impressions = total
            elif metric_type == "REACTION":
                metrics.reactions = total
            elif metric_type == "COMMENT":
                metrics.comments = total
            elif metric_type == "SHARE":
                metrics.shares = total
            elif metric_type == "CLICK":
                metrics.clicks = total

        except requests.HTTPError as e:
            print(f"  Warning: Failed to fetch {metric_type} for {post_urn}: {e}")
            continue

    return metrics


def check_analytics_access() -> bool:
    """
    Quick check to see if we have access to the analytics API.

    Returns True if the API is accessible, False otherwise.
    """
    access_token, _ = get_valid_token()

    end = datetime.now()
    start = end - timedelta(days=7)

    params = {
        "q": "me",
        "queryType": "IMPRESSION",
        "aggregation": "DAILY",
        "dateRange": (
            f"(start:(day:{start.day},month:{start.month},year:{start.year}),"
            f"end:(day:{end.day},month:{end.month},year:{end.year}))"
        ),
    }

    try:
        resp = requests.get(
            f"{Config.API_BASE}/rest/memberCreatorPostAnalytics",
            headers=_headers(access_token),
            params=params,
        )
        return resp.status_code == 200
    except Exception:
        return False