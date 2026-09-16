"""
The editor's surface: one route per thing that happens under someone's fingers.

Every route here is scoped to a post the caller owns, so the block uuid in the
path is looked up *within* that post — knowing a uuid is never enough to reach
somebody else's paragraph.

Writes return a list of blocks, not one, because a newline is a block boundary
and the server is the one that enforces it (see app/services/block_service.py).
Typing one line gives one block back; typing several gives several.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, own_blog
from app.api.schemas import BlockCreate, BlockMove, BlockOut, BlockUpdate
from app.api import serializers
from app.db.database import get_db
from app.db.models import User
from app.services import block_service
from app.services.block_service import BlockError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["blocks"])


def _fresh(db: Session, blog, blocks) -> list[BlockOut]:
    """Serialise blocks against the post as it now stands.

    Reloading the tree after a write is what makes `depth` and
    `parent_block_uuid` right in the response — a block that just moved has a
    new place in it.
    """
    return serializers.created_blocks_out(block_service.load_tree(db, blog.id), blocks)


def _block(db: Session, blog, block_uuid: str):
    try:
        return block_service.get_block(db, blog.id, block_uuid)
    except BlockError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such block in this post"
        )


@router.get("/blogs/{blog_uuid}/blocks", response_model=list[BlockOut])
def list_blocks(
    blog_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every block of one of my posts, in reading order, each with its depth."""
    blog = own_blog(db, current_user, blog_uuid)
    return serializers.blocks_out(block_service.load_tree(db, blog.id))


@router.post(
    "/blogs/{blog_uuid}/blocks",
    response_model=list[BlockOut],
    status_code=status.HTTP_201_CREATED,
)
def create_block(
    blog_uuid: str,
    payload: BlockCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a block. Usually one; more if what was typed had newlines in it."""
    blog = own_blog(db, current_user, blog_uuid)
    try:
        blocks = block_service.create_blocks(
            db,
            blog,
            content=payload.content,
            block_type=payload.block_type,
            meta=payload.meta,
            image_uuid=payload.image_uuid,
            after_block_uuid=payload.after_block_uuid,
            parent_block_uuid=payload.parent_block_uuid,
        )
    except BlockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _fresh(db, blog, blocks)


@router.patch("/blogs/{blog_uuid}/blocks/{block_uuid}", response_model=list[BlockOut])
def update_block(
    blog_uuid: str,
    block_uuid: str,
    payload: BlockUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a block. The first line stays put; any others become blocks after it."""
    blog = own_blog(db, current_user, blog_uuid)
    block = _block(db, blog, block_uuid)
    try:
        blocks = block_service.update_block(
            db,
            blog,
            block,
            content=payload.content,
            block_type=payload.block_type,
            meta=payload.meta,
            image_uuid=payload.image_uuid,
            clear_image=payload.clear_image,
        )
    except BlockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _fresh(db, blog, blocks)


@router.delete(
    "/blogs/{blog_uuid}/blocks/{block_uuid}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_block(
    blog_uuid: str,
    block_uuid: str,
    cascade: bool = Query(
        default=False, description="Delete everything nested under it too"
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a block. Its children are promoted into its place unless `cascade`."""
    blog = own_blog(db, current_user, blog_uuid)
    block = _block(db, blog, block_uuid)
    block_service.delete_block(db, blog, block, cascade=cascade)


@router.post("/blogs/{blog_uuid}/blocks/{block_uuid}/move", response_model=BlockOut)
def move_block(
    blog_uuid: str,
    block_uuid: str,
    payload: BlockMove,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Move a block: one step with `direction`, or to an exact spot without it.

    The arrows are deliberately forgiving — a block already at the top of its
    list comes back unchanged rather than as an error, so the button can be
    held down.
    """
    blog = own_blog(db, current_user, blog_uuid)
    block = _block(db, blog, block_uuid)
    try:
        moved = block_service.move_block(
            db,
            blog,
            block,
            direction=payload.direction,
            parent_block_uuid=payload.parent_block_uuid,
            after_block_uuid=payload.after_block_uuid,
        )
    except BlockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _fresh(db, blog, [moved])[0]


@router.post("/blogs/{blog_uuid}/blocks/{block_uuid}/indent", response_model=BlockOut)
def indent_block(
    blog_uuid: str,
    block_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Tab: nest this block under the block above it at its own level.

    Pressing it again goes a level deeper. Refused if there is nothing above
    it, or if it would nest past the depth limit.
    """
    blog = own_blog(db, current_user, blog_uuid)
    block = _block(db, blog, block_uuid)
    try:
        moved = block_service.indent_block(db, blog, block)
    except BlockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _fresh(db, blog, [moved])[0]


@router.post("/blogs/{blog_uuid}/blocks/{block_uuid}/outdent", response_model=BlockOut)
def outdent_block(
    blog_uuid: str,
    block_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Shift+Tab: pop this block out to sit just after its old parent."""
    blog = own_blog(db, current_user, blog_uuid)
    block = _block(db, blog, block_uuid)
    try:
        moved = block_service.outdent_block(db, blog, block)
    except BlockError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _fresh(db, blog, [moved])[0]
