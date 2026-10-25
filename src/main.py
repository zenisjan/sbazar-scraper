"""
Sbazar.cz Scraper - Scraping tool for sbazar.cz classified ads (Seznam.cz marketplace).

This Actor scrapes listings from sbazar.cz across multiple categories and extracts
detailed information from individual listing pages with full pagination support.
Includes PostgreSQL database integration.

The site pages through at most ~10,000 results (172 pages) of any result list,
so a category with more offers than that is split into its subcategories, and a
subcategory that is still too big into price bands, until every slice fits;
whatever cannot be reached that way is recorded as a reason the run is
incomplete (and the run is marked 'failed').
"""

from __future__ import annotations

import os
import asyncio
import html as ihtml
import json
import math
import re
import sys
import unicodedata
from collections import deque
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse
from datetime import datetime, timedelta, timezone

from apify import Actor
from bs4 import BeautifulSoup
from httpx import AsyncClient, HTTPStatusError, TransportError

# Import our database manager and geocoding - handle different import paths
try:
    from .database import db_manager
    from .czech_cities import geocode_czech_city
except ImportError:
    try:
        from database import db_manager
        from czech_cities import geocode_czech_city
    except ImportError:
        # Add current directory to path if needed
        current_dir = os.path.dirname(os.path.abspath(__file__))
        if current_dir not in sys.path:
            sys.path.insert(0, current_dir)
        from database import db_manager
        from czech_cities import geocode_czech_city

try:  # exact (non-fuzzy) gazetteer lookup, if this copy of czech_cities has it
    from .czech_cities import _direct_lookup as _gazetteer_exact
except ImportError:
    try:
        from czech_cities import _direct_lookup as _gazetteer_exact
    except ImportError:
        _gazetteer_exact = None

BASE_URL = "https://www.sbazar.cz"

# The site's 12 top-level categories: the level-1 seoNames of its category tree
# (the `memoizedCategories` JSON embedded in every page), checked live 2026-10-25.
# Slugs that are not in the tree return HTTP 404.
DEFAULT_CATEGORIES = [
    "1-auto-moto",
    "8-dum-byt-zahrada",
    "15-obleceni-obuv-doplnky",
    "27-sport",
    "28-zdravi-krasa",
    "29-detsky-bazar",
    "30-elektro-pocitace",
    "33-starozitnosti-hobby-umeni",
    "77-nemovitosti",
    "82-sluzby",
    "90-zvirata",
    "295-hudba-knihy-hry-zabava",
]

# Offers per result page when the page does not report it: 46 offers + 12 e-shop
# ad tiles, or 58 offers on pages without ad tiles.
LISTINGS_PER_PAGE = 46

# Listing pages are fetched one at a time, at least this far apart.
LISTING_PAGE_INTERVAL_SECS = 1.0

# Detail pages are fetched DETAIL_CONCURRENCY at a time, with request starts at
# least DETAIL_MIN_INTERVAL_SECS apart (at most 4 requests/s).
DETAIL_CONCURRENCY = 4
DETAIL_MIN_INTERVAL_SECS = 0.25

# The Postgres `price` column is INTEGER; larger values can't be stored.
_PRICE_MAX = 2_147_483_647

# Upper end of the price range when a slice is split into price bands. The site's
# price filter is inclusive at both ends and every band also returns the
# "Dohodou" (price by agreement) offers, whatever their price.
PRICE_CEILING = 2_147_483_647

# Guard against runaway splitting: page-1 probes per input category.
MAX_SLICE_PROBES = 3000

# Size suffix the site itself uses for full-size photos (JSON-LD / og:image).
IMAGE_SUFFIX = "?fl=exf|res,1024,768,1|wrm,/watermark/sbazar.png,10,10|webp,75"

# Length of the one-line `description` preview cut from the full description.
DESCRIPTION_PREVIEW_CHARS = 1000


def _clean_text(value: Optional[str]) -> str:
    """Collapse whitespace (incl. nbsp / zero-width chars) into single spaces."""
    if not value:
        return ""
    value = value.replace("\xa0", " ").replace("\u200b", "").replace("\u200d", "")
    return re.sub(r"\s+", " ", value).strip()


_LOCATION_PREFIX_RE = re.compile(r"^(?:v|ve)\s+", re.IGNORECASE)


def _strip_location_prefix(text: Optional[str]) -> str:
    """'v\\xa0Slaný' -> 'Slaný' (the site joins the preposition with a nbsp)."""
    return _LOCATION_PREFIX_RE.sub("", _clean_text(text), count=1)


def _slugify(text: str) -> str:
    """Lowercase, strip Czech diacritics and non-url chars -> a-z0-9- slug."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower().strip())
    return slug.strip("-")


def _parse_price(text: Optional[str]) -> Optional[int]:
    """Parse '149 000 Kč' -> 149000; None for 'Dohodou' or out-of-range values.

    Only the first run of digits counts (Czech prices use spaces / nbsp as
    thousands separators), so a trailing number can't be glued onto the price.
    """
    if not text:
        return None
    match = re.search(r"\d[\d\s]*", text)
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(0))
    if not digits:
        return None
    value = int(digits)
    return value if value <= _PRICE_MAX else None


def _abs_url(url: Optional[str]) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("/"):
        return f"{BASE_URL}{url}"
    return url


def _best_srcset_url(srcset: Optional[str]) -> str:
    """Return the widest candidate of an <img srcset>.

    The site's image URLs contain commas themselves ('...|res,606,606,3|webp,75
    606w, ...'), so candidates are read as whitespace-separated URL/descriptor
    pairs instead of being split on commas.
    """
    best_url, best_width = "", -1.0
    url: Optional[str] = None
    for token in (srcset or "").split():
        if url is None:
            url = token.rstrip(",")
            if token.endswith(","):  # a candidate without a descriptor
                if best_width < 0:
                    best_url, best_width = url, 0.0
                url = None
            continue
        descriptor = token.rstrip(",")
        width = 0.0
        if descriptor[-1:] in ("w", "x"):
            try:
                width = float(descriptor[:-1])
            except ValueError:
                width = 0.0
        if width > best_width:
            best_url, best_width = url, width
        url = None
    if url and best_width < 0:
        best_url = url
    return _abs_url(best_url)


def _image_url(url: Optional[str]) -> str:
    """Full-size photo URL for an image path from the page's structured data."""
    url = _abs_url(url)
    if url and "?" not in url:
        url += IMAGE_SUFFIX
    return url


def _astro_decode(value: Any) -> Any:
    """Decode an Astro island prop value: [0, v] = value, [1, [...]] = array, [0] = undefined."""
    if isinstance(value, list):
        if len(value) != 2 or not isinstance(value[0], int):
            return None  # [0] = undefined
        kind, inner = value
        if kind == 0:
            if isinstance(inner, dict):
                return {k: _astro_decode(v) for k, v in inner.items()}
            return inner
        if kind == 1 and isinstance(inner, list):
            return [_astro_decode(v) for v in inner]
        if kind in (3, 6, 7):  # Date, BigInt, URL: serialised as strings
            return inner
        return None
    if isinstance(value, dict):
        return {k: _astro_decode(v) for k, v in value.items()}
    return value


