"""Main entry point for the bot."""

import logging
import sys

from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from src.bot import TelegramBot
from src.config import settings

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, settings.log_level),
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("bot.log")],
)

logger = logging.getLogger(__name__)


def main():
    """Run the Telegram bot."""
    try:
        bot = TelegramBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
