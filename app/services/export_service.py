"""
Turning a post back into a file.

Blocks are stored as markdown fragments, so the markdown export is mostly
concatenation — the only real decision is what nesting means on the way out.

**Nesting.** Markdown has no way to put a paragraph under a paragraph. It does
have nested lists, so that is what a child block becomes: indented two spaces
per level and given a `- ` marker, unless it already starts with one of its
own. A top-level block is written out exactly as it was typed, untouched, which
is the case that matters — most posts have no nesting at all and their export
is byte-for-byte what their author wrote.

**Images** use stable server URLs in a text export. A portable bundle instead
puts the document and its image files in one zip and writes relative
`images/...` paths, so it keeps working offline and can be moved as a unit.
"""

import html
import io
import logging
import os
import re
import zipfile
from datetime import datetime
from typing import Callable, Optional

import bleach
import markdown as markdown_lib

from app.blocks.tree import BlockTree
from app.enums import BlockType, ExportFormat
from app.services import storage_service

logger = logging.getLogger(__name__)

# Content that is already a list item, and so must not be given a second marker.
LIST_MARKER = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s)")

MARKDOWN_EXTENSIONS = ["extra", "sane_lists"]

# An exported post is a file somebody opens in a browser, and its author is not
# necessarily somebody the reader knows. Everything structural survives;
# scripts, styles, iframes and event handlers do not.
ALLOWED_TAGS = [
    "p", "br", "hr", "div", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "strong", "em", "b", "i", "u", "s", "del", "ins", "mark", "sub", "sup",
    "ul", "ol", "li", "dl", "dt", "dd",
    "blockquote", "pre", "code",
    "a", "img", "figure", "figcaption",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
]
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height", "loading"],
    "div": ["class", "data-depth"],
    "p": ["class"],
    "span": ["class"],
    "code": ["class"],
    "pre": ["class"],
    "th": ["align", "colspan", "rowspan"],
    "td": ["align", "colspan", "rowspan"],
    "ol": ["start"],
}
# No `data:` — a data URL in an href is a script that survives sanitising.
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]


def public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


def default_image_url(image) -> str:
    """A stable, absolute link to an image, for a file that outlives this request.

    Falls back to a relative path when `PUBLIC_BASE_URL` is unset — in local
    development that still works from a browser on the same origin, and there
    is no correct absolute URL to invent.
    """
    return f"{public_base_url()}/api/images/{image.image_uuid}/raw"


class ExportBundleError(RuntimeError):
    """A portable export could not include every file it references."""


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def _block_markdown(block, image_url: Callable) -> str:
    """One block as markdown, before any nesting indentation is applied."""
    if block.block_type == BlockType.DIVIDER:
        return "---"

    if block.block_type == BlockType.IMAGE:
        caption = (block.content or "").strip()
        if block.image is None:
            # The file is gone but the caption explains the hole. Better than a
            # broken image tag, and better than silently dropping the block.
            return f"*[missing image{': ' + caption if caption else ''}]*"
        alt = caption or block.image.image_name or "image"
        # A markdown image's alt text is not normally visible. Keep it for
        # accessibility and repeat a real caption below the image so the
        # exported document says the same thing as the reader view.
        escaped_alt = re.sub(r"([\\\[\]])", r"\\\1", alt.replace("\n", " "))
        image = f"![{escaped_alt}]({image_url(block.image)})"
        return f"{image}\n\n*{caption}*" if caption else image

    return block.content or ""


def _indent(text: str, depth: int) -> str:
    """Nest a block's markdown `depth` levels deep. Depth 0 is left alone."""
    if depth <= 0:
        return text

    pad = "  " * depth
    lines = text.split("\n")
    first = lines[0]
    marker = "" if LIST_MARKER.match(first) else "- "
    out = [f"{pad}{marker}{first}"]
    # Continuation lines sit inside the list item, which means aligned past the
    # marker — otherwise a table or a code fence breaks out of the nesting.
    out += [f"{pad}  {line}" if line.strip() else "" for line in lines[1:]]
    return "\n".join(out)


def _header_markdown(blog) -> list[str]:
    parts = [f"# {blog.title}"]
    byline = f"by {blog.author.display_name}" if blog.author else None
    published = blog.published_at or blog.created_at
    if isinstance(published, datetime):
        stamp = published.strftime("%d %B %Y")
        byline = f"{byline} — {stamp}" if byline else stamp
    if byline:
        parts.append(f"*{byline}*")
    if blog.summary:
        parts.append(blog.summary)
    parts.append("---")
    return parts


def to_markdown(
    blog,
    tree: BlockTree,
    *,
    include_header: bool = True,
    image_url: Optional[Callable] = None,
) -> str:
    """A whole post as one markdown document.

    Blocks are separated by a blank line, the way paragraphs are — except for a
    run of nested ones, which are joined tightly so they come out as one list
    rather than as a list with a gap between every item.
    """
    image_url = image_url or default_image_url

    parts: list[tuple[int, str]] = [(0, part) for part in (_header_markdown(blog) if include_header else [])]
    for block, depth in tree.walk():
        parts.append((depth, _indent(_block_markdown(block, image_url), depth)))
    parts = [(depth, text) for depth, text in parts if text.strip() != ""]

    out = ""
    for index, (depth, text) in enumerate(parts):
        if index:
            previous_depth = parts[index - 1][0]
            out += "\n" if depth and previous_depth else "\n\n"
        out += text
    return out + "\n"


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _render(text: str) -> str:
    """Markdown to HTML. A fresh converter each time — they are stateful."""
    return markdown_lib.markdown(text, extensions=MARKDOWN_EXTENSIONS)


