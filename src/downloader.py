"""Media downloader with caching."""

import hashlib
import logging
from pathlib import Path
from typing import Optional
import aiohttp
import aiofiles

from .config import CACHE_DIR, TELEGRAM_MAX_FILE_SIZE
from .models import MediaItem

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Raised when download fails."""

    pass


class MediaDownloader:
    """Downloads and caches media files."""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def download_media(self, media_item: MediaItem, post_id: str) -> str:
        """
        Download media file and return local path.

        Args:
            media_item: MediaItem to download
            post_id: Post ID for cache organization

        Returns:
            Local file path

        Raises:
            DownloadError: If download fails
        """
        # Check cache first
        cached_path = self._get_cached_path(media_item.url, post_id)
        if cached_path.exists():
            logger.info(f"Using cached file: {cached_path}")
            media_item.local_path = str(cached_path)
            return str(cached_path)

        # Download file
        logger.info(f"Downloading: {media_item.url}")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    media_item.url, timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    response.raise_for_status()

                    # Check file size
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > TELEGRAM_MAX_FILE_SIZE:
                        raise DownloadError(
                            f"File too large: {int(content_length) / 1024 / 1024:.1f}MB "
                            f"(max: {TELEGRAM_MAX_FILE_SIZE / 1024 / 1024:.1f}MB)"
                        )

                    # Save to cache
                    cached_path.parent.mkdir(parents=True, exist_ok=True)

                    async with aiofiles.open(cached_path, "wb") as f:
                        total_size = 0
                        async for chunk in response.content.iter_chunked(8192):
                            total_size += len(chunk)
                            if total_size > TELEGRAM_MAX_FILE_SIZE:
                                cached_path.unlink(missing_ok=True)
                                raise DownloadError("File exceeds Telegram size limit")
                            await f.write(chunk)

                    logger.info(
                        f"Downloaded {total_size / 1024 / 1024:.1f}MB to {cached_path}"
                    )
                    media_item.local_path = str(cached_path)
                    return str(cached_path)

        except aiohttp.ClientError as e:
            raise DownloadError(f"Download failed: {e}")
        except Exception as e:
            cached_path.unlink(missing_ok=True)
            raise DownloadError(f"Unexpected error: {e}")

    async def download_all(
        self, media_items: list[MediaItem], post_id: str
    ) -> list[str]:
        """
        Download multiple media items.

        Args:
            media_items: List of MediaItem objects
            post_id: Post ID for cache organization

        Returns:
            List of local file paths
        """
        paths = []
        for item in media_items:
            try:
                path = await self.download_media(item, post_id)
                paths.append(path)
            except DownloadError as e:
                logger.error(f"Failed to download {item.url}: {e}")
                # Continue with other items

        return paths

    def _get_cached_path(self, url: str, post_id: str) -> Path:
        """Generate cache file path."""
        # Hash URL for unique filename
        url_hash = hashlib.md5(url.encode()).hexdigest()[:12]

        # Extract extension from URL
        ext = self._get_extension(url)

        # Organize by post_id
        post_cache_dir = self.cache_dir / post_id
        return post_cache_dir / f"{url_hash}{ext}"

    @staticmethod
    def _get_extension(url: str) -> str:
        """Extract file extension from URL."""
        # Common video extensions
        for ext in [".mp4", ".mov", ".avi"]:
            if ext in url.lower():
                return ext

        # Common image extensions
        for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
            if ext in url.lower():
                return ext

        # Default extensions
        if "video" in url.lower():
            return ".mp4"
        return ".jpg"

    def cleanup_post_cache(self, post_id: str):
        """Remove cached files for a post."""
        post_cache_dir = self.cache_dir / post_id
        if post_cache_dir.exists():
            for file in post_cache_dir.iterdir():
                file.unlink()
            post_cache_dir.rmdir()
            logger.info(f"Cleaned up cache for post {post_id}")
