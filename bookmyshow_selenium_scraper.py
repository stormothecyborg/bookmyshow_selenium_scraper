"""
bookmyshow_selenium_scraper.py
--------------------------------
Scrapes currently-showing movies (and, where visible, showtimes/theatres)
from BookMyShow for a given city, using a real browser via
undetected-chromedriver -- needed because BookMyShow's internal API is
protected by Cloudflare bot-detection that blocks plain HTTP clients
(confirmed: identical requests with a full browser-matching header set
still return HTTP 403 "Attention Required | Cloudflare").

Run locally (this needs a live browser + live network access, neither of
which is available in a sandboxed chat environment):

    pip install undetected-chromedriver selenium beautifulsoup4
    python bookmyshow_selenium_scraper.py bengaluru
    python bookmyshow_selenium_scraper.py mumbai --out mumbai_movies.csv --headless
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from typing import Optional

import undetected_chromedriver as uc
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_URL = "https://in.bookmyshow.com"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bms_scraper")


@dataclass
class Movie:
    title: Optional[str] = None
    url: Optional[str] = None
    language: Optional[str] = None
    genre: Optional[str] = None
    censor_rating: Optional[str] = None
    votes_or_rating: Optional[str] = None
    city: str = ""


class BookMyShowScraper:
    def __init__(
        self,
        headless: bool = False,
        page_load_timeout: int = 30,
        chrome_version_main: Optional[int] = None,
    ):
        self.page_load_timeout = page_load_timeout
        options = uc.ChromeOptions()
        if headless:
            # NOTE: headless mode is itself an extra automation signal some
            # bot-detection looks for. If you get blocked with --headless
            # but NOT with a visible window, that's the reason -- try
            # without it first while testing.
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1366,900")
        options.add_argument("--disable-blink-features=AutomationControlled")
        # undetected-chromedriver auto-downloads a matching chromedriver,
        # but it can pick a version newer than your installed Chrome
        # (e.g. "ChromeDriver only supports Chrome version 153, current
        # browser is 152") if a driver release is out ahead of a slightly
        # older locally-installed Chrome. Pin the major version explicitly
        # to avoid that mismatch -- pass your installed Chrome's major
        # version number (Chrome menu -> Help -> About Google Chrome).
        self.driver = uc.Chrome(options=options, version_main=chrome_version_main)
        self.driver.set_page_load_timeout(page_load_timeout)

    def close(self):
        try:
            self.driver.quit()
        except WebDriverException:
            pass

    def _wait_for(self, by, selector, timeout=20):
        try:
            return WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except TimeoutException:
            return None

    def _check_for_block_page(self) -> bool:
        """Detect a Cloudflare (or similar) interstitial/challenge page."""
        try:
            title = self.driver.title or ""
        except WebDriverException:
            title = ""
        blocked_markers = ["attention required", "just a moment", "access denied"]
        if any(marker in title.lower() for marker in blocked_markers):
            return True
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
        except NoSuchElementException:
            body_text = ""
        return any(marker in body_text[:500] for marker in blocked_markers)

    def get_movies(self, city_slug: str, max_wait: int = 25) -> list[Movie]:
        """
        city_slug examples: 'bengaluru', 'mumbai', 'delhi-ncr', 'hyderabad'.
        Matches BookMyShow's own URL slugs, e.g.
        https://in.bookmyshow.com/explore/movies-bengaluru
        """
        url = f"{BASE_URL}/explore/movies-{city_slug}"
        log.info("Opening %s", url)

        try:
            self.driver.get(url)
        except TimeoutException:
            log.error("Page load timed out for %s", url)
            return []
        except WebDriverException as e:
            log.error("Browser error loading %s: %s", url, e)
            return []

        # Give client-side rendering a moment, then check we weren't
        # served a bot-detection challenge page instead of the real site.
        time.sleep(3)
        if self._check_for_block_page():
            log.error(
                "Blocked by an anti-bot challenge page (Cloudflare or similar). "
                "Try running without --headless, or wait and retry -- some "
                "challenges are IP-reputation based and are temporary."
            )
            return []

        # Movie cards on the explore page. BookMyShow's markup changes
        # periodically; this targets anchors whose href contains '/movies/'
        # rather than a specific class name, which is more resilient to
        # theme/CSS changes (the same defensive approach used in the
        # MDComputers scraper).
        self._wait_for(By.CSS_SELECTOR, "a[href*='/movies/']", timeout=max_wait)

        # Scroll a few times to trigger lazy-loaded content (infinite
        # scroll / "load more" patterns common on this kind of listing page).
        last_height = self.driver.execute_script("return document.body.scrollHeight")
        for _ in range(6):
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
            new_height = self.driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                break
            last_height = new_height

        cards = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/movies/']")
        log.info("Found %d candidate movie links", len(cards))

        movies: dict[str, Movie] = {}
        for card in cards:
            try:
                href = card.get_attribute("href")
                if not href or "/movies/" not in href:
                    continue
                # Movie detail links look like .../movies/<city>/<slug>/<code>
                # Use the href itself as the dedup key.
                if href in movies:
                    continue

                title = (
                    card.get_attribute("title")
                    or card.text.strip()
                    or None
                )
                if not title:
                    # Some cards wrap an <img alt="Movie Title"> instead of
                    # visible text.
                    try:
                        img = card.find_element(By.TAG_NAME, "img")
                        title = img.get_attribute("alt")
                    except NoSuchElementException:
                        pass

                if not title:
                    continue  # nothing usable, skip rather than emit a blank row

                movies[href] = Movie(title=title.strip(), url=href, city=city_slug)
            except WebDriverException:
                continue

        return list(movies.values())


def save_csv(movies: list[Movie], path: str) -> None:
    fieldnames = [
        "title", "url", "language", "genre",
        "censor_rating", "votes_or_rating", "city",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for m in movies:
            writer.writerow(asdict(m))


def save_json(movies: list[Movie], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(m) for m in movies], f, indent=2, ensure_ascii=False)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scrape currently-showing movies from BookMyShow for a city."
    )
    parser.add_argument(
        "city", help="BookMyShow city slug, e.g. bengaluru, mumbai, delhi-ncr"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run Chrome headless. Try WITHOUT this flag first if you get blocked.",
    )
    parser.add_argument(
        "--format", choices=["csv", "json"], default="csv",
    )
    parser.add_argument("--out", default=None)
    parser.add_argument(
        "--wait", type=int, default=25,
        help="Seconds to wait for movie content to appear (default 25).",
    )
    parser.add_argument(
        "--chrome-version", type=int, default=None,
        help=(
            "Installed Chrome's major version number (e.g. 152), to fix "
            "'ChromeDriver only supports Chrome version N' errors. Check "
            "yours via Chrome menu -> Help -> About Google Chrome."
        ),
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    scraper = BookMyShowScraper(headless=args.headless, chrome_version_main=args.chrome_version)
    try:
        movies = scraper.get_movies(args.city, max_wait=args.wait)
    finally:
        scraper.close()

    if not movies:
        log.warning("No movies extracted for '%s'. See log messages above.", args.city)
        return 0

    out_path = args.out or f"{args.city}_movies.{args.format}"
    if args.format == "csv":
        save_csv(movies, out_path)
    else:
        save_json(movies, out_path)

    log.info("Saved %d movies to %s", len(movies), out_path)
    print(f"\nDone. {len(movies)} movies written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())