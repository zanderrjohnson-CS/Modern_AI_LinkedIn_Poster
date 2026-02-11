"""
SQLite database for tracking posts and their categories.

This is the local layer that maps LinkedIn post URNs to your custom
content categories/channels so you can aggregate analytics later.
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from contextlib import contextmanager

from src.config import Config


SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    linkedin_urn    TEXT UNIQUE NOT NULL,
    category_id     INTEGER,
    content_preview TEXT,
    article_url     TEXT,
    visibility      TEXT DEFAULT 'PUBLIC',
    posted_at       TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS metrics_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         INTEGER NOT NULL,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    impressions     INTEGER DEFAULT 0,
    reactions       INTEGER DEFAULT 0,
    comments        INTEGER DEFAULT 0,
    shares          INTEGER DEFAULT 0,
    clicks          INTEGER DEFAULT 0,
    profile_views   INTEGER DEFAULT 0,
    follower_gains  INTEGER DEFAULT 0,
    FOREIGN KEY (post_id) REFERENCES posts(id)
);

CREATE TABLE IF NOT EXISTS scheduled_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    content         TEXT NOT NULL,
    category_name   TEXT NOT NULL,
    article_url     TEXT,
    visibility      TEXT DEFAULT 'PUBLIC',
    scheduled_for   TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    linkedin_urn    TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db(db_path: Path = Config.DB_FILE):
    """Create the database and tables if they don't exist."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


@contextmanager
def get_db(db_path: Path = Config.DB_FILE):
    """Context manager for database connections."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_or_create_category(name: str) -> int:
    """Get a category ID by name, creating it if it doesn't exist."""
    with get_db() as conn:
        row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cursor = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        return cursor.lastrowid


def save_post(linkedin_urn: str, category_name: str, content_preview: str,
              article_url: str | None = None, visibility: str = "PUBLIC") -> int:
    """
    Save a post to the local database, linked to a category.

    Returns the local post ID.
    """
    category_id = get_or_create_category(category_name)

    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO posts (linkedin_urn, category_id, content_preview, article_url, visibility, posted_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (linkedin_urn, category_id, content_preview[:200], article_url, visibility,
             datetime.now().isoformat()),
        )
        return cursor.lastrowid


def list_posts(limit: int = 20) -> list[dict]:
    """List recent tracked posts with their categories."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT p.id, p.linkedin_urn, c.name as category, p.content_preview,
                      p.posted_at, p.article_url
               FROM posts p
               LEFT JOIN categories c ON p.category_id = c.id
               ORDER BY p.posted_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_categories() -> list[dict]:
    """List all categories with post counts."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.name, COUNT(p.id) as post_count, c.created_at
               FROM categories c
               LEFT JOIN posts p ON c.id = p.category_id
               GROUP BY c.id
               ORDER BY post_count DESC""",
        ).fetchall()
        return [dict(r) for r in rows]


def save_metrics(linkedin_urn: str = None, post_id: int = None,
                 impressions: int = 0, reactions: int = 0,
                 comments: int = 0, shares: int = 0, clicks: int = 0,
                 profile_views: int = 0, follower_gains: int = 0) -> int | None:
    """
    Save a metrics snapshot for a tracked post.

    Identify the post by either linkedin_urn or local post_id.
    Returns the snapshot ID, or None if the post isn't found.
    """
    with get_db() as conn:
        if post_id:
            row = conn.execute(
                "SELECT id FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
        elif linkedin_urn:
            row = conn.execute(
                "SELECT id FROM posts WHERE linkedin_urn = ?", (linkedin_urn,)
            ).fetchone()
        else:
            return None

        if not row:
            return None

        cursor = conn.execute(
            """INSERT INTO metrics_snapshots
               (post_id, impressions, reactions, comments, shares, clicks,
                profile_views, follower_gains)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (row["id"], impressions, reactions, comments, shares, clicks,
             profile_views, follower_gains),
        )
        return cursor.lastrowid


def get_latest_metrics(linkedin_urn: str) -> dict | None:
    """Get the most recent metrics snapshot for a post."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT ms.* FROM metrics_snapshots ms
               JOIN posts p ON ms.post_id = p.id
               WHERE p.linkedin_urn = ?
               ORDER BY ms.fetched_at DESC LIMIT 1""",
            (linkedin_urn,),
        ).fetchone()
        return dict(row) if row else None