def _island_props(soup: BeautifulSoup, component: str) -> Optional[Dict[str, Any]]:
    """Decoded props of the first <astro-island> rendering `component` (None if absent)."""
    for island in soup.find_all("astro-island"):
        name = (island.get("component-url") or "").rsplit("/", 1)[-1]
        if name.split(".", 1)[0] != component:
            continue
        try:
            props = json.loads(island.get("props") or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(props, dict):
            return None
        return {k: _astro_decode(v) for k, v in props.items()}
    return None


def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _format_price(price: int) -> str:
    return f"{price:,} Kč".replace(",", " ")


def _geocode(location: str, municipality: str = "", district: str = "") -> Optional[Tuple[float, float]]:
    """Gazetteer coordinates for a listing's place.

    The place itself is tried as an exact name first (the municipality, the
    location text, its part before a comma: "Opava, Jaktař" -> "Opava"; "Praha 9"
    -> Praha); then the district town, which puts villages missing from the
    gazetteer at their district seat rather than nowhere; and only then the
    gazetteer's loose matching (which can find a city name inside a village name,
    so it is the last resort when the district is unknown).
    """
    first = location.split(",")[0].strip()
    if _gazetteer_exact is not None:
        for name in (municipality, location, first):
            coords = _gazetteer_exact(name) if name else None
            if coords:
                return coords
    if re.match(r"^Praha(\s*\d+|-)", first, re.IGNORECASE):
        return geocode_czech_city("Praha")
    for name in (district, municipality, location):
        coords = geocode_czech_city(name) if name else None
        if coords:
            return coords
    return None


def _price_slug(price_lo: Optional[int], price_hi: Optional[int]) -> str:
    """The URL's price segment; the site's form is 'cena-od-{from}-do-{to}-kc'."""
    if price_lo is None and price_hi is None:
        return "cena-neomezena"
    lo = 0 if price_lo is None else price_lo
    hi = PRICE_CEILING if price_hi is None else price_hi
    return f"cena-od-{lo}-do-{hi}-kc"


def _split_band(lo: int, hi: int) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """Split an inclusive price band in two, at the geometric middle (prices are skewed)."""
    if hi <= lo:
        return None
    mid = int(math.sqrt(max(lo, 1) * hi))
    mid = min(max(mid, lo), hi - 1)
    return (lo, mid), (mid + 1, hi)


# Safety margin (seconds) subtracted from the Apify hard timeout so the run can
# stop scraping, flush its data, and write the actor_run's final status before
# the platform kills it (which would otherwise leave the run stuck as "running"
# and break sold-detection).
_DEADLINE_MARGIN_SECS = 120


def _run_deadline() -> Optional[datetime]:
    """Return the UTC time to stop scraping by, derived from the Apify timeout.

    Apify exposes the run's hard-timeout instant as an ISO 8601 env var. We stop
    a couple of minutes early so there is time to save and close out cleanly.
    Returns None (no limit) for local runs where the var is absent.
    """
    iso = os.environ.get("APIFY_TIMEOUT_AT") or os.environ.get("ACTOR_TIMEOUT_AT")
    if not iso:
        return None
    try:
        deadline = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return deadline - timedelta(seconds=_DEADLINE_MARGIN_SECS)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class ConsentWallError(RuntimeError):
    """Seznam's CMP consent wall intercepted a request.

    The hardcoded consent cookies have rotated, so EVERY request will fail the
    same way — fail the whole run loudly (sold-detection then ignores the run)
    instead of quietly completing with zero listings. Never caught by the
    scraper's broad error handlers.
    """


class _SliceError(RuntimeError):
    """A result page was served, but not the one requested (filter dropped etc.)."""


# Bounded exponential backoff for transient fetch failures (timeouts, resets,
# 429/5xx). A single flaky response used to abort an entire category scrape.
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def _request_with_retry(client, method, url, attempts=3, **kwargs):
    """Issue a request, retrying transient failures with exponential backoff.

    Returns the final response without raising for status (callers keep their
    own raise_for_status()); re-raises the last transport error if the
    connection itself keeps failing.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except TransportError as e:
            if attempt == attempts:
                raise
            reason = repr(e)
        else:
            if response.status_code not in _RETRYABLE_STATUS or attempt == attempts:
                return response
            reason = f"HTTP {response.status_code}"
        delay = 2.0 * (2 ** (attempt - 1))
        Actor.log.warning(
            f"Transient fetch failure ({reason}); retry {attempt}/{attempts - 1} "
            f"in {delay:.0f}s: {url}"
        )
        await asyncio.sleep(delay)


class _FailRunOnCrash:
    """Marks the actor_runs row 'failed' if the run dies with an unhandled error.

    Historically a terminal status was written only on the happy path, so a crash
    (or an Apify hard-timeout kill) left the row 'running' forever and the web app
    could not tell a dead run from a live one — sold-detection then mistreated the
    run's listings. Used as `async with Actor, _FailRunOnCrash():` so it exits
    before Actor; the exception still propagates and fails the platform run too.
    `saved` is kept up to date by main() so the row records what was inserted.
    """

    def __init__(self):
        self.saved = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                Actor.log.error(f"Run failed with unhandled error: {exc!r}")
                db_manager.update_actor_run_status("failed", self.saved)
                db_manager.close_pool()
            except Exception as finalize_error:
                Actor.log.error(f"Could not mark run failed: {finalize_error}")
            try:
                await Actor.set_status_message(f"Stopped early: {exc}")
            except Exception as message_error:
                Actor.log.warning(f"Could not set the run's status message: {message_error}")
        return False


class _SearchPage:
    """One parsed result page."""

    def __init__(self, cards: List[Dict[str, Any]], n_offers: int,
                 pagination: Dict[str, Any], filters: Dict[str, Any]):
        self.cards = cards
        # Offer cards on the page (numeric data-offer-id), parsed or not: a card
        # that fails to parse must not make a full page look like the last one.
        self.n_offers = n_offers
        self.filters = filters
        self.total = _as_int(pagination.get("total"))
        reachable = _as_int(pagination.get("totalWithUnpacked"))
        self.reachable = reachable if reachable is not None else self.total
        per_page = _as_int(pagination.get("offset"))
        self.per_page = per_page if per_page and per_page > 0 else LISTINGS_PER_PAGE

    @property
    def too_big(self) -> bool:
        """The site pages through only `reachable` of the slice's `total` offers."""
        return (self.total is not None and self.reachable is not None
                and self.total > self.reachable)

    @property
    def last_page(self) -> Optional[int]:
        if self.total is None:
            return None
        return max(1, math.ceil(min(self.total, self.reachable or self.total) / self.per_page))


class SbazarScraper:
    """Main scraper class for Sbazar.cz listings."""

    def __init__(self, client: AsyncClient):
        self.client = client
        self.scraped_listings: set[str] = set()
        self._session_warmed = False
        # UTC instant to stop scraping by (set from the Apify timeout in main()).
        self.deadline: Optional[datetime] = None
        # Why this run is incomplete (paging stopped early, a slice was too big for
        # the site's paging window, the time budget ran out); main() adds its own
        # reasons and marks such a run 'failed'.
        self.incomplete_reasons: List[str] = []
        # Detail pages that still failed after retries (saved from their card).
        self.detail_failures = 0
        # Request pacing (event-loop times): when the last detail request started,
        # and when the next listing page may start.
        self._detail_pace_lock = asyncio.Lock()
        self._last_detail_start = float("-inf")
        self._next_listing_start = 0.0
        # Category tree (seoName -> child seoNames), read from the first result page.
        self._children: Optional[Dict[str, List[str]]] = None

    def _past_deadline(self) -> bool:
        return self.deadline is not None and _now_utc() >= self.deadline

    async def _fetch_page(self, url: str) -> "httpx.Response":
        """Fetch a page by manually following the Seznam redirect chain.

        A fresh session goes through a consent chain once:
        sbazar → login.szn.cz/autologin → sbazar?noredirect=1 → sbazar →
        bcr.iva.seznam.cz → sbazar?cwtkn=... → sbazar (200); the cookies it sets
        let later requests through directly. Raises ConsentWallError if the chain
        reaches cmp.seznam.cz, as that means the consent flow failed.
        """
        for hop in range(20):
            response = await _request_with_retry(self.client, "GET", url, follow_redirects=False)

            # Got a real page — return it
            if response.status_code == 200:
                return response

            # Not a redirect — return as-is
            if response.status_code not in (301, 302, 303, 307, 308):
                return response

            location = response.headers.get("location", "")
            if not location:
                return response

            # Resolve relative URLs
            if location.startswith("/"):
                parsed = urlparse(str(response.url))
                location = f"{parsed.scheme}://{parsed.netloc}{location}"

            # Stop at CMP consent page — consent flow failed
            if "cmp.seznam.cz" in location:
                raise ConsentWallError(
                    f"Redirect chain reached the CMP consent wall after {hop + 1} "
                    f"hops — consent cookies rotated; update the consent flow"
                )

            url = location

        Actor.log.warning(f"Redirect chain exhausted after 20 hops")
        return response

    def _set_consent_cookies(self):
        """Set Seznam.cz CMP consent cookies to bypass the consent redirect.

        Without these cookies, every request to sbazar.cz redirects through
        bcr.iva.seznam.cz → cmp.seznam.cz (consent management page).
        We set a valid euconsent-v2 (IAB TCF v2.0) cookie to skip this.
        """
        # szncmpone=1 is the Seznam.cz "consent given" flag
        self.client.cookies.set("szncmpone", "1", domain=".seznam.cz")
        self.client.cookies.set("szncmpone", "1", domain=".sbazar.cz")

        # euconsent-v2 is the IAB TCF v2.0 consent string.
        # This is a real consent string captured from the Seznam.cz CMP
        # with all purposes and vendors accepted. Valid for 13 months.
        euconsent = (
            "CQgzIcAQgzIcAD3ACQCSCUFsAP_gAEPgAATIJNQJgAFAAQAAqABkAEAAKAAZAA0"
            "ACSAEwAJwAWwAvwBhAGIAQEAggCEAEUAI4ATgAoQBxADuAIQAUgA04COgE2gKkAV"
            "kAtwBeYDGQGWAMuAf4BAcCMwEmgSrgKgAVABAADIAGgATAAxAB-AEIAI4ATgA7gCE"
            "AEWATaAqQBWQC3AF5gMsAZcBKsAA.IJVwKgAFAAQAAqABkAEAAKAAZAA0ACSAEwA"
            "JwAWwAvwBhAGIAPwAgIBBAEIAIoARwAnABQgDNAHEAO4AhABFgCkAGnAR0Am0BUg"
            "CsgFuALzAYyAywBlwD_AIDgRmAk0BKsAA.YAAAAAAAAWAA"
        )
        self.client.cookies.set("euconsent-v2", euconsent, domain=".seznam.cz")
        self.client.cookies.set("euconsent-v2", euconsent, domain=".sbazar.cz")

    async def _warm_session(self):
        """Warm up the HTTP session with consent cookies + autologin chain.

        Sets consent cookies first, then follows the autologin redirect
        chain to accumulate session cookies from login.szn.cz. A consent wall
        fails the run (ConsentWallError propagates); other errors are only
        logged, as every later request follows the chain on its own.
        """
        if self._session_warmed:
            return

        Actor.log.info("Warming up session...")

        # Set consent cookies BEFORE any requests
        self._set_consent_cookies()

        try:
            response = await self._fetch_page(f"{BASE_URL}/")
            Actor.log.info(
                f"Warmup result: status={response.status_code} "
                f"url={response.url} size={len(response.content)}"
            )

            # Log cookies for debugging
            cookie_names = [f"{c.name}={c.domain}" for c in self.client.cookies.jar]
            Actor.log.info(f"Cookies after warmup: {cookie_names}")
        except ConsentWallError:
            raise
        except Exception as e:
            Actor.log.warning(f"Session warmup failed: {e}")
        self._session_warmed = True

    # ------------------------------------------------------------------ paging

    def _build_category_url(
        self,
        category: str,
        page_number: int,
        search_query: Optional[str] = None,
        location: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
    ) -> str:
        """Build a result-page URL with filters and pagination.

        URL patterns (checked live):
        - Page 2+:  /{category}/{locality}/{price}/nejnovejsi/{page}, where
          locality is 'cela-cr' (whole country) or the place's ASCII slug, and
          price is 'cena-neomezena' or 'cena-od-{from}-do-{to}-kc'.
        - Page 1:   the same path without its default trailing segments
          (/{category}, /{category}/{locality}, /{category}/{locality}/{price});
          the site 301-redirects the long form there.
        - Search within category: /hledej/{query}/{category} + the same suffix.
        Filters apply on every page, page 1 included.
        """
        if search_query:
            base = f"{BASE_URL}/hledej/{quote(search_query)}/{category}"
        else:
            base = f"{BASE_URL}/{category}"

        location_slug = _slugify(location) if location else "cela-cr"
        price_slug = _price_slug(price_min, price_max)

        if page_number == 1:
            if price_slug == "cena-neomezena":
                return base if location_slug == "cela-cr" else f"{base}/{location_slug}"
            return f"{base}/{location_slug}/{price_slug}"
        return f"{base}/{location_slug}/{price_slug}/nejnovejsi/{page_number}"

    async def _pace_listing_request(self) -> None:
        """Wait for this listing page's start slot, LISTING_PAGE_INTERVAL_SECS apart."""
        loop = asyncio.get_running_loop()
        start = max(loop.time(), self._next_listing_start)
        self._next_listing_start = start + LISTING_PAGE_INTERVAL_SECS
        while loop.time() < start:  # asyncio may wake a timer up to ~1 ms early
            await asyncio.sleep(start - loop.time())

    async def _get_search_page(
        self,
        url: str,
        category: str,
        slice_category: str,
        page_number: int,
        location: Optional[str],
        price_lo: Optional[int],
        price_hi: Optional[int],
    ) -> _SearchPage:
        """Fetch + parse one result page and check the site served what was asked.

        Raises HTTPStatusError for an error status, _SliceError when the page's own
        filter state shows another category/page/location/price than requested
        (the site 301-redirects unknown filter slugs to the unfiltered listing).
        """
        await self._pace_listing_request()
        Actor.log.info(f"Scraping page {page_number}: {url}")
        response = await self._fetch_page(url)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "lxml")
        page = self._parse_search_page(soup, category)
        problem = self._filter_mismatch(
            page.filters, slice_category, page_number, location, price_lo, price_hi
        )
        if problem:
            raise _SliceError(f"{problem}; served {response.url}")
        return page

    def _parse_search_page(self, soup: BeautifulSoup, category: str) -> _SearchPage:
        listing_props = _island_props(soup, "SearchPageList") or {}
        offers = listing_props.get("offers") if isinstance(listing_props.get("offers"), dict) else {}
        results = offers.get("results") if isinstance(offers.get("results"), list) else []
        pagination = offers.get("pagination") if isinstance(offers.get("pagination"), dict) else {}
        info_props = _island_props(soup, "SearchInfo") or {}
        filters = info_props.get("ssrFilters") if isinstance(info_props.get("ssrFilters"), dict) else {}
        if self._children is None:
            self._children = self._read_category_tree(soup)
        island_offers = {
            str(offer.get("id")): offer for offer in results if isinstance(offer, dict)
        }
        cards = self._extract_listings_from_page(soup, category, island_offers)
        n_offers = sum(
            1 for li in soup.find_all("li", attrs={"data-offer-id": True})
            if str(li.get("data-offer-id")).isdigit()
        )
        return _SearchPage(cards, n_offers, pagination, filters)

    @staticmethod
    def _read_category_tree(soup: BeautifulSoup) -> Optional[Dict[str, List[str]]]:
        """seoName -> child seoNames, from the category tree embedded in the page."""
        nodes: Any = None
        script = soup.find("script", id="memoizedCategories")
        if script and script.string:
            try:
                nodes = json.loads(script.string)
            except json.JSONDecodeError:
                nodes = None
        if not isinstance(nodes, list):
            search_props = _island_props(soup, "SearchIsland") or {}
            nodes = search_props.get("ssrAllCategories")
        if not isinstance(nodes, list) or not nodes:
            return None
        by_id = {n.get("id"): n for n in nodes if isinstance(n, dict) and n.get("seoName")}
        children: Dict[str, List[str]] = {n["seoName"]: [] for n in by_id.values()}
        for node in by_id.values():
            parent = by_id.get(node.get("parentId"))
            if parent:
                children[parent["seoName"]].append(node["seoName"])
        return children

    @staticmethod
    def _filter_mismatch(
        filters: Dict[str, Any],
        slice_category: str,
        page_number: int,
        location: Optional[str],
        price_lo: Optional[int],
        price_hi: Optional[int],
    ) -> Optional[str]:
        """Why the page's own filter state differs from the request (None if it matches)."""
        if not filters:
            return None  # page doesn't expose its filter state; nothing to check
        category = filters.get("category") if isinstance(filters.get("category"), dict) else {}
        served_category = category.get("seoName")
        if served_category and served_category != slice_category:
            return f"the site served category {served_category} instead of {slice_category}"
        page = filters.get("page") if isinstance(filters.get("page"), dict) else {}
        served_page = _as_int(page.get("value"))
        if served_page is not None and served_page != page_number:
            return f"the site served page {served_page} instead of page {page_number}"
        locality = filters.get("locality") if isinstance(filters.get("locality"), dict) else {}
        wanted_locality = _slugify(location) if location else "cela-cr"
        served_locality = locality.get("seoName")
        if served_locality and served_locality != wanted_locality:
            return f"the site ignored the location filter '{location}' ({served_locality})"
        price = filters.get("price") if isinstance(filters.get("price"), dict) else {}
        if price and (price_lo is not None or price_hi is not None):
            want = (0 if price_lo is None else price_lo,
                    PRICE_CEILING if price_hi is None else price_hi)
            served_from = _as_int(price.get("from"))
            served_to = _as_int(price.get("to"))
            served = (served_from or 0, PRICE_CEILING if served_to is None else served_to)
            if served != want:
                return f"the site ignored the price filter {want} ({price.get('seoName')})"
        return None

    async def iter_category(
        self,
        category: str,
        max_listings: int = 0,
        search_query: Optional[str] = None,
        location: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """Yield a category's new listings, one batch per slice it is scraped in.

        The site pages through at most ~10,000 results of any list (172 pages).
        A slice whose total exceeds what the site will page through is split into
        its subcategories, and then into price bands, until every slice fits.
        Anything that still cannot be reached, and every early stop, is recorded
        in self.incomplete_reasons. Stopping at max_listings is a normal end.
        """
        Actor.log.info(f"Starting to scrape category: {category}")

        # Ensure session cookies are set before scraping
        await self._warm_session()

        collected = 0
        probes = 0
        todo = deque([(category, price_min, price_max, category)])
        while todo:
            if max_listings > 0 and collected >= max_listings:
                Actor.log.info(f"Reached maximum listings limit ({max_listings}) for {category}")
                return
            if self._past_deadline():
                Actor.log.warning(f"Run time budget reached while scraping {category}")
                self.incomplete_reasons.append(
                    f"{category}: run time budget reached with {len(todo)} slice(s) "
                    f"not scraped"
                )
                return
            probes += 1
            if probes > MAX_SLICE_PROBES:
                self.incomplete_reasons.append(
                    f"{category}: gave up after {MAX_SLICE_PROBES} slices; "
                    f"{len(todo)} slice(s) not scraped"
                )
                return

            slice_category, lo, hi, label = todo.popleft()
            url = self._build_category_url(slice_category, 1, search_query, location, lo, hi)
            try:
                page = await self._get_search_page(
                    url, category, slice_category, 1, location, lo, hi
                )
            except ConsentWallError:
                raise
            except HTTPStatusError as e:
                Actor.log.error(f"HTTP error while scraping {url}: {e}")
                self.incomplete_reasons.append(
                    f"{label}: page 1 returned HTTP {e.response.status_code}"
                )
                continue
            except _SliceError as e:
                Actor.log.error(f"Unexpected page for {url}: {e}")
                self.incomplete_reasons.append(f"{label}: page 1: {e}")
                continue
            except Exception as e:
                Actor.log.error(f"Error while scraping {url}: {e}")
                self.incomplete_reasons.append(f"{label}: page 1 failed: {e!r}")
                continue

            remaining = max_listings - collected if max_listings > 0 else 0
            capped_fits = remaining > 0 and page.reachable is not None and remaining <= page.reachable
            if page.too_big and not capped_fits:
                split, reason = await self._split_slice(
                    category, slice_category, lo, hi, price_min, price_max, label, page,
                    search_query, location,
                )
                if split:
                    todo.extendleft(reversed(split))
                    continue
                self.incomplete_reasons.append(
                    reason
                    or f"{label}: has {page.total} offers but the site pages through "
                    f"only {page.reachable}"
                )

            listings = await self._scrape_slice(
                page, url, category, slice_category, label, search_query,
                location, lo, hi, remaining,
            )
            collected += len(listings)
            if listings:
                yield listings

    async def _split_slice(
        self,
        category: str,
        slice_category: str,
        lo: Optional[int],
        hi: Optional[int],
        user_lo: Optional[int],
        user_hi: Optional[int],
        label: str,
        page: _SearchPage,
        search_query: Optional[str],
        location: Optional[str],
    ) -> Tuple[List[Tuple[str, Optional[int], Optional[int], str]], Optional[str]]:
        """Smaller slices covering a slice that's too big for the paging window.

        Subcategories first (they partition their parent exactly), then price
        bands. Returns ([], reason) when the slice can't be split further.
        """
        children = (self._children or {}).get(slice_category) or []
        if children and lo == user_lo and hi == user_hi:
            Actor.log.info(
                f"{label}: {page.total} offers exceed the {page.reachable} the site pages "
                f"through; splitting into {len(children)} subcategories"
            )
            root = label.split(" > ")[0]
            return [(child, lo, hi, f"{root} > {child}") for child in children], None

        if lo == user_lo and hi == user_hi:
            # Every price band also returns all of the slice's "Dohodou" (price by
            # agreement) offers, so no band gets smaller than their count, and each
            # band re-reads them. Past half the paging window that stops paying off.
            by_agreement = await self._count_price_by_agreement(
                category, slice_category, search_query, location
            )
            if by_agreement is not None and 2 * by_agreement > (page.reachable or 0):
                return [], (
                    f"{label}: has {page.total} offers but the site pages through only "
                    f"{page.reachable}; its {by_agreement} 'Dohodou' offers come with "
                    f"every price band, so price bands can't split it"
                )

        band = _split_band(0 if lo is None else lo, PRICE_CEILING if hi is None else hi)
        if not band:
            return [], None
        Actor.log.info(
            f"{label}: {page.total} offers exceed the {page.reachable} the site pages "
            f"through; splitting into price bands {band[0]} and {band[1]}"
        )
        base_label = label.split(" [")[0]
        return [
            (slice_category, b_lo, b_hi, f"{base_label} [{b_lo}-{b_hi} Kč]")
            for b_lo, b_hi in band
        ], None

    async def _count_price_by_agreement(
        self,
        category: str,
        slice_category: str,
        search_query: Optional[str],
        location: Optional[str],
    ) -> Optional[int]:
        """Total of a price band nothing is priced in = the slice's 'Dohodou' offers."""
        url = self._build_category_url(
            slice_category, 1, search_query, location, PRICE_CEILING, PRICE_CEILING
        )
        try:
            page = await self._get_search_page(
                url, category, slice_category, 1, location, PRICE_CEILING, PRICE_CEILING
            )
        except ConsentWallError:
            raise
        except Exception as e:
            Actor.log.warning(f"Could not count the 'Dohodou' offers of {slice_category}: {e}")
            return None
        return page.total

    async def _scrape_slice(
        self,
        first_page: _SearchPage,
        first_url: str,
        category: str,
        slice_category: str,
        label: str,
        search_query: Optional[str],
        location: Optional[str],
        lo: Optional[int],
        hi: Optional[int],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Page through one slice (its first page already fetched).

        Natural ends: a short page (fewer offers than a full page), an empty first
        page of an empty result, a 404 past the last page the totals promise, or
        `limit` reached. Everything else that stops paging is an incomplete reason.
        """
        listings: List[Dict[str, Any]] = []
        page = first_page
        page_number = 1
        url = first_url
        unparsed = 0
        while True:
            if not page.cards:
                if page_number == 1 and page.total == 0 and not page.n_offers:
                    Actor.log.info(f"No listings for {label}")
                else:
                    Actor.log.error(f"No listing cards on page {page_number}: {url}")
                    self.incomplete_reasons.append(
                        f"{label}: page {page_number} had no listing cards"
                    )
                break
            if len(page.cards) < page.n_offers:
                unparsed += page.n_offers - len(page.cards)
                Actor.log.warning(
                    f"{page.n_offers - len(page.cards)} of {page.n_offers} listing cards "
                    f"on page {page_number} could not be parsed: {url}"
                )

            new = [c for c in page.cards if c["id"] not in self.scraped_listings]
            for card in new:
                self.scraped_listings.add(card["id"])
            listings.extend(new)
            Actor.log.info(
                f"Extracted {len(new)} new listings from page {page_number} "
                f"(total so far: {len(listings)})"
            )

            if limit > 0 and len(listings) >= limit:
                for extra in listings[limit:]:
                    self.scraped_listings.discard(extra["id"])
                listings = listings[:limit]
                Actor.log.info(f"Reached maximum listings limit for {label}")
                break
            if page.n_offers < page.per_page:
                break  # a short page is the last page
            last_page = page.last_page
            if self._past_deadline():
                Actor.log.warning(
                    f"Run time budget reached while paging {label}; "
                    f"stopping at {len(listings)} listings"
                )
                self.incomplete_reasons.append(
                    f"{label}: run time budget reached before page {page_number + 1}"
                )
                break

            page_number += 1
            url = self._build_category_url(slice_category, page_number, search_query, location, lo, hi)
            try:
                page = await self._get_search_page(
                    url, category, slice_category, page_number, location, lo, hi
                )
            except ConsentWallError:
                raise
            except HTTPStatusError as e:
                status = e.response.status_code
                if status == 404 and last_page is not None and page_number > last_page:
                    break  # the site 404s past the last page
                Actor.log.error(f"HTTP error while scraping {url}: {e}")
                self.incomplete_reasons.append(
                    f"{label}: page {page_number} returned HTTP {status}"
                )
                break
            except _SliceError as e:
                Actor.log.error(f"Unexpected page for {url}: {e}")
                self.incomplete_reasons.append(f"{label}: page {page_number}: {e}")
                break
            except Exception as e:
                Actor.log.error(f"Error while scraping {url}: {e}")
                self.incomplete_reasons.append(f"{label}: page {page_number} failed: {e!r}")
                break

        if unparsed:
            self.incomplete_reasons.append(
                f"{label}: {unparsed} listing card(s) could not be parsed"
            )
        Actor.log.info(
            f"Scraped {len(listings)} listings from {label} across {page_number} pages"
        )
        return listings

    # ------------------------------------------------------------------ cards

    def _extract_listings_from_page(
        self,
        soup: BeautifulSoup,
        category: str,
        island_offers: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Extract every offer card on a result page (deduplication is the caller's).

        Sbazar listings are <li data-offer-id> elements; the e-shop ad tiles
        mixed in have hex ids and /rozbalena-nabidka/ links and are skipped.
        """
        listings = []
        for element in soup.find_all("li", attrs={"data-offer-id": True}):
            try:
                listing = self._extract_listing_data(element, category, island_offers or {})
            except Exception as e:
                Actor.log.warning(f"Error extracting listing: {e}")
                continue
            if listing:
                listings.append(listing)
        return listings

    def _extract_listing_data(
        self,
        element: BeautifulSoup,
        category: str,
        island_offers: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Extract data from a single listing <li> element.

        The page's structured offer data (the SearchPageList island) fills what
        the card's HTML lacks: date, district/region, and fallbacks.
        """
        offer_id = element.get("data-offer-id")
        if not offer_id or not str(offer_id).isdigit():
            return None

        link = element.find("a", href=re.compile(r"/inzerat/"))
        if not link:
            return None
        url = f"{BASE_URL}{link.get('href', '').split('?')[0]}"
        offer = (island_offers or {}).get(str(offer_id)) or {}
        locality = offer.get("locality") if isinstance(offer.get("locality"), dict) else {}

        # Title - text inside the red-colored div within the link
        title_div = element.find("div", class_=re.compile(r"text-red"))
        title = _clean_text(title_div.get_text()) if title_div else ""
        if not title:
            title = _clean_text(offer.get("name") or "")

        # Price - bold text element ("7 000 Kč", "Dohodou")
        price_el = element.find("b", class_=re.compile(r"text-neutral-black"))
        price_text = _clean_text(price_el.get_text()) if price_el else ""
        price = _parse_price(price_text)
        if not price_text and offer:
            if offer.get("priceByAgreement"):
                price_text = "Dohodou"
            elif _as_int(offer.get("price")) is not None:
                price = _as_int(offer.get("price"))
                price_text = _format_price(price)

        # Location - the span reading "v\xa0{place}"
        location = ""
        for span in element.find_all("span", class_=re.compile(r"text-dark-blue-60")):
            text = _clean_text(span.get_text())
            if _LOCATION_PREFIX_RE.match(text):
                location = _strip_location_prefix(text)
                break
        if not location:
            location = _clean_text(locality.get("municipality") or "")

        # Image - the card <img> carries only a srcset
        image_url = ""
        img = element.find("img")
        if img:
            image_url = _best_srcset_url(img.get("srcset")) or _abs_url(img.get("src"))
        if not image_url and isinstance(offer.get("images"), list) and offer["images"]:
            first = offer["images"][0]
            if isinstance(first, dict):
                image_url = _image_url(first.get("url"))

        # TOP status - check for "Top" badge
        is_top = False
        top_badge = element.find("div", class_=re.compile(r"bg-red"))
        if top_badge and "Top" in top_badge.get_text():
            is_top = True
        if offer.get("topped") is True:
            is_top = True

        return {
            "id": str(offer_id),
            "title": title,
            "url": url,
            "category": category,
            "price": price,
            "price_text": price_text,
            "description": "",  # filled from the detail page's description
            "location": location,
            "coordinates_lat": None,
            "coordinates_lng": None,
            "views": 0,  # not shown on the site
            "date": str(offer.get("editDate") or offer.get("createDate") or ""),
            "is_top": is_top,
            "image_url": image_url,
            "district": _clean_text(locality.get("district") or ""),
            "region": _clean_text(locality.get("region") or ""),
            "_municipality": _clean_text(locality.get("municipality") or ""),
            "scraped_at": datetime.now().isoformat(),
        }

    # ---------------------------------------------------------------- details

    async def scrape_details(
        self, category: str, listings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Merge detail-page data into listings, keeping their order.

        Up to DETAIL_CONCURRENCY detail pages are in flight at once. Once the run's
        time budget is spent the remaining listings keep their card data, so we
        still save what we have, and the run is recorded as incomplete. A consent
        wall cancels the remaining fetches and fails the run.
        """
        semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)
        done = 0
        skipped = 0

        async def enrich(listing: Dict[str, Any]) -> Dict[str, Any]:
            nonlocal done, skipped
            async with semaphore:
                if self._past_deadline():
                    skipped += 1
                    return listing
                await self._pace_detail_request()
                detailed = await self.scrape_detailed_data(listing)
            done += 1
            if done % 50 == 0:
                Actor.log.info(f"Scraped detail {done}/{len(listings)}")
            return detailed

        tasks = [asyncio.ensure_future(enrich(item)) for item in listings]
        try:
            detailed_listings = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if skipped:
            Actor.log.warning(
                f"Run time budget reached mid-detail; {skipped} of {len(listings)} "
                f"listings saved card-only"
            )
            self.incomplete_reasons.append(
                f"{category}: run time budget reached; {skipped} of "
                f"{len(listings)} detail pages not fetched"
            )
        return list(detailed_listings)

    async def _pace_detail_request(self) -> None:
        """Hold a detail request until DETAIL_MIN_INTERVAL_SECS after the previous one started.

        Measured from when the previous request was actually released, so a late
        wake-up (the event loop busy parsing a page) can't bunch two requests up.
        """
        async with self._detail_pace_lock:
            loop = asyncio.get_running_loop()
            while (wait := self._last_detail_start + DETAIL_MIN_INTERVAL_SECS - loop.time()) > 0:
                await asyncio.sleep(wait)
            self._last_detail_start = loop.time()

    async def scrape_detailed_data(self, listing: Dict[str, Any]) -> Dict[str, Any]:
        """Fetch and merge detail-page data for a single listing.

        If the page still fails after retries (or isn't this listing's page) the
        listing keeps its card data; the failure is counted (self.detail_failures),
        not treated as an early stop. A consent wall propagates.
        """
        try:
            response = await self._fetch_page(listing["url"])
            response.raise_for_status()
            soup = BeautifulSoup(response.content, "lxml")
            details = self._extract_detailed_data(soup, listing["id"], str(response.url))
            if not details:
                raise RuntimeError("no listing data on the page")
        except ConsentWallError:
            raise
        except Exception as e:
            self.detail_failures += 1
            Actor.log.warning(f"Error scraping detailed data for {listing['url']}: {e}")
            return listing

        merged = {**listing}
        for key, value in details.items():
            if value is not None and value != "" and value != []:
                merged[key] = value
        return merged

    def _extract_detailed_data(
        self, soup: BeautifulSoup, offer_id: str = "", page_url: str = ""
    ) -> Dict[str, Any]:
        """Extract detailed information from a listing detail page.

        Sources, lowest to highest precedence: the HTML itself, the schema.org
        Product JSON-LD (absent for "Dohodou" listings), and the page's structured
        offer data (OfferIsland's ssrOffer: full description, every photo, seller,
        place). Empty values never override.
        """
        details: Dict[str, Any] = {}
        sources = (
            self._extract_html_fallback(soup, offer_id, page_url),
            self._extract_json_ld(soup, offer_id) or {},
            self._extract_island_offer(soup, offer_id) or {},
        )
        for source in sources:
            for key, value in source.items():
                if value is not None and value != "" and value != []:
                    details[key] = value
        return details

    def _extract_html_fallback(
        self, soup: BeautifulSoup, offer_id: str, page_url: str
    ) -> Dict[str, Any]:
        """Fields read straight from the detail page's HTML (only if it is this listing's page)."""
        og_url = soup.find("meta", attrs={"property": "og:url"})
        own_page = f"/inzerat/{offer_id}" in (og_url.get("content", "") if og_url else "")
        if offer_id and not own_page and f"/inzerat/{offer_id}" not in page_url:
            return {}

        details: Dict[str, Any] = {}
        h1 = soup.find("h1")
        if h1:
            details["title"] = _clean_text(h1.get_text())

        desc = soup.find("div", class_=re.compile(r"whitespace-pre-wrap"))
        if desc:
            text = desc.get_text().strip()
            if text:
                details["full_description"] = text

        price_el = soup.find(class_=re.compile(r"(^|\s)price(\s|$)"))
        if price_el:
            price_text = _clean_text(price_el.get_text())
            if price_text:
                details["price_text"] = price_text[:100]
                details["price"] = _parse_price(price_text)

        for span in soup.find_all("span", class_=re.compile(r"text-dark-blue-60.*font-medium")):
            text = _clean_text(span.get_text())
            if _LOCATION_PREFIX_RE.match(text):
                details["location"] = _strip_location_prefix(text)
                break

        time_el = soup.find("time", attrs={"datetime": True})
        if time_el:
            details["date"] = time_el.get("datetime", "")

        # Seller: the profile link carrying the name (the first one is the avatar)
        for link in soup.find_all("a", href=re.compile(r"/bazar/")):
            name = _clean_text(link.get_text())
            if name and not name.lower().startswith("všechny nabídky"):
                details["contact_name"] = name
                break

        # Gallery photos (only the first is rendered server-side); skip the
        # other offers' cards further down the page.
        images: List[str] = []
        for img in soup.find_all("img"):
            if "group-hover/card" in " ".join(img.get("class") or []):
                continue
            if img.find_parent("a", href=re.compile(r"/inzerat/")):
                continue
            url = _best_srcset_url(img.get("srcset")) or _abs_url(img.get("src"))
            if "sdn.cz" in url and "c_img" in url and url not in images:
                images.append(url)
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if not images and og_image and og_image.get("content"):
            images.append(_abs_url(og_image["content"]))
        if images:
            details["images"] = images
        return details

    def _extract_json_ld(self, soup: BeautifulSoup, offer_id: str = "") -> Optional[Dict[str, Any]]:
        """Extract listing data from JSON-LD structured data on detail pages.

        The site HTML-escapes text inside its JSON-LD (&quot;, &#39;), so string
        values are unescaped.
        """
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (json.JSONDecodeError, TypeError):
                continue
            candidates = data if isinstance(data, list) else [data]
            if isinstance(data, dict) and isinstance(data.get("@graph"), list):
                candidates = data["@graph"]
            for item in candidates:
                if isinstance(item, dict) and item.get("@type") == "Product":
                    return self._json_ld_product(item, offer_id)
        return None

    @staticmethod
    def _json_ld_product(data: Dict[str, Any], offer_id: str = "") -> Dict[str, Any]:
        def text(value: Any) -> str:
            return ihtml.unescape(value).strip() if isinstance(value, str) else ""

        offers = data.get("offers", [])
        if isinstance(offers, list) and offers:
            offer = offers[0] if isinstance(offers[0], dict) else {}
        elif isinstance(offers, dict):
            offer = offers
        else:
            offer = {}
        offer_url = offer.get("url") if isinstance(offer.get("url"), str) else ""
        if offer_id and offer_url and f"/inzerat/{offer_id}" not in offer_url:
            return {}  # another offer's data

        result: Dict[str, Any] = {}
        if text(data.get("name")):
            result["title"] = _clean_text(text(data.get("name")))
        if text(data.get("description")):
            result["full_description"] = text(data.get("description"))

        if offer.get("price") is not None:
            try:
                price = int(float(offer["price"]))
                if 0 <= price <= _PRICE_MAX:
                    result["price"] = price
            except (ValueError, TypeError):
                pass

        seller = offer.get("seller") if isinstance(offer.get("seller"), dict) else {}
        if text(seller.get("name")):
            result["contact_name"] = _clean_text(text(seller.get("name")))

        ld_images = data.get("image", [])
        if isinstance(ld_images, (dict, str)):
            ld_images = [ld_images]
        img_urls = []
        if isinstance(ld_images, list):
            for img in ld_images:
                if isinstance(img, dict):
                    img = img.get("contentUrl") or img.get("url")
                if isinstance(img, str) and img:
                    img_url = _abs_url(ihtml.unescape(img))
                    if img_url not in img_urls:
                        img_urls.append(img_url)
        if img_urls:
            result["images"] = img_urls
        return result

    @staticmethod
    def _extract_island_offer(soup: BeautifulSoup, offer_id: str) -> Optional[Dict[str, Any]]:
        """The listing as the page's own structured data has it (OfferIsland / DotOfferDetail)."""
        offer = None
        for component, key in (("OfferIsland", "ssrOffer"), ("DotOfferDetail", "offer")):
            props = _island_props(soup, component) or {}
            if isinstance(props.get(key), dict):
                offer = props[key]
                break
        if not offer or (offer_id and str(offer.get("id")) != str(offer_id)):
            return None

        result: Dict[str, Any] = {}
        if offer.get("name"):
            result["title"] = _clean_text(str(offer["name"]))
        if isinstance(offer.get("description"), str) and offer["description"].strip():
            result["full_description"] = offer["description"].strip()
        if offer.get("priceByAgreement"):
            result["price_text"] = "Dohodou"
        else:
            price = _as_int(offer.get("price"))
            if price is not None and 0 <= price <= _PRICE_MAX:
                result["price"] = price
        date = offer.get("editDate") or offer.get("createDate")
        if date:
            result["date"] = str(date)
        user = offer.get("user") if isinstance(offer.get("user"), dict) else {}
        service = user.get("userService") if isinstance(user.get("userService"), dict) else {}
        if service.get("shopName"):
            result["contact_name"] = _clean_text(str(service["shopName"]))
        images = []
        for image in offer.get("images") or []:
            if isinstance(image, dict) and image.get("url"):
                url = _image_url(image["url"])
                if url not in images:
                    images.append(url)
        if images:
            result["images"] = images
        locality = offer.get("locality") if isinstance(offer.get("locality"), dict) else {}
        if locality.get("municipality"):
            result["_municipality"] = _clean_text(locality["municipality"])
        if locality.get("district"):
            result["district"] = _clean_text(locality["district"])
        if locality.get("region"):
            result["region"] = _clean_text(locality["region"])
        if offer.get("topped") is True:
            result["is_top"] = True
        return result

    # ---------------------------------------------------------------- output

    def finalize_listing(self, listing: Dict[str, Any]) -> Dict[str, Any]:
        """Derive the remaining columns (after the detail merge) and fit them to the DB."""
        row = {k: v for k, v in listing.items() if not k.startswith("_")}
        if not row.get("location"):
            row["location"] = listing.get("_municipality") or ""
        if not row.get("description") and row.get("full_description"):
            row["description"] = _clean_text(row["full_description"])[:DESCRIPTION_PREVIEW_CHARS]
        if row.get("coordinates_lat") is None or row.get("coordinates_lng") is None:
            coords = _geocode(
                row.get("location") or "",
                listing.get("_municipality") or "",
                row.get("district") or "",
            )
            if coords:
                row["coordinates_lat"], row["coordinates_lng"] = coords
        if not row.get("image_url") and row.get("images"):
            row["image_url"] = row["images"][0]
        price = row.get("price")
        if row.get("price_text") == "Dohodou" or (
            price is not None and not (0 <= price <= _PRICE_MAX)
        ):
            row["price"] = None
        if not row.get("price_text") and row.get("price") is not None:
            row["price_text"] = _format_price(row["price"])
        row["price_text"] = (row.get("price_text") or "")[:100]
        row["category"] = (row.get("category") or "")[:50]
        row["date"] = (row.get("date") or "")[:100]
        if row.get("contact_name"):
            row["contact_name"] = row["contact_name"][:255]
        return row


async def main() -> None:
    """Main entry point for the Sbazar.cz scraper."""

    crash_guard = _FailRunOnCrash()
    async with Actor, crash_guard:
        # Get input configuration
        actor_input = await Actor.get_input() or {}

        # Extract configuration with proper defaults
        categories = actor_input.get("categories")
        if not categories or len(categories) == 0:
            categories = DEFAULT_CATEGORIES

        # Per category; 0 (the default) = unlimited, i.e. every listing.
        try:
            max_listings = max(0, int(actor_input.get("maxListings") or 0))
        except (TypeError, ValueError):
            max_listings = 0
        include_detailed_data = actor_input.get("includeDetailedData", True)
        search_query = actor_input.get("searchQuery") or None
        location = actor_input.get("location") or None
        price_min = actor_input.get("priceMin")
        price_max = actor_input.get("priceMax")
        price_min = None if price_min in (None, "") else int(price_min)
        price_max = None if price_max in (None, "") else int(price_max)

        # Get actor run information
        actor_run_id = os.environ.get("APIFY_ACTOR_RUN_ID") or os.environ.get(
            "ACTOR_RUN_ID"
        )
        if not actor_run_id:
            actor_run_id = f"local-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        actor_run_start = datetime.now()

        Actor.log.info(f"Starting Sbazar.cz scraper with categories: {categories}")
        Actor.log.info(
            f"Max listings per category: {max_listings} (0 = unlimited)"
        )
        Actor.log.info(f"Include detailed data: {include_detailed_data}")
        Actor.log.info(f"Actor Run ID: {actor_run_id}")
        if search_query:
            Actor.log.info(f"Search query: {search_query}")
        if location:
            Actor.log.info(f"Location filter: {location} ({_slugify(location)})")
        if price_min is not None or price_max is not None:
            Actor.log.info(
                f"Price range: {price_min or 0} - {price_max if price_max is not None else 'unlimited'} CZK"
            )

        # Initialize database connection
        try:
            scraper_name = os.environ.get("SCRAPER_NAME", "sbazar")
            db_manager.scraper_name = scraper_name

            db_manager.initialize_pool()
            db_manager.set_actor_run_info(actor_run_id, actor_run_start)

            db_manager.create_actor_run(
                categories=categories,
                max_listings=max_listings,
                search_query=search_query,
                location=location,
                price_min=price_min,
                price_max=price_max,
            )

            Actor.log.info("Database connection established and actor run created")
            db_manager_available = True

        except Exception as e:
            Actor.log.error(f"Failed to initialize database: {e}")
            Actor.log.warning("Continuing without database integration")
            db_manager_available = False

        # Create HTTP client with proper headers.
        # Sbazar.cz redirects first-time visitors through login.szn.cz/autologin
        # and the Seznam consent exchange; _fetch_page follows that chain.
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/webp,*/*;q=0.8",
            "Accept-Language": "cs,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        # Sbazar.cz (Seznam) returns HTTP 404 to datacenter / non-Czech IPs, so
        # requests must be routed through a Czech residential proxy. The proxy
        # input defaults to Apify Proxy (RESIDENTIAL, country CZ) — see
        # input_schema.json — so scheduled runs get it without extra config.
        proxy_configuration = await Actor.create_proxy_configuration(
            actor_proxy_input=actor_input.get("proxyConfiguration")
        )
        proxy_url = await proxy_configuration.new_url() if proxy_configuration else None
        if proxy_url:
            Actor.log.info("Routing requests through Apify Proxy")
        else:
            Actor.log.warning(
                "No proxy configured — Seznam blocks datacenter IPs and will "
                "return 404. Enable Apify Proxy (residential, Czechia)."
            )

        async with AsyncClient(
            headers=headers, timeout=30.0, proxy=proxy_url
        ) as client:
            scraper = SbazarScraper(client)
            scraper.deadline = _run_deadline()
            if scraper.deadline:
                Actor.log.info(f"Run time budget until (UTC): {scraper.deadline.isoformat()}")

            scraped = 0  # listings pushed to the dataset
            saved = 0  # rows actually inserted into the database
            # Run-level list of why the run is incomplete: the scraper records
            # paging/time-budget stops, the loop below category and DB failures.
            incomplete_reasons = scraper.incomplete_reasons

            # Scrape each category
            for category_index, category in enumerate(categories):
                try:
                    if scraper._past_deadline():
                        Actor.log.warning(
                            "Run time budget reached; stopping before remaining categories"
                        )
                        incomplete_reasons.append(
                            "run time budget reached before "
                            + ", ".join(categories[category_index:])
                        )
                        break

                    Actor.log.info(f"=== Starting category: {category} ===")

                    # Refresh database connection pool every few categories
                    if (
                        db_manager_available
                        and category_index > 0
                        and category_index % 3 == 0
                    ):
                        try:
                            Actor.log.info(
                                "Refreshing database connection pool for long-running operation"
                            )
                            db_manager.refresh_pool()
                        except Exception as e:
                            Actor.log.warning(
                                f"Failed to refresh connection pool: {e}"
                            )

                    async for batch in scraper.iter_category(
                        category=category,
                        max_listings=max_listings,
                        search_query=search_query,
                        location=location,
                        price_min=price_min,
                        price_max=price_max,
                    ):
                        if include_detailed_data:
                            Actor.log.info(
                                f"Scraping detailed data for {len(batch)} listings from {category}"
                            )
                            batch = await scraper.scrape_details(category, batch)
                        batch = [scraper.finalize_listing(item) for item in batch]

                        await Actor.push_data(batch)
                        scraped += len(batch)
                        Actor.log.info(
                            f"Saved {len(batch)} listings to Apify dataset from {category}"
                        )

                        if db_manager_available:
                            try:
                                db_manager.insert_listings(batch)
                                saved += len(batch)
                                Actor.log.info(
                                    f"Saved {len(batch)} listings to database from {category}"
                                )
                            except Exception as e:
                                Actor.log.error(
                                    f"Failed to save listings to database: {e}"
                                )
                                # Refresh the connection pool and retry once
                                try:
                                    db_manager.refresh_pool()
                                    db_manager.insert_listings(batch)
                                    saved += len(batch)
                                    Actor.log.info(
                                        f"Saved {len(batch)} listings to database after retry"
                                    )
                                except Exception as retry_e:
                                    Actor.log.error(
                                        f"Failed to save listings to database "
                                        f"even after retry: {retry_e}"
                                    )
                                    incomplete_reasons.append(
                                        f"{category}: database insert failed: {retry_e!r}"
                                    )
                        crash_guard.saved = saved

                    Actor.log.info(f"=== Completed category: {category} ===")

                except ConsentWallError:
                    raise
                except Exception as e:
                    Actor.log.error(f"Error scraping category {category}: {e}")
                    incomplete_reasons.append(f"{category}: {e!r}")
                    continue

            # Final summary and database cleanup
            Actor.log.info("=== SCRAPING COMPLETED ===")
            Actor.log.info(f"Total listings scraped: {scraped}")
            Actor.log.info(f"Categories processed: {len(categories)}")
            if include_detailed_data:
                Actor.log.info(
                    f"Detail pages failed after retries (saved from their card): "
                    f"{scraper.detail_failures}"
                )

            # Zero listings almost always means upstream breakage (site change,
            # consent wall, block) rather than an empty market — mark the run
            # 'failed' so sold-detection ignores it instead of flagging every
            # previously-seen listing as sold. The same goes for a run that
            # stopped early or could not reach part of a category: sold-detection
            # would flag everything it didn't reach.
            run_status = "failed" if incomplete_reasons or not saved else "completed"
            for reason in incomplete_reasons:
                Actor.log.error(f"Run incomplete — {reason}")

            # Update actor run status in database
            if db_manager_available:
                try:
                    db_manager.update_actor_run_status(run_status, saved)
                    db_manager.close_pool()
                    Actor.log.info("Database connection closed successfully")
                except Exception as e:
                    Actor.log.error(f"Failed to update actor run status: {e}")

            if incomplete_reasons:
                more = (
                    f" (+{len(incomplete_reasons) - 1} more)"
                    if len(incomplete_reasons) > 1
                    else ""
                )
                status_message = f"Stopped early: {incomplete_reasons[0]}{more}"
            else:
                status_message = (
                    f"Completed: {scraped} listings scraped "
                    f"from {len(categories)} categories"
                )
            try:
                await Actor.set_status_message(status_message)
            except Exception as e:
                Actor.log.warning(f"Could not set the run's status message: {e}")


if __name__ == "__main__":
    asyncio.run(main())
