"""
Everything that can happen to a block.

The rules are deliberately few, because they have to be obvious while somebody
is typing:

* A new block goes immediately after the one you were on, as its sibling.
* **A newline is a block boundary.** Prose arriving with newlines in it
  becomes one block per line, right here in the service, so the invariant
  holds no matter which client is talking. Only TEXT splits: a newline inside
  a code block or a table is part of the thing.
* Tab nests a block under the block above it *at its own level*. Pressing it
  again goes a level deeper, because the block above is now one level in.
  Shift+Tab pops it back out, to just after its old parent.
* The up and down arrows swap a block with its neighbour and never change its
  parent, so mashing them cannot rearrange the shape of a post.
* Deleting a block keeps its children, promoting them into the hole it leaves.
  Ask for `cascade` to take the subtree with it.

Ordering is fractional (app/blocks/ordering.py), so every one of these is an
UPDATE of one row — the exceptions being promotion on delete, which rewrites
the promoted children, and nothing else.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.blocks.ordering import key_between, keys_between
from app.blocks.tree import BlockTree
from app.db.models import Block, Blog, Image
from app.enums import BlockType, MoveDirection

logger = logging.getLogger(__name__)

# Nesting deeper than this stops being an outline and starts being a maze, and
# every markdown renderer gives up somewhere around here anyway. Top level is 0.
MAX_BLOCK_DEPTH = 4

# One block is one paragraph. This is generous enough for a long one and for a
# markdown table with real content in it, and small enough that a runaway
# client cannot post a novel into a single row.
MAX_CONTENT_CHARS = 20000

# A ceiling on a single post, so a loop in a client cannot fill the database.
# At one block per paragraph this is a very long book.
MAX_BLOCKS_PER_BLOG = 10000

# Only prose splits on newlines. Everywhere else a newline is either part of
# the content (code, tables) or has nothing to do with it (an image caption, a
# horizontal rule).
SPLIT_TYPES = frozenset({BlockType.TEXT})


class BlockError(ValueError):
    """Something the caller asked for that the rules above do not allow."""


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_tree(db: Session, blog_id: int) -> BlockTree:
    """Every block of one post, indexed.

    One query, no ordering clause — the sort is in Python, for the reasons in
    app/blocks/ordering.py.
    """
    blocks = db.query(Block).filter(Block.blog_id == blog_id).all()
    return BlockTree(blocks)


def list_blocks(db: Session, blog_id: int) -> list[tuple[Block, int]]:
    """A post's blocks in reading order, each with its depth."""
    return load_tree(db, blog_id).flatten()


def get_block(db: Session, blog_id: int, block_uuid: str) -> Block:
    block = (
        db.query(Block)
        .filter(Block.block_uuid == block_uuid, Block.blog_id == blog_id)
        .first()
    )
    if block is None:
        raise BlockError("No such block in this post")
    return block


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(content: Optional[str], block_type: str) -> str:
    """Normalise incoming content and check it against the size limit.

    Windows line endings become plain newlines first, so the split below sees
    one kind of boundary rather than three.
    """
    content = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(content) > MAX_CONTENT_CHARS:
        raise BlockError(
            f"A block holds at most {MAX_CONTENT_CHARS} characters — "
            f"start a new one"
        )
    if block_type == BlockType.DIVIDER:
        # A rule is a rule. Whatever came with it is not content.
        return ""
    return content


def _split_lines(content: str, block_type: str) -> list[str]:
    """One block per line, for the block types where a line is a block.

    Trailing blank lines are dropped — pressing Enter at the end of a paragraph
    should leave you one new block, not one new block and an empty one after it
    — but blank lines *between* two lines are kept, because a deliberate gap is
    a deliberate gap.
    """
    if block_type not in SPLIT_TYPES or "\n" not in content:
        return [content]

    lines = content.split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    return lines or [""]


def _resolve_image(db: Session, blog: Blog, image_uuid: Optional[str]) -> Optional[Image]:
    if image_uuid is None:
        return None
    image = (
        db.query(Image)
        .filter(Image.image_uuid == image_uuid, Image.blog_id == blog.id)
        .first()
    )
    if image is None:
        raise BlockError("No such image in this post")
    return image


def _check_capacity(db: Session, blog: Blog, adding: int) -> None:
    if adding <= 0:
        return
    current = db.query(Block).filter(Block.blog_id == blog.id).count()
    if current + adding > MAX_BLOCKS_PER_BLOG:
        raise BlockError(
            f"This post is at the {MAX_BLOCKS_PER_BLOG}-block limit. "
            f"Split it into two posts."
        )


def _check_depth(tree: BlockTree, block: Block, new_depth: int) -> None:
    """Refuse a move that would push the block — or anything under it — too deep."""
    deepest = new_depth + tree.subtree_height(block)
    if deepest > MAX_BLOCK_DEPTH:
        raise BlockError(
            f"That would nest {deepest} levels deep; {MAX_BLOCK_DEPTH} is the limit"
        )


