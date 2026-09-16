"""
Comments. Anyone can leave one, nobody has to sign in, and the server has to
survive that.

Three things keep it survivable, none of which involve asking a reader who they
are:

* Comments only exist on published posts, and only where the author left them
  switched on.
* Every commenter is remembered as a salted hash of their address, which is
  enough to rate-limit them and enough to remove everything they wrote, and not
  enough to be a record of who read what.
* The same address posting the same words twice inside ten minutes is a double
  submit, not a second opinion, and is refused.

The per-request throttle in front of this lives in app/api/rate_limit.py. This
module holds the limits that have to outlive a restart, which means the ones
counted in the database.
"""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import Blog, Comment
from app.enums import BlogStatus, CommentStatus

logger = logging.getLogger(__name__)

MAX_BODY_CHARS = 4000
MAX_NAME_CHARS = 80

# Per address, per post, over an hour. High enough for an argument, low enough
# that a script gets bored.
MAX_PER_HOUR_PER_BLOG = 20
DUPLICATE_WINDOW_MINUTES = 10

# Replies nest one level and no further. A reply to a reply attaches to the
# same parent, so a thread is always a comment and its answers — which is the
# whole of what a comment section under a blog post needs, and it removes the
# question of how deep the frontend has to render.
MAX_REPLY_DEPTH = 1


class CommentError(ValueError):
    """A comment that will not be accepted, with the reason a reader can act on."""


class CommentRefused(CommentError):
    """Accepted-looking but throttled or duplicated. Answered as 429/409."""


def hash_ip(ip: str) -> str:
    """A salted hash of an address.

    Salted with `SECRET_KEY`, so the table is not a rainbow table of everyone
    who has ever commented, and stable, so rate limits and bulk removal work.
    """
    salt = os.getenv("SECRET_KEY", "")
    return hashlib.sha256(f"{salt}:{ip}".encode("utf-8")).hexdigest()


def _resolve_parent(db: Session, blog: Blog, parent_uuid: Optional[str]) -> Optional[Comment]:
    if parent_uuid is None:
        return None
    parent = (
        db.query(Comment)
        .filter(Comment.comment_uuid == parent_uuid, Comment.blog_id == blog.id)
        .first()
    )
    if parent is None:
        raise CommentError("The comment being replied to no longer exists")
    if parent.status != CommentStatus.VISIBLE:
        raise CommentError("The comment being replied to has been removed")
    # Flatten anything deeper than one level onto the top of its thread.
    depth = 0
    while parent.parent_comment_id is not None and depth < 32:
        parent = parent.parent
        depth += 1
    return parent


def create_comment(
    db: Session,
    blog: Blog,
    *,
    body: str,
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
    parent_comment_uuid: Optional[str] = None,
    ip: str = "unknown",
) -> Comment:
    if blog.status != BlogStatus.PUBLISHED:
        raise CommentError("This post isn't published yet")
    if not blog.comments_enabled:
        raise CommentError("Comments are switched off for this post")

    body = (body or "").replace("\r\n", "\n").strip()
    if not body:
        raise CommentError("A comment needs something in it")
    if len(body) > MAX_BODY_CHARS:
        raise CommentError(f"Comments are limited to {MAX_BODY_CHARS} characters")

    name = (author_name or "").strip()[:MAX_NAME_CHARS] or None
    email = (author_email or "").strip().lower() or None
    parent = _resolve_parent(db, blog, parent_comment_uuid)

    ip_hash = hash_ip(ip)
    now = datetime.now(timezone.utc)

    recent = (
        db.query(Comment)
        .filter(
            Comment.blog_id == blog.id,
            Comment.ip_hash == ip_hash,
            Comment.created_at >= now - timedelta(hours=1),
        )
        .count()
    )
    if recent >= MAX_PER_HOUR_PER_BLOG:
        raise CommentRefused(
            "That's a lot of comments on one post in an hour. Try again later."
        )

    duplicate = (
        db.query(Comment.id)
        .filter(
            Comment.blog_id == blog.id,
            Comment.ip_hash == ip_hash,
            Comment.body == body,
            Comment.created_at >= now - timedelta(minutes=DUPLICATE_WINDOW_MINUTES),
        )
        .first()
    )
    if duplicate is not None:
        raise CommentRefused("You've already posted that one")

    comment = Comment(
        blog_id=blog.id,
        parent_comment_id=parent.id if parent else None,
        author_name=name,
        author_email=email,
        body=body,
        ip_hash=ip_hash,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return comment


def list_comments(db: Session, blog_id: int, include_hidden: bool = False) -> list[Comment]:
    """Every comment on a post, oldest first.

    Flat, with `parent_comment_uuid` on each: nesting is one level deep by
    construction, so the client can group them in a single pass and the server
    doesn't have to serialise a tree.
    """
    query = db.query(Comment).filter(Comment.blog_id == blog_id)
    if not include_hidden:
        query = query.filter(Comment.status == CommentStatus.VISIBLE)
    return query.order_by(Comment.created_at.asc(), Comment.id.asc()).all()


def get_comment(db: Session, blog_id: int, comment_uuid: str) -> Comment:
    comment = (
        db.query(Comment)
        .filter(Comment.comment_uuid == comment_uuid, Comment.blog_id == blog_id)
        .first()
    )
    if comment is None:
        raise CommentError("No such comment")
    return comment


def set_status(db: Session, comment: Comment, status: str) -> Comment:
    """Hide or restore a comment. The author of the post decides."""
    comment.status = status
    db.commit()
    db.refresh(comment)
    return comment


def delete_comment(db: Session, comment: Comment) -> None:
    """Delete a comment and its replies."""
    db.delete(comment)
    db.commit()


def delete_by_ip_hash(db: Session, blog_id: int, ip_hash: str) -> int:
    """Remove everything one commenter left on one post. Returns how many went."""
    removed = (
        db.query(Comment)
        .filter(Comment.blog_id == blog_id, Comment.ip_hash == ip_hash)
        .delete(synchronize_session=False)
    )
    db.commit()
    logger.info(f"Removed {removed} comments from one commenter on blog {blog_id}")
    return removed
