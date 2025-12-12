# Threads Content Extractor Bot

Telegram bot that extracts and delivers content from public Threads.com posts.

## Features

✅ **Text extraction** - Extracts post captions from og:description meta tags  
✅ **Image extraction** - Downloads and sends images from Threads posts  
✅ **Smart caching** - Caches downloaded media to reduce redundant downloads  
✅ **Robust scraping** - Uses Playwright for reliable JS-heavy website handling  
✅ **Data validation** - Uses Pydantic for automatic input validation and type safety  
✅ **Error handling** - Graceful handling of invalid URLs, private posts, and size limits  
✅ **URL parameter handling** - Strips tracking parameters like `?xmt=...`

### Limitations

⚠️ **Video support** - Currently limited to posts where Threads provides `og:video` meta tags. Many video posts don't include these tags and will only return text/images.  
⚠️ **Carousel support** - Multiple images/videos in a single post may not be fully extracted (only the first media from og:image is returned).  

## Architecture

```
src/
├── bot.py          # Telegram bot handlers and delivery logic
├── scraper.py      # Playwright-based Threads scraper
├── downloader.py   # Media downloader with caching
├── url_parser.py   # URL validation and parsing
├── models.py       # Data models
└── config.py       # Configuration management

main.py             # Entry point
```

## How It Works

1. **URL Validation** - Parses and validates Threads URL format
2. **Content Extraction** - Launches headless browser to scrape post content
3. **Media Download** - Downloads images/videos with size validation
4. **Telegram Delivery** - Sends content based on type:
   - Video → `send_video()`
   - Single image → `send_photo()`
   - Multiple images → `send_media_group()`
   - Text only → `send_message()`

## Installation

### Prerequisites

- Python 3.12+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))

### Setup

```bash
# Clone repository
git clone <repo-url>
cd save-media-from-threads-telegram-bot

# Install dependencies
pip install -e .

# Install Playwright browsers
playwright install chromium

# Configure environment
cp .env.example .env
# Edit .env and add your TELEGRAM_BOT_TOKEN
```

### Environment Variables

```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
CACHE_DIR=./cache              # Optional, defaults to ./cache
LOG_LEVEL=INFO                 # Optional, defaults to INFO
MAX_RETRIES=3                  # Optional, defaults to 3
BROWSER_TIMEOUT=30000          # Optional, defaults to 30s
```

## Usage

### Start the bot

```bash
python main.py
```

### In Telegram

1. Start a chat with your bot
2. Send `/start` to see instructions
3. Send a Threads post URL:

   ```
   https://www.threads.com/@username/post/POST_ID
   ```

4. Receive the extracted content

### Example URLs

```
https://www.threads.com/@alvinfoo/post/DSJbc6ciVdv
https://www.threads.com/@zuck/post/C8rOe_Hr4Sm
```

## Testing Pydantic Integration

```bash
# Run validation tests
python test_pydantic.py

# Run unit tests
pytest -v
```

See `PYDANTIC_GUIDE.md` for detailed documentation on data validation.

## Telegram Limits

The bot respects Telegram API limits:

- **Max file size**: 50 MB
- **Max photo size**: 10 MB
- **Max media group**: 10 items
- **Caption length**: 1024 characters (splits longer text)

## Error Handling

| Error | Response |
|-------|----------|
| Invalid URL | "❌ Invalid URL. Please send a valid Threads post URL." |
| Post not found | "❌ Post not found or is private." |
| File too large | "❌ File exceeds Telegram size limit" |
| Extraction timeout | Retries 3 times with exponential backoff |

## Technical Details

### Scraping Strategy

1. **Playwright**: Headless Chromium for JS rendering
2. **Wait for content**: Waits for `article` element + 2s buffer
3. **Multi-selector extraction**: Tries multiple DOM selectors for robustness
4. **Media detection**: Searches for `video` and `img` elements in post article
5. **URL filtering**: Filters out avatars, thumbnails, profile pics

### Retry Logic

- **Max retries**: 3 attempts
- **Backoff**: Exponential (2^attempt seconds)
- **Timeout**: 30s per page load

### Caching

Files are cached by post ID:

```
cache/
└── POST_ID/
    ├── abc123def456.jpg
    └── xyz789ghi012.mp4
```

Cache is cleaned after successful delivery.

### Data Validation (Pydantic)

All data models use Pydantic for automatic validation:

- **URL validation**: Ensures URLs have proper format (http/https)
- **Field constraints**: Non-empty strings, min/max lengths
- **Type safety**: Automatic type conversion and validation
- **Settings management**: Environment variables with validation
- **JSON serialization**: Built-in conversion to/from JSON

See `PYDANTIC_GUIDE.md` for detailed usage and examples.

## Development

### Install dev dependencies

```bash
pip install -e ".[dev]"
```

### Code formatting

```bash
black src/
ruff check src/
```

### Testing

```bash
pytest
```

## Limitations

- **Public posts only** - No authentication for private accounts
- **JS-dependent** - Requires full browser rendering (slower than API)
- **Meta changes** - DOM selectors may break if Threads updates their HTML
- **Rate limits** - No built-in rate limiting (add if needed)

## Troubleshooting

### "Browser not initialized"

- Make sure Playwright browsers are installed: `playwright install chromium`

### "Post not found"

- Verify URL format
- Check if post is public
- Try opening URL in browser manually

### "Timeout loading post"

- Increase `BROWSER_TIMEOUT` in `.env`
- Check internet connection
- Threads may be blocking automated access

### "File too large"

- Videos from Threads can exceed 50MB
- Bot automatically rejects files over Telegram limit
- No workaround without transcoding (not implemented)

## Roadmap

- [ ] Add rate limiting per user
- [ ] Implement video transcoding for large files
- [ ] Add support for post threads (multiple posts)
- [ ] Store post metadata in database
- [ ] Add admin commands for cache management
- [ ] Implement webhook mode (alternative to polling)

## License

MIT

## Credits

Built with:

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [Playwright](https://playwright.dev/python/)
- [aiohttp](https://docs.aiohttp.org/)
