"""Data models for Threads content."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class MediaType(str, Enum):
    """Type of media in a post."""

    TEXT_ONLY = "text_only"
    IMAGE = "image"
    VIDEO = "video"
    CAROUSEL = "carousel"


class MediaItem(BaseModel):
    """Represents a single media item (image or video)."""

    url: str = Field(..., description="URL of the media item")
    type: MediaType = Field(..., description="Type of media (image or video)")
    local_path: Optional[str] = Field(None, description="Local file path after download")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Validate URL format."""
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        return v

    model_config = {"use_enum_values": True}


class ThreadsPost(BaseModel):
    """Represents extracted Threads post content."""

    post_id: str = Field(..., min_length=1, description="Unique post identifier")
    username: str = Field(..., min_length=1, description="Username of post author")
    text: Optional[str] = Field(None, description="Text content of the post")
    media_type: MediaType = Field(..., description="Primary media type in post")
    media_items: list[MediaItem] = Field(default_factory=list, description="List of media items")

    @field_validator("post_id", "username")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        """Ensure strings are not empty."""
        if not v.strip():
            raise ValueError("Field cannot be empty or whitespace")
        return v.strip()

    @property
    def has_media(self) -> bool:
        """Check if post contains media."""
        return len(self.media_items) > 0

    @property
    def has_video(self) -> bool:
        """Check if post contains video."""
        return any(item.type == MediaType.VIDEO for item in self.media_items)

    @property
    def has_images(self) -> bool:
        """Check if post contains images."""
        return any(item.type == MediaType.IMAGE for item in self.media_items)

    model_config = {"use_enum_values": True}
