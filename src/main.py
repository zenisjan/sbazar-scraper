"""
Sbazar.cz Scraper - Scraping tool for sbazar.cz classified ads (Seznam.cz marketplace).

This Actor scrapes listings from sbazar.cz across multiple categories and extracts
detailed information from individual listing pages with full pagination support.
Includes PostgreSQL database integration.
"""

from __future__ import annotations

import os
import asyncio
import json
import re
import sys
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, quote
from datetime import datetime

from apify import Actor
from bs4 import BeautifulSoup
from httpx import AsyncClient, HTTPStatusError

# Import our database manager - handle different import paths
try:
    from .database import db_manager
except ImportError:
    try:
        from database import db_manager
    except ImportError:
        # Add current directory to path if needed
        current_dir = os.path.dirname(os.path.abspath(__file__))
        if current_dir not in sys.path:
            sys.path.insert(0, current_dir)
        from database import db_manager

BASE_URL = "https://www.sbazar.cz"

# Default top-level categories to scrape when none specified.
# These are the main seoName values from sbazar's category taxonomy.
DEFAULT_CATEGORIES = [
    "1-auto-moto",
    "8-dum-byt-zahrada",
    "15-obleceni-obuv-doplnky",
    "27-sport",
    "28-zdravi-krasa",
    "29-hobby-volny-cas",
    "30-elektronika",
    "38-pc-notebooky-software",
    "42-mobily-a-komunikace",
    "45-audio-video-foto",
    "49-deti-a-miminka",
    "54-zvirata",
    "56-kultura-zabava",
    "58-knihy-ucebnice-casopisy",
    "60-hudebni-nastroje",
    "62-sberatelstvi",
    "64-stavebniny-nastroje",
    "66-stroje-a-pristroje",
    "68-kancelare-obchody",
    "70-ostatni",
]

# Listings per page on sbazar.cz (observed ~46)
LISTINGS_PER_PAGE = 46