def _reposition(block: Block, parent_id: Optional[int], previous: Optional[Block],
                following: Optional[Block]) -> None:
    """Put a block between two of its new siblings. One row changes: this one."""
    block.parent_block_id = parent_id
    block.order_key = key_between(
        previous.order_key if previous else None,
        following.order_key if following else None,
    )


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

def create_blocks(
    db: Session,
    blog: Blog,
    *,
    content: str = "",
    block_type: str = BlockType.TEXT,
    meta: Optional[dict] = None,
    image_uuid: Optional[str] = None,
    after_block_uuid: Optional[str] = None,
    parent_block_uuid: Optional[str] = None,
) -> list[Block]:
    """Add one or more blocks to a post, and say where.

    Placement, in the order it is decided:

    * `after_block_uuid` — right after that block, as its sibling. This is what
      pressing Enter does, and it is why a block with children gets its new
      sibling *below* the whole subtree.
    * `parent_block_uuid` — as the last child of that block.
    * neither — at the end of the post.

    Returns every block created, in order. That is usually one; it is more when
    the content had newlines in it (see the module docstring), which is the one
    case where the server, not the client, decides how many blocks you typed.
    """
    if block_type == BlockType.IMAGE and image_uuid is None:
        raise BlockError("An image block needs an image_uuid")

    image = _resolve_image(db, blog, image_uuid)
    content = _clean(content, block_type)
    lines = _split_lines(content, block_type)
    _check_capacity(db, blog, len(lines))

    tree = load_tree(db, blog.id)

    if after_block_uuid is not None:
        anchor = get_block(db, blog.id, after_block_uuid)
        parent_id = anchor.parent_block_id
        previous, following = anchor, tree.neighbours(anchor)[1]
    elif parent_block_uuid is not None:
        anchor = get_block(db, blog.id, parent_block_uuid)
        parent_id = anchor.id
        previous, following = (tree.siblings(anchor.id) or [None])[-1], None
        depth = tree.depth(anchor) + 1
        if depth > MAX_BLOCK_DEPTH:
            raise BlockError(
                f"That would nest {depth} levels deep; {MAX_BLOCK_DEPTH} is the limit"
            )
    else:
        parent_id = None
        previous, following = (tree.siblings(None) or [None])[-1], None

    keys = keys_between(
        previous.order_key if previous else None,
        following.order_key if following else None,
        len(lines),
    )

    created = []
    for line, key in zip(lines, keys):
        block = Block(
            blog_id=blog.id,
            parent_block_id=parent_id,
            order_key=key,
            block_type=block_type,
            content=line,
            meta=meta,
            image_id=image.id if image else None,
        )
        db.add(block)
        created.append(block)

    db.commit()
    for block in created:
        db.refresh(block)
    return created


# ---------------------------------------------------------------------------
# Editing
# ---------------------------------------------------------------------------

def update_block(
    db: Session,
    blog: Blog,
    block: Block,
    *,
    content: Optional[str] = None,
    block_type: Optional[str] = None,
    meta: Optional[dict] = None,
    image_uuid: Optional[str] = None,
    clear_image: bool = False,
) -> list[Block]:
    """Edit a block in place.

    Returns a list for the same reason `create_blocks` does: if the new content
    has newlines in it, the first line stays in this block and the rest become
    new siblings straight after it.
    """
    if block_type is not None:
        if not BlockType.has(block_type):
            raise BlockError(f"Unknown block type '{block_type}'")
        block.block_type = block_type

    if image_uuid is not None:
        block.image_id = _resolve_image(db, blog, image_uuid).id
    elif clear_image:
        block.image_id = None

    if block.block_type == BlockType.IMAGE and block.image_id is None:
        raise BlockError("An image block needs an image_uuid")

    if meta is not None:
        block.meta = meta

    extra: list[Block] = []
    if content is not None:
        lines = _split_lines(_clean(content, block.block_type), block.block_type)
        block.content = lines[0]
        if len(lines) > 1:
            db.commit()
            extra = create_blocks(
                db,
                blog,
                content="\n".join(lines[1:]),
                block_type=block.block_type,
                meta=block.meta,
                after_block_uuid=block.block_uuid,
            )

    db.commit()
    db.refresh(block)
    return [block] + extra


def delete_block(db: Session, blog: Blog, block: Block, cascade: bool = False) -> None:
    """Remove a block.

    By default its children are kept and promoted into the gap it leaves,
    because deleting the line you are on should not silently delete everything
    you nested under it. `cascade=True` deletes the subtree — that is the
    "delete this whole section" gesture, and it is never the default.
    """
    if not cascade:
        tree = load_tree(db, blog.id)
        children = tree.siblings(block.id)
        if children:
            following = tree.neighbours(block)[1]
            keys = keys_between(
                block.order_key,
                following.order_key if following else None,
                len(children),
            )
            for child, key in zip(children, keys):
                child.parent_block_id = block.parent_block_id
                child.order_key = key
            # Flush before the delete, not after. The session runs with
            # autoflush off, so without this the ORM's delete cascade reads
            # `children` straight from the database, still sees them pointing
            # here, and deletes the blocks this branch exists to keep.
            db.flush()

    db.delete(block)
    db.commit()


