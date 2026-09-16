"""
Object storage for images, over the S3 API (boto3).

The environment variables are called `MINIO_*` because that is what every other
backend in this cluster calls them, and because the endpoint they point at is
the same MinIO. Nothing in here is MinIO-specific — set `MINIO_ENDPOINT` to an
AWS regional endpoint and the same code talks to S3.

Key layout:

    blogs/{blog_id}/{image_uuid}.{ext}

Post first, so everything belonging to one post sits under a single prefix and
can be listed (or deleted) in one call.
"""

import io
import logging
import os
from typing import Optional

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

BUCKET = os.getenv("MINIO_BUCKET", "humlog")


class StorageService:
    """A lazily-connected S3 client. Storage being down must not stop the app booting."""

    def __init__(self):
        self.endpoint_url = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
        self.access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        self.secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
        self.bucket_name = BUCKET
        self.region = os.getenv("MINIO_REGION", "us-east-1")
        self._client = None
        self._initialized = False

    @property
    def client(self):
        """Lazy-init the S3 client so the app doesn't crash if storage is down at startup."""
        if self._client is None:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
                config=Config(signature_version="s3v4"),
            )
        if not self._initialized:
            self._ensure_bucket_exists()
            self._initialized = True
        return self._client

    def _ensure_bucket_exists(self):
        """Create the bucket if missing. Non-fatal if storage is unreachable."""
        try:
            self._client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchBucket"):
                try:
                    self._client.create_bucket(Bucket=self.bucket_name)
                    logger.info(f"Created bucket: {self.bucket_name}")
                except ClientError as create_err:
                    logger.error(f"Error creating bucket: {create_err}")
            else:
                logger.warning(f"Could not verify bucket: {e}")
        except Exception as e:
            # Connection errors, DNS failures — don't crash the app
            logger.warning(f"Storage not reachable during bucket check: {e}")

    def upload_bytes(self, data: bytes, object_key: str, content_type: str) -> bool:
        try:
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=object_key,
                Body=io.BytesIO(data),
                ContentType=content_type,
            )
            return True
        except ClientError as e:
            logger.error(f"Error uploading {object_key}: {e}")
            return False

    def download_bytes(self, object_key: str) -> Optional[bytes]:
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=object_key)
            return response["Body"].read()
        except ClientError as e:
            logger.error(f"Error downloading {object_key}: {e}")
            return None

    def get_presigned_url(self, object_key: str, expiration: int = 3600) -> Optional[str]:
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": object_key},
                ExpiresIn=expiration,
            )
        except ClientError as e:
            logger.error(f"Error generating presigned URL for {object_key}: {e}")
            return None

    def delete_object(self, object_key: str) -> bool:
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=object_key)
            return True
        except ClientError as e:
            logger.error(f"Error deleting {object_key}: {e}")
            return False

    def delete_objects_batch(self, object_keys: list) -> bool:
        if not object_keys:
            return True
        try:
            # The S3 API caps one delete_objects call at 1000 keys.
            for start in range(0, len(object_keys), 1000):
                chunk = object_keys[start:start + 1000]
                self.client.delete_objects(
                    Bucket=self.bucket_name,
                    Delete={"Objects": [{"Key": key} for key in chunk]},
                )
            return True
        except ClientError as e:
            logger.error(f"Error batch deleting images: {e}")
            return False


_storage_service = None


def get_storage_service() -> StorageService:
    global _storage_service
    if _storage_service is None:
        _storage_service = StorageService()
    return _storage_service


def blog_image_key(blog_id: int, image_uuid: str, extension: str) -> str:
    return f"blogs/{blog_id}/{image_uuid}.{extension}"



def upload_image(data: bytes, object_key: str, content_type: str) -> str:
    service = get_storage_service()
    if not service.upload_bytes(data, object_key, content_type):
        raise RuntimeError(f"Failed to upload image to storage: {object_key}")
    return object_key


def download_image(object_key: str) -> Optional[bytes]:
    """An object's bytes, or None if storage is unreachable or the key is gone."""
    try:
        return get_storage_service().download_bytes(object_key)
    except Exception as e:
        logger.error(f"Error in download_image: {e}")
        return None


def generate_presigned_url(object_key: str, expiration: int = 3600) -> Optional[str]:
    try:
        return get_storage_service().get_presigned_url(object_key, expiration)
    except Exception as e:
        logger.error(f"Error in generate_presigned_url: {e}")
        return None


def delete_image(object_key: str) -> bool:
    try:
        return get_storage_service().delete_object(object_key)
    except Exception as e:
        logger.error(f"Error in delete_image: {e}")
        return False


def delete_images(object_keys: list) -> bool:
    """Batch-delete by key. Callers read the keys out of the database, so this
    stays exact instead of guessing at prefixes."""
    try:
        return get_storage_service().delete_objects_batch(object_keys)
    except Exception as e:
        logger.error(f"Error in delete_images: {e}")
        return False
