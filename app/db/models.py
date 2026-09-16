"""
Database models for hum.log.

Everything time-related is stored in UTC (`DateTime(timezone=True)`).

The interesting table is `blocks`. A post is not a document — it is a list of
rows, one per paragraph, image or table, each holding a fragment of markdown.
That is what makes "move this paragraph above that one" a single UPDATE instead
of a rewrite of the whole post, and it is what lets the editor forbid pasting
without also forbidding rearranging.

Enumerated column values live in app/enums.py.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from app.db.database import Base
from app.enums import BlockType, BlogStatus, CommentStatus


def generate_uuid():
    """Generate a unique UUID string"""
    return str(uuid.uuid4())


class User(Base):
    """A person who writes here.

    `username` is the unique handle and appears in public URLs. `email` is
    unique too, because it is the only way back into an account whose password
    has been forgotten.
    """

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    user_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    display_name = Column(Text, nullable=False)
    password_hash = Column(String(255), nullable=False)

    bio = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    refresh_tokens = relationship(
        "RefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    reset_codes = relationship(
        "PasswordResetCode", back_populates="user", cascade="all, delete-orphan"
    )
    blogs = relationship("Blog", back_populates="author", cascade="all, delete-orphan")


class RefreshToken(Base):
    """Stores refresh tokens for JWT authentication"""

    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(500), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="refresh_tokens")


class PasswordResetCode(Base):
    """One outstanding "forgot my password" code.

    The code itself is never stored — only its SHA-256, the same way a password
    is never stored. Whoever reads this table therefore cannot reset anyone's
    password with what they find in it.

    `attempts` is what stops an eight-letter code being guessed: five wrong
    answers burn the code and the user has to ask for a new one. Codes are
    deleted once spent, and a user's old codes are swept every time they ask
    for another, so this table stays a handful of rows.
    """

    __tablename__ = "password_reset_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash = Column(String(64), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="reset_codes")


class Blog(Base):
    """One post.

    A post starts life with a title and no blocks at all — an empty page is the
    normal state, not an error.

    `slug` is unique per author rather than globally, so two people can both
    write "on mondays" and both get a readable URL
    (`/{username}/{slug}`). Published posts are world-readable; drafts are
    visible only to their author, and the public routes never join to them.
    """

    __tablename__ = "blogs"
    __table_args__ = (
        UniqueConstraint("author_id", "slug", name="uq_blog_slug_per_author"),
        Index("ix_blogs_status_published_at", "status", "published_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    blog_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    author_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title = Column(Text, nullable=False)
    slug = Column(String(120), nullable=False)
    summary = Column(Text, nullable=True)

    # The `images.image_uuid` of the cover, if there is one. Deliberately not a
    # foreign key: `images` points back at `blogs`, and a real constraint in
    # both directions is a cycle that has to be broken with an ALTER on every
    # create. A cover that has been deleted simply renders as no cover.
    cover_image_uuid = Column(String(36), nullable=True)

    status = Column(String(20), default=BlogStatus.DRAFT, nullable=False, index=True)
    comments_enabled = Column(Boolean, default=True, nullable=False)

    # Set the first time the post is published and left alone afterwards, so
    # unpublishing to fix a typo doesn't move the post to the top of the feed.
    published_at = Column(DateTime(timezone=True), nullable=True)
    view_count = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    author = relationship("User", back_populates="blogs")
    blocks = relationship(
        "Block", back_populates="blog", cascade="all, delete-orphan"
    )
    images = relationship(
        "Image", back_populates="blog", cascade="all, delete-orphan"
    )
    comments = relationship(
        "Comment", back_populates="blog", cascade="all, delete-orphan"
    )


class Image(Base):
    """An uploaded image, in S3. The one thing you are allowed to paste.

    Rows here outlive the block that shows them — deleting an image block
    leaves the file, so an undo doesn't have to re-upload. Files with no block
    pointing at them are cleaned up when the post is deleted, which cascades
    this table and hands the object keys to the storage layer.
    """

    __tablename__ = "images"

    id = Column(Integer, primary_key=True, index=True)
    image_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    blog_id = Column(
        Integer, ForeignKey("blogs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploaded_by_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    image_name = Column(Text, nullable=True)  # Original filename
    mime_type = Column(String(100), nullable=True)  # e.g. "image/jpeg"
    file_size = Column(Integer, nullable=True)  # Size in bytes
    file_format = Column(String(10), nullable=True)  # jpg, png, etc.

    object_key = Column(String(500), nullable=False)
    bucket = Column(String(100), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    blog = relationship("Blog", back_populates="images")
    uploaded_by = relationship("User")
    blocks = relationship("Block", back_populates="image")


class Block(Base):
    """One paragraph, image or table. The unit everything else is built from.

    **Ordering.** `order_key` is a fractional index: a short string, compared as
    a plain string, with the property that a new key can always be produced
    strictly between any two existing ones. Moving block C between A and B
    rewrites C's key and touches nothing else, which is the whole point — a
    thousand-block post reorders in one UPDATE. The generator lives in
    app/blocks/ordering.py.

    Keys are only meaningful *among siblings*: the sort is per
    (`blog_id`, `parent_block_id`).

    **Sorting happens in Python, never in SQL.** Fractional keys are base-62,
    so `"Zz" < "a0"` has to be true — and that is ASCII order, which a Postgres
    database created with an `en_US.UTF-8` collation does not use. Every read
    path loads a post's blocks (a bounded set: they are one post's paragraphs)
    and sorts them with `app.blocks.ordering.sort_key`.

    **Nesting.** `parent_block_id` is the block this one sits under; NULL means
    top level. Tab makes a block a child of the block above it, Shift+Tab
    undoes that, and depth is capped — see app/services/block_service.py.
    Depth is not stored: it is one walk up the parents of an already-loaded
    post, and a stored copy is a stored copy that can drift.

    **Content.** Always markdown, always as typed. `content` for an IMAGE block
    is its caption; the file is `image_id`.
    """

    __tablename__ = "blocks"
    __table_args__ = (
        Index("ix_blocks_blog_parent", "blog_id", "parent_block_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    block_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    blog_id = Column(
        Integer, ForeignKey("blogs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_block_id = Column(
        Integer, ForeignKey("blocks.id", ondelete="CASCADE"), nullable=True, index=True
    )

    # Sorts this block among its siblings. See the class docstring.
    order_key = Column(String(128), nullable=False)

    block_type = Column(String(20), default=BlockType.TEXT, nullable=False)
    content = Column(Text, default="", nullable=False)

    # Anything the editor wants to remember that isn't the text: a table's
    # column alignment, a code block's language, an image's dimensions. The API
    # stores and returns it and otherwise has no opinion about it.
    meta = Column(JSON, nullable=True)

    # Only set on IMAGE blocks. SET NULL rather than CASCADE: losing the file
    # should leave a caption behind to explain the hole, not silently delete
    # the paragraph.
    image_id = Column(
        Integer, ForeignKey("images.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    blog = relationship("Blog", back_populates="blocks")
    image = relationship("Image", back_populates="blocks")
    parent = relationship("Block", remote_side=[id], back_populates="children")
    children = relationship(
        "Block", back_populates="parent", cascade="all, delete-orphan"
    )


class Comment(Base):
    """Somebody's reply to a published post. No account required.

    Anonymity is the default and the point: `author_name` may be blank, and
    `author_email` — if given at all — is never returned by any endpoint. What
    the server does keep is `ip_hash`, a salted hash of the commenter's
    address, which is what makes "delete everything from whoever did that" a
    single query without the raw address ever being written down.

    Deleting a comment deletes its replies, so a thread can't outlive the thing
    it was replying to.
    """

    __tablename__ = "comments"
    __table_args__ = (
        Index("ix_comments_blog_created", "blog_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    comment_uuid = Column(String(36), default=generate_uuid, unique=True, index=True)

    blog_id = Column(
        Integer, ForeignKey("blogs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_comment_id = Column(
        Integer, ForeignKey("comments.id", ondelete="CASCADE"), nullable=True, index=True
    )

    author_name = Column(String(80), nullable=True)
    # Never serialised out. Kept only so the author can be told about a reply
    # later, and so an abusive commenter is more than an IP.
    author_email = Column(String(255), nullable=True)
    body = Column(Text, nullable=False)

    status = Column(String(20), default=CommentStatus.VISIBLE, nullable=False, index=True)
    ip_hash = Column(String(64), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    blog = relationship("Blog", back_populates="comments")
    parent = relationship("Comment", remote_side=[id], back_populates="replies")
    replies = relationship(
        "Comment", back_populates="parent", cascade="all, delete-orphan"
    )
