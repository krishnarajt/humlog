"""A writer's own posts: the list, the settings, publishing, and the export."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, own_blog
from app.api.schemas import (
    BlogCreate,
    BlogDetailOut,
    BlogOut,
    BlogUpdate,
    CommentModerate,
    CommentOut,
    ExportOut,
    MessageOut,
)
from app.api import serializers
from app.db.database import get_db
from app.db.models import Block, Blog, User
from app.enums import BlogStatus, ExportFormat
from app.services import block_service, blog_service, comment_service, export_service
from app.services import storage_service
from app.services.blog_service import BlogError
from app.services.comment_service import CommentError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/blogs", tags=["blogs"])


@router.post("", response_model=BlogOut, status_code=status.HTTP_201_CREATED)
def create_blog(
    payload: BlogCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a post. It arrives empty — the first block comes from the first keystroke."""
    blog = blog_service.create_blog(
        db, current_user, title=payload.title, summary=payload.summary
    )
    return serializers.blog_out(blog)


@router.get("", response_model=list[BlogOut])
def list_my_blogs(
    status_filter: BlogStatus | None = Query(default=None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Everything I have written, newest first. Drafts included — they are mine."""
    query = db.query(Blog).filter(Blog.author_id == current_user.id)
    if status_filter is not None:
        query = query.filter(Blog.status == status_filter)
    # Last touched first. A post that has never been edited falls back to when
    # it was started, rather than sorting as a NULL and landing wherever the
    # database feels like putting it.
    blogs = query.order_by(
        func.coalesce(Blog.updated_at, Blog.created_at).desc(), Blog.id.desc()
    ).all()
    return serializers.blog_list_out(db, blogs)


@router.get("/{blog_uuid}", response_model=BlogDetailOut)
def get_blog(
    blog_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """One of my posts, with every block in reading order."""
    blog = own_blog(db, current_user, blog_uuid)
    tree = block_service.load_tree(db, blog.id)
    return serializers.blog_detail_out(db, blog, tree)


@router.patch("/{blog_uuid}", response_model=BlogOut)
def update_blog(
    blog_uuid: str,
    payload: BlogUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    blog = own_blog(db, current_user, blog_uuid)
    try:
        blog = blog_service.update_blog(
            db,
            blog,
            title=payload.title,
            summary=payload.summary,
            slug=payload.slug,
            cover_image_uuid=payload.cover_image_uuid,
            clear_cover=payload.clear_cover,
            comments_enabled=payload.comments_enabled,
        )
    except BlogError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return serializers.blog_out(blog)


@router.post("/{blog_uuid}/publish", response_model=BlogOut)
def publish_blog(
    blog_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Make a post public. An empty post is not one."""
    blog = own_blog(db, current_user, blog_uuid)
    if not db.query(Block.id).filter(Block.blog_id == blog.id).first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="There's nothing in this post yet",
        )
    return serializers.blog_out(blog_service.publish(db, blog))


@router.post("/{blog_uuid}/unpublish", response_model=BlogOut)
def unpublish_blog(
    blog_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Pull a post back to draft. Its comments are kept, and come back with it."""
    blog = own_blog(db, current_user, blog_uuid)
    return serializers.blog_out(blog_service.unpublish(db, blog))


@router.delete("/{blog_uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_blog(
    blog_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a post, its blocks, its comments and its images.

    The object keys are read before the row goes, because after the cascade
    there is nothing left to say which files belonged to it. Storage failing
    afterwards leaves orphaned objects, not a half-deleted post.
    """
    blog = own_blog(db, current_user, blog_uuid)
    keys = block_service.image_keys_for_blog(db, blog.id)

    db.delete(blog)
    db.commit()

    if keys and not storage_service.delete_images(keys):
        logger.warning(f"Deleted post {blog_uuid} but left {len(keys)} objects behind")


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@router.get("/{blog_uuid}/export")
def export_blog(
    blog_uuid: str,
    export_format: ExportFormat = Query(default=ExportFormat.MARKDOWN, alias="format"),
    download: bool = Query(default=False),
    bundle: bool = Query(default=False),
    include_header: bool = Query(default=True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The whole post as one file — markdown or HTML, draft or published.

    JSON by default, so a client can put the text somewhere itself. With
    `download=true` the same bytes come back as an attachment. `bundle=true`
    returns a zip with relative image paths and all referenced image files.
    """
    blog = own_blog(db, current_user, blog_uuid)
    return _export(db, blog, export_format, download, include_header, bundle)


def _export(
    db: Session,
    blog,
    export_format: str,
    download: bool,
    include_header: bool,
    bundle: bool = False,
):
    """Shared by the author's export and the public one."""
    tree = block_service.load_tree(db, blog.id)

    if bundle:
        try:
            content = export_service.to_bundle(
                blog,
                tree,
                export_format=export_format,
                include_header=include_header,
            )
        except export_service.ExportBundleError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            )
        filename = export_service.filename_for(blog, "zip")
        return Response(
            content=content,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if export_format == ExportFormat.HTML:
        content = export_service.to_html(blog, tree, include_header=include_header)
        media_type, extension = "text/html; charset=utf-8", "html"
    else:
        content = export_service.to_markdown(blog, tree, include_header=include_header)
        media_type, extension = "text/markdown; charset=utf-8", "md"

    filename = export_service.filename_for(blog, extension)
    if download:
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    return ExportOut(
        blog_uuid=blog.blog_uuid,
        format=export_format,
        filename=filename,
        content=content,
    )


# ---------------------------------------------------------------------------
# Moderation
# ---------------------------------------------------------------------------

@router.get("/{blog_uuid}/comments", response_model=list[CommentOut])
def list_comments_for_author(
    blog_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every comment on my post, hidden ones included."""
    blog = own_blog(db, current_user, blog_uuid)
    return serializers.comments_out(
        comment_service.list_comments(db, blog.id, include_hidden=True)
    )


@router.patch("/{blog_uuid}/comments/{comment_uuid}", response_model=CommentOut)
def moderate_comment(
    blog_uuid: str,
    comment_uuid: str,
    payload: CommentModerate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Hide a comment, or put it back. Hiding keeps it; deleting does not."""
    blog = own_blog(db, current_user, blog_uuid)
    try:
        comment = comment_service.get_comment(db, blog.id, comment_uuid)
    except CommentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return serializers.comment_out(comment_service.set_status(db, comment, payload.status))


@router.delete("/{blog_uuid}/comments/{comment_uuid}", response_model=MessageOut)
def delete_comment(
    blog_uuid: str,
    comment_uuid: str,
    same_commenter: bool = Query(
        default=False,
        description="Also delete everything else this commenter left on this post",
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a comment and its replies.

    `same_commenter=true` sweeps every comment from the same address off this
    post in one go — which is what a flood actually needs, and why the address
    is kept as a hash in the first place.
    """
    blog = own_blog(db, current_user, blog_uuid)
    try:
        comment = comment_service.get_comment(db, blog.id, comment_uuid)
    except CommentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    if same_commenter and comment.ip_hash:
        removed = comment_service.delete_by_ip_hash(db, blog.id, comment.ip_hash)
        return MessageOut(detail=f"Removed {removed} comments from that commenter")

    comment_service.delete_comment(db, comment)
    return MessageOut(detail="Comment deleted")