def _blocks_html(tree: BlockTree, image_url: Callable, parent_id=None, depth: int = 0) -> str:
    out = []
    for block in tree.siblings(parent_id):
        if block.block_type == BlockType.IMAGE and block.image is not None:
            caption = (block.content or "").strip()
            alt = caption or block.image.image_name or "image"
            figcaption = (
                f"<figcaption>{html.escape(caption)}</figcaption>" if caption else ""
            )
            rendered = (
                f'<figure><img src="{html.escape(image_url(block.image), quote=True)}" '
                f'alt="{html.escape(alt, quote=True)}" loading="lazy">{figcaption}</figure>'
            )
        else:
            rendered = _render(_block_markdown(block, image_url))
        children = _blocks_html(tree, image_url, block.id, depth + 1)
        out.append(
            f'<div class="hl-block" data-depth="{depth}">{rendered}{children}</div>'
        )
    if not out:
        return ""
    body = "".join(out)
    return body if depth == 0 else f'<div class="hl-children">{body}</div>'


DOCUMENT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    max-width: 42rem; margin: 3rem auto; padding: 0 1.25rem;
    font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  h1, h2, h3, h4 {{ line-height: 1.25; }}
  .hl-byline {{ opacity: 0.7; font-style: italic; margin-bottom: 2rem; }}
  .hl-children {{ margin-left: 1.5rem; border-left: 2px solid rgba(127,127,127,.25); padding-left: 1rem; }}
  .hl-block > p:first-child {{ margin-top: 0; }}
  img {{ max-width: 100%; height: auto; border-radius: 6px; }}
  figure {{ margin: 1.5rem 0; }}
  figcaption {{ margin-top: .45rem; font-size: .9rem; opacity: .7; }}
  pre {{ overflow-x: auto; padding: .75rem 1rem; background: rgba(127,127,127,.12); border-radius: 6px; }}
  code {{ font-size: .9em; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid rgba(127,127,127,.35); padding: .4rem .6rem; text-align: left; }}
  blockquote {{ margin-left: 0; padding-left: 1rem; border-left: 3px solid rgba(127,127,127,.35); opacity: .85; }}
  footer {{ margin-top: 4rem; font-size: .85rem; opacity: .6; }}
</style>
</head>
<body>
{body}
<footer>Typed by hand on hum.log.</footer>
</body>
</html>
"""


def to_html(
    blog,
    tree: BlockTree,
    *,
    include_header: bool = True,
    image_url: Optional[Callable] = None,
    standalone: bool = True,
) -> str:
    """A whole post as HTML.

    Sanitised before it is returned, always. The markdown belongs to the post's
    author, the file gets opened by their readers, and neither of those two
    people should have to trust the other.
    """
    image_url = image_url or default_image_url

    parts = []
    if include_header:
        parts.append(f"<h1>{html.escape(blog.title)}</h1>")
        byline = f"by {blog.author.display_name}" if blog.author else ""
        published = blog.published_at or blog.created_at
        if isinstance(published, datetime):
            stamp = published.strftime("%d %B %Y")
            byline = f"{byline} — {stamp}" if byline else stamp
        if byline:
            parts.append(f'<p class="hl-byline">{html.escape(byline)}</p>')
        if blog.summary:
            parts.append(_render(blog.summary))

    parts.append(_blocks_html(tree, image_url))
    body = bleach.clean(
        "\n".join(parts),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )

    if not standalone:
        return body
    return DOCUMENT.format(title=html.escape(blog.title), body=body)


def filename_for(blog, extension: str) -> str:
    """A download filename that a file manager will accept."""
    stem = blog.slug or "post"
    return f"{stem}.{extension}"


# ---------------------------------------------------------------------------
# Portable zip
# ---------------------------------------------------------------------------

def _bundle_image_name(image) -> str:
    """A collision-free, path-safe name inside an export bundle."""
    extension = (image.file_format or "").lower()
    if not re.fullmatch(r"[a-z0-9]+", extension):
        extension = "bin"
    return f"images/{image.image_uuid}.{extension}"


def to_bundle(
    blog,
    tree: BlockTree,
    *,
    export_format: ExportFormat = ExportFormat.MARKDOWN,
    include_header: bool = True,
) -> bytes:
    """A zip containing the document and every image it references.

    Missing image bytes fail the export instead of producing a zip whose
    relative links are already broken when it is downloaded.
    """
    images = {}
    for block, _depth in tree.walk():
        if block.block_type == BlockType.IMAGE and block.image is not None:
            images[block.image.image_uuid] = block.image

    names = {uuid: _bundle_image_name(image) for uuid, image in images.items()}
    relative_url = lambda image: names[image.image_uuid]

    if export_format == ExportFormat.HTML:
        extension = "html"
        document = to_html(
            blog,
            tree,
            include_header=include_header,
            image_url=relative_url,
        )
    else:
        extension = "md"
        document = to_markdown(
            blog,
            tree,
            include_header=include_header,
            image_url=relative_url,
        )

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(filename_for(blog, extension), document.encode("utf-8"))
        for uuid, image in images.items():
            data = storage_service.download_image(image.object_key)
            if data is None:
                raise ExportBundleError(
                    f"Couldn't include image {image.image_name or uuid} in the export"
                )
            archive.writestr(names[uuid], data)
    return output.getvalue()
