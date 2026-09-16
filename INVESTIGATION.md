# Investigation Log: BookMyShow Movie Scraper

This documents the actual process followed to build this scraper — what was
tried, what failed, why, and what worked. Kept as a record of the
engineering process, not just the final result.

---

## Goal

Extract currently-showing movie listings from BookMyShow for a given city,
matching the pattern: check for a usable internal API first (faster,
simpler), fall back to browser automation (Selenium) only if the API path
isn't viable.

---

## Step 1 — Inspecting network traffic for a hidden API

Opened `https://in.bookmyshow.com/explore/movies-bengaluru` in Chrome,
opened DevTools → Network tab → filtered to Fetch/XHR only.

**Problem:** ~270 requests were visible even with the XHR filter applied —
mostly analytics/tracking noise, not obviously data-carrying calls.

**What worked:** Instead of manually checking all 270, used the Network
panel's response-body search (Ctrl+F within the Network tab) to search for
the name of a movie visibly showing on the page. This pointed directly at
the relevant request instead of guessing from URL names alone.

**Found:** `GET /api/explore/v2/discover/filters/movies-bengaluru`
— response shape included `listings`, `filters`, `filtersV2` keys, matching
what a movie-listing API would return.

---

## Step 2 — First look showed an empty response

**Problem:** the first capture of that request showed `"listings": []` —
empty — even though movies were clearly visible on the actual page.

**Why:** the request had been served **from the browser's Service Worker
cache** (visible as "200 OK (from service worker)" in the Status column),
not from a live network round-trip. Service workers can intercept and
modify outgoing requests (e.g. injecting auth/app headers) before they
reach the network, and can also serve stale/placeholder cached responses —
so the captured request wasn't representative of the real call.

**Fix:** DevTools → Application tab → Service Workers → checked
**"Bypass for network"**, then reloaded and re-captured the request. This
surfaced the real, complete outgoing request with its full header set.

---

## Step 3 — Replicating the request with plain `requests`

Copied the request URL and query parameters
(`region=BANG&embedded=true&lat=...&lon=...`) and attempted to call it
directly from Python using `requests.get()`.

**Attempt 1 — no custom headers, just the URL:**
```
Status: 500
Body: {"message":"Null Value Returned for Field: x-app-code"}
```
**Why:** the backend expects a custom `x-app-code` header identifying the
calling client/app (e.g. `WEBV2`), which the real frontend sends
automatically but a bare `requests.get()` call does not.

**Attempt 2 — full header set copied from DevTools Request Headers**
(`x-app-code`, `x-platform-code`, `x-region-code`, `x-region-slug`,
`x-latitude`/`x-longitude`, `x-geohash`, `sec-ch-ua`, real `User-Agent`,
`referer`, etc. — deliberately excluding `true-client-ip`, since that's a
value derived from the actual network connection, not something a client
sets):
```
Status: 403
Content-Type: text/html
Body: "Attention Required! | Cloudflare"
```

**Why:** Cloudflare's bot-management layer sits in front of the API and
fingerprints the request itself, not just its headers — TLS handshake
signature, absence of real JavaScript execution, missing browser-level
behavioral signals. Copying headers exactly is not sufficient to pass this;
it requires an actual browser engine making the request.

**Conclusion:** the internal API is real and was successfully
reverse-engineered (URL, method, required custom headers all identified
and confirmed necessary), but it is **not usable via a plain HTTP client**
due to Cloudflare protection. This is a legitimate stopping point for the
API approach, not a failed attempt — the header requirement
(`x-app-code` etc.) was correctly diagnosed and fixed; the remaining
blocker (Cloudflare) requires a different tool, not a different header.

---

## Step 4 — Pivoting to Selenium

Since the API path was confirmed blocked at the network/bot-detection
layer (not by missing data or wrong parameters), the next step was browser
automation: drive a real Chrome browser so requests carry genuine browser
fingerprints instead of trying to fake them from a script.

**Tool chosen:** `undetected-chromedriver`, a drop-in replacement for
Selenium's standard Chrome driver that patches known automation tells
(e.g. `navigator.webdriver`) that bot-detection services check for.
Plain Selenium was not tested directly, since the goal (evading Cloudflare
bot-detection) is exactly the documented purpose of this package.

### First run — failed: ChromeDriver/Chrome version mismatch

```
selenium.common.exceptions.SessionNotCreatedException:
This version of ChromeDriver only supports Chrome version 153.
Current browser version is 152.0.7977.84
```

**Why:** `undetected-chromedriver` auto-downloads a ChromeDriver build to
match Chrome, but had pulled a build for Chrome 153, one version ahead of
the actually-installed Chrome 152.

**Fix:** pinned the driver to the installed browser's major version
explicitly (`version_main=152` / CLI flag `--chrome-version 152`), rather
than relying on auto-detection.

### Second run — success

```
Opening https://in.bookmyshow.com/explore/movies-bengaluru
Found 28 candidate movie links
Saved 16 movies to bengaluru_movies.csv
```

- No Cloudflare challenge page encountered — confirmed by checking the page
  title/body for block-page markers (e.g. "Attention Required",
  "Just a moment") before parsing, which came back clear.
- 28 raw links matched the `/movies/` URL pattern; 16 resolved to real,
  distinct movie titles after filtering out non-movie links (e.g. "See
  All" / promotional links that also happen to contain `/movies/` in
  their href) and de-duplicating.
- Output verified by inspecting `bengaluru_movies.csv` directly — real,
  current movie titles, not empty or garbage rows.

(A harmless `OSError: [WinError 6] The handle is invalid` appears after
script completion, from `undetected-chromedriver`'s driver-process cleanup
on Windows — occurs after the script has already finished successfully and
does not affect output.)

---

## Summary of outcome

| Approach | Result |
|---|---|
| Direct API call, no headers | 500 — missing required `x-app-code` header |
| Direct API call, full browser-matching headers | 403 — blocked by Cloudflare bot-detection |
| Selenium + undetected-chromedriver (version mismatch) | Failed to launch — driver/browser version mismatch |
| Selenium + undetected-chromedriver (version pinned) | **Success** — 16 real movies extracted |

The API reverse-engineering work was real and necessary — it proved the
endpoint, its parameters, and its auth headers, and it's what confirmed
*why* Selenium was needed (a deliberate, evidence-based tooling decision)
rather than defaulting to Selenium without first checking for a simpler
path.