"""
Post scheduler — publishes posts when their scheduled time arrives.

Run manually:  python -m src.cli publish-due
Run via cron:  */5 * * * * cd /path/to/project && python -m src.cli publish-due
"""

from src.api.posts import create_text_post, create_article_post
from src.db.models import get_due_posts, mark_published, mark_failed, save_post


def publish_due_posts() -> tuple[int, int]:
    """
    Find and publish all scheduled posts that are due.

    Returns (published_count, failed_count).
    """
    due = get_due_posts()
    published = 0
    failed = 0

    for post in due:
        try:
            if post["article_url"]:
                urn = create_article_post(
                    text=post["content"],
                    article_url=post["article_url"],
                    visibility=post["visibility"],
                )
            else:
                urn = create_text_post(
                    text=post["content"],
                    visibility=post["visibility"],
                )

            # Mark as published in schedule
            mark_published(post["id"], urn)

            # Also track in main posts table
            save_post(
                linkedin_urn=urn,
                category_name=post["category_name"],
                content_preview=post["content"],
                article_url=post["article_url"],
                visibility=post["visibility"],
            )

            published += 1

        except Exception as e:
            mark_failed(post["id"], str(e))
            print(f"  ✗ Failed to publish scheduled post #{post['id']}: {e}")
            failed += 1

    return published, failed