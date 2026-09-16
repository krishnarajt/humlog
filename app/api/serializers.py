"""
Turning rows into the shapes in app/api/schemas.py.

Two things live here rather than on the models. A block's `depth` and its
parent's uuid are properties of the *tree*, not of the row, and are resolved
from the already-loaded `BlockTree` rather than by lazy-loading a parent per
block. And counts — blocks, comments — are one query for a whole list instead
of one per row.
"""

from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.schemas import (
    AuthorOut,
    BlockOut,
    BlogDetailOut,
    BlogOut,
    CommentOut,
    ImageOut,
    PublicBlogDetailOut,
    PublicBlogOut,
)
from app.blocks.tree import BlockTree
from app.db.models import Block, Blog, Comment
from app.enums import CommentStatus
from app.services.export_service import default_image_url


def image_out(image) -> Optional[ImageOut]:
    if image is None:
        return None
    return ImageOut(
        image_uuid=image.image_uuid,
        image_name=image.image_name,
        mime_type=image.mime_type,
        file_size=image.file_size,
        url=default_image_url(image),
        created_at=image.created_at,
    )


def block_out(block: Block, depth: int, tree: Optional[BlockTree] = None) -> BlockOut:
    parent_uuid = None
    if block.parent_block_id is not None:
        parent = tree.by_id.get(block.parent_block_id) if tree else block.parent
        parent_uuid = parent.block_uuid if parent else None

    return BlockOut(
        block_uuid=block.block_uuid,
        parent_block_uuid=parent_uuid,
        depth=depth,
        order_key=block.order_key,
        block_type=block.block_type,
        content=block.content or "",
        meta=block.meta,
        image=image_out(block.image),
        created_at=block.created_at,
        updated_at=block.updated_at,
    )


def blocks_out(tree: BlockTree) -> list[BlockOut]:
    """A whole post's blocks, in reading order, each carrying its depth."""
    return [block_out(block, depth, tree) for block, depth in tree.walk()]


def created_blocks_out(tree: BlockTree, blocks: list[Block]) -> list[BlockOut]:
    """The blocks a write just produced, in the order they were produced."""
    depths = {block.id: depth for block, depth in tree.walk()}
    return [block_out(block, depths.get(block.id, 0), tree) for block in blocks]


# ---------------------------------------------------------------------------
# Counts
# ---------------------------------------------------------------------------

def _counts(db: Session, model, column, blog_ids: list[int], *extra_filters) -> dict[int, int]:
    if not blog_ids:
        return {}
    query = db.query(column, func.count(model.id)).filter(column.in_(blog_ids))
    for condition in extra_filters:
        query = query.filter(condition)
    return dict(query.group_by(column).all())


def block_counts(db: Session, blog_ids: list[int]) -> dict[int, int]:
    return _counts(db, Block, Block.blog_id, blog_ids)


def comment_counts(db: Session, blog_ids: list[int]) -> dict[int, int]:
    return _counts(
        db, Comment, Comment.blog_id, blog_ids,
        Comment.status == CommentStatus.VISIBLE,
    )


# ---------------------------------------------------------------------------
# Blogs
# ---------------------------------------------------------------------------

def blog_out(blog: Blog, blocks: int = 0, comments: int = 0) -> BlogOut:
    out = BlogOut.model_validate(blog)
    out.block_count = blocks
    out.comment_count = comments
    return out


def blog_list_out(db: Session, blogs: list[Blog]) -> list[BlogOut]:
    ids = [blog.id for blog in blogs]
    blocks, comments = block_counts(db, ids), comment_counts(db, ids)
    return [blog_out(b, blocks.get(b.id, 0), comments.get(b.id, 0)) for b in blogs]


def blog_detail_out(db: Session, blog: Blog, tree: BlockTree) -> BlogDetailOut:
    # Built from the summary rather than validated straight off the row: `Blog`
    # has a `blocks` relationship of ORM objects, and a field of the same name
    # here would be filled from it instead of from the sorted tree.
    blocks = blocks_out(tree)
    base = blog_out(blog, len(blocks), comment_counts(db, [blog.id]).get(blog.id, 0))
    return BlogDetailOut(
        **base.model_dump(),
        author=AuthorOut.model_validate(blog.author),
        blocks=blocks,
    )


def public_blog_out(blog: Blog, comments: int = 0) -> PublicBlogOut:
    out = PublicBlogOut.model_validate(blog)
    out.author = AuthorOut.model_validate(blog.author)
    out.comment_count = comments
    return out


def public_blog_list_out(db: Session, blogs: list[Blog]) -> list[PublicBlogOut]:
    comments = comment_counts(db, [blog.id for blog in blogs])
    return [public_blog_out(blog, comments.get(blog.id, 0)) for blog in blogs]


def public_blog_detail_out(db: Session, blog: Blog, tree: BlockTree) -> PublicBlogDetailOut:
    base = public_blog_out(blog, comment_counts(db, [blog.id]).get(blog.id, 0))
    return PublicBlogDetailOut(**base.model_dump(), blocks=blocks_out(tree))


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def comment_out(comment: Comment) -> CommentOut:
    return CommentOut(
        comment_uuid=comment.comment_uuid,
        parent_comment_uuid=(
            comment.parent.comment_uuid if comment.parent_comment_id and comment.parent else None
        ),
        author_name=comment.author_name,
        body=comment.body,
        status=comment.status,
        created_at=comment.created_at,
    )


def comments_out(comments: list[Comment]) -> list[CommentOut]:
    """A post's comments, flat, with parents resolved without a query per row."""
    by_id = {comment.id: comment for comment in comments}
    out = []
    for comment in comments:
        parent = by_id.get(comment.parent_comment_id) if comment.parent_comment_id else None
        out.append(
            CommentOut(
                comment_uuid=comment.comment_uuid,
                parent_comment_uuid=parent.comment_uuid if parent else None,
                author_name=comment.author_name,
                body=comment.body,
                status=comment.status,
                created_at=comment.created_at,
            )
        )
    return out
