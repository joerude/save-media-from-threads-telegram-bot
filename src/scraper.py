"""Threads content scraper using Playwright."""

import asyncio
import json
import logging
from typing import Optional
from bs4 import BeautifulSoup

from playwright.async_api import (
    async_playwright,
    Browser,
    Page,
    TimeoutError as PlaywrightTimeout,
)

from .config import BROWSER_TIMEOUT, MAX_RETRIES
from .models import ThreadsPost, MediaItem, MediaType
from .url_parser import ThreadsURL

logger = logging.getLogger(__name__)


class ScraperError(Exception):
    """Base exception for scraper errors."""

    pass


class PostNotFoundError(ScraperError):
    """Raised when post is deleted or private."""

    pass


class ExtractionError(ScraperError):
    """Raised when content extraction fails."""

    pass


class ThreadsScraper:
    """Scrapes Threads posts using Playwright."""

    def __init__(self):
        self.browser: Optional[Browser] = None

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def start(self):
        """Initialize browser."""
        logger.info("Starting browser")
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True,  # Run headless for speed
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )

    async def close(self):
        """Close browser."""
        try:
            if self.browser:
                logger.info("Closing browser")
                await self.browser.close()
        except Exception as e:
            logger.warning(f"Error closing browser: {e}")
        finally:
            if hasattr(self, "playwright"):
                try:
                    await self.playwright.stop()
                except Exception as e:
                    logger.warning(f"Error stopping playwright: {e}")

    async def scrape_post(self, threads_url: ThreadsURL) -> ThreadsPost:
        """
        Scrape a Threads post.

        Args:
            threads_url: Parsed Threads URL

        Returns:
            ThreadsPost with extracted content

        Raises:
            PostNotFoundError: If post doesn't exist or is private
            ExtractionError: If extraction fails
        """
        if not self.browser:
            raise ScraperError("Browser not initialized. Call start() first.")

        # Single attempt - no retry loop that can cause browser issues
        logger.info(f"Scraping post {threads_url.post_id}")
        return await self._scrape_single_attempt(threads_url)

    async def _scrape_single_attempt(self, threads_url: ThreadsURL) -> ThreadsPost:
        """Internal scraping logic."""
        # Create page with realistic browser context
        page = await self.browser.new_page(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )

        try:
            # Inject stealth scripts to avoid detection
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = {runtime: {}};
            """)

            # Set extra headers to look more real
            await page.set_extra_http_headers(
                {
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "sec-ch-ua": '"Chromium";v="120", "Not(A:Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Linux"',
                }
            )

            # Navigate to post - use domcontentloaded for faster response
            logger.info(f"Loading {threads_url.full_url}")

            response = await page.goto(
                threads_url.full_url,
                wait_until="networkidle",  # Wait for network to settle for media
                timeout=30000,  # 30 seconds is plenty
            )
            logger.info(f"Page loaded with status: {response.status if response else 'unknown'}")

            # Check page title early for debugging
            early_title = await page.title()
            logger.debug(f"Early page title: {early_title[:100]}")

            # Check if we hit a login wall
            if "join threads" in early_title.lower() or "log in" in early_title.lower():
                logger.warning("⚠️ Hit login wall, waiting longer for redirect...")
                await asyncio.sleep(5)
                early_title = await page.title()
                logger.debug(f"After wait, title: {early_title[:100]}")

            # Check if post exists
            if await self._is_post_unavailable(page):
                raise PostNotFoundError(f"Post {threads_url.post_id} not found or is private")

            # Brief wait for dynamic content (title/meta tags load fast)
            await asyncio.sleep(2)

            logger.debug("Starting content extraction...")

            # Get page HTML for parsing
            html_content = await page.content()

            # Log DOM structure for debugging (focus on images)
            self._log_dom_structure(html_content, threads_url.post_id)

            # Extract data from page
            post_data = await self._extract_post_data(page, threads_url, html_content)

            return post_data

        finally:
            await page.close()

    async def _is_post_unavailable(self, page: Page) -> bool:
        """Check if post is unavailable."""
        # Check for error messages or redirects
        title = await page.title()
        if "not found" in title.lower() or "error" in title.lower():
            return True

        # Check for specific error elements
        error_selectors = [
            'text="Sorry, this page isn\'t available"',
            'text="This post is unavailable"',
        ]

        for selector in error_selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=2000)
                if element:
                    return True
            except:
                continue

        return False

    def _log_dom_structure(self, html_content: str, post_id: str):
        """Log DOM structure focusing on media elements."""
        try:
            soup = BeautifulSoup(html_content, "html.parser")

            logger.info(f"\n{'=' * 80}")
            logger.info(f"DOM STRUCTURE FOR POST: {post_id}")
            logger.info(f"{'=' * 80}\n")

            # Save full HTML for debugging
            debug_file = f"debug_html_{post_id}.html"
            with open(debug_file, "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.debug(f"Full HTML saved to {debug_file}")

            # Log all img tags (show first 5 only, full URLs)
            images = soup.find_all("img")
            logger.info(f"📷 Found {len(images)} <img> tags (showing first 5):")
            for i, img in enumerate(images[:5], 1):
                src = img.get("src", "NO SRC")
                alt = img.get("alt", "NO ALT")
                logger.info(f"  [{i}] {src}")
                logger.info(f"      alt: {alt[:60]}")
                logger.info("")

            # Log all video tags
            videos = soup.find_all("video")
            logger.info(f"🎥 Found {len(videos)} <video> tags:")
            for i, video in enumerate(videos, 1):
                src = video.get("src", "")
                poster = video.get("poster", "NO POSTER")
                classes = " ".join(video.get("class", []))
                logger.info(f"  [{i}] src: {src[:80] if src else 'NO SRC'}")
                logger.info(f"      poster: {poster[:80]}")
                logger.info(f"      class: {classes[:50]}")

                # Check for source tags inside video
                sources = video.find_all("source")
                for j, source in enumerate(sources, 1):
                    s_src = source.get("src", "NO SRC")
                    s_type = source.get("type", "NO TYPE")
                    logger.info(f"      <source #{j}> src: {s_src[:80]}...")
                    logger.info(f"                   type: {s_type}")
                logger.info("")

            # Log meta tags with video info (FULL URLs - no truncation)
            og_videos = soup.find_all("meta", property=lambda x: x and "og:video" in x)
            logger.info(f"🎬 Found {len(og_videos)} og:video meta tags:")
            for i, meta in enumerate(og_videos, 1):
                prop = meta.get("property", "")
                content = meta.get("content", "NO CONTENT")
                logger.info(f"  [{i}] {prop}:")
                logger.info(f"      {content}")

            # Log meta tags with image info
            og_images = soup.find_all("meta", property="og:image")
            logger.info(f"🖼️  Found {len(og_images)} og:image meta tags:")
            for i, meta in enumerate(og_images, 1):
                content = meta.get("content", "NO CONTENT")
                logger.info(f"  [{i}] {content[:100]}...")

            logger.info(f"\n{'=' * 80}\n")

        except Exception as e:
            logger.warning(f"Failed to log DOM structure: {e}")

    async def _extract_post_data(
        self, page: Page, threads_url: ThreadsURL, html_content: str
    ) -> ThreadsPost:
        """
        Extract post content from page.

        Strategy (simplified - TEXT ONLY for now):
        1. Extract from meta og:description (has formatting)
        2. Extract from page title (fallback)
        3. Basic DOM text search
        """
        text = None

        # Strategy 1: Try OpenGraph description - often has better formatting
        meta_og = await page.query_selector('meta[property="og:description"]')
        if meta_og:
            text = await meta_og.get_attribute("content")
            if text and len(text) > 15:
                logger.info(f"✅ Extracted text from og:description ({len(text)} chars)")

        # Strategy 2: Page title - most reliable fallback
        if not text:
            title = await page.title()
            logger.debug(f"Page title: {title[:100] if title else 'None'}...")

            # Clean up title - remove "Threads" branding
            if title and len(title) > 20:
                # Remove common suffixes
                cleaned = title.replace(" | Threads", "").replace(" - Threads", "").strip()
                if len(cleaned) > 15:  # Still has meaningful content
                    text = cleaned
                    logger.info(f"✅ Extracted text from title ({len(text)} chars)")

        # Strategy 3: Meta description
        if not text:
            meta_desc = await page.query_selector('meta[name="description"]')
            if meta_desc:
                text = await meta_desc.get_attribute("content")
                if text:
                    logger.info(f"✅ Extracted text from meta description ({len(text)} chars)")

        # Strategy 4: Try basic body text
        if not text:
            text = await self._extract_text_from_dom(page)
            if text:
                logger.info(f"✅ Extracted text from DOM ({len(text)} chars)")

        if not text:
            logger.warning("⚠️ No text content found")
            text = ""

        # Extract media (videos first, then images)
        media_items, media_type = await self._extract_media(page)

        return ThreadsPost(
            post_id=threads_url.post_id,
            username=threads_url.username,
            text=text,
            media_type=media_type if media_items else MediaType.TEXT_ONLY,
            media_items=media_items,
        )

    async def _extract_text_from_dom(self, page: Page) -> Optional[str]:
        """Extract post text content."""
        logger.debug("Extracting text content...")
        # Try multiple broader selectors
        selectors = [
            'div[dir="auto"]',  # Removed article requirement
            '[role="textbox"]',
            "div span",
            "p",
            "div",
        ]

        all_texts = []
        for selector in selectors:
            try:
                logger.debug(f"Trying selector: {selector}")
                elements = await page.query_selector_all(selector)
                logger.debug(f"Found {len(elements)} elements")
                for element in elements:
                    text = await element.text_content()
                    if text and len(text.strip()) > 20:  # Meaningful text
                        all_texts.append(text.strip())
                        logger.debug(f"Found text: {text[:50]}...")
            except Exception as e:
                logger.debug(f"Selector {selector} failed: {e}")
                continue

        if all_texts:
            # Return longest text (likely the main content)
            result = max(all_texts, key=len)
            logger.debug(f"Extracted text (length {len(result)}): {result[:100]}...")
            return result

        logger.warning("No text content found")
        return None

    async def _extract_media(self, page: Page) -> tuple[list[MediaItem], MediaType]:
        """
        Extract media (videos and images) from post.

        Strategy - ONLY use OpenGraph meta tags (post-specific):
        1. og:video or og:video:secure_url for videos
        2. og:image for images

        We avoid scanning <video>/<img> tags because Threads loads multiple posts
        in the feed and we'd grab media from wrong posts. OpenGraph tags are
        specific to the requested post URL.
        """
        media_items = []

        # Get page content
        html_content = await page.content()
        soup = BeautifulSoup(html_content, "html.parser")

        # Priority 1: Check og:video meta tags (most reliable for THIS specific post)
        og_video = soup.find("meta", property="og:video")
        if og_video:
            video_url = og_video.get("content", "")
            if video_url and self._is_valid_video_url(video_url):
                logger.info(f"✅ Found video from og:video")
                logger.info(f"   URL: {video_url}")
                media_items.append(MediaItem(url=video_url, type=MediaType.VIDEO))
                return media_items, MediaType.VIDEO

        # Also try og:video:secure_url
        og_video_secure = soup.find("meta", property="og:video:secure_url")
        if og_video_secure:
            video_url = og_video_secure.get("content", "")
            if video_url and self._is_valid_video_url(video_url):
                logger.info(f"✅ Found video from og:video:secure_url")
                logger.info(f"   URL: {video_url}")
                media_items.append(MediaItem(url=video_url, type=MediaType.VIDEO))
                return media_items, MediaType.VIDEO

        # Priority 2: Check for image (only if no video)
        og_image = soup.find("meta", property="og:image")
        if og_image:
            image_url = og_image.get("content", "")
            if image_url and self._is_valid_image_url(image_url):
                logger.info(f"✅ Found image from og:image")
                logger.info(f"   URL: {image_url}")
                media_items.append(MediaItem(url=image_url, type=MediaType.IMAGE))
                return media_items, MediaType.IMAGE

        logger.warning(
            "⚠️ No og:video or og:image found - post may have carousel or unsupported media"
        )
        return [], MediaType.TEXT_ONLY

    def _is_valid_image_url(self, url: str) -> bool:
        """Check if URL looks like a valid image."""
        if not url or not url.startswith("http"):
            return False

        # Must be from Instagram CDN (Threads uses Instagram's infrastructure)
        if "instagram" not in url.lower() and "fbcdn" not in url.lower():
            return False

        # Filter out profile pictures and small images
        exclude_patterns = ["profile", "avatar", "44x44", "150x150"]
        if any(pattern in url.lower() for pattern in exclude_patterns):
            return False

        return True

    def _is_valid_video_url(self, url: str) -> bool:
        """Check if URL looks like a valid video."""
        if not url or not url.startswith("http"):
            return False

        # Must be from Instagram/Facebook CDN
        if "instagram" not in url.lower() and "fbcdn" not in url.lower():
            return False

        # Videos often have .mp4 extension or video in path
        if ".mp4" in url.lower() or "video" in url.lower():
            return True

        return True  # If from CDN, likely valid
