"""
Every enumerated value used by hum.log.

These are `StrEnum`s, so a member *is* a string: SQLAlchemy stores it in a plain
`String` column, `json.dumps` handles it, and Pydantic validates request bodies
against it for free. Reading a row back gives a plain `str`, and comparing that
to a member still works (`row.status == BlogStatus.PUBLISHED`).

Deliberately not Postgres ENUM types — adding a block type should be a code
change, not a migration.
"""

from enum import StrEnum


class _Enum(StrEnum):
    """StrEnum plus a couple of conveniences for validation."""

    @classmethod
    def values(cls) -> list[str]:
        return [member.value for member in cls]

    @classmethod
    def has(cls, value: str) -> bool:
        return value in cls._value2member_map_


class BlockType(_Enum):
    """What one row of `blocks` holds.

    Every block's `content` is markdown, so a heading, a quote and a bullet are
    all TEXT — the markdown already says which. The other members exist because
    they are the blocks that are *not* a line of prose: they need their own
    editing affordance in the UI, and IMAGE needs a row in `images` behind it.
    """

    # Anything you type: a paragraph, "# a heading", "> a quote", "- a bullet".
    TEXT = "text"
    # An uploaded image. `content` is the caption/alt text, `image_id` is the file.
    IMAGE = "image"
    # A markdown table, kept whole so the UI can offer a grid editor.
    TABLE = "table"
    # A fenced code block, kept whole so newlines inside it are not new blocks.
    CODE = "code"
    # A horizontal rule. `content` is ignored.
    DIVIDER = "divider"


class BlogStatus(_Enum):
    """Draft posts are private to their author; published ones are world-readable."""

    DRAFT = "draft"
    PUBLISHED = "published"


class CommentStatus(_Enum):
    """Comments are visible unless the post's author hides one."""

    VISIBLE = "visible"
    HIDDEN = "hidden"


class ExportFormat(_Enum):
    """What `GET /blogs/{uuid}/export` can hand back."""

    MARKDOWN = "markdown"
    HTML = "html"


class MoveDirection(_Enum):
    """The up/down arrows next to a block.

    Sibling-only, which is what makes them safe to mash: a block can never
    change parent or depth this way, so the worst an extra press does is
    nothing.
    """

    UP = "up"
    DOWN = "down"