def get_category_stats() -> list[dict]:
    """
    Get aggregated performance stats per category using the latest
    metrics snapshot for each post.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT
                c.name as category,
                COUNT(DISTINCT p.id) as post_count,
                COALESCE(SUM(latest.impressions), 0) as total_impressions,
                COALESCE(SUM(latest.reactions), 0) as total_reactions,
                COALESCE(SUM(latest.comments), 0) as total_comments,
                COALESCE(SUM(latest.shares), 0) as total_shares,
                COALESCE(SUM(latest.clicks), 0) as total_clicks,
                CASE WHEN COUNT(DISTINCT p.id) > 0
                     THEN ROUND(CAST(COALESCE(SUM(latest.impressions), 0) AS FLOAT) / COUNT(DISTINCT p.id), 1)
                     ELSE 0 END as avg_impressions,
                CASE WHEN COUNT(DISTINCT p.id) > 0
                     THEN ROUND(CAST(COALESCE(SUM(latest.reactions), 0) AS FLOAT) / COUNT(DISTINCT p.id), 1)
                     ELSE 0 END as avg_reactions,
                CASE WHEN COUNT(DISTINCT p.id) > 0
                     THEN ROUND(CAST(COALESCE(SUM(latest.comments), 0) AS FLOAT) / COUNT(DISTINCT p.id), 1)
                     ELSE 0 END as avg_comments,
                CASE WHEN COALESCE(SUM(latest.impressions), 0) > 0
                     THEN ROUND(
                        CAST(COALESCE(SUM(latest.reactions), 0) + COALESCE(SUM(latest.comments), 0) + COALESCE(SUM(latest.shares), 0) AS FLOAT)
                        / COALESCE(SUM(latest.impressions), 0) * 100, 2)
                     ELSE 0 END as engagement_rate
               FROM categories c
               LEFT JOIN posts p ON c.id = p.category_id
               LEFT JOIN (
                   SELECT ms1.*
                   FROM metrics_snapshots ms1
                   INNER JOIN (
                       SELECT post_id, MAX(fetched_at) as max_fetched
                       FROM metrics_snapshots
                       GROUP BY post_id
                   ) ms2 ON ms1.post_id = ms2.post_id AND ms1.fetched_at = ms2.max_fetched
               ) latest ON p.id = latest.post_id
               GROUP BY c.id
               ORDER BY total_impressions DESC""",
        ).fetchall()
        return [dict(r) for r in rows]


def get_posts_with_metrics(category_name: str | None = None, limit: int = 20) -> list[dict]:
    """
    Get posts with their latest metrics, optionally filtered by category.
    """
    with get_db() as conn:
        query = """
            SELECT p.id, p.linkedin_urn, c.name as category, p.content_preview,
                   p.posted_at, p.article_url,
                   latest.impressions, latest.reactions, latest.comments,
                   latest.shares, latest.clicks, latest.fetched_at as metrics_updated
            FROM posts p
            LEFT JOIN categories c ON p.category_id = c.id
            LEFT JOIN (
                SELECT ms1.*
                FROM metrics_snapshots ms1
                INNER JOIN (
                    SELECT post_id, MAX(fetched_at) as max_fetched
                    FROM metrics_snapshots
                    GROUP BY post_id
                ) ms2 ON ms1.post_id = ms2.post_id AND ms1.fetched_at = ms2.max_fetched
            ) latest ON p.id = latest.post_id
        """
        params = []
        if category_name:
            query += " WHERE c.name = ?"
            params.append(category_name)
        query += " ORDER BY p.posted_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


# --- Scheduling ---

def schedule_post(content: str, category_name: str, scheduled_for: str,
                  article_url: str | None = None, visibility: str = "PUBLIC") -> int:
    """
    Save a post to be published later.

    Args:
        scheduled_for: ISO format datetime string (e.g., "2026-02-11T09:00:00")

    Returns the scheduled post ID.
    """
    with get_db() as conn:
        cursor = conn.execute(
            """INSERT INTO scheduled_posts
               (content, category_name, article_url, visibility, scheduled_for)
               VALUES (?, ?, ?, ?, ?)""",
            (content, category_name, article_url, visibility, scheduled_for),
        )
        return cursor.lastrowid


def get_due_posts() -> list[dict]:
    """Get all pending scheduled posts that are due for publishing."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM scheduled_posts
               WHERE status = 'pending' AND scheduled_for <= datetime('now')
               ORDER BY scheduled_for ASC""",
        ).fetchall()
        return [dict(r) for r in rows]


def mark_published(scheduled_id: int, linkedin_urn: str):
    """Mark a scheduled post as published."""
    with get_db() as conn:
        conn.execute(
            "UPDATE scheduled_posts SET status = 'published', linkedin_urn = ? WHERE id = ?",
            (linkedin_urn, scheduled_id),
        )


def mark_failed(scheduled_id: int, error: str):
    """Mark a scheduled post as failed."""
    with get_db() as conn:
        conn.execute(
            "UPDATE scheduled_posts SET status = 'failed', error_message = ? WHERE id = ?",
            (error, scheduled_id),
        )


def list_scheduled(include_done: bool = False) -> list[dict]:
    """List scheduled posts."""
    with get_db() as conn:
        if include_done:
            rows = conn.execute(
                "SELECT * FROM scheduled_posts ORDER BY scheduled_for ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM scheduled_posts WHERE status = 'pending' ORDER BY scheduled_for ASC"
            ).fetchall()
        return [dict(r) for r in rows]


def delete_scheduled(scheduled_id: int) -> bool:
    """Delete a pending scheduled post."""
    with get_db() as conn:
        cursor = conn.execute(
            "DELETE FROM scheduled_posts WHERE id = ? AND status = 'pending'",
            (scheduled_id,),
        )
        return cursor.rowcount > 0