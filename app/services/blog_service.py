"""
Posts: creating them, naming them, publishing them, taking them away again.
"""

import logging
import re
import unicodedata
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Blog, Comment, User
from app.enums import BlogStatus, CommentStatus

logger = logging.getLogger(__name__)

MAX_SLUG_LENGTH = 100
MAX_TITLE_LENGTH = 300
DEFAULT_TITLE = "Untitled"


class BlogError(ValueError):
    """Something the caller asked for that a post cannot do."""


# ---------------------------------------------------------------------------
# Slugs
# ---------------------------------------------------------------------------

def slugify(title: str) -> str:
    """A title turned into the part of the URL a person can read.

    Accents are folded rather than dropped, so "Café" becomes "cafe" and not
    "caf". A title with nothing left after folding — punctuation, or a script
    with no ASCII equivalent — falls back to "post", and `unique_slug` numbers
    it from there.
    """
    normalised = unicodedata.normalize("NFKD", title or "")
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return slug[:MAX_SLUG_LENGTH].strip("-") or "post"


def unique_slug(db: Session, author_id: int, title: str, exclude_blog_id: int | None = None) -> str:
    """A slug free for this author. Two people may both write "on-mondays"."""
    base = slugify(title)
    candidate = base
    suffix = 2
    while True:
        query = db.query(Blog.id).filter(
            Blog.author_id == author_id, Blog.slug == candidate
        )
        if exclude_blog_id is not None:
            query = query.filter(Blog.id != exclude_blog_id)
        if query.first() is None:
            return candidate
        # Trim the base so the counter never pushes the slug past the column.
        candidate = f"{base[:MAX_SLUG_LENGTH - len(str(suffix)) - 1]}-{suffix}"
        suffix += 1


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def create_blog(
    db: Session, author: User, *, title: str = "", summary: Optional[str] = None
) -> Blog:
    """Start a post. It has no blocks, and that is the normal first state."""
    title = (title or "").strip()[:MAX_TITLE_LENGTH] or DEFAULT_TITLE
    blog = Blog(
        author_id=author.id,
        title=title,
        slug=unique_slug(db, author.id, title),
        summary=(summary or "").strip() or None,
        status=BlogStatus.DRAFT,
    )
    db.add(blog)
    db.commit()
    db.refresh(blog)
    logger.info(f"{author.username} started '{blog.title}' ({blog.blog_uuid})")
    return blog


def update_blog(
    db: Session,
    blog: Blog,
    *,
    title: Optional[str] = None,
    summary: Optional[str] = None,
    slug: Optional[str] = None,
    cover_image_uuid: Optional[str] = None,
    clear_cover: bool = False,
    comments_enabled: Optional[bool] = None,
) -> Blog:
    """Edit a post's own fields. Its blocks are a separate matter entirely.

    Renaming a published post leaves its slug alone unless one is given
    explicitly: the old URL is out in the world and quietly breaking it is
    worse than a slug that no longer matches the title.
    """
    if title is not None:
        cleaned = title.strip()[:MAX_TITLE_LENGTH]
        if not cleaned:
            raise BlogError("A post needs a title")
        was_untitled = blog.title == DEFAULT_TITLE
        blog.title = cleaned
        if blog.status == BlogStatus.DRAFT or was_untitled:
            blog.slug = unique_slug(db, blog.author_id, cleaned, exclude_blog_id=blog.id)

    if slug is not None:
        wanted = slugify(slug)
        if wanted != blog.slug:
            blog.slug = unique_slug(db, blog.author_id, wanted, exclude_blog_id=blog.id)

    if summary is not None:
        blog.summary = summary.strip() or None

    if cover_image_uuid is not None:
        blog.cover_image_uuid = cover_image_uuid
    elif clear_cover:
        blog.cover_image_uuid = None

    if comments_enabled is not None:
        blog.comments_enabled = comments_enabled

    db.commit()
    db.refresh(blog)
    return blog


def publish(db: Session, blog: Blog) -> Blog:
    """Make a post public.

    `published_at` is stamped once and never again, so unpublishing to fix a
    typo and publishing again doesn't jump the post back to the top of the
    feed.
    """
    blog.status = BlogStatus.PUBLISHED
    if blog.published_at is None:
        blog.published_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(blog)
    logger.info(f"Published '{blog.title}' ({blog.blog_uuid})")
    return blog


def unpublish(db: Session, blog: Blog) -> Blog:
    """Pull a post back to draft. Comments already left on it are kept."""
    blog.status = BlogStatus.DRAFT
    db.commit()
    db.refresh(blog)
    return blog


def register_view(db: Session, blog: Blog) -> None:
    """Count a read. Never worth failing a page load over."""
    try:
        blog.view_count = (blog.view_count or 0) + 1
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not count a view on {blog.blog_uuid}: {e}")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def get_own_blog(db: Session, author: User, blog_uuid: str) -> Blog:
    blog = (
        db.query(Blog)
        .filter(Blog.blog_uuid == blog_uuid, Blog.author_id == author.id)
        .first()
    )
    if blog is None:
        raise BlogError("No such post")
    return blog


def get_published(db: Session, blog_uuid: str) -> Optional[Blog]:
    return (
        db.query(Blog)
        .filter(Blog.blog_uuid == blog_uuid, Blog.status == BlogStatus.PUBLISHED)
        .first()
    )


def get_published_by_slug(db: Session, username: str, slug: str) -> Optional[Blog]:
    return (
        db.query(Blog)
        .join(User, Blog.author_id == User.id)
        .filter(
            User.username == username.strip().lower(),
            Blog.slug == slug,
            Blog.status == BlogStatus.PUBLISHED,
        )
        .first()
    )


def comment_count(db: Session, blog_id: int) -> int:
    return (
        db.query(Comment)
        .filter(Comment.blog_id == blog_id, Comment.status == CommentStatus.VISIBLE)
        .count()
    )
