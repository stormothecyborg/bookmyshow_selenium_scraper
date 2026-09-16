# BookMyShow Movie Scraper

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
| `language` | Populated only with `--details` (see below) |
| `genre` | Populated only with `--details` |
| `censor_rating` | Populated only with `--details` |
| `votes_or_rating` | Populated only with `--details` |
| `city` | City slug searched |

By default, only `title`/`url`/`city` are collected from the listing page
(fast — one page load total). Pass `--details` to also visit each movie's
own page and fill in language/genre/censor rating/rating — this is one
extra page load per movie, so it's opt-in rather than default. If you run
without `--details`, the other fields will correctly show up as `null`/
empty in your output — that's expected, not a bug.

Detail extraction tries structured `JSON-LD` data on the movie page first
(schema.org markup meant for search engines — more stable than CSS
classes), and falls back to scanning visible page text for recognizable
patterns (known language names, censor-rating formats like `U/A 16+`) if
that's missing.

## Install

```bash
pip install undetected-chromedriver selenium
```

You also need Google Chrome installed locally.

## Usage

```bash
# Basic — opens a visible Chrome window, saves bengaluru_movies.csv
python bookmyshow_selenium_scraper.py bengaluru

# Also fetch language/genre/censor rating/votes per movie (slower)
python bookmyshow_selenium_scraper.py bengaluru --details

# Different city, custom output
python bookmyshow_selenium_scraper.py mumbai --out mumbai_movies.json --format json

# If you hit a ChromeDriver/Chrome version mismatch error, pin your
# installed Chrome's major version explicitly (Chrome menu -> Help ->
# About Google Chrome to find it):
python bookmyshow_selenium_scraper.py bengaluru --chrome-version 152

# Debug a --details run that's coming back empty: dumps HTML + a
# screenshot of any movie page that yields no fields at all, into ./debug/
python bookmyshow_selenium_scraper.py bengaluru --details --debug
```

### CLI options

- `--headless` — run without a visible browser window. Try **without** this
  flag first; headless mode is itself an extra automation signal some
  bot-detection checks for, and testing showed the non-headless run
  succeeding cleanly.
- `--details` — also visit each movie's page for language/genre/censor
  rating/votes. Slower (one extra page load per movie). Without this flag,
  those fields are simply not collected and will be blank/`null` — that's
  the default, not an error.
- `--debug` — only takes effect together with `--details`. Whenever a
  movie's detail page yields *no* fields at all (or the page turns out to
  be a bot-detection block page), saves that page's raw HTML and a
  screenshot into `--debug-dir` so you can inspect exactly what the
  browser saw. Also turns on extra per-movie logging: whether the page
  loaded or got blocked, how many JSON-LD `<script>` tags were found and
  how many matched a `Movie` object, and the fallback text-scan length.
- `--debug-dir` — where `--debug` dumps go (default: `./debug`).
- `--detail-delay SECONDS` — pause between detail-page visits when using
  `--details` (default 1.5, be polite to the server).
- `--format {csv,json}`
- `--out PATH`
- `--wait SECONDS` — how long to wait for movie content to load (default 25)
- `--chrome-version N` — pin ChromeDriver to a specific Chrome major
  version. Needed whenever you see `SessionNotCreatedException: ... This
  version of ChromeDriver only supports Chrome version N, current browser
  version is M` — happens because `undetected-chromedriver` grabs the
  latest ChromeDriver release, which can land ahead of your locally
  installed Chrome. Pass your installed Chrome's major version (Chrome
  menu -> Help -> About Google Chrome) to fix it, e.g. `--chrome-version
  152`.

## City slugs

Use BookMyShow's own URL slugs, e.g. `bengaluru`, `mumbai`, `delhi-ncr`,
`hyderabad`, `chennai`, `pune`, `kolkata`.

## Troubleshooting

- **`SessionNotCreatedException` / "ChromeDriver only supports Chrome
  version N"** — version mismatch between auto-downloaded ChromeDriver and
  your installed Chrome. Pass `--chrome-version` with your Chrome's major
  version (see above). Alternatively, update Chrome itself to the latest
  version and omit the flag.
- **`OSError: [WinError 6] The handle is invalid` after "Done"** — harmless
  cleanup noise from `undetected-chromedriver`'s `Chrome.__del__` on
  Windows, printed after your output file has already been written
  successfully. Safe to ignore.
- **All `--details` fields come back empty/`null`** — first, confirm you
  actually passed `--details` (without it, this is expected — see above).
  If you did pass it and still get nothing, re-run with `--debug` added
  and check the log: it will tell you whether each detail page loaded
  cleanly, hit a block page, or loaded but had 0 matching JSON-LD `Movie`
  objects. Inspect the corresponding file in `./debug/` for the exact
  HTML/screenshot BookMyShow served.

## Limitations (v1)

- `--details` extraction is best-effort: it depends on BookMyShow still
  embedding JSON-LD structured data (or recognizable text patterns) on
  movie detail pages, which can change without notice.
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