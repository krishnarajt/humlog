#!/usr/bin/env python
"""
Put something in a local database to look at.

    SECRET_KEY=dev DATABASE_URL=sqlite:///humlog_dev.db python scripts/dev_seed.py

Creates one writer (`tester` / `password`), one published post with nesting, a
table, a code block and a couple of comments, and one draft. Safe to re-run:
it deletes the writer it made and everything that cascades from them first.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("SECRET_KEY", "dev-seed-secret")

from app.db.database import SessionLocal, init_db  # noqa: E402
from app.enums import BlockType  # noqa: E402
from app.services import auth_service, block_service, blog_service, comment_service  # noqa: E402

USERNAME = "tester"
PASSWORD = "password"


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        existing = auth_service.get_user_by_username(db, USERNAME)
        if existing:
            db.delete(existing)
            db.commit()
            print(f"Removed the previous '{USERNAME}' and everything under them")

        user = auth_service.create_user(
            db,
            username=USERNAME,
            email="tester@example.com",
            password=PASSWORD,
            display_name="A Tester",
        )

        post = blog_service.create_blog(
            db,
            user,
            title="Why this blog makes you type",
            summary="On pasting, and the absence of it.",
        )

        # One call, three blocks: the newline rule doing its job.
        block_service.create_blocks(
            db,
            post,
            content=(
                "# The rule\n"
                "You cannot paste here. Not a word of it.\n"
                "Images are the exception, because nobody types a photograph."
            ),
        )
        reasons = block_service.create_blocks(db, post, content="Three reasons:")[0]
        for line in ("It is slower.", "Slower is the point.", "Nothing else needs saying."):
            child = block_service.create_blocks(
                db, post, content=line, after_block_uuid=reasons.block_uuid
            )[0]
            block_service.indent_block(db, post, child)

        block_service.create_blocks(
            db,
            post,
            content="| what | allowed |\n| --- | --- |\n| typing | yes |\n| pasting | no |",
            block_type=BlockType.TABLE,
        )
        block_service.create_blocks(db, post, content="", block_type=BlockType.DIVIDER)
        block_service.create_blocks(
            db,
            post,
            content="```js\ndocument.addEventListener('paste', e => e.preventDefault())\n```",
            block_type=BlockType.CODE,
        )

        blog_service.publish(db, post)

        comment_service.create_comment(
            db, post, body="I typed this whole comment. It took ages.",
            author_name="A Reader", ip="203.0.113.10",
        )
        comment_service.create_comment(
            db, post, body="Worth it though.", ip="203.0.113.11"
        )

        draft = blog_service.create_blog(db, user, title="Half an idea")
        block_service.create_blocks(db, draft, content="Not finished.")

        print(f"Seeded '{USERNAME}' / '{PASSWORD}'")
        print(f"  published: {post.blog_uuid}  /{user.username}/{post.slug}")
        print(f"  draft:     {draft.blog_uuid}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
