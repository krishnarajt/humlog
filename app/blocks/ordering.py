"""
Fractional indexing: the strings in `blocks.order_key`.

The problem this solves is the one every drag-and-drop list has. Numbering the
rows 1, 2, 3 means dropping something between 1 and 2 renumbers everything
after it — a thousand-block post becomes a thousand UPDATEs, and two people
dragging at once produce a mess. The fix is to let positions be *strings* that
compare lexicographically, because between any two strings there is always
another one: between "a0" and "a1" sits "a0V", and between those sits "a0F".
Moving a block rewrites exactly one row.

The generator itself is `fractional-indexing`, a port of the algorithm David
Greenspan wrote up for Figma. Keys are base-62, so appending a thousand blocks
at the end of a post leaves them three characters long rather than growing one
character per block.

Two things about these keys that the rest of the codebase depends on:

* **They are compared as plain ASCII strings.** Base-62 orders digits before
  uppercase before lowercase, so `"Zz" < "a0"` must hold. Python's `str` does
  that. A Postgres index on a `en_US.UTF-8` database does not, which is why
  nothing in this app ever writes `ORDER BY order_key` — see `sort_key`.

* **A duplicate key is untidy, not broken.** `sort_key` breaks ties on the row
  id, so two blocks that somehow land on the same key still have a stable,
  defined order instead of shuffling between requests.
"""

import logging
from typing import Optional

from fractional_indexing import FIError, generate_key_between, generate_n_keys_between

logger = logging.getLogger(__name__)


class OrderingError(ValueError):
    """A pair of keys that cannot have anything generated between them."""


def key_between(previous: Optional[str], following: Optional[str]) -> str:
    """A key that sorts strictly after `previous` and strictly before `following`.

    `None` on either side means "nothing there": `key_between(None, None)` is
    the key for the first block of an empty post, `key_between(last, None)`
    appends, and `key_between(None, first)` prepends.

    Bad input is repaired rather than raised on wherever there is an obvious
    right answer. If the two keys are equal or out of order — which means
    something already went wrong upstream — this appends after `previous`
    instead, so a wonky pair of neighbours can't make a block unsaveable.
    """
    if previous is not None and following is not None and previous >= following:
        logger.warning(
            f"Out-of-order neighbours ({previous!r}, {following!r}); appending instead"
        )
        following = None

    try:
        return generate_key_between(previous, following)
    except FIError as exc:
        raise OrderingError(
            f"Could not order a block between {previous!r} and {following!r}: {exc}"
        ) from exc


def keys_between(previous: Optional[str], following: Optional[str], count: int) -> list[str]:
    """`count` keys, in order, all strictly between `previous` and `following`.

    Used when several blocks have to land in one gap at once — deleting a
    parent and promoting its children into its place, mostly.
    """
    if count <= 0:
        return []
    if previous is not None and following is not None and previous >= following:
        logger.warning(
            f"Out-of-order neighbours ({previous!r}, {following!r}); appending instead"
        )
        following = None

    try:
        return generate_n_keys_between(previous, following, count)
    except FIError as exc:
        raise OrderingError(
            f"Could not order {count} blocks between {previous!r} and "
            f"{following!r}: {exc}"
        ) from exc


def sort_key(block) -> tuple[str, int]:
    """The sort key for a block among its siblings.

    Always sort with this, and always in Python. Two reasons, both in the
    module docstring: Postgres collations do not order base-62 the way base-62
    means it, and duplicate keys need a tie-break rather than a coin flip.
    """
    return (block.order_key or "", block.id or 0)


def sorted_blocks(blocks) -> list:
    """The given blocks in reading order. Assumes they are already siblings."""
    return sorted(blocks, key=sort_key)
