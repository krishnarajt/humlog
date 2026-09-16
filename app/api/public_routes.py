"""
What a reader sees. No account, no token, no cookie.

Every query here filters on `status == published`. A draft is not 403 from
these routes, it is 404 — whether an unpublished post exists is part of what
being unpublished means.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.blog_routes import _export
from app.api.dependencies import get_optional_user
from app.api.rate_limit import client_ip, rate_limit_comment, rate_limit_public
from app.api.schemas import (
    AuthorOut,
    CommentCreate,
    CommentOut,
    FeedOut,
    PublicBlogDetailOut,
)
from app.api import serializers
from app.db.database import get_db
from app.db.models import Blog, User
from app.enums import BlogStatus, ExportFormat
from app.services import block_service, blog_service, comment_service
from app.services.comment_service import CommentError, CommentRefused

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/public", tags=["public"], dependencies=[Depends(rate_limit_public)]
)

MAX_PAGE_SIZE = 50


def _published_or_404(db: Session, blog_uuid: str) -> Blog:
    blog = blog_service.get_published(db, blog_uuid)
    if blog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such post")
    return blog


@router.get("/blogs", response_model=FeedOut)
def feed(
    author: Optional[str] = Query(default=None, description="Filter to one username"),
    q: Optional[str] = Query(default=None, max_length=120, description="Search titles"),
    limit: int = Query(default=20, ge=1, le=MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Published posts, newest first."""
    query = (
        db.query(Blog)
        .join(User, Blog.author_id == User.id)
        .filter(Blog.status == BlogStatus.PUBLISHED)
    )
    if author:
        query = query.filter(User.username == author.strip().lower())
    if q:
        # Titles only. Searching bodies means searching `blocks`, which is a
        # full-text index and a different piece of work than a feed.
        needle = f"%{q.strip().lower()}%"
        query = query.filter(func.lower(Blog.title).like(needle))

    total = query.count()
    blogs = (
        query.order_by(Blog.published_at.desc(), Blog.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return FeedOut(
        total=total,
        limit=limit,
        offset=offset,
        blogs=serializers.public_blog_list_out(db, blogs),
    )


@router.get("/authors/{username}", response_model=AuthorOut)
def author_profile(username: str, db: Session = Depends(get_db)):
    """One writer. Their posts come from the feed, filtered by `author`."""
    user = db.query(User).filter(User.username == username.strip().lower()).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such writer"
        )
    return AuthorOut.model_validate(user)


@router.get("/blogs/{blog_uuid}", response_model=PublicBlogDetailOut)
def read_blog(
    blog_uuid: str,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """A published post and all of its blocks, in reading order."""
    blog = _published_or_404(db, blog_uuid)
    # An author re-reading their own post is not a reader.
    if current_user is None or current_user.id != blog.author_id:
        blog_service.register_view(db, blog)
    tree = block_service.load_tree(db, blog.id)
    return serializers.public_blog_detail_out(db, blog, tree)


@router.get("/posts/{username}/{slug}", response_model=PublicBlogDetailOut)
def read_blog_by_slug(
    username: str,
    slug: str,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """The same post, by the URL a person would actually type or share."""
    blog = blog_service.get_published_by_slug(db, username, slug)
    if blog is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such post")
    if current_user is None or current_user.id != blog.author_id:
        blog_service.register_view(db, blog)
    tree = block_service.load_tree(db, blog.id)
    return serializers.public_blog_detail_out(db, blog, tree)


@router.get("/blogs/{blog_uuid}/export")
def export_published_blog(
    blog_uuid: str,
    export_format: ExportFormat = Query(default=ExportFormat.MARKDOWN, alias="format"),
    download: bool = Query(default=False),
    bundle: bool = Query(default=False),
    include_header: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """Anyone can take a published post away as markdown or HTML.

    The same code path the author's own export uses, so what a reader downloads
    is what the writer would have.
    """
    blog = _published_or_404(db, blog_uuid)
    return _export(db, blog, export_format, download, include_header, bundle)


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

@router.get("/blogs/{blog_uuid}/comments", response_model=list[CommentOut])
def list_comments(blog_uuid: str, db: Session = Depends(get_db)):
    """A post's comments, oldest first, hidden ones left out.

    Flat: `parent_comment_uuid` says which comment each one answers, and
    replies only ever nest one level, so grouping them is a single pass on the
    client.
    """
    blog = _published_or_404(db, blog_uuid)
    return serializers.comments_out(comment_service.list_comments(db, blog.id))


@router.post(
    "/blogs/{blog_uuid}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_comment)],
)
def add_comment(
    blog_uuid: str,
    payload: CommentCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Leave a comment. No account needed, and a name is optional."""
    blog = _published_or_404(db, blog_uuid)

    if payload.website:
        # The honeypot field. No human ever sees it, so anything in it came
        # from something filling in every input on the page. Answered as a
        # success so the thing doing it has no signal to adapt to.
        logger.info(f"Honeypot caught a comment on {blog_uuid}")
        return CommentOut(
            comment_uuid="", author_name=payload.author_name, body=payload.body
        )

    try:
        comment = comment_service.create_comment(
            db,
            blog,
            body=payload.body,
            author_name=payload.author_name,
            author_email=str(payload.author_email) if payload.author_email else None,
            parent_comment_uuid=payload.parent_comment_uuid,
            ip=client_ip(request),
        )
    except CommentRefused as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        )
    except CommentError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return serializers.comment_out(comment)
