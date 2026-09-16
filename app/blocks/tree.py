"""
Turning a post's rows into a shape you can read.

`blocks` is a flat table with a `parent_block_id`, so every read path starts by
loading one post's rows and arranging them. That is cheap and deliberate: a
post is a bounded set of paragraphs, so one query plus a pass in Python beats a
recursive CTE and beats N queries walking the tree.

Everything in here takes the rows and gives back plain structures. Nothing
touches the database, which is what makes the ordering rules testable without
one.
"""

from collections import defaultdict
from typing import Iterable, Optional

from app.blocks.ordering import sort_key


class BlockTree:
    """One post's blocks, indexed for the questions the editor actually asks.

    Built once per request from a flat list of rows; every lookup after that is
    a dict hit.
    """

    def __init__(self, blocks: Iterable):
        self.by_id: dict[int, object] = {}
        children: dict[Optional[int], list] = defaultdict(list)

        for block in blocks:
            self.by_id[block.id] = block
            children[block.parent_block_id].append(block)

        # Sorted here, once, so no caller has to remember to (and so no caller
        # is tempted to ask the database for it — see app/blocks/ordering.py).
        self.children: dict[Optional[int], list] = {
            parent: sorted(group, key=sort_key) for parent, group in children.items()
        }

    # -- lookups ------------------------------------------------------------

    def siblings(self, parent_id: Optional[int]) -> list:
        """Every block directly under `parent_id`, in order. `None` is top level."""
        return self.children.get(parent_id, [])

    def position(self, block) -> int:
        """Where a block sits among its siblings, 0-based."""
        siblings = self.siblings(block.parent_block_id)
        return next(i for i, sibling in enumerate(siblings) if sibling.id == block.id)

    def neighbours(self, block) -> tuple[Optional[object], Optional[object]]:
        """The blocks immediately before and after this one among its siblings."""
        siblings = self.siblings(block.parent_block_id)
        index = self.position(block)
        return (
            siblings[index - 1] if index > 0 else None,
            siblings[index + 1] if index + 1 < len(siblings) else None,
        )

    def depth(self, block) -> int:
        """How deeply nested a block is. Top level is 0.

        Walks up `parent_block_id`, and stops if the chain ever loops — a cycle
        should be impossible (`move` refuses to make one) but a hang here would
        be a hang of the whole request.
        """
        depth = 0
        seen = {block.id}
        parent_id = block.parent_block_id
        while parent_id is not None and parent_id in self.by_id:
            if parent_id in seen:
                break
            seen.add(parent_id)
            depth += 1
            parent_id = self.by_id[parent_id].parent_block_id
        return depth

    def subtree_height(self, block) -> int:
        """How many levels hang below a block. A childless block is 0."""
        children = self.siblings(block.id)
        if not children:
            return 0
        return 1 + max(self.subtree_height(child) for child in children)

    def is_descendant_of(self, block, ancestor) -> bool:
        """Is `block` somewhere inside `ancestor`'s subtree? A block is not its own."""
        parent_id = block.parent_block_id
        seen = set()
        while parent_id is not None and parent_id not in seen:
            if parent_id == ancestor.id:
                return True
            seen.add(parent_id)
            parent = self.by_id.get(parent_id)
            if parent is None:
                return False
            parent_id = parent.parent_block_id
        return False

    def last_key(self, parent_id: Optional[int]) -> Optional[str]:
        """The order key of the last child of `parent_id`, or None if it has none."""
        siblings = self.siblings(parent_id)
        return siblings[-1].order_key if siblings else None

    # -- reading order ------------------------------------------------------

    def walk(self, parent_id: Optional[int] = None, depth: int = 0):
        """Depth-first, in reading order, yielding `(block, depth)`.

        This is the order a reader sees, so it is the order the exports use and
        the order the API returns. Children come immediately after their
        parent, exactly as they render.
        """
        for block in self.siblings(parent_id):
            yield block, depth
            yield from self.walk(block.id, depth + 1)

    def flatten(self) -> list[tuple[object, int]]:
        """`walk` as a list."""
        return list(self.walk())
