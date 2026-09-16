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
import re
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
            options.add_argument("--headless=new")
        options.add_argument("--window-size=1366,900")
        options.add_argument("--disable-blink-features=AutomationControlled")
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

        time.sleep(3)
        if self._check_for_block_page():
            log.error(
                "Blocked by an anti-bot challenge page (Cloudflare or similar). "
                "Try running without --headless, or wait and retry -- some "
                "challenges are IP-reputation based and are temporary."
            )
            return []

        self._wait_for(By.CSS_SELECTOR, "a[href*='/movies/']", timeout=max_wait)

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
                if href in movies:
                    continue

                title = (
                    card.get_attribute("title")
                    or card.text.strip()
                    or None
                )
                if not title:
                    try:
                        img = card.find_element(By.TAG_NAME, "img")
                        title = img.get_attribute("alt")
                    except NoSuchElementException:
                        pass

                if not title:
                    continue

                movies[href] = Movie(title=title.strip(), url=href, city=city_slug)
            except WebDriverException:
                continue

        return list(movies.values())

    def get_movie_details(
        self, movie: Movie, debug: bool = False, debug_dir: str = "debug"
    ) -> Movie:
        """
        Visits a single movie's detail page and fills in language, genre,
        censor_rating, votes_or_rating. Mutates and returns the same Movie.
        """
        if not movie.url:
            log.warning("  -> skipping '%s': no URL", movie.title)
            return movie

        try:
            self.driver.get(movie.url)
        except TimeoutException:
            log.warning("  -> timed out loading detail page for '%s'", movie.title)
            return movie
        except WebDriverException as e:
            log.warning("  -> browser error loading detail page for '%s': %s", movie.title, e)
            return movie

        time.sleep(2)

        page_title = ""
        try:
            page_title = self.driver.title or ""
        except WebDriverException:
            pass

        if self._check_for_block_page():
            log.warning(
                "  -> BLOCKED on detail page for '%s' (page title: %r); skipping details.",
                movie.title, page_title,
            )
            if debug:
                self._dump_debug(movie, debug_dir, suffix="blocked")
            return movie

        log.info("  -> loaded detail page (title: %r)", page_title)

        # --- Attempt 1: JSON-LD structured data ---
        jsonld_found = 0
        jsonld_movie_objs = 0
        try:
            scripts = self.driver.find_elements(
                By.CSS_SELECTOR, "script[type='application/ld+json']"
            )
            jsonld_found = len(scripts)
            for script in scripts:
                raw = script.get_attribute("innerHTML")
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                candidates = data if isinstance(data, list) else [data]
                if isinstance(data, dict) and "@graph" in data:
                    candidates = data["@graph"]

                for obj in candidates:
                    if not isinstance(obj, dict):
                        continue
                    obj_type = obj.get("@type", "")
                    if isinstance(obj_type, list):
                        is_movie = "Movie" in obj_type
                    else:
                        is_movie = obj_type == "Movie"
                    if not is_movie:
                        continue

                    jsonld_movie_objs += 1
                    movie.genre = self._stringify(obj.get("genre")) or movie.genre
                    movie.language = self._stringify(obj.get("inLanguage")) or movie.language
                    movie.censor_rating = (
                        obj.get("contentRating") or movie.censor_rating
                    )
                    rating = obj.get("aggregateRating")
                    if isinstance(rating, dict):
                        value = rating.get("ratingValue")
                        count = rating.get("ratingCount") or rating.get("reviewCount")
                        if value:
                            movie.votes_or_rating = (
                                f"{value} ({count} votes)" if count else str(value)
                            )
                    break
        except WebDriverException as e:
            log.warning("  -> WebDriverException while reading JSON-LD: %s", e)

        log.info(
            "  -> JSON-LD: %d <script> tag(s) found, %d parsed as an object with @type Movie",
            jsonld_found, jsonld_movie_objs,
        )

        # --- Attempt 2: fallback DOM/text scan, only for fields still missing ---
        used_fallback = False
        if not (movie.genre and movie.language and movie.censor_rating):
            used_fallback = True
            try:
                body_text = self.driver.find_element(By.TAG_NAME, "body").text
            except (NoSuchElementException, WebDriverException):
                body_text = ""

            log.info("  -> fallback text scan: body_text length = %d chars", len(body_text))

            if not movie.censor_rating:
                match = re.search(
                    r"\b(U/?A\s?\d*\+?|U|A|S)\b(?=\s|$)", body_text[:3000]
                )
                if match:
                    movie.censor_rating = match.group(1)

            if not movie.language:
                known_languages = [
                    "Hindi", "English", "Tamil", "Telugu", "Kannada",
                    "Malayalam", "Marathi", "Bengali", "Punjabi", "Gujarati",
                ]
                found = [lang for lang in known_languages if lang in body_text[:3000]]
                if found:
                    movie.language = ", ".join(found)

        log.info(
            "  -> result for '%s': language=%r genre=%r censor_rating=%r votes_or_rating=%r",
            movie.title, movie.language, movie.genre, movie.censor_rating, movie.votes_or_rating,
        )

        # If we still got nothing at all after both attempts, this movie's
        # page is worth inspecting by hand -- dump it if --debug was passed.
        if debug and not (movie.language or movie.genre or movie.censor_rating or movie.votes_or_rating):
            self._dump_debug(movie, debug_dir, suffix="empty", note=f"jsonld_scripts={jsonld_found} used_fallback={used_fallback}")

        return movie

    def _dump_debug(self, movie: Movie, debug_dir: str, suffix: str, note: str = "") -> None:
        """Save the current page's HTML + a screenshot to disk so the actual
        rendered page can be inspected by hand. Only called with --debug."""
        import os
        os.makedirs(debug_dir, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", movie.title or "untitled")[:60]
        html_path = os.path.join(debug_dir, f"{safe_name}_{suffix}.html")
        png_path = os.path.join(debug_dir, f"{safe_name}_{suffix}.png")
        try:
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
        except (WebDriverException, OSError) as e:
            log.warning("  -> could not save debug HTML for '%s': %s", movie.title, e)
        try:
            self.driver.save_screenshot(png_path)
        except (WebDriverException, OSError) as e:
            log.warning("  -> could not save debug screenshot for '%s': %s", movie.title, e)
        log.info("  -> DEBUG dump saved: %s / %s %s", html_path, png_path, f"({note})" if note else "")

    @staticmethod
    def _stringify(value) -> Optional[str]:
        if not value:
            return None
        if isinstance(value, list):
            return ", ".join(str(v) for v in value if v)
        return str(value)


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
    parser.add_argument(
        "--details", action="store_true",
        help=(
            "Also visit each movie's own page to fill in language, genre, "
            "censor rating, and rating/votes. Slower -- one extra page "
            "load per movie -- so it's opt-in."
        ),
    )
    parser.add_argument(
        "--detail-delay", type=float, default=1.5,
        help="Seconds to pause between detail-page visits (default 1.5, be polite).",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help=(
            "When --details finds nothing for a movie (or hits a block page), "
            "save that page's HTML + a screenshot into ./debug/ so you can "
            "see exactly what the browser was looking at."
        ),
    )
    parser.add_argument(
        "--debug-dir", default="debug",
        help="Directory to write --debug HTML/screenshot dumps to (default: ./debug).",
    )
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    scraper = BookMyShowScraper(headless=args.headless, chrome_version_main=args.chrome_version)
    try:
        movies = scraper.get_movies(args.city, max_wait=args.wait)

        if movies and args.details:
            log.info("Fetching details for %d movies (this takes a while)...", len(movies))
            for i, movie in enumerate(movies, start=1):
                log.info("[%d/%d] %s", i, len(movies), movie.title)
                scraper.get_movie_details(movie, debug=args.debug, debug_dir=args.debug_dir)
                if i < len(movies):
                    time.sleep(args.detail_delay)
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