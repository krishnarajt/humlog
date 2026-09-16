"""The rules a writer actually feels: Enter, Tab, the arrows, and delete."""

import pytest

from app.enums import BlockType, MoveDirection
from app.services import block_service
from app.services.block_service import BlockError


def add(db, blog, content, **kwargs):
    """Add blocks and return the last one, which is what a caller usually wants."""
    return block_service.create_blocks(db, blog, content=content, **kwargs)[-1]


# --- typing ----------------------------------------------------------------

def test_a_new_post_has_no_blocks(db, blog, contents):
    assert contents(blog.id) == []


def test_blocks_append_in_the_order_they_were_typed(db, blog, contents):
    for line in ("first", "second", "third"):
        add(db, blog, line)
    assert contents(blog.id) == [(0, "first"), (0, "second"), (0, "third")]


def test_a_newline_is_a_block_boundary(db, blog, contents):
    """The rule the whole editor rests on, enforced server-side."""
    created = block_service.create_blocks(db, blog, content="one\ntwo\nthree")
    assert len(created) == 3
    assert contents(blog.id) == [(0, "one"), (0, "two"), (0, "three")]


def test_a_blank_line_between_two_lines_survives(db, blog, contents):
    block_service.create_blocks(db, blog, content="one\n\ntwo")
    assert contents(blog.id) == [(0, "one"), (0, ""), (0, "two")]


def test_a_trailing_newline_does_not_leave_an_empty_block(db, blog, contents):
    block_service.create_blocks(db, blog, content="one\n")
    assert contents(blog.id) == [(0, "one")]


def test_code_blocks_keep_their_newlines(db, blog, contents):
    source = "def f():\n    return 1"
    created = block_service.create_blocks(
        db, blog, content=source, block_type=BlockType.CODE
    )
    assert len(created) == 1
    assert created[0].content == source


def test_a_new_block_lands_after_the_one_you_were_on(db, blog, contents):
    first = add(db, blog, "first")
    add(db, blog, "last")
    add(db, blog, "middle", after_block_uuid=first.block_uuid)
    assert contents(blog.id) == [(0, "first"), (0, "middle"), (0, "last")]


def test_editing_a_block_into_several_lines_splits_it(db, blog, contents):
    first = add(db, blog, "one")
    add(db, blog, "after")
    result = block_service.update_block(db, blog, first, content="one\ntwo")
    assert len(result) == 2
    assert contents(blog.id) == [(0, "one"), (0, "two"), (0, "after")]


def test_content_past_the_limit_is_refused(db, blog):
    with pytest.raises(BlockError):
        add(db, blog, "x" * (block_service.MAX_CONTENT_CHARS + 1))


# --- nesting ---------------------------------------------------------------

def test_tab_nests_a_block_under_the_one_above(db, blog, contents):
    add(db, blog, "parent")
    child = add(db, blog, "child")
    block_service.indent_block(db, blog, child)
    assert contents(blog.id) == [(0, "parent"), (1, "child")]


def test_tab_on_the_first_block_is_refused(db, blog):
    first = add(db, blog, "first")
    with pytest.raises(BlockError):
        block_service.indent_block(db, blog, first)


def test_shift_tab_puts_a_block_back_after_its_parent(db, blog, contents):
    add(db, blog, "parent")
    child = add(db, blog, "child")
    add(db, blog, "after")

    block_service.indent_block(db, blog, child)
    assert contents(blog.id) == [(0, "parent"), (1, "child"), (0, "after")]

    block_service.outdent_block(db, blog, child)
    assert contents(blog.id) == [(0, "parent"), (0, "child"), (0, "after")]


def test_shift_tab_at_the_top_level_is_refused(db, blog):
    first = add(db, blog, "first")
    with pytest.raises(BlockError):
        block_service.outdent_block(db, blog, first)


def test_tab_twice_nests_a_second_level(db, blog, contents):
    """Tab nests under the previous *sibling*, so pressing it again goes deeper."""
    add(db, blog, "a")
    b = add(db, blog, "b")
    c = add(db, blog, "c")
    block_service.indent_block(db, blog, b)   # b under a
    block_service.indent_block(db, blog, c)   # c under a, beside b
    assert contents(blog.id) == [(0, "a"), (1, "b"), (1, "c")]
    block_service.indent_block(db, blog, c)   # c under b
    assert contents(blog.id) == [(0, "a"), (1, "b"), (2, "c")]

    block_service.outdent_block(db, blog, b)
    assert contents(blog.id) == [(0, "a"), (0, "b"), (1, "c")]


