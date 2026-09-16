"""Request and response bodies. Enums come straight from app/enums.py."""

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.enums import BlockType, BlogStatus, CommentStatus, ExportFormat, MoveDirection


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageOut(BaseModel):
    detail: str


# ---------------------------------------------------------------------------
# Auth / users
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    email: EmailStr
    # No rules on the password itself, deliberately. Length is not what makes
    # an account safe here; the hash and the rate limit are.
    password: str = Field(min_length=1)
    display_name: Optional[str] = Field(default=None, max_length=200)

    @field_validator("username")
    @classmethod
    def clean_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not value:
            raise ValueError("Username cannot be blank")
        if any(char.isspace() for char in value):
            raise ValueError("Username cannot contain spaces")
        if not all(char.isalnum() or char in "-_." for char in value):
            raise ValueError(
                "Usernames can hold letters, numbers, dots, dashes and underscores"
            )
        return value


class LoginRequest(BaseModel):
    # Username or email — whichever the person remembers.
    identifier: str = Field(min_length=1)
    password: str

    @field_validator("identifier")
    @classmethod
    def clean_identifier(cls, value: str) -> str:
        return value.strip()


class RefreshRequest(BaseModel):
    refresh_token: str


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=200)
    bio: Optional[str] = Field(default=None, max_length=2000)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    # Eight letters, typed back in from the email. Case and spacing are the
    # reader's business, not theirs.
    code: str = Field(min_length=1, max_length=32)
    new_password: str = Field(min_length=1)


class UserOut(ORMModel):
    user_uuid: str
    username: str
    email: EmailStr
    display_name: str
    bio: Optional[str] = None
    created_at: Optional[datetime] = None


class AuthorOut(ORMModel):
    """A writer as a reader sees them. No email."""
    username: str
    display_name: str
    bio: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

class ImageOut(ORMModel):
    image_uuid: str
    image_name: Optional[str] = None
    mime_type: Optional[str] = None
    file_size: Optional[int] = None
    # A stable link on this server, good for as long as the image exists. The
    # short-lived signed one is a separate call.
    url: Optional[str] = None
    created_at: Optional[datetime] = None


class PresignedUrlOut(BaseModel):
    image_uuid: str
    url: str
    expires_in: int


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

class BlockCreate(BaseModel):
    """Where a new block goes, and what is in it.

    Placement is decided in this order: `after_block_uuid` (as its next
    sibling — what Enter does), then `parent_block_uuid` (as its last child),
    then neither, meaning the end of the post.
    """

    content: str = ""
    block_type: BlockType = BlockType.TEXT
    meta: Optional[dict[str, Any]] = None
    image_uuid: Optional[str] = None
    after_block_uuid: Optional[str] = None
    parent_block_uuid: Optional[str] = None


class BlockUpdate(BaseModel):
    content: Optional[str] = None
    block_type: Optional[BlockType] = None
    meta: Optional[dict[str, Any]] = None
    image_uuid: Optional[str] = None
    # Explicit, because `image_uuid: None` means "leave it alone".
    clear_image: bool = False


class BlockMove(BaseModel):
    """Either one step, or one exact landing.

    Give `direction` for the up/down arrows and the rest is ignored. Leave it
    out and this is a drag-and-drop result, in which case both other fields are
    read literally: a null `parent_block_uuid` means top level, and a null
    `after_block_uuid` means first among its new siblings.
    """

    direction: Optional[MoveDirection] = None
    parent_block_uuid: Optional[str] = None
    after_block_uuid: Optional[str] = None


class BlockOut(BaseModel):
    block_uuid: str
    parent_block_uuid: Optional[str] = None
    # How deeply nested, top level being 0. Derived, not stored — see
    # app/db/models.py.
    depth: int = 0
    order_key: str
    block_type: str
    content: str
    meta: Optional[dict[str, Any]] = None
    image: Optional[ImageOut] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Blogs
# ---------------------------------------------------------------------------

class BlogCreate(BaseModel):
    title: str = Field(default="", max_length=300)
    summary: Optional[str] = Field(default=None, max_length=2000)


class BlogUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    summary: Optional[str] = Field(default=None, max_length=2000)
    # Giving one explicitly overrides the one derived from the title. Renaming
    # a published post does not move its URL on its own.
    slug: Optional[str] = Field(default=None, max_length=120)
    cover_image_uuid: Optional[str] = None
    clear_cover: bool = False
    comments_enabled: Optional[bool] = None


class BlogOut(ORMModel):
    """A post as its author sees it in their list."""

    blog_uuid: str
    title: str
    slug: str
    summary: Optional[str] = None
    cover_image_uuid: Optional[str] = None
    status: BlogStatus
    comments_enabled: bool
    published_at: Optional[datetime] = None
    view_count: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    block_count: int = 0
    comment_count: int = 0


class BlogDetailOut(BlogOut):
    author: AuthorOut
    blocks: list[BlockOut] = []


class PublicBlogOut(ORMModel):
    """A post as a reader sees it. No draft ever reaches this shape."""

    blog_uuid: str
    title: str
    slug: str
    summary: Optional[str] = None
    cover_image_uuid: Optional[str] = None
    published_at: Optional[datetime] = None
    view_count: int = 0
    comments_enabled: bool = True
    comment_count: int = 0
    author: AuthorOut


class PublicBlogDetailOut(PublicBlogOut):
    blocks: list[BlockOut] = []


class FeedOut(BaseModel):
    total: int
    limit: int
    offset: int
    blogs: list[PublicBlogOut]


class ExportOut(BaseModel):
    """The export as JSON, for a client that wants to do its own downloading.

    `GET .../export?download=true` returns the same bytes as a file instead.
    """

    blog_uuid: str
    format: ExportFormat
    filename: str
    content: str


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

class CommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    author_name: Optional[str] = Field(default=None, max_length=80)
    # Optional, never returned to anybody, and not verified. It exists so a
    # commenter can be told about a reply one day.
    author_email: Optional[EmailStr] = None
    parent_comment_uuid: Optional[str] = None
    # A honeypot. No human sees this field, so anything in it came from
    # something filling in every input on the page.
    website: Optional[str] = Field(default=None, max_length=200)


class CommentOut(BaseModel):
    comment_uuid: str
    parent_comment_uuid: Optional[str] = None
    # None means the commenter left it blank; render that as "Anonymous".
    author_name: Optional[str] = None
    body: str
    status: CommentStatus = CommentStatus.VISIBLE
    created_at: Optional[datetime] = None


class CommentModerate(BaseModel):
    status: CommentStatus
