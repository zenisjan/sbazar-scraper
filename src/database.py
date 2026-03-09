"""
PostgreSQL database integration for scrapers.

This module handles all database operations including connection management,
data insertion, and querying with the schema where actor_runs is the parent table.
Uses Apify SDK for environment variable handling.

NOTE: This file is shared between bazos-scraper, gfr-scraper, and sbazar-scraper.
      Keep all copies in sync. See CLAUDE.md for details.
"""

from __future__ import annotations

import json
import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from psycopg2.pool import SimpleConnectionPool
from contextlib import contextmanager
import time

import os
from apify import Actor


class DatabaseManager:
    """Manages PostgreSQL database connections and operations."""

    def __init__(self, scraper_name: str = 'bazos_scraper'):
        self.connection_pool = None
        self.actor_run_id = None  # This will be the database ID (integer)
        self.actor_run_uuid = None  # This will be the Apify run ID (string)
        self.actor_run_start = None
        self.scraper_name = scraper_name
        self.scraper_id = None  # Set from SCRAPER_ID env var (links to scrapers table)

    def initialize_pool(self):
        """Initialize the database connection pool using environment variables."""
        try:
            db_config = {
                'host': os.environ.get('DB_HOST') or 'localhost',
                'port': os.environ.get('DB_PORT') or '25432',
                'database': os.environ.get('DB_NAME') or 'bazos_scraper',
                'user': os.environ.get('DB_USER') or 'postgres',
                'password': os.environ.get('DB_PASSWORD') or '',
                'sslmode': os.environ.get('DB_SSL_MODE') or 'prefer',
                'connect_timeout': 30,
                'application_name': self.scraper_name,
                'keepalives_idle': 600,
                'keepalives_interval': 30,
                'keepalives_count': 3
            }

            # Read scraper_id from env (set by scheduler when triggering runs)
            scraper_id_str = os.environ.get('SCRAPER_ID')
            if scraper_id_str:
                try:
                    self.scraper_id = int(scraper_id_str)
                    Actor.log.info(f"SCRAPER_ID set from env: {self.scraper_id}")
                except ValueError:
                    Actor.log.warning(f"Invalid SCRAPER_ID env var: {scraper_id_str}")

            pool_size = int(os.environ.get('DB_POOL_SIZE') or '5')
            self.connection_pool = SimpleConnectionPool(
                minconn=1,
                maxconn=pool_size,
                **db_config
            )

            Actor.log.info("Database connection pool initialized successfully")
            Actor.log.info(f"Connected to database: {db_config['host']}:{db_config['port']}/{db_config['database']}")

        except Exception as e:
            Actor.log.error(f"Failed to initialize database connection pool: {e}")
            raise

    def set_actor_run_info(self, run_uuid: str, start_time: datetime):
        """Set the current actor run information."""
        self.actor_run_uuid = run_uuid
        self.actor_run_start = start_time
        Actor.log.info(f"Set actor run info: {run_uuid} started at {start_time}")

    def _is_connection_alive(self, conn) -> bool:
        """Check if a database connection is still alive."""
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return True
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            return False

    def _get_healthy_connection(self, max_retries: int = 3):
        """Get a healthy database connection with retry logic."""
        for attempt in range(max_retries):
            try:
                conn = self.connection_pool.getconn()
                if self._is_connection_alive(conn):
                    return conn
                else:
                    try:
                        conn.close()
                    except:
                        pass
                    Actor.log.warning(f"Connection was dead, attempting to get new connection (attempt {attempt + 1})")
            except Exception as e:
                Actor.log.warning(f"Failed to get connection (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    raise

        raise Exception(f"Failed to get healthy connection after {max_retries} attempts")

    @contextmanager
    def get_connection(self):
        """Get a healthy database connection from the pool with retry logic."""
        if not self.connection_pool:
            self.initialize_pool()

        conn = None
        try:
            conn = self._get_healthy_connection()
            yield conn
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            Actor.log.error(f"Database connection error: {e}")
            raise
        finally:
            if conn:
                try:
                    self.connection_pool.putconn(conn)
                except Exception as e:
                    Actor.log.warning(f"Failed to return connection to pool: {e}")
                    try:
                        conn.close()
                    except:
                        pass

    def _execute_with_retry(self, operation_func, max_retries: int = 3):
        """Execute a database operation with automatic retry logic."""
        for attempt in range(max_retries):
            try:
                return operation_func()
            except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
                Actor.log.warning(f"Database operation failed (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    if attempt == 1:
                        Actor.log.info("Reinitializing connection pool due to persistent connection issues")
                        try:
                            self.close_pool()
                        except:
                            pass
                        self.initialize_pool()
                else:
                    Actor.log.error(f"Database operation failed after {max_retries} attempts")
                    raise
            except Exception as e:
                Actor.log.error(f"Database operation failed with non-retryable error: {e}")
                raise

    def create_actor_run(
        self,
        categories: List[str],
        max_listings: int,
        search_query: Optional[str] = None,
        location: Optional[str] = None,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None
    ) -> int:
        """Create a new actor run record and return the database ID.

        If an actor_run with this run_id already exists (e.g. pre-created by the
        scheduler), update it with our details and reuse it.
        """

        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                # Upsert: insert new record or update existing one (e.g. pre-created by scheduler)
                cursor.execute("""
                    INSERT INTO actor_runs (
                        run_id, start_time, categories, max_listings,
                        search_query, location_filter, price_min, price_max,
                        status, scraper_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id) DO UPDATE SET
                        start_time = EXCLUDED.start_time,
                        categories = EXCLUDED.categories,
                        max_listings = EXCLUDED.max_listings,
                        search_query = EXCLUDED.search_query,
                        location_filter = EXCLUDED.location_filter,
                        price_min = EXCLUDED.price_min,
                        price_max = EXCLUDED.price_max,
                        status = 'running'
                    RETURNING id, scraper_id
                """, (
                    self.actor_run_uuid,
                    self.actor_run_start,
                    categories,
                    max_listings,
                    search_query,
                    location,
                    price_min,
                    price_max,
                    'running',
                    self.scraper_id
                ))

                row = cursor.fetchone()
                self.actor_run_id = row[0]
                # Inherit scraper_id from pre-created record if we don't have one
                if not self.scraper_id and row[1]:
                    self.scraper_id = row[1]
                conn.commit()
                Actor.log.info(f"Actor run record ready with ID: {self.actor_run_id}")
                return self.actor_run_id

    def update_actor_run_status(self, status: str, total_listings: int = 0):
        """Update actor run status and total listings count with retry logic."""

        def _update_operation():
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE actor_runs
                        SET status = %s,
                            total_listings_scraped = %s,
                            end_time = %s
                        WHERE id = %s
                    """, (
                        status,
                        total_listings,
                        datetime.now(),
                        self.actor_run_id
                    ))
                    conn.commit()
                    Actor.log.info(f"Updated actor run status: {status} with {total_listings} listings")

        self._execute_with_retry(_update_operation)

    def insert_listings(self, listings: List[Dict[str, Any]]):
        """Insert multiple listings into the database with retry logic."""

        if not listings:
            return

        if not self.actor_run_id:
            raise ValueError("Actor run ID not set. Call create_actor_run() first.")

        def _insert_operation():
            listing_data = []
            for listing in listings:
                coords_lat = None
                coords_lng = None
                if 'coordinates' in listing and listing['coordinates']:
                    coords_lat = listing['coordinates'].get('latitude')
                    coords_lng = listing['coordinates'].get('longitude')

                # Also check top-level coordinate fields
                if coords_lat is None:
                    coords_lat = listing.get('coordinates_lat')
                if coords_lng is None:
                    coords_lng = listing.get('coordinates_lng')

                images_json = json.dumps(listing.get('images', [])) if listing.get('images') else None
                similar_json = json.dumps(listing.get('similar_listings', [])) if listing.get('similar_listings') else None

                listing_data.append((
                    listing.get('id', ''),
                    self.actor_run_id,
                    self.scraper_name,
                    listing.get('title', ''),
                    listing.get('url', ''),
                    listing.get('category', ''),
                    listing.get('price'),
                    listing.get('price_text', ''),
                    listing.get('description', ''),
                    listing.get('full_description', ''),
                    listing.get('location', ''),
                    listing.get('views', 0),
                    listing.get('date', ''),
                    listing.get('is_top', False),
                    listing.get('image_url', ''),
                    listing.get('contact_name', ''),
                    listing.get('phone', ''),
                    coords_lat,
                    coords_lng,
                    images_json,
                    similar_json,
                    datetime.fromisoformat(listing.get('scraped_at', datetime.now().isoformat()))
                ))

            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    execute_values(
                        cursor,
                        """
                        INSERT INTO listings (
                            id, actor_run_id, scraper_name, title, url, category,
                            price, price_text, description, full_description, location,
                            views, date, is_top, image_url, contact_name, phone,
                            coordinates_lat, coordinates_lng, images, similar_listings,
                            scraped_at
                        ) VALUES %s
                        ON CONFLICT (id, actor_run_id, scraper_name) DO UPDATE SET
                            title = EXCLUDED.title,
                            url = EXCLUDED.url,
                            category = EXCLUDED.category,
                            price = EXCLUDED.price,
                            price_text = EXCLUDED.price_text,
                            description = EXCLUDED.description,
                            full_description = EXCLUDED.full_description,
                            location = EXCLUDED.location,
                            views = EXCLUDED.views,
                            date = EXCLUDED.date,
                            is_top = EXCLUDED.is_top,
                            image_url = EXCLUDED.image_url,
                            contact_name = EXCLUDED.contact_name,
                            phone = EXCLUDED.phone,
                            coordinates_lat = EXCLUDED.coordinates_lat,
                            coordinates_lng = EXCLUDED.coordinates_lng,
                            images = EXCLUDED.images,
                            similar_listings = EXCLUDED.similar_listings,
                            scraped_at = EXCLUDED.scraped_at
                        """,
                        listing_data,
                        template=None,
                        page_size=100
                    )
                    conn.commit()
                    Actor.log.info(f"Inserted {len(listings)} listings into database")

        self._execute_with_retry(_insert_operation)

    def get_latest_listings(self, category: Optional[str] = None, scraper_name: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """Get the latest listings from the database."""

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                if category and scraper_name:
                    cursor.execute("""
                        SELECT * FROM latest_listings
                        WHERE category = %s AND scraper_name = %s
                        ORDER BY scraped_at DESC
                        LIMIT %s
                    """, (category, scraper_name, limit))
                elif category:
                    cursor.execute("""
                        SELECT * FROM latest_listings
                        WHERE category = %s
                        ORDER BY scraped_at DESC
                        LIMIT %s
                    """, (category, limit))
                elif scraper_name:
                    cursor.execute("""
                        SELECT * FROM latest_listings
                        WHERE scraper_name = %s
                        ORDER BY scraped_at DESC
                        LIMIT %s
                    """, (scraper_name, limit))
                else:
                    cursor.execute("""
                        SELECT * FROM latest_listings
                        ORDER BY scraped_at DESC
                        LIMIT %s
                    """, (limit,))

                results = cursor.fetchall()
                return [dict(row) for row in results]

    def get_actor_run_stats(self, run_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get statistics for actor runs."""

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                if run_uuid:
                    cursor.execute("""
                        SELECT * FROM actor_run_stats
                        WHERE run_id = %s
                    """, (run_uuid,))
                else:
                    cursor.execute("""
                        SELECT * FROM actor_run_stats
                        ORDER BY start_time DESC
                        LIMIT 10
                    """)

                results = cursor.fetchall()
                return [dict(row) for row in results]

    def get_listings_by_actor_run(self, run_uuid: str, scraper_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all listings for a specific actor run."""

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                if scraper_name:
                    cursor.execute("""
                        SELECT l.*, ar.run_id, ar.start_time as actor_run_start
                        FROM listings l
                        JOIN actor_runs ar ON l.actor_run_id = ar.id
                        WHERE ar.run_id = %s AND l.scraper_name = %s
                        ORDER BY l.scraped_at DESC
                    """, (run_uuid, scraper_name))
                else:
                    cursor.execute("""
                        SELECT l.*, ar.run_id, ar.start_time as actor_run_start
                        FROM listings l
                        JOIN actor_runs ar ON l.actor_run_id = ar.id
                        WHERE ar.run_id = %s
                        ORDER BY l.scraped_at DESC
                    """, (run_uuid,))

                results = cursor.fetchall()
                return [dict(row) for row in results]

    def get_scraper_stats(self, scraper_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get statistics for scrapers."""

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                if scraper_name:
                    cursor.execute("""
                        SELECT * FROM scraper_stats
                        WHERE scraper_name = %s
                    """, (scraper_name,))
                else:
                    cursor.execute("""
                        SELECT * FROM scraper_stats
                        ORDER BY total_listings DESC
                    """)

                results = cursor.fetchall()
                return [dict(row) for row in results]

    def refresh_pool(self):
        """Refresh the connection pool by closing and reinitializing it."""
        try:
            if self.connection_pool:
                self.connection_pool.closeall()
                Actor.log.info("Closed existing connection pool")
            self.initialize_pool()
            Actor.log.info("Connection pool refreshed successfully")
        except Exception as e:
            Actor.log.error(f"Failed to refresh connection pool: {e}")
            raise

    def close_pool(self):
        """Close the database connection pool."""
        if self.connection_pool:
            self.connection_pool.closeall()
            Actor.log.info("Database connection pool closed")


# Global database manager instance
db_manager = DatabaseManager('sbazar')
