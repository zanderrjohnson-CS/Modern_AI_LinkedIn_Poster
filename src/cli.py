"""
CLI for LinkedIn Channel Tracker.

Usage:
    python -m src.cli auth                          # Authenticate with LinkedIn
    python -m src.cli post --category "Topic" --text "..."   # Create a text post
    python -m src.cli post --category "Topic" --text "..." --url "https://..."  # Article post
    python -m src.cli track --urn <URL_OR_URN> --category "Topic"  # Track existing post
    python -m src.cli posts                         # List tracked posts
    python -m src.cli categories                    # List categories with counts
    python -m src.cli log-metrics --urn <URN> --impressions 500  # Log metrics manually
    python -m src.cli collect                       # Auto-collect analytics from API
    python -m src.cli stats                         # Category-level performance
    python -m src.cli detail                        # Per-post metrics
    python -m src.cli fetch-posts                   # Pull recent posts from LinkedIn
    python -m src.cli status                        # Check auth status
"""

import argparse
import json
import re
import sys
import time

from src.config import Config
from src.api.auth import authorize, get_valid_token, TokenManager
from src.api.posts import create_text_post, create_article_post, get_my_posts
from src.api.analytics import check_analytics_access, fetch_post_analytics
from src.db.models import (
    save_post, list_posts, list_categories, init_db,
    save_metrics, get_category_stats, get_posts_with_metrics, get_latest_metrics,
)


