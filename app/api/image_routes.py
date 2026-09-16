"""
Images: the one thing hum.log lets you paste.

An upload puts the file in object storage and returns a row. Turning that row
into something a reader sees is a separate call — `POST .../blocks` with the
`image_uuid` — so the two operations stay independent and a failed upload never
leaves an empty block behind.

Two ways to read an image back:

* `/raw` streams the bytes through this server. The URL is stable for as long
  as the image exists, which is what exports and public pages need.
* `/url` hands back a short-lived signed link straight to storage. Cheaper, and
  what the editor should use while somebody is working.
"""

import logging
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_optional_user, own_blog
from app.api.rate_limit import rate_limit_upload
from app.api.schemas import ImageOut, PresignedUrlOut
from app.api import serializers
from app.db.database import get_db
from app.db.models import Block, Image, User, generate_uuid
from app.enums import BlogStatus
from app.services import storage_service

logger = logging.getLogger(__name__)
router = APIRouter(tags=["images"])

MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

# No SVG. An SVG is a document that can run script, and these files are served
# back from this origin to readers who did not write them.
ALLOWED_MIME_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/avif": "avif",
}

PRESIGNED_URL_TTL = 3600
# A given uuid is a given file forever, so a browser never needs to ask twice.
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


async def _read_upload(upload: UploadFile) -> tuple[bytes, str, str]:
    """Validate a multipart upload and return (bytes, mime type, extension)."""
    mime_type = (upload.content_type or "").split(";")[0].strip().lower()
    extension = ALLOWED_MIME_TYPES.get(mime_type)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"'{mime_type or 'unknown'}' isn't an image type we take. "
                f"Allowed: {', '.join(sorted(ALLOWED_MIME_TYPES))}"
            ),
        )

    data = await upload.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="That file was empty"
        )
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Images are limited to {MAX_IMAGE_BYTES // (1024 * 1024)} MB",
        )
    return data, mime_type, extension


def _readable_image(db: Session, image_uuid: str, user: Optional[User]) -> Image:
    """An image the caller is allowed to see.

    Published post: anyone. Draft: its author only — a draft's images are as
    private as the draft, and a leaked uuid must not be a way around that.
    """
    image = db.query(Image).filter(Image.image_uuid == image_uuid).first()
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such image"
        )
    if image.blog.status == BlogStatus.PUBLISHED:
        return image
    if user is not None and image.blog.author_id == user.id:
        return image
    # 404, not 403: whether a draft exists is part of what a draft is.
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image")


@router.post(
    "/blogs/{blog_uuid}/images",
    response_model=ImageOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_upload)],
)
async def upload_image(
    blog_uuid: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload an image into one of my posts."""
    blog = own_blog(db, current_user, blog_uuid)
    data, mime_type, extension = await _read_upload(file)

    image_uuid = generate_uuid()
    object_key = storage_service.blog_image_key(blog.id, image_uuid, extension)
    try:
        storage_service.upload_image(data, object_key, mime_type)
    except RuntimeError as exc:
        logger.error(f"Upload failed for post {blog_uuid}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't store that image. Try again.",
        )

    # The row is written only once the bytes are safely in storage, so a
    # failed upload leaves nothing to clean up.
    image = Image(
        image_uuid=image_uuid,
        blog_id=blog.id,
        uploaded_by_id=current_user.id,
        image_name=file.filename,
        mime_type=mime_type,
        file_size=len(data),
        file_format=extension,
        object_key=object_key,
        bucket=storage_service.BUCKET,
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    logger.info(f"{current_user.username} uploaded {object_key}")
    return serializers.image_out(image)


@router.get("/blogs/{blog_uuid}/images", response_model=list[ImageOut])
def list_images(
    blog_uuid: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every image uploaded into one of my posts, including ones no block uses."""
    blog = own_blog(db, current_user, blog_uuid)
    images = (
        db.query(Image)
        .filter(Image.blog_id == blog.id)
        .order_by(Image.id.desc())
        .all()
    )
    return [serializers.image_out(image) for image in images]


@router.get("/images/{image_uuid}/raw")
def get_image_bytes(
    image_uuid: str,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """The image itself, at a URL that keeps working.

    This goes through the app rather than redirecting, so the link in an
    exported file is one that does not expire and does not leak a signed
    storage URL to whoever opens the file.
    """
    image = _readable_image(db, image_uuid, current_user)
    data = storage_service.download_image(image.object_key)
    if data is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't fetch that image right now",
        )
    return Response(
        content=data,
        media_type=image.mime_type or "application/octet-stream",
        headers={"Cache-Control": IMMUTABLE_CACHE},
    )


@router.get("/images/{image_uuid}/url", response_model=PresignedUrlOut)
def get_image_url(
    image_uuid: str,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    """A short-lived link straight to storage. For the editor, not for exports."""
    image = _readable_image(db, image_uuid, current_user)
    url = storage_service.generate_presigned_url(image.object_key, PRESIGNED_URL_TTL)
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't generate an image link right now",
        )
    return PresignedUrlOut(
        image_uuid=image.image_uuid, url=url, expires_in=PRESIGNED_URL_TTL
    )


@router.delete("/blogs/{blog_uuid}/images/{image_uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    blog_uuid: str,
    image_uuid: str,
    delete_blocks: bool = Query(
        default=True, description="Also delete the image blocks that showed it"
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete an image from one of my posts.

    The blocks showing it go too by default, because an image block with no
    image is a hole in the post. Ask for `delete_blocks=false` and they stay,
    keeping their captions and rendering as a missing image.
    """
    blog = own_blog(db, current_user, blog_uuid)
    image = (
        db.query(Image)
        .filter(Image.image_uuid == image_uuid, Image.blog_id == blog.id)
        .first()
    )
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such image in this post"
        )

    if delete_blocks:
        for block in db.query(Block).filter(Block.image_id == image.id).all():
            db.delete(block)

    object_key = image.object_key
    db.delete(image)
    db.commit()

    if not storage_service.delete_image(object_key):
        logger.warning(f"Removed image row {image_uuid} but left {object_key} in storage")
