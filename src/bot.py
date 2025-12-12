"""Telegram bot handlers and delivery logic."""

import logging
from pathlib import Path
from typing import Optional

from telegram import Update, InputMediaPhoto, InputMediaVideo
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

from .config import TELEGRAM_BOT_TOKEN, TELEGRAM_MAX_MEDIA_GROUP
from .models import ThreadsPost, MediaType
from .url_parser import ThreadsURLParser, URLParserError
from .scraper import ThreadsScraper, PostNotFoundError, ExtractionError
from .downloader import MediaDownloader, DownloadError

logger = logging.getLogger(__name__)


class TelegramBot:
    """Telegram bot for Threads content."""

    def __init__(self):
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.scraper: Optional[ThreadsScraper] = None
        self.downloader = MediaDownloader()

        # Register handlers
        self.app.add_handler(CommandHandler("start", self.start_command))
        self.app.add_handler(CommandHandler("help", self.help_command))
        self.app.add_handler(CommandHandler("stop", self.stop_command))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        await update.message.reply_text(
            "👋 Send me a Threads post URL and I'll extract the content for you!\n\n"
            "Example:\n"
            "https://www.threads.com/@username/post/POST_ID"
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await update.message.reply_text(
            "📖 How to use:\n\n"
            "1. Find a public Threads post\n"
            "2. Copy the URL\n"
            "3. Send it to me\n\n"
            "I'll extract:\n"
            "✅ Videos\n"
            "✅ Images (single or carousel)\n"
            "✅ Text content\n\n"
            "Commands:\n"
            "/start - Start the bot\n"
            "/help - Show this help\n"
            "/stop - Stop the bot\n\n"
            "Note: Only public posts are supported."
        )

    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop command."""
        await update.message.reply_text(
            "👋 Goodbye! Bot stopped.\n\nSend /start to use the bot again."
        )
        # Stop the application
        await self.app.stop()
        await self.app.shutdown()

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages (URLs)."""
        message_text = update.message.text.strip()

        # Parse URL
        try:
            threads_url = ThreadsURLParser.parse(message_text)
        except URLParserError:
            await update.message.reply_text(
                "❌ Invalid URL. Please send a valid Threads post URL.\n\n"
                "Example:\n"
                "https://www.threads.com/@username/post/POST_ID"
            )
            return

        # Process post
        status_msg = await update.message.reply_text("⏳ Extracting content...")

        try:
            # Scrape post
            post = await self.scraper.scrape_post(threads_url)
            await status_msg.edit_text("📥 Downloading media...")

            # Download media if present
            if post.has_media:
                await self.downloader.download_all(post.media_items, post.post_id)

            # Send to user
            await status_msg.edit_text("📤 Sending...")
            await self.send_post(update, post)

            # Delete status message
            await status_msg.delete()

            # Cleanup cache after sending
            self.downloader.cleanup_post_cache(post.post_id)

        except PostNotFoundError:
            await status_msg.edit_text(
                "❌ Post not found or is private.\n\nMake sure the post exists and is public."
            )
        except ExtractionError as e:
            await status_msg.edit_text(f"❌ Failed to extract content: {e}")
        except DownloadError as e:
            await status_msg.edit_text(f"❌ Failed to download media: {e}")
        except TelegramError as e:
            await status_msg.edit_text(f"❌ Failed to send content: {e}")
        except Exception as e:
            logger.exception("Unexpected error")
            await status_msg.edit_text(f"❌ Unexpected error: {e}")

    async def send_post(self, update: Update, post: ThreadsPost):
        """
        Send post content to user.

        Strategy:
        - Video: Send video with text as caption
        - Single image: Send photo with text as caption
        - Multiple images: Send as media group
        - Text only: Send as text message
        """
        caption = post.text if post.text else None

        # Video
        if post.has_video:
            await self._send_video(update, post, caption)

        # Images
        elif post.has_images:
            if len(post.media_items) == 1:
                await self._send_single_image(update, post, caption)
            else:
                await self._send_image_carousel(update, post, caption)

        # Text only
        else:
            if caption:
                await update.message.reply_text(caption)
            else:
                await update.message.reply_text("✅ Post has no text or media content.")

    async def _send_video(self, update: Update, post: ThreadsPost, caption: Optional[str]):
        """Send video to user."""
        video_item = next(item for item in post.media_items if item.type == MediaType.VIDEO)

        if not video_item.local_path:
            await update.message.reply_text("❌ Video not downloaded")
            return

        try:
            with open(video_item.local_path, "rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=caption[:1024] if caption else None,  # Telegram caption limit
                    supports_streaming=True,
                )
        except TelegramError as e:
            logger.error(f"Failed to send video: {e}")
            # Fallback: send as document
            with open(video_item.local_path, "rb") as video_file:
                await update.message.reply_document(
                    document=video_file, caption=caption[:1024] if caption else None
                )

    async def _send_single_image(self, update: Update, post: ThreadsPost, caption: Optional[str]):
        """Send single image to user."""
        image_item = post.media_items[0]

        if not image_item.local_path:
            await update.message.reply_text("❌ Image not downloaded")
            return

        with open(image_item.local_path, "rb") as image_file:
            await update.message.reply_photo(
                photo=image_file, caption=caption[:1024] if caption else None
            )

    async def _send_image_carousel(self, update: Update, post: ThreadsPost, caption: Optional[str]):
        """Send multiple images as media group."""
        # Limit to Telegram's max
        items = post.media_items[:TELEGRAM_MAX_MEDIA_GROUP]

        if not all(item.local_path for item in items):
            await update.message.reply_text("❌ Some images were not downloaded")
            return

        # Build media group
        media_group = []
        for i, item in enumerate(items):
            with open(item.local_path, "rb") as image_file:
                # Add caption to first image only
                item_caption = caption[:1024] if i == 0 and caption else None
                media_group.append(InputMediaPhoto(media=image_file.read(), caption=item_caption))

        await update.message.reply_media_group(media=media_group)

        # If text is too long, send separately
        if caption and len(caption) > 1024:
            await update.message.reply_text(f"Full text:\n\n{caption}")

    async def startup(self, application: Application):
        """Initialize resources on startup."""
        logger.info("Initializing bot resources")
        self.scraper = ThreadsScraper()
        await self.scraper.start()

    async def shutdown(self, application: Application):
        """Cleanup resources on shutdown."""
        logger.info("Cleaning up bot resources")
        if self.scraper:
            await self.scraper.close()

    def run(self):
        """Run the bot."""
        # Set up startup/shutdown hooks
        self.app.post_init = self.startup
        self.app.post_shutdown = self.shutdown

        logger.info("Starting bot")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)
