# bookmyshow_selenium_scraper# BookMyShow Movie Scraper

Extracts currently-showing movie listings from [BookMyShow](https://in.bookmyshow.com)
for a given city, using Selenium browser automation.

See [`INVESTIGATION.md`](./INVESTIGATION.md) for the full process — including
a genuine attempt to reverse-engineer BookMyShow's internal API first, why
that hit a Cloudflare bot-detection wall, and why Selenium was the correct
next step rather than the starting point.

## Why Selenium (not just `requests`)

BookMyShow's movie data is served by an internal API
(`/api/explore/v2/discover/filters/movies-<city>`) that was fully
identified and reverse-engineered — including its required custom headers
(`x-app-code`, `x-region-code`, etc.). However, calling it directly with a
plain HTTP client is blocked by Cloudflare bot-detection (HTTP 403,
"Attention Required"), which fingerprints requests at the TLS/JS-execution
level, not just headers. A real browser is required to get through — hence
Selenium, specifically `undetected-chromedriver`, which patches the
automation signals (e.g. `navigator.webdriver`) that Cloudflare checks for.

## What it extracts

| Field | Description |
|---|---|
| `title` | Movie title |
| `url` | Link to the movie's BookMyShow page |
| `language` | *(reserved — not populated in v1, see Limitations)* |
| `genre` | *(reserved — not populated in v1)* |
| `censor_rating` | *(reserved — not populated in v1)* |
| `votes_or_rating` | *(reserved — not populated in v1)* |
| `city` | City slug searched |

## Install

```bash
pip install undetected-chromedriver selenium
```

You also need Google Chrome installed locally.

## Usage

```bash
# Basic — opens a visible Chrome window, saves bengaluru_movies.csv
python bookmyshow_selenium_scraper.py bengaluru

# Different city, custom output
python bookmyshow_selenium_scraper.py mumbai --out mumbai_movies.json --format json

# If you hit a ChromeDriver/Chrome version mismatch error, pin your
# installed Chrome's major version explicitly (Chrome menu -> Help ->
# About Google Chrome to find it):
python bookmyshow_selenium_scraper.py bengaluru --chrome-version 152
```

### CLI options

- `--headless` — run without a visible browser window. Try **without** this
  flag first; headless mode is itself an extra automation signal some
  bot-detection checks for, and testing showed the non-headless run
  succeeding cleanly.
- `--format {csv,json}`
- `--out PATH`
- `--wait SECONDS` — how long to wait for movie content to load (default 25)
- `--chrome-version N` — pin ChromeDriver to a specific Chrome major version

## City slugs

Use BookMyShow's own URL slugs, e.g. `bengaluru`, `mumbai`, `delhi-ncr`,
`hyderabad`, `chennai`, `pune`, `kolkata`.

## Limitations (v1)

- Only movie title + URL are currently extracted reliably from the listing
  page. Language/genre/censor rating/ratings would need either clicking
  into each movie's detail page (not yet implemented) or further DOM
  inspection of the listing cards.
- Selector logic (`a[href*='/movies/']`) is intentionally broad/defensive
  rather than tied to specific CSS class names, since BookMyShow's markup
  can change — but this also means a small number of non-movie links
  (e.g. promotional banners) may need manual filtering; 28 raw candidate
  links resolved to 16 real distinct movies in testing.
- Cloudflare's bot-detection is adaptive; a configuration that works today
  may need adjustment later (e.g. added delays, different Chrome profile)
  if detection patterns change.
- Not tested against showtimes/theatre-level pages — city-level movie
  listing only.

## Responsible use

This project is for learning/portfolio purposes. Check
`https://in.bookmyshow.com/robots.txt` and BookMyShow's Terms of Use before
any larger-scale or non-personal use, and avoid concurrent/high-frequency
requests against the live site.