def cmd_auth(args):
    """Run the OAuth2 authorization flow."""
    try:
        authorize()
    except RuntimeError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
    """Show current authentication status."""
    mgr = TokenManager()
    tokens = mgr.load()

    if not tokens:
        print("Not authenticated. Run: python -m src.cli auth")
        return

    print(f"User:        {tokens.get('user_name', 'Unknown')}")
    print(f"Person URN:  {tokens.get('person_urn', 'Unknown')}")

    expires_at = tokens.get("expires_at", 0)
    remaining = expires_at - time.time()
    if remaining > 0:
        days = int(remaining // 86400)
        hours = int((remaining % 86400) // 3600)
        print(f"Token:       Valid ({days}d {hours}h remaining)")
    else:
        print("Token:       EXPIRED — run: python -m src.cli auth")

    has_refresh = "Yes" if tokens.get("refresh_token") else "No"
    print(f"Refresh:     {has_refresh}")


def cmd_post(args):
    """Create a post and track it locally."""
    if not args.text:
        print("Error: --text is required", file=sys.stderr)
        sys.exit(1)
    if not args.category:
        print("Error: --category is required", file=sys.stderr)
        sys.exit(1)

    try:
        if args.url:
            post_urn = create_article_post(
                text=args.text,
                article_url=args.url,
                title=args.title,
                visibility=args.visibility,
            )
        else:
            post_urn = create_text_post(
                text=args.text,
                visibility=args.visibility,
            )

        # Save to local DB with category
        post_id = save_post(
            linkedin_urn=post_urn,
            category_name=args.category,
            content_preview=args.text,
            article_url=args.url,
            visibility=args.visibility,
        )
        print(f"  Tracked locally (id={post_id}, category='{args.category}')")

    except RuntimeError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        sys.exit(1)


def cmd_track(args):
    """Track an existing LinkedIn post by its URL or URN."""
    init_db()

    # Extract URN from a full LinkedIn URL if needed
    urn = args.urn
    if "linkedin.com" in urn:
        # Format 1: /feed/update/urn:li:activity:12345/
        match = re.search(r'(urn:li:(?:activity|share|ugcPost):\d+)', urn)
        if match:
            urn = match.group(1)
        else:
            # Format 2: /posts/username_slug-activity-7387544980854173696-xxxx
            match = re.search(r'activity-(\d+)', urn)
            if match:
                urn = f"urn:li:activity:{match.group(1)}"
            else:
                print("✗ Couldn't extract a post URN from that URL.", file=sys.stderr)
                print("  Expected formats:", file=sys.stderr)
                print("    https://www.linkedin.com/feed/update/urn:li:activity:12345/", file=sys.stderr)
                print("    https://www.linkedin.com/posts/username_slug-activity-12345-xxxx/", file=sys.stderr)
                sys.exit(1)

    preview = args.text or "(imported post)"

    post_id = save_post(
        linkedin_urn=urn,
        category_name=args.category,
        content_preview=preview,
    )
    print(f"✓ Tracked: {urn}")
    print(f"  Category: {args.category}")
    print(f"  Local ID: {post_id}")

    if args.impressions or args.reactions or args.comments or args.shares or args.clicks:
        save_metrics(
            linkedin_urn=urn,
            impressions=args.impressions or 0,
            reactions=args.reactions or 0,
            comments=args.comments or 0,
            shares=args.shares or 0,
            clicks=args.clicks or 0,
        )
        print(f"  Metrics logged too!")


def cmd_posts(args):
    """List locally tracked posts."""
    init_db()
    posts = list_posts(limit=args.limit)

    if not posts:
        print("No tracked posts yet. Create one with: python -m src.cli post --category '...' --text '...'")
        return

    print(f"\n{'Date':<20} {'Category':<20} {'Content':<50} {'URN'}")
    print("─" * 120)
    for p in posts:
        date = p["posted_at"][:16] if p["posted_at"] else "?"
        cat = p["category"] or "uncategorized"
        preview = (p["content_preview"] or "")[:47]
        if len(p.get("content_preview", "") or "") > 47:
            preview += "..."
        urn = p["linkedin_urn"]
        print(f"{date:<20} {cat:<20} {preview:<50} {urn}")
    print()


def cmd_categories(args):
    """List categories with post counts."""
    init_db()
    cats = list_categories()

    if not cats:
        print("No categories yet. Create a post with --category to get started.")
        return

    print(f"\n{'Category':<30} {'Posts':>6}")
    print("─" * 40)
    for c in cats:
        print(f"{c['name']:<30} {c['post_count']:>6}")
    print()


def cmd_fetch_posts(args):
    """Fetch recent posts from LinkedIn (useful for seeing what's on your profile)."""
    try:
        posts = get_my_posts(count=args.limit)
        if not posts:
            print("No posts found on your LinkedIn profile.")
            return

        print(f"\nYour {len(posts)} most recent LinkedIn posts:\n")
        for p in posts:
            print(f"  {p}")
        print()
    except Exception as e:
        print(f"\n✗ {e}", file=sys.stderr)
        sys.exit(1)


def cmd_log_metrics(args):
    """Manually log metrics for a tracked post."""
    init_db()

    if not args.urn and not args.id:
        print("Error: provide either --id or --urn", file=sys.stderr)
        sys.exit(1)

    snapshot_id = save_metrics(
        linkedin_urn=args.urn,
        post_id=args.id,
        impressions=args.impressions or 0,
        reactions=args.reactions or 0,
        comments=args.comments or 0,
        shares=args.shares or 0,
        clicks=args.clicks or 0,
    )
    label = f"id={args.id}" if args.id else args.urn
    if snapshot_id:
        print(f"✓ Metrics saved for {label} (snapshot #{snapshot_id})")
    else:
        print(f"✗ Post not found: {label}", file=sys.stderr)
        print("  Use 'python -m src.cli posts' to see tracked posts.", file=sys.stderr)
        sys.exit(1)


def cmd_collect(args):
    """Try to auto-collect analytics from LinkedIn API for all tracked posts."""
    init_db()

    print("Checking LinkedIn analytics API access...")
    has_api = check_analytics_access()

    if not has_api:
        print("\n⚠  No API access to memberCreatorPostAnalytics.")
        print("   This requires the Community Management API product (r_member_social scope).")
        print("\n   Options:")
        print("   1. Log metrics manually:  python -m src.cli log-metrics --urn <URN> --impressions 500 --reactions 20")
        print("   2. Apply for Community Management API at developer.linkedin.com (requires a registered business)")
        print("   3. Use a partner tool like Buffer or Metricool for analytics, and log key numbers here")
        return

    print("✓ API access confirmed! Fetching analytics...\n")

    posts = list_posts(limit=100)
    if not posts:
        print("No tracked posts to collect analytics for.")
        return

    success = 0
    for p in posts:
        urn = p["linkedin_urn"]
        print(f"  Fetching: {urn[:50]}...", end=" ")
        metrics = fetch_post_analytics(urn, days_back=args.days)
        if metrics:
            save_metrics(
                linkedin_urn=urn,
                impressions=metrics.impressions,
                reactions=metrics.reactions,
                comments=metrics.comments,
                shares=metrics.shares,
                clicks=metrics.clicks,
            )
            print(f"✓ ({metrics.impressions} imp, {metrics.reactions} react)")
            success += 1
        else:
            print("✗ failed")

    print(f"\nCollected analytics for {success}/{len(posts)} posts.")


def cmd_stats(args):
    """Show performance stats aggregated by category."""
    init_db()
    stats = get_category_stats()

    if not stats:
        print("No data yet. Post some content and log metrics first.")
        print("  Post:  python -m src.cli post -c 'Topic' -t 'Your text'")
        print("  Log:   python -m src.cli log-metrics --urn <URN> --impressions 500 --reactions 20")
        return

    has_data = any(s["total_impressions"] > 0 or s["total_reactions"] > 0 for s in stats)
    if not has_data:
        print("Categories exist but no metrics logged yet.")
        print("Log metrics: python -m src.cli log-metrics --urn <URN> --impressions 500 --reactions 20")
        return

    print(f"\n{'Category':<22} {'Posts':>5} {'Impr':>8} {'React':>7} {'Cmts':>6} {'Shares':>7} {'Eng%':>7}")
    print("─" * 68)
    for s in stats:
        print(
            f"{s['category']:<22} "
            f"{s['post_count']:>5} "
            f"{s['total_impressions']:>8} "
            f"{s['total_reactions']:>7} "
            f"{s['total_comments']:>6} "
            f"{s['total_shares']:>7} "
            f"{s['engagement_rate']:>6.1f}%"
        )

    print(f"\n{'':>22} {'Avg/post:'}")
    print(f"{'Category':<22} {'Posts':>5} {'Impr':>8} {'React':>7} {'Cmts':>6}")
    print("─" * 48)
    for s in stats:
        if s['post_count'] > 0:
            print(
                f"{s['category']:<22} "
                f"{s['post_count']:>5} "
                f"{s['avg_impressions']:>8.0f} "
                f"{s['avg_reactions']:>7.0f} "
                f"{s['avg_comments']:>6.0f}"
            )
    print()


def cmd_detail(args):
    """Show individual post metrics, optionally filtered by category."""
    init_db()
    posts = get_posts_with_metrics(category_name=args.category, limit=args.limit)

    if not posts:
        print("No posts found." + (f" (category filter: '{args.category}')" if args.category else ""))
        return

    print(f"\n{'Date':<12} {'Category':<18} {'Impr':>7} {'React':>6} {'Cmts':>5} {'Content'}")
    print("─" * 85)
    for p in posts:
        date = (p["posted_at"] or "")[:10]
        cat = (p["category"] or "?")[:16]
        imp = p["impressions"] if p["impressions"] is not None else "-"
        react = p["reactions"] if p["reactions"] is not None else "-"
        cmts = p["comments"] if p["comments"] is not None else "-"
        preview = (p["content_preview"] or "")[:30]
        print(f"{date:<12} {cat:<18} {str(imp):>7} {str(react):>6} {str(cmts):>5} {preview}")
    print()


def main():
    parser = argparse.ArgumentParser(
        prog="linkedin-tracker",
        description="LinkedIn Channel Tracker — post & track content performance by category",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # auth
    subparsers.add_parser("auth", help="Authenticate with LinkedIn OAuth2")

    # status
    subparsers.add_parser("status", help="Check authentication status")

    # post
    post_parser = subparsers.add_parser("post", help="Create and track a LinkedIn post")
    post_parser.add_argument("--text", "-t", required=True, help="Post text content")
    post_parser.add_argument("--category", "-c", required=True, help="Content category/channel")
    post_parser.add_argument("--url", "-u", help="Article URL to attach")
    post_parser.add_argument("--title", help="Title override for article posts")
    post_parser.add_argument("--visibility", "-v", default="PUBLIC",
                            choices=["PUBLIC", "CONNECTIONS", "LOGGED_IN"],
                            help="Post visibility (default: PUBLIC)")

    # track (import existing posts)
    track_parser = subparsers.add_parser("track", help="Track an existing LinkedIn post by URL or URN")
    track_parser.add_argument("--urn", "-u", required=True,
                             help="Post URL or URN (e.g. https://linkedin.com/feed/update/urn:li:activity:123/)")
    track_parser.add_argument("--category", "-c", required=True, help="Content category")
    track_parser.add_argument("--text", "-t", help="Short description of the post content")
    track_parser.add_argument("--impressions", type=int, default=0, help="Impressions (optional)")
    track_parser.add_argument("--reactions", type=int, default=0, help="Reactions (optional)")
    track_parser.add_argument("--comments", type=int, default=0, help="Comments (optional)")
    track_parser.add_argument("--shares", type=int, default=0, help="Shares (optional)")
    track_parser.add_argument("--clicks", type=int, default=0, help="Clicks (optional)")

    # posts
    posts_parser = subparsers.add_parser("posts", help="List tracked posts")
    posts_parser.add_argument("--limit", "-n", type=int, default=20, help="Number of posts")

    # categories
    subparsers.add_parser("categories", help="List categories with post counts")

    # fetch-posts
    fetch_parser = subparsers.add_parser("fetch-posts", help="Fetch recent posts from LinkedIn API")
    fetch_parser.add_argument("--limit", "-n", type=int, default=10, help="Number of posts")

    # log-metrics (manual entry)
    log_parser = subparsers.add_parser("log-metrics", help="Manually log metrics for a tracked post")
    log_parser.add_argument("--id", type=int, help="Local post ID (from 'posts' command)")
    log_parser.add_argument("--urn", help="LinkedIn post URN (alternative to --id)")
    log_parser.add_argument("--impressions", type=int, default=0, help="Number of impressions")
    log_parser.add_argument("--reactions", type=int, default=0, help="Number of reactions")
    log_parser.add_argument("--comments", type=int, default=0, help="Number of comments")
    log_parser.add_argument("--shares", type=int, default=0, help="Number of shares/reposts")
    log_parser.add_argument("--clicks", type=int, default=0, help="Number of clicks")

    # collect (auto-fetch from API)
    collect_parser = subparsers.add_parser("collect", help="Auto-collect analytics from LinkedIn API")
    collect_parser.add_argument("--days", "-d", type=int, default=30, help="Days of data to fetch")

    # stats (category-level summary)
    subparsers.add_parser("stats", help="Show performance stats by category")

    # detail (post-level metrics)
    detail_parser = subparsers.add_parser("detail", help="Show per-post metrics")
    detail_parser.add_argument("--category", "-c", help="Filter by category name")
    detail_parser.add_argument("--limit", "-n", type=int, default=20, help="Number of posts")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "auth": cmd_auth,
        "status": cmd_status,
        "post": cmd_post,
        "track": cmd_track,
        "posts": cmd_posts,
        "categories": cmd_categories,
        "fetch-posts": cmd_fetch_posts,
        "log-metrics": cmd_log_metrics,
        "collect": cmd_collect,
        "stats": cmd_stats,
        "detail": cmd_detail,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()