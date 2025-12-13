"""Threads content scraper using Playwright."""

import asyncio
import base64
import logging
import urllib.parse
from typing import Optional

from bs4 import BeautifulSoup
from playwright.async_api import (
    Browser,
    Page,
    async_playwright,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeout,
)

from .models import MediaItem, MediaType, ThreadsPost
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

            # Block unnecessary resources to speed up loading
            # Note: We don't block 'media' because it might be needed for video elements to initialize properly
            await page.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ["image", "font", "stylesheet", "other"]
                else route.continue_(),
            )

            logger.info(f"Loading {threads_url.full_url}")

            response = await page.goto(
                threads_url.full_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            logger.info(f"Page loaded with status: {response.status if response else 'unknown'}")

            # Wait for essential content to load instead of fixed sleep
            try:
                # Wait for either og:description or any image to appear
                await page.wait_for_selector('meta[property="og:description"], img', timeout=5000)
            except PlaywrightTimeout:
                logger.warning("Timeout waiting for meta tags or images, proceeding anyway")

            # Check page title early for debugging
            early_title = await page.title()
            logger.debug(f"Early page title: {early_title[:100]}")

            # Check if we hit a login wall
            if "join threads" in early_title.lower() or "log in" in early_title.lower():
                logger.warning("⚠️ Hit login wall, waiting longer for redirect...")
                await asyncio.sleep(5)
                early_title = await page.title()
                logger.debug(f"After wait, title: {early_title[:100]}")

                # If still showing login wall after wait, post might be private
                if "join threads" in early_title.lower() or "log in" in early_title.lower():
                    logger.error(
                        "❌ Still showing login wall - post may be private or require authentication"
                    )
                    raise PostNotFoundError(
                        f"Post {threads_url.post_id} is private or requires authentication"
                    )

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
            # Filter out login prompts
            if text and (
                "join threads" in text.lower() or "log in with your instagram" in text.lower()
            ):
                logger.warning("⚠️ og:description contains login prompt, skipping")
                text = None
            elif text and len(text) > 15:
                logger.info(f"✅ Extracted text from og:description ({len(text)} chars)")

        # Strategy 2: Page title - most reliable fallback
        if not text:
            title = await page.title()
            logger.debug(f"Page title: {title[:100] if title else 'None'}...")

            # Skip login prompts in title
            if title and ("join threads" in title.lower() or "log in" in title.lower()):
                logger.warning("⚠️ Page title contains login prompt, skipping")
            elif title and len(title) > 20:
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
                # Filter out login prompts
                if text and ("join threads" in text.lower() or "log in" in text.lower()):
                    logger.warning("⚠️ Meta description contains login prompt, skipping")
                    text = None
                elif text:
                    logger.info(f"✅ Extracted text from meta description ({len(text)} chars)")

        # DO NOT extract from DOM - Threads loads multiple posts in feed
        # DOM extraction would grab content from wrong posts

        if not text:
            logger.warning("⚠️ No text content found in OpenGraph tags")
            text = ""

        # Extract media (videos first, then images)
        media_items, media_type = await self._extract_media(page)

        # If no text AND no media AND generic logo, it's likely a login wall
        if not text and not media_items:
            meta_og_img = await page.query_selector('meta[property="og:image"]')
            if meta_og_img:
                img_url = await meta_og_img.get_attribute("content")
                if img_url and "rsrc.php" in img_url:  # Threads default logo
                    logger.error(
                        "❌ Post appears to be private or requires authentication (no content found)"
                    )
                    raise PostNotFoundError(
                        f"Post {threads_url.post_id} is private or requires authentication"
                    )

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

        Strategy:
        1. og:video or og:video:secure_url for videos (most reliable)
        2. If og:image looks like video thumbnail → try DOM extraction
        3. og:image for regular images

        We prefer OpenGraph tags but fall back to DOM extraction for videos
        when og:video is missing but og:image indicates it's a video post.
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
                logger.info("✅ Found video from og:video")
                logger.info(f"   URL: {video_url}")
                media_items.append(MediaItem(url=video_url, type=MediaType.VIDEO))
                return media_items, MediaType.VIDEO

        # Also try og:video:secure_url
        og_video_secure = soup.find("meta", property="og:video:secure_url")
        if og_video_secure:
            video_url = og_video_secure.get("content", "")
            if video_url and self._is_valid_video_url(video_url):
                logger.info("✅ Found video from og:video:secure_url")
                logger.info(f"   URL: {video_url}")
                media_items.append(MediaItem(url=video_url, type=MediaType.VIDEO))
                return media_items, MediaType.VIDEO

        # Priority 2: Check if og:image is actually a video thumbnail or try DOM extraction
        og_image = soup.find("meta", property="og:image")
        image_url = og_image.get("content", "") if og_image else ""

        # Check if this looks like a video thumbnail
        is_video_thumbnail = False
        if image_url:
            # 1. Direct check in URL
            if "cover_frame" in image_url or "default_cover" in image_url:
                is_video_thumbnail = True
            # 2. Check encoded efg parameter
            elif "efg=" in image_url:
                try:
                    parsed = urllib.parse.urlparse(image_url)
                    params = urllib.parse.parse_qs(parsed.query)
                    if "efg" in params:
                        efg_encoded = params["efg"][0]
                        efg_encoded += "=" * (-len(efg_encoded) % 4)
                        efg_decoded = base64.urlsafe_b64decode(efg_encoded).decode("utf-8")
                        if "cover_frame" in efg_decoded or "default_cover" in efg_decoded:
                            is_video_thumbnail = True
                except Exception:
                    pass

        # Also check if og:image is a profile picture (indicates might be video post)
        is_profile_pic = image_url and "t51.2885-19" in image_url

        logger.debug(
            f"Video detection - is_video_thumbnail: {is_video_thumbnail}, is_profile_pic: {is_profile_pic}, image_url[:80]: {image_url[:80] if image_url else 'None'}"
        )

        # Try DOM video extraction if:
        # 1. og:image looks like video thumbnail, OR
        # 2. og:image is profile picture (text-only posts with videos), OR
        # 3. No valid og:image at all
        should_try_dom_video = (
            is_video_thumbnail or is_profile_pic or not self._is_valid_image_url(image_url)
        )

        logger.debug(f"should_try_dom_video: {should_try_dom_video}")

        if should_try_dom_video:
            if is_video_thumbnail:
                logger.info("🎬 og:image appears to be video thumbnail, trying DOM extraction...")
            elif is_profile_pic:
                logger.info("🎬 og:image is profile picture, checking for video in DOM...")
            else:
                logger.info("🎬 No valid og:image, checking for video in DOM...")

            video_url = await self._extract_video_from_dom(page)
            if video_url:
                logger.info("✅ Found video from DOM extraction")
                logger.info(f"   URL: {video_url}")
                media_items.append(MediaItem(url=video_url, type=MediaType.VIDEO))
                return media_items, MediaType.VIDEO

        # Priority 3: Regular image (if valid and not a video post)
        if image_url and self._is_valid_image_url(image_url):
            logger.info("✅ Found image from og:image")
            logger.info(f"   URL: {image_url}")
            media_items.append(MediaItem(url=image_url, type=MediaType.IMAGE))

            # Check if this is a carousel post (multiple images)
            carousel_images = await self._extract_carousel_images(
                page, soup, first_image_url=image_url
            )
            if carousel_images:
                logger.info(f"📸 Found {len(carousel_images)} additional carousel images")
                media_items.extend(carousel_images)

            return media_items, MediaType.IMAGE

        logger.warning(
            "⚠️ No og:video or og:image found - post may have carousel or unsupported media"
        )
        return [], MediaType.TEXT_ONLY

    async def _extract_video_from_dom(self, page: Page) -> Optional[str]:
        """
        Extract video URL from first <video> element in DOM.

        This is used as fallback when og:video is missing but post appears to be a video.
        We take the first video element assuming it's most likely the target post.
        """
        try:
            # Try to find first video element
            video_element = await page.query_selector("video")
            if not video_element:
                logger.debug("No <video> element found in DOM")
                return None

            # Try various attributes where video URL might be stored
            for attr in ["src", "data-src", "data-video-url"]:
                video_src = await video_element.get_attribute(attr)
                if video_src:
                    # Skip blob URLs (temporary browser URLs)
                    if video_src.startswith("blob:"):
                        logger.debug(f"Skipping blob URL: {video_src[:50]}...")
                        continue

                    if self._is_valid_video_url(video_src):
                        logger.debug(f"Found video {attr}: {video_src}")
                        return video_src

            # Try to find <source> child elements
            source_elements = await video_element.query_selector_all("source")
            for source in source_elements:
                for attr in ["src", "data-src"]:
                    src = await source.get_attribute(attr)
                    if src and not src.startswith("blob:") and self._is_valid_video_url(src):
                        logger.debug(f"Found video source {attr}: {src}")
                        return src

            logger.debug("Video element found but no valid video URL")
            return None

        except Exception as e:
            logger.warning(f"Error extracting video from DOM: {e}")
            return None

    async def _extract_carousel_images(
        self, page: Page, soup: BeautifulSoup, first_image_url: str
    ) -> list[MediaItem]:
        """
        Extract additional images from carousel posts.

        Carousel images have 'CAROUSEL_ITEM' in their URL (sometimes encoded in 'efg' param),
        which makes them safe to extract.

        Args:
            page: Playwright page object
            soup: BeautifulSoup parsed HTML
            first_image_url: URL of the first image (from og:image) to skip
        """
        carousel_items = []

        try:
            # Find all img tags
            all_imgs = soup.find_all("img")

            for img in all_imgs:
                src = img.get("src", "")
                if not src:
                    continue

                # Skip the first image (already added from og:image)
                if src == first_image_url:
                    continue

                if not self._is_valid_image_url(src):
                    continue

                # Check if this is a carousel item
                is_carousel = False

                # 1. Direct check
                if "CAROUSEL_ITEM" in src:
                    is_carousel = True

                # 2. Check encoded efg parameter
                elif "efg=" in src:
                    try:
                        parsed = urllib.parse.urlparse(src)
                        params = urllib.parse.parse_qs(parsed.query)
                        if "efg" in params:
                            efg_encoded = params["efg"][0]
                            # Add padding if needed
                            efg_encoded += "=" * (-len(efg_encoded) % 4)
                            # URL safe decode
                            efg_decoded = base64.urlsafe_b64decode(efg_encoded).decode("utf-8")
                            if "CAROUSEL_ITEM" in efg_decoded:
                                is_carousel = True
                    except Exception:
                        pass

                if is_carousel:
                    # Avoid duplicates
                    if not any(item.url == src for item in carousel_items):
                        carousel_items.append(MediaItem(url=src, type=MediaType.IMAGE))
                        logger.debug(f"Found carousel image: {src[:80]}...")

            return carousel_items

        except Exception as e:
            logger.warning(f"Error extracting carousel images: {e}")
            return []

    def _is_valid_image_url(self, url: str) -> bool:
        """Check if URL looks like a valid image."""
        if not url or not url.startswith("http"):
            return False

        # Must be from Instagram CDN (Threads uses Instagram's infrastructure)
        if "instagram" not in url.lower() and "fbcdn" not in url.lower():
            return False

        # Filter out Threads generic logo (indicates login wall or private post)
        if "rsrc.php" in url:
            logger.debug(f"Rejecting generic Threads logo: {url[:80]}...")
            return False

        # Filter out profile pictures - Instagram uses t51.2885-19 for profile pics
        # The -19 suffix specifically indicates profile picture resources
        if "t51.2885-19" in url:
            logger.debug(f"Rejecting profile picture: {url[:80]}...")
            return False

        # Filter out small thumbnail sizes commonly used for avatars
        exclude_patterns = ["profile", "avatar", "44x44", "150x150", "s150x150"]
        if any(pattern in url.lower() for pattern in exclude_patterns):
            logger.debug(f"Rejecting image with excluded pattern: {url[:80]}...")
            return False

        return True

    def _is_valid_video_url(self, url: str) -> bool:
        """Check if URL looks like a valid video."""
        if not url or not url.startswith("http"):
            return False

        # Must be from Instagram/Facebook CDN
        if "instagram" not in url.lower() and "fbcdn" not in url.lower():
            return False

        # Instagram video URLs often have patterns like:
        # - /o1/v/ or /o1/v/t16/ or /o1/v/t2/ (video content)
        # - .mp4 extension
        # - "video" in path
        video_indicators = ["/o1/v/", "/v/t16/", "/v/t2/", ".mp4", "video"]
        if any(indicator in url.lower() for indicator in video_indicators):
            return True

        return False  # Be strict - only accept clear video URLs
