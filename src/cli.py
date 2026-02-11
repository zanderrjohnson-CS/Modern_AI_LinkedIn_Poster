"""
CLI for LinkedIn Channel Tracker.

Usage:
    python -m src.cli auth                          # Authenticate with LinkedIn
    python -m src.cli status                        # Check auth status

    # --- Posting ---
    python -m src.cli post -c "Topic" -t "..."      # Post immediately
    python -m src.cli track -u <URL> -c "Topic"     # Track an existing post

    # --- AI Drafting ---
    python -m src.cli draft -c "Topic" --topic "..."           # Generate a post with AI
    python -m src.cli refine --feedback "make it shorter"      # Refine the last draft

    # --- Scheduling ---
    python -m src.cli schedule -c "Topic" -t "..." --at "2026-02-11 09:00"  # Schedule a post
    python -m src.cli queue                         # View scheduled posts
    python -m src.cli publish-due                   # Publish all due posts (run via cron)

    # --- Analytics ---
    python -m src.cli posts                         # List tracked posts
    python -m src.cli categories                    # List categories
    python -m src.cli log-metrics --id 1 --impressions 500  # Log metrics
    python -m src.cli stats                         # Category performance
    python -m src.cli detail                        # Per-post metrics
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from src.config import Config
from src.api.auth import authorize, get_valid_token, TokenManager
from src.api.posts import create_text_post, create_article_post, get_my_posts
from src.api.analytics import check_analytics_access, fetch_post_analytics
from src.api.drafts import draft_post, refine_post, check_gemini_access
from src.api.scheduler import publish_due_posts
from src.db.models import (
    save_post, list_posts, list_categories, init_db,
    save_metrics, get_category_stats, get_posts_with_metrics, get_latest_metrics,
    schedule_post, list_scheduled, delete_scheduled,
)

_DRAFT_FILE = Config.DB_FILE.parent / ".last_draft.json"


def _save_draft(text, category, topic):
    with open(_DRAFT_FILE, "w") as f:
        json.dump({"text": text, "category": category, "topic": topic}, f)


def _load_draft():
    if _DRAFT_FILE.exists():
        with open(_DRAFT_FILE) as f:
            return json.load(f)
    return None


def cmd_auth(args):
    try:
        authorize()
    except RuntimeError as e:
        print(f"\n✗ {e}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args):
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
    print(f"Gemini AI:   {'Configured' if check_gemini_access() else 'Not configured (add GEMINI_API_KEY to .env)'}")
    init_db()
    pending = list_scheduled(include_done=False)
    if pending:
        print(f"Scheduled:   {len(pending)} post(s) pending")


def cmd_post(args):
    if not args.text:
        print("Error: --text is required", file=sys.stderr); sys.exit(1)
    if not args.category:
        print("Error: --category is required", file=sys.stderr); sys.exit(1)
    try:
        if args.url:
            post_urn = create_article_post(text=args.text, article_url=args.url, title=args.title, visibility=args.visibility)
        else:
            post_urn = create_text_post(text=args.text, visibility=args.visibility)
        post_id = save_post(linkedin_urn=post_urn, category_name=args.category, content_preview=args.text, article_url=args.url, visibility=args.visibility)
        print(f"  Tracked locally (id={post_id}, category='{args.category}')")
    except RuntimeError as e:
        print(f"\n✗ {e}", file=sys.stderr); sys.exit(1)


def cmd_track(args):
    init_db()
    urn = args.urn
    if "linkedin.com" in urn:
        match = re.search(r'(urn:li:(?:activity|share|ugcPost):\d+)', urn)
        if match:
            urn = match.group(1)
        else:
            match = re.search(r'activity-(\d+)', urn)
            if match:
                urn = f"urn:li:activity:{match.group(1)}"
            else:
                print("✗ Couldn't extract a post URN from that URL.", file=sys.stderr)
                sys.exit(1)
    preview = args.text or "(imported post)"
    post_id = save_post(linkedin_urn=urn, category_name=args.category, content_preview=preview)
    print(f"✓ Tracked: {urn}")
    print(f"  Category: {args.category}")
    print(f"  Local ID: {post_id}")
    if args.impressions or args.reactions or args.comments or args.shares or args.clicks:
        save_metrics(linkedin_urn=urn, impressions=args.impressions or 0, reactions=args.reactions or 0, comments=args.comments or 0, shares=args.shares or 0, clicks=args.clicks or 0)
        print(f"  Metrics logged too!")


def cmd_draft(args):
    if not check_gemini_access():
        print("✗ GEMINI_API_KEY not set. Add to .env:", file=sys.stderr)
        print("  GEMINI_API_KEY=your_key_here", file=sys.stderr)
        print("  Get one at: https://aistudio.google.com/apikey", file=sys.stderr)
        sys.exit(1)

    prompt = args.prompt
    print(f"Generating post from prompt: {prompt}")
    if args.category:
        print(f"Category: {args.category}")
    print()

    try:
        text = draft_post(topic=prompt, category=args.category, tone=args.tone, max_words=args.words)
    except RuntimeError as e:
        print(f"✗ {e}", file=sys.stderr); sys.exit(1)

    print("─" * 60)
    print(text)
    print("─" * 60)
    print()

    _save_draft(text, args.category or "uncategorized", prompt)

    if args.post:
        # Post immediately
        category = args.category or "uncategorized"
        try:
            post_urn = create_text_post(text=text)
            post_id = save_post(linkedin_urn=post_urn, category_name=category, content_preview=text)
            print(f"✓ Posted to LinkedIn! URN: {post_urn}")
            print(f"  Tracked locally (id={post_id}, category='{category}')")
        except RuntimeError as e:
            print(f"✗ {e}", file=sys.stderr); sys.exit(1)
    elif args.schedule:
        category = args.category or "uncategorized"
        try:
            scheduled_time = _parse_datetime(args.schedule)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr); sys.exit(1)
        sid = schedule_post(content=text, category_name=category, scheduled_for=scheduled_time.isoformat())
        print(f"✓ Scheduled as #{sid} for {scheduled_time.strftime('%Y-%m-%d %H:%M')}")
    else:
        print("Next steps:")
        print("  Refine:    python -m src.cli refine -f 'make it more personal'")
        print("  Post now:  python -m src.cli draft-post")
        print("  Schedule:  python -m src.cli draft-post --at '2026-02-11 09:00'")


def cmd_refine(args):
    draft = _load_draft()
    if not draft:
        print("✗ No draft to refine. Run 'draft' first.", file=sys.stderr); sys.exit(1)
    print(f"Refining draft with feedback: {args.feedback}\n")
    try:
        text = refine_post(original=draft["text"], instruction=args.feedback)
    except RuntimeError as e:
        print(f"✗ {e}", file=sys.stderr); sys.exit(1)
    print("─" * 60)
    print(text)
    print("─" * 60)
    print()
    _save_draft(text, draft["category"], draft["topic"])
    print("Draft updated. Refine again, post, or schedule it.")


def cmd_draft_post(args):
    draft = _load_draft()
    if not draft:
        print("✗ No draft found. Run 'draft' first.", file=sys.stderr); sys.exit(1)
    category = args.category or draft["category"]
    if args.at:
        try:
            scheduled_time = _parse_datetime(args.at)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr); sys.exit(1)
        sid = schedule_post(content=draft["text"], category_name=category, scheduled_for=scheduled_time.isoformat())
        print(f"✓ Draft scheduled as #{sid}")
        print(f"  Category: {category}")
        print(f"  Publishes: {scheduled_time.strftime('%Y-%m-%d %H:%M')}")
    else:
        try:
            post_urn = create_text_post(text=draft["text"])
            post_id = save_post(linkedin_urn=post_urn, category_name=category, content_preview=draft["text"])
            print(f"✓ Draft posted! URN: {post_urn}")
            print(f"  Tracked locally (id={post_id}, category='{category}')")
        except RuntimeError as e:
            print(f"✗ {e}", file=sys.stderr); sys.exit(1)
    _DRAFT_FILE.unlink(missing_ok=True)


def _parse_datetime(s):
    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d %H:%M"]:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now().year)
            return dt
        except ValueError:
            continue
    raise ValueError(f"Couldn't parse '{s}'. Use format: YYYY-MM-DD HH:MM")


def cmd_schedule(args):
    init_db()
    try:
        scheduled_time = _parse_datetime(args.at)
    except ValueError as e:
        print(f"✗ {e}", file=sys.stderr); sys.exit(1)
    if scheduled_time <= datetime.now():
        print("⚠  That time is in the past. Post will publish on next 'publish-due' run.")
    sid = schedule_post(content=args.text, category_name=args.category, scheduled_for=scheduled_time.isoformat(), article_url=args.url, visibility=args.visibility)
    print(f"✓ Scheduled post #{sid}")
    print(f"  Category:  {args.category}")
    print(f"  Publishes: {scheduled_time.strftime('%Y-%m-%d %H:%M')}")
    print(f"  Content:   {args.text[:80]}{'...' if len(args.text) > 80 else ''}")
    print(f"\n  Set up cron to auto-publish:")
    print(f"  */5 * * * * cd {Config.DB_FILE.parent} && python -m src.cli publish-due")


def cmd_queue(args):
    init_db()
    posts = list_scheduled(include_done=args.all)
    if not posts:
        print("No scheduled posts."); return
    print(f"\n{'ID':>4} {'Status':<10} {'Scheduled For':<20} {'Category':<18} {'Content'}")
    print("─" * 90)
    for p in posts:
        status = p["status"]
        if status == "pending": status = "⏳ pending"
        elif status == "published": status = "✓ posted"
        elif status == "failed": status = "✗ failed"
        sched = p["scheduled_for"][:16] if p["scheduled_for"] else "?"
        cat = p["category_name"][:16]
        content = (p["content"] or "")[:35]
        if len(p.get("content", "") or "") > 35: content += "..."
        print(f"{p['id']:>4} {status:<10} {sched:<20} {cat:<18} {content}")
    print()


def cmd_cancel(args):
    init_db()
    if delete_scheduled(args.id):
        print(f"✓ Cancelled scheduled post #{args.id}")
    else:
        print(f"✗ Could not cancel #{args.id}", file=sys.stderr); sys.exit(1)


def cmd_publish_due(args):
    init_db()
    published, failed = publish_due_posts()
    if published == 0 and failed == 0:
        if not args.quiet: print("No posts due for publishing.")
    else:
        print(f"Published: {published}, Failed: {failed}")


def cmd_posts(args):
    init_db()
    posts = list_posts(limit=args.limit)
    if not posts:
        print("No tracked posts yet."); return
    print(f"\n{'ID':>4} {'Date':<18} {'Category':<18} {'Content':<45} {'URN'}")
    print("─" * 120)
    for p in posts:
        date = p["posted_at"][:16] if p["posted_at"] else "?"
        cat = p["category"] or "uncategorized"
        preview = (p["content_preview"] or "")[:42]
        if len(p.get("content_preview", "") or "") > 42: preview += "..."
        print(f"{p['id']:>4} {date:<18} {cat:<18} {preview:<45} {p['linkedin_urn']}")
    print()


def cmd_categories(args):
    init_db()
    cats = list_categories()
    if not cats:
        print("No categories yet."); return
    print(f"\n{'Category':<30} {'Posts':>6}")
    print("─" * 40)
    for c in cats:
        print(f"{c['name']:<30} {c['post_count']:>6}")
    print()


def cmd_fetch_posts(args):
    try:
        posts = get_my_posts(count=args.limit)
        if not posts: print("No posts found."); return
        print(f"\nYour {len(posts)} most recent LinkedIn posts:\n")
        for p in posts: print(f"  {p}")
        print()
    except Exception as e:
        print(f"\n✗ {e}", file=sys.stderr); sys.exit(1)


def cmd_log_metrics(args):
    init_db()
    if not args.urn and not args.id:
        print("Error: provide either --id or --urn", file=sys.stderr); sys.exit(1)
    snapshot_id = save_metrics(linkedin_urn=args.urn, post_id=args.id, impressions=args.impressions or 0, reactions=args.reactions or 0, comments=args.comments or 0, shares=args.shares or 0, clicks=args.clicks or 0)
    label = f"id={args.id}" if args.id else args.urn
    if snapshot_id:
        print(f"✓ Metrics saved for {label} (snapshot #{snapshot_id})")
    else:
        print(f"✗ Post not found: {label}", file=sys.stderr); sys.exit(1)


def cmd_collect(args):
    init_db()
    print("Checking LinkedIn analytics API access...")
    if not check_analytics_access():
        print("\n⚠  No API access. Log metrics manually: python -m src.cli log-metrics --id <ID> --impressions 500"); return
    print("✓ API access confirmed! Fetching...\n")
    posts = list_posts(limit=100)
    if not posts: print("No tracked posts."); return
    success = 0
    for p in posts:
        urn = p["linkedin_urn"]
        print(f"  Fetching: {urn[:50]}...", end=" ")
        metrics = fetch_post_analytics(urn, days_back=args.days)
        if metrics:
            save_metrics(linkedin_urn=urn, impressions=metrics.impressions, reactions=metrics.reactions, comments=metrics.comments, shares=metrics.shares, clicks=metrics.clicks)
            print(f"✓ ({metrics.impressions} imp, {metrics.reactions} react)"); success += 1
        else: print("✗ failed")
    print(f"\nCollected for {success}/{len(posts)} posts.")


def cmd_scrape(args):
    try:
        from src.api.scraper import scrape_all_tracked_posts
    except ImportError:
        print("✗ selenium is not installed. Run:", file=sys.stderr)
        print("  pip install selenium", file=sys.stderr)
        sys.exit(1)

    init_db()
    posts = list_posts(limit=100)
    if not posts:
        print("No tracked posts to scrape. Track some first with 'track' or 'post'."); return

    results = scrape_all_tracked_posts(posts, headless=args.headless)

    if not results:
        print("\nNo stats collected."); return

    saved = 0
    for r in results:
        sid = save_metrics(
            linkedin_urn=r["linkedin_urn"],
            impressions=r["impressions"],
            reactions=r["reactions"],
            comments=r["comments"],
            shares=r["reposts"],
        )
        if sid:
            saved += 1

    print(f"\n✓ Saved metrics for {saved}/{len(results)} posts.")
    print("  Run 'python -m src.cli stats' to see category performance.")


def cmd_stats(args):
    init_db()
    stats = get_category_stats()
    if not stats: print("No data yet."); return
    has_data = any(s["total_impressions"] > 0 or s["total_reactions"] > 0 for s in stats)
    if not has_data: print("No metrics logged yet. Use: python -m src.cli log-metrics --id <ID> --impressions 500"); return
    print(f"\n{'Category':<22} {'Posts':>5} {'Impr':>8} {'React':>7} {'Cmts':>6} {'Shares':>7} {'Eng%':>7}")
    print("─" * 68)
    for s in stats:
        print(f"{s['category']:<22} {s['post_count']:>5} {s['total_impressions']:>8} {s['total_reactions']:>7} {s['total_comments']:>6} {s['total_shares']:>7} {s['engagement_rate']:>6.1f}%")
    print(f"\n{'':>22} {'Avg/post:'}")
    print(f"{'Category':<22} {'Posts':>5} {'Impr':>8} {'React':>7} {'Cmts':>6}")
    print("─" * 48)
    for s in stats:
        if s['post_count'] > 0:
            print(f"{s['category']:<22} {s['post_count']:>5} {s['avg_impressions']:>8.0f} {s['avg_reactions']:>7.0f} {s['avg_comments']:>6.0f}")
    print()


def cmd_detail(args):
    init_db()
    posts = get_posts_with_metrics(category_name=args.category, limit=args.limit)
    if not posts: print("No posts found."); return
    print(f"\n{'Date':<12} {'Category':<18} {'Impr':>7} {'React':>6} {'Cmts':>5} {'Content'}")
    print("─" * 85)
    for p in posts:
        date = (p["posted_at"] or "")[:10]
        cat = (p["category"] or "?")[:16]
        imp = p["impressions"] if p["impressions"] is not None else "-"
        react = p["reactions"] if p["reactions"] is not None else "-"
        cmts = p["comments"] if p["comments"] is not None else "-"
        print(f"{date:<12} {cat:<18} {str(imp):>7} {str(react):>6} {str(cmts):>5} {(p['content_preview'] or '')[:30]}")
    print()


def main():
    parser = argparse.ArgumentParser(prog="linkedin-tracker", description="LinkedIn Channel Tracker — post, draft, schedule & track content by category")
    sub = parser.add_subparsers(dest="command", help="Available commands")

    sub.add_parser("auth", help="Authenticate with LinkedIn OAuth2")
    sub.add_parser("status", help="Check authentication status")

    p = sub.add_parser("post", help="Create and track a LinkedIn post")
    p.add_argument("--text", "-t", required=True); p.add_argument("--category", "-c", required=True)
    p.add_argument("--url", "-u"); p.add_argument("--title"); p.add_argument("--visibility", "-v", default="PUBLIC", choices=["PUBLIC","CONNECTIONS","LOGGED_IN"])

    p = sub.add_parser("track", help="Track an existing LinkedIn post by URL or URN")
    p.add_argument("--urn", "-u", required=True); p.add_argument("--category", "-c", required=True)
    p.add_argument("--text", "-t"); p.add_argument("--impressions", type=int, default=0)
    p.add_argument("--reactions", type=int, default=0); p.add_argument("--comments", type=int, default=0)
    p.add_argument("--shares", type=int, default=0); p.add_argument("--clicks", type=int, default=0)

    p = sub.add_parser("draft", help="Generate a LinkedIn post with AI and optionally publish it")
    p.add_argument("prompt", help="Describe what you want the post to be about")
    p.add_argument("--category", "-c", help="Content category")
    p.add_argument("--tone", help="Tone: professional, casual, storytelling, provocative, educational")
    p.add_argument("--words", type=int, help="Target word count")
    p.add_argument("--post", action="store_true", help="Immediately post to LinkedIn")
    p.add_argument("--schedule", help="Schedule for later: 'YYYY-MM-DD HH:MM'")

    p = sub.add_parser("refine", help="Refine the last AI draft")
    p.add_argument("--feedback", "-f", required=True)

    p = sub.add_parser("draft-post", help="Post or schedule the current draft")
    p.add_argument("--at"); p.add_argument("--category", "-c")

    p = sub.add_parser("schedule", help="Schedule a post for later")
    p.add_argument("--text", "-t", required=True); p.add_argument("--category", "-c", required=True)
    p.add_argument("--at", required=True); p.add_argument("--url", "-u")
    p.add_argument("--visibility", "-v", default="PUBLIC", choices=["PUBLIC","CONNECTIONS","LOGGED_IN"])

    p = sub.add_parser("queue", help="View scheduled posts")
    p.add_argument("--all", "-a", action="store_true")

    p = sub.add_parser("cancel", help="Cancel a scheduled post")
    p.add_argument("--id", type=int, required=True)

    p = sub.add_parser("publish-due", help="Publish all due scheduled posts")
    p.add_argument("--quiet", "-q", action="store_true")

    p = sub.add_parser("posts", help="List tracked posts")
    p.add_argument("--limit", "-n", type=int, default=20)

    sub.add_parser("categories", help="List categories with post counts")

    p = sub.add_parser("fetch-posts", help="Fetch recent posts from LinkedIn API")
    p.add_argument("--limit", "-n", type=int, default=10)

    p = sub.add_parser("log-metrics", help="Manually log metrics for a tracked post")
    p.add_argument("--id", type=int); p.add_argument("--urn")
    p.add_argument("--impressions", type=int, default=0); p.add_argument("--reactions", type=int, default=0)
    p.add_argument("--comments", type=int, default=0); p.add_argument("--shares", type=int, default=0)
    p.add_argument("--clicks", type=int, default=0)

    p = sub.add_parser("collect", help="Auto-collect analytics from LinkedIn API")
    p.add_argument("--days", "-d", type=int, default=30)

    p = sub.add_parser("scrape", help="Scrape stats for all tracked posts via Chrome")
    p.add_argument("--headless", action="store_true", help="Run Chrome without a visible window")

    sub.add_parser("stats", help="Show performance stats by category")

    p = sub.add_parser("detail", help="Show per-post metrics")
    p.add_argument("--category", "-c"); p.add_argument("--limit", "-n", type=int, default=20)

    args = parser.parse_args()
    if not args.command: parser.print_help(); sys.exit(0)

    cmds = {"auth":cmd_auth,"status":cmd_status,"post":cmd_post,"track":cmd_track,"draft":cmd_draft,"refine":cmd_refine,"draft-post":cmd_draft_post,"schedule":cmd_schedule,"queue":cmd_queue,"cancel":cmd_cancel,"publish-due":cmd_publish_due,"posts":cmd_posts,"categories":cmd_categories,"fetch-posts":cmd_fetch_posts,"log-metrics":cmd_log_metrics,"collect":cmd_collect,"scrape":cmd_scrape,"stats":cmd_stats,"detail":cmd_detail}
    cmds[args.command](args)

if __name__ == "__main__":
    main()