class SbazarScraper:
    """Main scraper class for Sbazar.cz listings."""

    def __init__(self, client: AsyncClient):
        self.client = client
        self.scraped_listings: set[str] = set()
        self._session_warmed = False

    async def _warm_session(self):
        """Warm up the HTTP session by following the Seznam autologin redirect chain.

        Sbazar.cz redirects first-time visitors through a chain:
        sbazar → login.szn.cz/autologin → sbazar?noredirect=1 → bcr.iva.seznam.cz → cmp.seznam.cz

        We follow redirects manually to accumulate autologin cookies, then set
        the Seznam consent cookie (szncmpone) to bypass the CMP consent page.
        After this, subsequent requests return real content.
        """
        if self._session_warmed:
            return

        Actor.log.info("Warming up session (manual redirect chain + consent cookie)...")
        try:
            # Step 1: Manually follow the redirect chain to accumulate cookies
            # from login.szn.cz/autologin. Stop before reaching cmp.seznam.cz.
            url = f"{BASE_URL}/"
            for hop in range(15):
                response = await self.client.get(url, follow_redirects=False)
                Actor.log.info(
                    f"  Warmup hop {hop}: {response.status_code} "
                    f"url={response.url} size={len(response.content)}"
                )

                # If we got a 200 with substantial content, session is ready
                if response.status_code == 200 and len(response.content) > 30000:
                    Actor.log.info("Session warmed up (got content page directly)")
                    self._session_warmed = True
                    return

                if response.status_code not in (301, 302, 303, 307, 308):
                    break

                location = response.headers.get("location", "")
                if not location:
                    break

                # Resolve relative URLs
                if location.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(str(response.url))
                    location = f"{parsed.scheme}://{parsed.netloc}{location}"

                # Stop before CMP consent page — but DO follow bcr.iva.seznam.cz
                # as it may set consent cookies via Set-Cookie headers
                if "cmp.seznam.cz" in location:
                    Actor.log.info(f"  Reached CMP consent page, stopping chain")
                    break

                url = location

            # Step 2: Set szncmpone consent flag cookie.
            # The bcr.iva.seznam.cz hop should have set euconsent-v2 via
            # Set-Cookie headers. We add szncmpone as an additional flag.
            self.client.cookies.set("szncmpone", "1", domain=".seznam.cz")
            self.client.cookies.set("szncmpone", "1", domain=".sbazar.cz")
            Actor.log.info("Set szncmpone consent cookie")

            # Log all accumulated cookies for debugging
            cookie_names = [f"{c.name}={c.domain}" for c in self.client.cookies.jar]
            Actor.log.info(f"Cookies after warmup: {cookie_names}")

            # Step 3: Verify session works — request a category page
            response = await self.client.get(
                f"{BASE_URL}/27-sport", follow_redirects=True
            )
            Actor.log.info(
                f"Post-consent check: status={response.status_code} "
                f"url={response.url} size={len(response.content)}"
            )

            self._session_warmed = True

        except Exception as e:
            Actor.log.warning(f"Session warmup failed: {e}")
            self.client.cookies.set("szncmpone", "1", domain=".seznam.cz")
            self.client.cookies.set("szncmpone", "1", domain=".sbazar.cz")
            self._session_warmed = True

    async def scrape_category_listings(
        self,
        category: str,
        max_listings: int = 0,
        search_query: Optional[str] = None,
        location: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Scrape listings from a specific category with full pagination support."""

        listings: List[Dict[str, Any]] = []
        page_number = 1
        total_pages_scraped = 0

        Actor.log.info(f"Starting to scrape category: {category}")

        # Ensure session cookies are set before scraping
        await self._warm_session()

        while True:
            url = self._build_category_url(
                category, page_number, search_query, location, price_min, price_max
            )

            Actor.log.info(f"Scraping page {page_number}: {url}")

            try:
                response = await self.client.get(url, follow_redirects=True)
                response.raise_for_status()

                Actor.log.info(
                    f"Page response: status={response.status_code} "
                    f"url={response.url} size={len(response.content)}"
                )

                soup = BeautifulSoup(response.content, "lxml")

                # Extract listings from current page
                page_listings = self._extract_listings_from_page(soup, category)

                if not page_listings:
                    Actor.log.info(
                        f"No more listings found for category {category} on page {page_number}"
                    )
                    break

                listings.extend(page_listings)
                total_pages_scraped += 1

                Actor.log.info(
                    f"Extracted {len(page_listings)} listings from page {page_number} "
                    f"(total so far: {len(listings)})"
                )

                # Check if we've reached the maximum
                if max_listings > 0 and len(listings) >= max_listings:
                    listings = listings[:max_listings]
                    Actor.log.info(
                        f"Reached maximum listings limit ({max_listings}) "
                        f"after {total_pages_scraped} pages"
                    )
                    break

                # Check if there's a next page
                has_next = self._check_next_page(soup, page_number)
                if not has_next:
                    Actor.log.info(f"No next page found after page {page_number}")
                    break

                page_number += 1

                # Rate limiting between pages
                await asyncio.sleep(1)

            except HTTPStatusError as e:
                Actor.log.error(f"HTTP error while scraping {url}: {e}")
                break
            except Exception as e:
                Actor.log.error(f"Error while scraping {url}: {e}")
                break

        Actor.log.info(
            f"Scraped {len(listings)} listings from category {category} "
            f"across {total_pages_scraped} pages"
        )
        return listings

    def _build_category_url(
        self,
        category: str,
        page_number: int,
        search_query: Optional[str] = None,
        location: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
    ) -> str:
        """Build category listing URL with filters and pagination.

        URL patterns (discovered by testing):
        - Category browse:           /{category}
        - Search within category:    /hledej/{query}/{category}
        - Page 2+ adds suffix:       .../cela-cr/cena-neomezena/nejnovejsi/{page}

        IMPORTANT: Page 1 URLs must NOT include the /cela-cr/.../nejnovejsi
        suffix — sbazar 301-redirects those to the plain form, which breaks
        the session cookie chain and returns an empty page. Page 2+ works
        fine with the suffix (the pagination links on the page use it).
        """

        # Build the base path
        if search_query:
            base = f"{BASE_URL}/hledej/{quote(search_query)}/{category}"
        else:
            base = f"{BASE_URL}/{category}"

        # Page 1: use the plain URL (no filter suffix)
        if page_number == 1:
            return base

        # Page 2+: include the filter path for pagination
        location_slug = "cela-cr"
        if location:
            location_slug = location.lower().replace(" ", "-")

        if price_min and price_max:
            price_slug = f"cena-od-{price_min}-do-{price_max}"
        elif price_min:
            price_slug = f"cena-od-{price_min}"
        elif price_max:
            price_slug = f"cena-do-{price_max}"
        else:
            price_slug = "cena-neomezena"

        return f"{base}/{location_slug}/{price_slug}/nejnovejsi/{page_number}"

    def _extract_listings_from_page(
        self, soup: BeautifulSoup, category: str
    ) -> List[Dict[str, Any]]:
        """Extract listing data from a category page."""

        listings = []

        # Sbazar listings are <li> elements with data-offer-id attribute
        listing_elements = soup.find_all("li", attrs={"data-offer-id": True})

        for element in listing_elements:
            try:
                listing = self._extract_listing_data(element, category)
                if listing and listing["id"] not in self.scraped_listings:
                    listings.append(listing)
                    self.scraped_listings.add(listing["id"])
            except Exception as e:
                Actor.log.warning(f"Error extracting listing: {e}")
                continue

        return listings

    def _extract_listing_data(
        self, element: BeautifulSoup, category: str
    ) -> Optional[Dict[str, Any]]:
        """Extract data from a single listing <li> element."""

        # Offer ID from data attribute
        offer_id = element.get("data-offer-id")
        if not offer_id:
            return None

        # Link and URL
        link = element.find("a", href=re.compile(r"/inzerat/"))
        if not link:
            return None

        relative_url = link.get("href", "")
        # Strip query params (like ?lokalita=...) for clean URL
        clean_url = relative_url.split("?")[0]
        url = f"{BASE_URL}{clean_url}"

        # Title - text inside the red-colored div within the link
        title_div = element.find(
            "div", class_=re.compile(r"text-red")
        )
        title = title_div.get_text(strip=True) if title_div else ""

        # Price - bold text element
        price_el = element.find("b", class_=re.compile(r"text-neutral-black"))
        price_text = price_el.get_text(strip=True) if price_el else ""
        price = self._extract_price(price_text)

        # Location - span with location text (typically starts with "v " or "V ")
        location = ""
        location_spans = element.find_all(
            "span", class_=re.compile(r"text-dark-blue-60")
        )
        for span in location_spans:
            text = span.get_text(strip=True)
            if text.startswith("v ") or text.startswith("V "):
                location = text[2:]  # Strip the "v " prefix
                break

        # Image
        image_url = ""
        img = element.find("img")
        if img:
            src = img.get("src", "")
            if src:
                image_url = src if src.startswith("http") else f"https:{src}"

        # TOP status - check for "Top" badge
        is_top = False
        top_badge = element.find("div", class_=re.compile(r"bg-red"))
        if top_badge and "Top" in top_badge.get_text():
            is_top = True

        return {
            "id": str(offer_id),
            "title": title,
            "url": url,
            "category": category,
            "price": price,
            "price_text": price_text,
            "description": "",  # Only available on detail page
            "location": location,
            "views": 0,  # Not shown on listing cards
            "date": "",  # Only available on detail page
            "is_top": is_top,
            "image_url": image_url,
            "scraped_at": datetime.now().isoformat(),
        }

    async def scrape_detailed_data(self, listing: Dict[str, Any]) -> Dict[str, Any]:
        """Scrape detailed data from individual listing page.

        Extracts JSON-LD structured data, full description, seller info, and images.
        """

        try:
            response = await self.client.get(listing["url"], follow_redirects=True)
            response.raise_for_status()

            content_size = len(response.content)
            final_url = str(response.url)

            # Warn if we got redirected to CMP or got suspiciously small content
            if "cmp.seznam.cz" in final_url:
                Actor.log.warning(
                    f"Detail page redirected to CMP: {listing['url']} → {final_url}"
                )
                return listing
            if content_size < 5000:
                Actor.log.warning(
                    f"Detail page too small ({content_size}b): {listing['url']}"
                )
                return listing

            soup = BeautifulSoup(response.content, "lxml")
            details = self._extract_detailed_data(soup)

            # Merge with existing listing data (detail data overrides)
            detailed_listing = {**listing, **details}

            fields_found = [k for k in ("title", "full_description", "contact_name", "date") if details.get(k)]
            Actor.log.debug(
                f"Detail {listing['id']}: {content_size}b, fields={fields_found}"
            )
            return detailed_listing

        except Exception as e:
            Actor.log.warning(
                f"Error scraping detailed data for {listing['url']}: {e}"
            )
            return listing

    def _extract_detailed_data(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract detailed information from listing detail page."""

        details: Dict[str, Any] = {}

        # Try to extract structured data from JSON-LD first (most reliable)
        json_ld_data = self._extract_json_ld(soup)
        if json_ld_data:
            details.update(json_ld_data)

        # Full description from the page
        desc_div = soup.find("div", class_=re.compile(r"description"))
        if desc_div:
            details["full_description"] = desc_div.get_text(strip=True)

        # Title from h1 if not already set
        if "title" not in details or not details.get("title"):
            h1 = soup.find("h1")
            if h1:
                details["title"] = h1.get_text(strip=True)

        # Price from detail page
        price_span = soup.find("span", class_=re.compile(r"price"))
        if price_span:
            price_text = price_span.get_text(strip=True)
            details["price_text"] = price_text
            details["price"] = self._extract_price(price_text)

        # Location from detail page
        location_span = soup.find(
            "span", class_=re.compile(r"text-dark-blue-60.*font-medium")
        )
        if location_span:
            loc_text = location_span.get_text(strip=True)
            if loc_text.startswith("v ") or loc_text.startswith("V "):
                loc_text = loc_text[2:]
            details["location"] = loc_text

        # Date modified
        time_el = soup.find("time", attrs={"datetime": True})
        if time_el:
            details["date"] = time_el.get("datetime", "")

        # Seller info from profile link
        seller_link = soup.find("a", href=re.compile(r"/bazar/"))
        if seller_link:
            seller_name = seller_link.get_text(strip=True)
            if seller_name:
                details["contact_name"] = seller_name

        # All images from the page
        images = []
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if "sdn.cz" in src and "c_img" in src:
                full_url = src if src.startswith("http") else f"https:{src}"
                if full_url not in images:
                    images.append(full_url)
        if images:
            details["images"] = images

        return details

    def _extract_json_ld(self, soup: BeautifulSoup) -> Optional[Dict[str, Any]]:
        """Extract listing data from JSON-LD structured data on detail pages."""

        scripts = soup.find_all("script", type="application/ld+json")

        for script in scripts:
            try:
                data = json.loads(script.string or "")

                # Look for Product schema
                if isinstance(data, dict) and data.get("@type") == "Product":
                    result: Dict[str, Any] = {}

                    if data.get("name"):
                        result["title"] = data["name"]
                    if data.get("description"):
                        result["full_description"] = data["description"]

                    # Extract offer/price info
                    offers = data.get("offers", [])
                    if isinstance(offers, list) and offers:
                        offer = offers[0]
                    elif isinstance(offers, dict):
                        offer = offers
                    else:
                        offer = {}

                    if offer.get("price") is not None:
                        try:
                            result["price"] = int(float(offer["price"]))
                            currency = offer.get("priceCurrency", "CZK")
                            result["price_text"] = f"{result['price']} {currency}"
                        except (ValueError, TypeError):
                            pass

                    # Seller info
                    seller = offer.get("seller", {})
                    if seller.get("name"):
                        result["contact_name"] = seller["name"]

                    # Images from JSON-LD
                    ld_images = data.get("image", [])
                    if isinstance(ld_images, list):
                        img_urls = []
                        for img in ld_images:
                            if isinstance(img, dict) and img.get("contentUrl"):
                                img_url = img["contentUrl"]
                                if not img_url.startswith("http"):
                                    img_url = f"https:{img_url}"
                                img_urls.append(img_url)
                            elif isinstance(img, str):
                                img_url = img if img.startswith("http") else f"https:{img}"
                                img_urls.append(img_url)
                        if img_urls:
                            result["images"] = img_urls

                    return result

            except (json.JSONDecodeError, TypeError):
                continue

        return None

    def _extract_price(self, price_text: str) -> Optional[int]:
        """Extract numeric price from price text like '149 000 Kč'."""

        if not price_text:
            return None

        # Remove non-breaking spaces and regular spaces, then extract digits
        cleaned = price_text.replace("\xa0", "").replace(" ", "").replace("Kč", "").replace("KC", "").strip()

        # Try to parse the number
        match = re.search(r"(\d+)", cleaned)
        if match:
            try:
                return int(match.group(0))
            except ValueError:
                pass

        # Handle multi-digit with spaces: "149 000" -> 149000
        digits = re.findall(r"\d+", price_text.replace("\xa0", " "))
        if digits:
            try:
                return int("".join(digits))
            except ValueError:
                pass

        return None

    def _check_next_page(self, soup: BeautifulSoup, current_page: int) -> bool:
        """Check if there's a next page of results."""

        # Method 1: Look for pagination "next" button (data-testid="pagination-next")
        next_btn = soup.find(attrs={"data-testid": "pagination-next"})
        if next_btn:
            return True

        # Method 2: Look for pagination item with next page number
        next_page_str = str(current_page + 1)
        page_items = soup.find_all(attrs={"data-testid": "pagination-item"})
        for item in page_items:
            if item.get_text(strip=True) == next_page_str:
                return True

        # Method 3: Check pagination links for next page in URL
        pagination_links = soup.find_all("a", href=re.compile(r"/nejnovejsi/\d+"))
        for link in pagination_links:
            href = link.get("href", "")
            page_match = re.search(r"/nejnovejsi/(\d+)", href)
            if page_match and int(page_match.group(1)) > current_page:
                return True

        # Method 4: "Load more" button
        load_more = soup.find(attrs={"data-testid": "pagination-more"})
        if load_more:
            return True

        # Method 5: If we got a full page of listings, there might be more
        listing_elements = soup.find_all("li", attrs={"data-offer-id": True})
        if len(listing_elements) >= LISTINGS_PER_PAGE:
            return True

        return False


async def main() -> None:
    """Main entry point for the Sbazar.cz scraper."""

    async with Actor:
        # Get input configuration
        actor_input = await Actor.get_input() or {}

        # Extract configuration with proper defaults
        categories = actor_input.get("categories")
        if not categories or len(categories) == 0:
            categories = DEFAULT_CATEGORIES

        max_listings = actor_input.get("maxListings", 100)
        include_detailed_data = actor_input.get("includeDetailedData", True)
        search_query = actor_input.get("searchQuery")
        location = actor_input.get("location")
        price_min = actor_input.get("priceMin")
        price_max = actor_input.get("priceMax")

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
            Actor.log.info(f"Location filter: {location}")
        if price_min or price_max:
            Actor.log.info(
                f"Price range: {price_min or 0} - {price_max or 'unlimited'} CZK"
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
        # Sbazar.cz redirects first-time visitors through login.szn.cz/autologin.
        # The scraper warms up the session by following that redirect chain once,
        # which sets the necessary cookies. No hardcoded consent cookies needed.
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

        async with AsyncClient(headers=headers, timeout=30.0) as client:
            scraper = SbazarScraper(client)

            all_listings: List[Dict[str, Any]] = []

            # Scrape each category
            for category_index, category in enumerate(categories):
                try:
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

                    category_listings = await scraper.scrape_category_listings(
                        category=category,
                        max_listings=max_listings,
                        search_query=search_query,
                        location=location,
                        price_min=price_min,
                        price_max=price_max,
                    )

                    # Scrape detailed data if requested
                    if include_detailed_data and category_listings:
                        Actor.log.info(
                            f"Scraping detailed data for {len(category_listings)} "
                            f"listings from {category}"
                        )

                        detailed_listings = []
                        for i, listing in enumerate(category_listings):
                            detailed_listing = await scraper.scrape_detailed_data(
                                listing
                            )
                            detailed_listings.append(detailed_listing)

                            # Progress update
                            if (i + 1) % 10 == 0:
                                Actor.log.info(
                                    f"Scraped detailed data for {i + 1}/"
                                    f"{len(category_listings)} listings"
                                )

                            # Rate limiting for detailed scraping
                            await asyncio.sleep(0.5)

                        category_listings = detailed_listings

                    all_listings.extend(category_listings)

                    # Save data to both Apify dataset and database
                    if category_listings:
                        # Save to Apify dataset
                        await Actor.push_data(category_listings)
                        Actor.log.info(
                            f"Saved {len(category_listings)} listings to Apify dataset "
                            f"from category {category}"
                        )

                        # Save to database if available
                        if db_manager_available:
                            try:
                                Actor.log.debug("Inserting listings to database...")
                                db_manager.insert_listings(category_listings)
                                Actor.log.info(
                                    f"Saved {len(category_listings)} listings to database "
                                    f"from category {category}"
                                )
                            except Exception as e:
                                Actor.log.error(
                                    f"Failed to save listings to database: {e}"
                                )
                                # Try to refresh the connection pool and retry once
                                try:
                                    Actor.log.info(
                                        "Attempting to refresh connection pool and retry"
                                    )
                                    db_manager.refresh_pool()
                                    db_manager.insert_listings(category_listings)
                                    Actor.log.info(
                                        f"Successfully saved {len(category_listings)} "
                                        f"listings to database after retry"
                                    )
                                except Exception as retry_e:
                                    Actor.log.error(
                                        f"Failed to save listings to database "
                                        f"even after retry: {retry_e}"
                                    )

                    Actor.log.info(f"=== Completed category: {category} ===")

                except Exception as e:
                    Actor.log.error(f"Error scraping category {category}: {e}")
                    continue

            # Final summary and database cleanup
            Actor.log.info("=== SCRAPING COMPLETED ===")
            Actor.log.info(f"Total listings scraped: {len(all_listings)}")
            Actor.log.info(f"Categories processed: {len(categories)}")

            # Update actor run status in database
            if db_manager_available:
                try:
                    db_manager.update_actor_run_status("completed", len(all_listings))
                    db_manager.close_pool()
                    Actor.log.info("Database connection closed successfully")
                except Exception as e:
                    Actor.log.error(f"Failed to update actor run status: {e}")

            # Set status message
            await Actor.set_status_message(
                f"Completed: {len(all_listings)} listings scraped "
                f"from {len(categories)} categories"
            )


if __name__ == "__main__":
    asyncio.run(main())
