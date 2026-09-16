"""The markdown and HTML a post comes out as."""

import io
import zipfile

from app.db.models import Image
from app.enums import BlockType
from app.services import block_service, export_service
from app.services.block_service import load_tree


def add(db, blog, content, **kwargs):
    return block_service.create_blocks(db, blog, content=content, **kwargs)[-1]


def markdown(db, blog, **kwargs):
    return export_service.to_markdown(blog, load_tree(db, blog.id), **kwargs)


def html(db, blog, **kwargs):
    return export_service.to_html(blog, load_tree(db, blog.id), **kwargs)


# --- markdown --------------------------------------------------------------

def test_a_flat_post_exports_exactly_as_typed(db, blog):
    """The case that matters most: no nesting, nothing rewritten."""
    add(db, blog, "# A heading")
    add(db, blog, "Some prose.")
    add(db, blog, "- a bullet")

    body = markdown(db, blog, include_header=False)
    assert body == "# A heading\n\nSome prose.\n\n- a bullet\n"


def test_the_header_carries_the_title_and_byline(db, blog):
    add(db, blog, "Words.")
    body = markdown(db, blog)
    assert body.startswith("# A post about typing\n")
    assert "*by Tester" in body
    assert "---" in body


def test_children_become_nested_list_items(db, blog):
    add(db, blog, "parent")
    child = add(db, blog, "child")
    block_service.indent_block(db, blog, child)

    body = markdown(db, blog, include_header=False)
    assert body == "parent\n\n  - child\n"


def test_a_run_of_children_comes_out_as_one_tight_list(db, blog):
    add(db, blog, "Three reasons:")
    for line in ("first", "second", "third"):
        block_service.indent_block(db, blog, add(db, blog, line))

    body = markdown(db, blog, include_header=False)
    assert body == "Three reasons:\n\n  - first\n  - second\n  - third\n"


def test_a_child_that_is_already_a_bullet_keeps_its_own_marker(db, blog):
    add(db, blog, "parent")
    child = add(db, blog, "- child")
    block_service.indent_block(db, blog, child)

    assert markdown(db, blog, include_header=False) == "parent\n\n  - child\n"


def test_a_nested_code_block_keeps_its_fence_aligned(db, blog):
    add(db, blog, "parent")
    child = block_service.create_blocks(
        db, blog, content="```py\nx = 1\n```", block_type=BlockType.CODE
    )[0]
    block_service.indent_block(db, blog, child)

    body = markdown(db, blog, include_header=False)
    assert body == "parent\n\n  - ```py\n    x = 1\n    ```\n"


def test_a_divider_exports_as_a_rule(db, blog):
    block_service.create_blocks(db, blog, content="ignored", block_type=BlockType.DIVIDER)
    assert markdown(db, blog, include_header=False) == "---\n"


def test_a_table_survives_whole(db, blog):
    table = "| a | b |\n| - | - |\n| 1 | 2 |"
    block_service.create_blocks(db, blog, content=table, block_type=BlockType.TABLE)
    assert markdown(db, blog, include_header=False) == table + "\n"


def test_an_image_block_with_no_file_left_says_so(db, blog):
    """An image row can be deleted out from under its block; the caption stays."""
    block = add(db, blog, "the caption")
    block.block_type = BlockType.IMAGE
    db.commit()
    assert "missing image: the caption" in markdown(db, blog, include_header=False)


def test_an_image_caption_is_visible_in_markdown(db, blog):
    image = Image(
        blog_id=blog.id,
        uploaded_by_id=blog.author_id,
        image_name="lake.png",
        mime_type="image/png",
        file_format="png",
        object_key="blogs/1/lake.png",
    )
    db.add(image)
    db.commit()
    block_service.create_blocks(
        db,
        blog,
        content="A cold lake",
        block_type=BlockType.IMAGE,
        image_uuid=image.image_uuid,
    )

    body = markdown(db, blog, include_header=False)
    assert "![A cold lake]" in body
    assert "*A cold lake*" in body


# --- html ------------------------------------------------------------------

def test_html_renders_the_markdown(db, blog):
    add(db, blog, "# A heading")
    add(db, blog, "Some **bold** prose.")

    body = html(db, blog, include_header=False, standalone=False)
    assert "<h1>A heading</h1>" in body
    assert "<strong>bold</strong>" in body


def test_html_nests_children_in_their_own_container(db, blog):
    add(db, blog, "parent")
    child = add(db, blog, "child")
    block_service.indent_block(db, blog, child)

    body = html(db, blog, include_header=False, standalone=False)
    assert 'class="hl-children"' in body
    assert body.index("parent") < body.index("child")


def test_a_table_renders_as_a_table(db, blog):
    block_service.create_blocks(
        db, blog, content="| a | b |\n| - | - |\n| 1 | 2 |", block_type=BlockType.TABLE
    )
    body = html(db, blog, include_header=False, standalone=False)
    assert "<table>" in body and "<td>1</td>" in body


def test_script_in_a_post_does_not_survive_the_export(db, blog):
    """A post's author and its readers do not have to trust each other."""
    add(db, blog, "<script>alert(1)</script>")
    add(db, blog, '<img src=x onerror="alert(2)">')
    add(db, blog, "[click](javascript:alert(3))")

    body = html(db, blog, include_header=False, standalone=False)
    assert "<script" not in body
    assert "onerror" not in body
    assert "javascript:" not in body


def test_a_standalone_document_is_a_whole_page(db, blog):
    add(db, blog, "Words.")
    page = html(db, blog)
    assert page.startswith("<!DOCTYPE html>")
    assert "<title>A post about typing</title>" in page
    assert page.rstrip().endswith("</html>")


def test_a_title_with_html_in_it_is_escaped(db, blog):
    blog.title = "<script>x</script>"
    db.commit()
    add(db, blog, "Words.")
    page = html(db, blog)
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


def test_html_uses_a_real_image_caption(db, blog):
    image = Image(
        blog_id=blog.id,
        uploaded_by_id=blog.author_id,
        image_name="lake.png",
        mime_type="image/png",
        file_format="png",
        object_key="blogs/1/lake.png",
    )
    db.add(image)
    db.commit()
    block_service.create_blocks(
        db,
        blog,
        content="A cold lake",
        block_type=BlockType.IMAGE,
        image_uuid=image.image_uuid,
    )

    body = html(db, blog, include_header=False, standalone=False)
    assert "<figure>" in body
    assert "<figcaption>A cold lake</figcaption>" in body


def test_portable_bundle_uses_relative_image_paths(db, blog, monkeypatch):
    image = Image(
        blog_id=blog.id,
        uploaded_by_id=blog.author_id,
        image_name="lake.png",
        mime_type="image/png",
        file_format="png",
        object_key="blogs/1/lake.png",
    )
    db.add(image)
    db.commit()
    block_service.create_blocks(
        db,
        blog,
        content="A cold lake",
        block_type=BlockType.IMAGE,
        image_uuid=image.image_uuid,
    )
    monkeypatch.setattr(export_service.storage_service, "download_image", lambda _key: b"png")

    bundle = export_service.to_bundle(blog, load_tree(db, blog.id))
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        image_name = f"images/{image.image_uuid}.png"
        assert image_name in archive.namelist()
        document = archive.read("a-post-about-typing.md").decode()
        assert f"]({image_name})" in document
        assert archive.read(image_name) == b"png"


def test_filenames_come_from_the_slug(db, blog):
    assert export_service.filename_for(blog, "md") == "a-post-about-typing.md"