# ---------------------------------------------------------------------------
# Moving
# ---------------------------------------------------------------------------

def move_block(
    db: Session,
    blog: Blog,
    block: Block,
    *,
    direction: Optional[str] = None,
    parent_block_uuid: Optional[str] = None,
    after_block_uuid: Optional[str] = None,
) -> Block:
    """Move a block, either by one step or to an exact spot.

    With `direction`, this is the up/down arrows: swap with the sibling above
    or below, never changing parent. Already at the end it is asked to move
    towards? Nothing happens and the block comes back unchanged — an arrow you
    can hold down is worth more than an error nobody reads.

    Without `direction`, this is a drag-and-drop landing, and both other
    arguments are read literally: `parent_block_uuid` of `None` means top
    level, and `after_block_uuid` of `None` means first among its new siblings.
    """
    tree = load_tree(db, blog.id)

    if direction is not None:
        return _move_one_step(db, tree, block, direction)

    parent = get_block(db, blog.id, parent_block_uuid) if parent_block_uuid else None
    after = get_block(db, blog.id, after_block_uuid) if after_block_uuid else None

    if parent is not None:
        if parent.id == block.id or tree.is_descendant_of(parent, block):
            raise BlockError("A block cannot be moved inside itself")
        _check_depth(tree, block, tree.depth(parent) + 1)
    else:
        _check_depth(tree, block, 0)

    parent_id = parent.id if parent else None
    if after is not None:
        if after.id == block.id:
            raise BlockError("A block cannot be placed after itself")
        if after.parent_block_id != parent_id:
            raise BlockError(
                "after_block_uuid must be a child of parent_block_uuid"
            )

    siblings = [b for b in tree.siblings(parent_id) if b.id != block.id]
    if after is None:
        previous, following = None, siblings[0] if siblings else None
    else:
        index = next(i for i, b in enumerate(siblings) if b.id == after.id)
        previous = siblings[index]
        following = siblings[index + 1] if index + 1 < len(siblings) else None

    _reposition(block, parent_id, previous, following)
    db.commit()
    db.refresh(block)
    return block


def _move_one_step(db: Session, tree: BlockTree, block: Block, direction: str) -> Block:
    """The up/down arrows: swap with a sibling, stay at the same depth."""
    siblings = tree.siblings(block.parent_block_id)
    index = tree.position(block)

    if direction == MoveDirection.UP:
        if index == 0:
            return block  # already first — nothing above to swap with
        previous = siblings[index - 2] if index >= 2 else None
        following = siblings[index - 1]
    elif direction == MoveDirection.DOWN:
        if index == len(siblings) - 1:
            return block  # already last
        previous = siblings[index + 1]
        following = siblings[index + 2] if index + 2 < len(siblings) else None
    else:
        raise BlockError(f"Unknown direction '{direction}'")

    _reposition(block, block.parent_block_id, previous, following)
    db.commit()
    db.refresh(block)
    return block


def indent_block(db: Session, blog: Blog, block: Block) -> Block:
    """Tab: nest this block under the one above it, as its last child.

    "The one above it" means its previous *sibling*, not whatever renders
    directly above. Tab therefore always moves exactly one level, and pressing
    it twice reaches the second level — which is the behaviour anybody who has
    used an outliner already has in their fingers.
    """
    tree = load_tree(db, blog.id)
    previous = tree.neighbours(block)[0]
    if previous is None:
        raise BlockError("There is nothing above this block to nest it under")

    _check_depth(tree, block, tree.depth(previous) + 1)
    last_child = (tree.siblings(previous.id) or [None])[-1]
    _reposition(block, previous.id, last_child, None)
    db.commit()
    db.refresh(block)
    return block


def outdent_block(db: Session, blog: Blog, block: Block) -> Block:
    """Shift+Tab: pop this block out to sit just after its old parent.

    Its own children come with it; the blocks that were below it under the old
    parent stay where they are. That keeps the rule to one sentence, which is
    what matters when it happens under someone's fingers.
    """
    tree = load_tree(db, blog.id)
    parent = tree.by_id.get(block.parent_block_id) if block.parent_block_id else None
    if parent is None:
        raise BlockError("This block is already at the top level")

    following = tree.neighbours(parent)[1]
    _reposition(block, parent.parent_block_id, parent, following)
    db.commit()
    db.refresh(block)
    return block


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def image_keys_for_blog(db: Session, blog_id: int) -> list[str]:
    """Every stored object belonging to a post, for deleting it from storage."""
    return [
        key
        for (key,) in db.query(Image.object_key).filter(Image.blog_id == blog_id).all()
    ]
