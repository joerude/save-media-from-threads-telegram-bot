"""URL validation and parsing for Threads posts."""

import re

from pydantic import BaseModel, Field, field_validator


class URLParserError(Exception):
    """Raised when URL parsing fails."""

    pass


class ThreadsURL(BaseModel):
    """Parsed Threads URL components with validation."""

    username: str = Field(..., min_length=1, description="Threads username")
    post_id: str = Field(..., min_length=1, description="Unique post identifier")
    full_url: str = Field(..., description="Full Threads URL")

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Validate username format."""
        if not re.match(r"^[a-zA-Z0-9_.]+$", v):
            raise ValueError("Username contains invalid characters")
        return v

    @field_validator("post_id")
    @classmethod
    def validate_post_id(cls, v: str) -> str:
        """Validate post ID format."""
        if not re.match(r"^[a-zA-Z0-9_-]+$", v):
            raise ValueError("Post ID contains invalid characters")
        return v

    @field_validator("full_url")
    @classmethod
    def validate_full_url(cls, v: str) -> str:
        """Validate URL format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        if "threads.com" not in v:
            raise ValueError("URL must be a threads.com URL")
        return v


class ThreadsURLParser:
    """Parser for Threads.com URLs."""

    # Pattern: https://www.threads.com/@username/post/POST_ID (with optional query params)
    PATTERN = re.compile(
        r"^https?://(?:www\.)?threads\.com/@([a-zA-Z0-9_.]+)/post/([a-zA-Z0-9_-]+)(?:/|\?|$)"
    )

    @classmethod
    def parse(cls, url: str) -> ThreadsURL:
        """
        Parse and validate a Threads URL.

        Args:
            url: The Threads post URL to parse

        Returns:
            ThreadsURL object with parsed components

        Raises:
            URLParserError: If URL is invalid
        """
        url = url.strip()

        # Remove query parameters (like ?xmt=...) for matching
        clean_url = url.split("?")[0]

        match = cls.PATTERN.match(clean_url)

        if not match:
            raise URLParserError(
                "Invalid Threads URL. Expected format: "
                "https://www.threads.com/@username/post/POST_ID"
            )

        username, post_id = match.groups()

        # Use clean URL without tracking parameters
        canonical_url = f"https://www.threads.com/@{username}/post/{post_id}"

        return ThreadsURL(username=username, post_id=post_id, full_url=canonical_url)

    @classmethod
    def is_valid(cls, url: str) -> bool:
        """Check if URL is a valid Threads post URL."""
        try:
            cls.parse(url)
            return True
        except (URLParserError, ValueError):
            return False
