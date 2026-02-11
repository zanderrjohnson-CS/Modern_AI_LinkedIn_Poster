"""
Selenium-based LinkedIn stats scraper.

Logs into LinkedIn via Chrome, visits each tracked post,
and scrapes impressions, reactions, comments, and reposts.

First run requires manual login (handles 2FA). Cookies are saved
for subsequent runs.
"""

import json
import re
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from src.config import Config

COOKIES_FILE = Config.DB_FILE.parent / ".linkedin_cookies.json"


def _urn_to_url(urn: str) -> str:
    """Convert a LinkedIn URN to a post URL."""
    # urn:li:activity:12345 -> https://www.linkedin.com/feed/update/urn:li:activity:12345/
    return f"https://www.linkedin.com/feed/update/{urn}/"


def _create_driver(headless: bool = False) -> webdriver.Chrome:
    """Create a Chrome WebDriver instance."""
    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=options)
    # Make selenium less detectable
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver


def _save_cookies(driver: webdriver.Chrome):
    """Save browser cookies to disk."""
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)


def _load_cookies(driver: webdriver.Chrome) -> bool:
    """Load saved cookies into the browser. Returns True if cookies were loaded."""
    if not COOKIES_FILE.exists():
        return False

    with open(COOKIES_FILE) as f:
        cookies = json.load(f)

    driver.get("https://www.linkedin.com")
    time.sleep(2)

    for cookie in cookies:
        # Some cookie fields can cause issues
        cookie.pop("sameSite", None)
        cookie.pop("storeId", None)
        try:
            driver.add_cookie(cookie)
        except Exception:
            continue

    return True


def _is_logged_in(driver: webdriver.Chrome) -> bool:
    """Check if we're logged into LinkedIn."""
    driver.get("https://www.linkedin.com/feed/")
    time.sleep(3)
    # If we're redirected to login, we're not logged in
    return "/login" not in driver.current_url and "/authwall" not in driver.current_url


def _manual_login(driver: webdriver.Chrome):
    """Navigate to login page and wait for user to log in manually."""
    driver.get("https://www.linkedin.com/login")
    print("\n" + "─" * 50)
    print("  LinkedIn login required.")
    print("  Please log in to LinkedIn in the Chrome window.")
    print("  (Complete any 2FA/CAPTCHA if prompted)")
    print("─" * 50)

    # Wait for user to complete login (up to 2 minutes)
    for _ in range(120):
        time.sleep(1)
        if "/feed" in driver.current_url:
            print("✓ Login successful!")
            _save_cookies(driver)
            return

    raise RuntimeError("Login timed out after 2 minutes.")


def _ensure_logged_in(driver: webdriver.Chrome):
    """Make sure we're logged in, using cookies or manual login."""
    cookies_loaded = _load_cookies(driver)

    if cookies_loaded:
        if _is_logged_in(driver):
            print("✓ Logged in via saved cookies.")
            return
        else:
            print("Saved cookies expired.")

    _manual_login(driver)


def _parse_number(text: str) -> int:
    """Parse a number from text like '479 impressions' or '1,234'."""
    if not text:
        return 0
    # Remove commas and extract digits
    nums = re.findall(r'[\d,]+', text.replace(",", ""))
    if nums:
        return int(nums[0])
    return 0


def scrape_post_stats(driver: webdriver.Chrome, post_url: str) -> dict | None:
    """
    Scrape stats for a single LinkedIn post.

    Returns dict with impressions, reactions, comments, reposts, or None on failure.
    """
    driver.get(post_url)
    time.sleep(3)  # Let the page load

    stats = {
        "impressions": 0,
        "reactions": 0,
        "comments": 0,
        "reposts": 0,
    }

    try:
        # Wait for post content to load
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.feed-shared-update-v2"))
        )
    except TimeoutException:
        print(f"  ⚠ Page didn't load properly")
        return None

    # --- Impressions ---
    # From: <span class="ca-entry-point__num-views ..."><strong>479 impressions</strong></span>
    try:
        impressions_el = driver.find_element(By.CSS_SELECTOR, "span.ca-entry-point__num-views strong")
        stats["impressions"] = _parse_number(impressions_el.text)
    except NoSuchElementException:
        # Try alternate selector
        try:
            impressions_el = driver.find_element(By.CSS_SELECTOR, "span.ca-entry-point__num-views")
            stats["impressions"] = _parse_number(impressions_el.text)
        except NoSuchElementException:
            pass  # Impressions not visible (might not be our post)

    # --- Reactions (likes, celebrates, etc.) ---
    try:
        reactions_el = driver.find_element(
            By.CSS_SELECTOR, "span.social-details-social-counts__reactions-count"
        )
        stats["reactions"] = _parse_number(reactions_el.text)
    except NoSuchElementException:
        pass

    # --- Comments ---
    try:
        comments_els = driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='comment']")
        for el in comments_els:
            text = el.text.strip()
            if text:
                n = _parse_number(text)
                if n > 0:
                    stats["comments"] = n
                    break
    except NoSuchElementException:
        pass

    # --- Reposts / Shares ---
    try:
        repost_els = driver.find_elements(By.CSS_SELECTOR, "button[aria-label*='repost']")
        for el in repost_els:
            text = el.text.strip()
            if text:
                n = _parse_number(text)
                if n > 0:
                    stats["reposts"] = n
                    break
    except NoSuchElementException:
        pass

    return stats


def scrape_all_tracked_posts(posts: list[dict], headless: bool = False) -> list[dict]:
    """
    Scrape stats for all tracked posts.

    Args:
        posts: List of post dicts from the DB (must have 'linkedin_urn' key).
        headless: Run Chrome without a visible window.

    Returns:
        List of dicts with urn, impressions, reactions, comments, reposts.
    """
    if not posts:
        print("No posts to scrape.")
        return []

    print(f"Starting Chrome to scrape {len(posts)} post(s)...\n")
    driver = _create_driver(headless=headless)

    try:
        _ensure_logged_in(driver)
        print()

        results = []
        for i, post in enumerate(posts, 1):
            urn = post["linkedin_urn"]
            url = _urn_to_url(urn)
            preview = (post.get("content_preview") or urn)[:40]
            print(f"  [{i}/{len(posts)}] {preview}...", end=" ")

            stats = scrape_post_stats(driver, url)

            if stats:
                stats["linkedin_urn"] = urn
                results.append(stats)
                parts = []
                if stats["impressions"]:
                    parts.append(f"{stats['impressions']} imp")
                if stats["reactions"]:
                    parts.append(f"{stats['reactions']} react")
                if stats["comments"]:
                    parts.append(f"{stats['comments']} cmts")
                print(f"✓ ({', '.join(parts) or 'no data'})")
            else:
                print("✗ failed")

            # Be polite — don't hammer LinkedIn
            if i < len(posts):
                time.sleep(2)

        # Save cookies after successful run
        _save_cookies(driver)

        return results

    finally:
        driver.quit()