def test_nesting_stops_at_the_depth_limit(db, blog, contents):
    limit = block_service.MAX_BLOCK_DEPTH
    blocks = [add(db, blog, f"level {i}") for i in range(limit + 2)]
    # Each block needs one more Tab than the last to reach the next level down.
    for depth, block in enumerate(blocks[1:], start=1):
        for _ in range(min(depth, limit)):
            block_service.indent_block(db, blog, block)

    # The last one ran out of room and came to rest beside its predecessor
    # rather than under it.
    assert contents(blog.id) == [(i, f"level {i}") for i in range(limit + 1)] + [
        (limit, f"level {limit + 1}")
    ]

    with pytest.raises(BlockError):
        block_service.indent_block(db, blog, blocks[-1])


# --- the arrows ------------------------------------------------------------

def test_the_down_arrow_swaps_with_the_next_block(db, blog, contents):
    first = add(db, blog, "first")
    add(db, blog, "second")
    block_service.move_block(db, blog, first, direction=MoveDirection.DOWN)
    assert contents(blog.id) == [(0, "second"), (0, "first")]


def test_the_up_arrow_swaps_with_the_previous_block(db, blog, contents):
    add(db, blog, "first")
    add(db, blog, "second")
    third = add(db, blog, "third")
    block_service.move_block(db, blog, third, direction=MoveDirection.UP)
    assert contents(blog.id) == [(0, "first"), (0, "third"), (0, "second")]


def test_an_arrow_at_the_end_of_the_list_does_nothing(db, blog, contents):
    """So the button can be held down without producing an error."""
    first = add(db, blog, "first")
    add(db, blog, "second")
    block_service.move_block(db, blog, first, direction=MoveDirection.UP)
    assert contents(blog.id) == [(0, "first"), (0, "second")]


def test_the_arrows_never_change_depth(db, blog, contents):
    parent = add(db, blog, "parent")
    child_a = add(db, blog, "a")
    child_b = add(db, blog, "b")
    block_service.indent_block(db, blog, child_a)
    block_service.indent_block(db, blog, child_b)
    assert contents(blog.id) == [(0, "parent"), (1, "a"), (1, "b")]

    block_service.move_block(db, blog, child_b, direction=MoveDirection.UP)
    assert contents(blog.id) == [(0, "parent"), (1, "b"), (1, "a")]
    # And moving up again, with nothing above, leaves it alone rather than
    # promoting it out of its parent.
    block_service.move_block(db, blog, child_b, direction=MoveDirection.UP)
    assert contents(blog.id) == [(0, "parent"), (1, "b"), (1, "a")]
    assert parent.id == child_b.parent_block_id


# --- dragging --------------------------------------------------------------

def test_dropping_a_block_at_the_top(db, blog, contents):
    add(db, blog, "first")
    last = add(db, blog, "last")
    block_service.move_block(db, blog, last, after_block_uuid=None, parent_block_uuid=None)
    assert contents(blog.id) == [(0, "last"), (0, "first")]


def test_dropping_a_block_inside_another(db, blog, contents):
    parent = add(db, blog, "parent")
    other = add(db, blog, "other")
    block_service.move_block(db, blog, other, parent_block_uuid=parent.block_uuid)
    assert contents(blog.id) == [(0, "parent"), (1, "other")]


def test_a_block_cannot_be_dropped_inside_itself(db, blog):
    parent = add(db, blog, "parent")
    child = add(db, blog, "child")
    block_service.indent_block(db, blog, child)
    with pytest.raises(BlockError):
        block_service.move_block(db, blog, parent, parent_block_uuid=child.block_uuid)


def test_dropping_after_a_block_with_a_different_parent_is_refused(db, blog):
    add(db, blog, "parent")
    child = add(db, blog, "child")
    other = add(db, blog, "other")
    block_service.indent_block(db, blog, child)
    with pytest.raises(BlockError):
        block_service.move_block(
            db, blog, other, parent_block_uuid=None, after_block_uuid=child.block_uuid
        )


# --- deleting --------------------------------------------------------------

def test_deleting_a_block_keeps_its_children(db, blog, contents):
    add(db, blog, "parent")
    child = add(db, blog, "child")
    add(db, blog, "after")
    block_service.indent_block(db, blog, child)

    parent = block_service.load_tree(db, blog.id).siblings(None)[0]
    block_service.delete_block(db, blog, parent)
    assert contents(blog.id) == [(0, "child"), (0, "after")]


def test_cascade_takes_the_whole_subtree(db, blog, contents):
    add(db, blog, "parent")
    child = add(db, blog, "child")
    add(db, blog, "after")
    block_service.indent_block(db, blog, child)

    parent = block_service.load_tree(db, blog.id).siblings(None)[0]
    block_service.delete_block(db, blog, parent, cascade=True)
    assert contents(blog.id) == [(0, "after")]


def test_a_block_from_another_post_is_not_found(db, blog, user):
    from app.services import blog_service

    other = blog_service.create_blog(db, user, title="Somewhere else")
    stranger = add(db, other, "not yours")
    with pytest.raises(BlockError):
        block_service.get_block(db, blog.id, stranger.block_uuid)
