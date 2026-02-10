"""
LinkedIn Posts API wrapper.

Handles creating and retrieving posts via the REST API.
Uses the newer /rest/posts endpoint (not the deprecated /v2/ugcPosts).
"""

import requests
from dataclasses import dataclass
from datetime import datetime

from src.config import Config
from src.api.auth import get_valid_token


# Use a recent API version
API_VERSION = "202504"

# Common headers for all LinkedIn REST API calls
def _headers(access_token: str) -> dict:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": API_VERSION,
    }


@dataclass
class LinkedInPost:
    """Represents a LinkedIn post (created or retrieved)."""
    post_urn: str
    author_urn: str
    text: str
    visibility: str
    published_at: datetime | None = None
    category: str | None = None  # Local-only field for channel tracking

    def __str__(self):
        date_str = self.published_at.strftime("%Y-%m-%d %H:%M") if self.published_at else "unknown date"
        cat = f" [{self.category}]" if self.category else ""
        preview = self.text[:80] + "..." if len(self.text) > 80 else self.text
        return f"{date_str}{cat}  {preview}  ({self.post_urn})"


def create_text_post(text: str, visibility: str = "PUBLIC") -> str:
    """
    Create a simple text post on the authenticated user's profile.

    Args:
        text: The post content.
        visibility: "PUBLIC", "CONNECTIONS", or "LOGGED_IN" (all LinkedIn members).

    Returns:
        The post URN (e.g., "urn:li:share:1234567890").
    """
    access_token, person_urn = get_valid_token()

    payload = {
        "author": person_urn,
        "commentary": text,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(
        f"{Config.API_BASE}/rest/posts",
        headers=_headers(access_token),
        json=payload,
    )

    if resp.status_code == 201:
        post_urn = resp.headers.get("x-restli-id", "unknown")
        print(f"✓ Post published! URN: {post_urn}")
        return post_urn
    else:
        raise RuntimeError(
            f"Failed to create post (HTTP {resp.status_code}):\n{resp.text}"
        )


def create_article_post(text: str, article_url: str, title: str | None = None, visibility: str = "PUBLIC") -> str:
    """
    Create a post with a link/article attachment.

    LinkedIn will auto-generate a link preview from the URL's Open Graph meta tags.

    Args:
        text: Commentary text above the link preview.
        article_url: URL to share.
        title: Optional title override (otherwise pulled from OG tags).
        visibility: Post visibility setting.

    Returns:
        The post URN.
    """
    access_token, person_urn = get_valid_token()

    article_content = {
        "article": {
            "source": article_url,
        }
    }
    if title:
        article_content["article"]["title"] = title

    payload = {
        "author": person_urn,
        "commentary": text,
        "visibility": visibility,
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": article_content,
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(
        f"{Config.API_BASE}/rest/posts",
        headers=_headers(access_token),
        json=payload,
    )

    if resp.status_code == 201:
        post_urn = resp.headers.get("x-restli-id", "unknown")
        print(f"✓ Article post published! URN: {post_urn}")
        return post_urn
    else:
        raise RuntimeError(
            f"Failed to create article post (HTTP {resp.status_code}):\n{resp.text}"
        )


def get_my_posts(count: int = 10) -> list[LinkedInPost]:
    """
    Retrieve the authenticated user's recent posts.

    Args:
        count: Number of posts to retrieve (max ~100 per page).

    Returns:
        List of LinkedInPost objects sorted by most recent first.
    """
    access_token, person_urn = get_valid_token()

    resp = requests.get(
        f"{Config.API_BASE}/rest/posts",
        headers={
            **_headers(access_token),
            "X-RestLi-Method": "FINDER",
        },
        params={
            "author": person_urn,
            "q": "author",
            "count": count,
            "sortBy": "LAST_MODIFIED",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    posts = []
    for elem in data.get("elements", []):
        published_ms = elem.get("publishedAt") or elem.get("createdAt")
        published_at = datetime.fromtimestamp(published_ms / 1000) if published_ms else None

        posts.append(LinkedInPost(
            post_urn=elem.get("id", "unknown"),
            author_urn=elem.get("author", ""),
            text=elem.get("commentary", ""),
            visibility=elem.get("visibility", ""),
            published_at=published_at,
        ))

    return posts


def delete_post(post_urn: str) -> bool:
    """
    Delete a post by its URN.

    Args:
        post_urn: The full post URN (e.g., "urn:li:share:123456").

    Returns:
        True if deleted successfully.
    """
    access_token, _ = get_valid_token()

    resp = requests.delete(
        f"{Config.API_BASE}/rest/posts/{post_urn}",
        headers=_headers(access_token),
    )

    if resp.status_code == 204:
        print(f"✓ Post deleted: {post_urn}")
        return True
    else:
        raise RuntimeError(
            f"Failed to delete post (HTTP {resp.status_code}):\n{resp.text}"
        )