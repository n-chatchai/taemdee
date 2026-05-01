import aioboto3
from botocore.config import Config
from app.core.config import settings
from typing import Optional, BinaryIO
import uuid
import logging
import io
from PIL import Image

logger = logging.getLogger(__name__)


def process_image_to_square(file_data: bytes) -> bytes:
    """Center-crops an image to 1:1 ratio and resizes to max 800x800."""
    img = Image.open(io.BytesIO(file_data))
    
    # Convert to RGB if necessary (e.g., PNG with alpha to JPEG-friendly or just for consistency)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    
    width, height = img.size
    new_size = min(width, height)
    
    left = (width - new_size) / 2
    top = (height - new_size) / 2
    right = (width + new_size) / 2
    bottom = (height + new_size) / 2
    
    img = img.crop((left, top, right, bottom))
    
    # Resize if too large
    if new_size > 800:
        img = img.resize((800, 800), Image.Resampling.LANCZOS)
    
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=85)
    return output.getvalue()

async def upload_to_r2(
    file_data: bytes,
    file_name: str,
    content_type: str,
    folder: str = "uploads",
    is_image: bool = False
) -> Optional[str]:
    """Uploads a file to Cloudflare R2 and returns the public URL."""
    if not all([settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key]):
        logger.warning("R2 storage is not configured. Skipping upload.")
        return None

    if is_image:
        try:
            file_data = process_image_to_square(file_data)
            content_type = "image/jpeg"
            file_name = file_name.rsplit(".", 1)[0] + ".jpg"
        except Exception as e:
            logger.error(f"Image processing failed: {e}")
            # Continue with original if processing fails? Probably safer to fail.
            return None

    session = aioboto3.Session()
    endpoint_url = settings.r2_endpoint_url.rstrip("/")

    # Generate a unique key
    ext = file_name.split(".")[-1] if "." in file_name else "bin"
    key = f"{folder}/{uuid.uuid4()}.{ext}"

    try:
        async with session.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            # R2 quirks: (a) boto3 ≥1.36 ships flexible checksums
            # (STREAMING-UNSIGNED-PAYLOAD-TRAILER) which R2 rejects as
            # `InvalidArgument: Authorization` — pin both knobs to
            # legacy "when_required" (boto/boto3#4392). (b) R2 only
            # accepts path-style addressing on the account endpoint;
            # boto3's default virtual-hosted style routes to a
            # nonexistent <bucket>.<account>.r2.cloudflarestorage.com
            # subdomain.
            config=Config(
                signature_version="s3v4",
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
                s3={"addressing_style": "path"},
            ),
            region_name="auto",
        ) as s3:
            await s3.put_object(
                Bucket=settings.r2_bucket,
                Key=key,
                Body=file_data,
                ContentType=content_type,
            )
            # PUT goes to the S3 API host; GET goes to the browser-facing
            # public host (r2.dev subdomain or custom domain). The two
            # are different — the API host doesn't serve unsigned reads.
            if settings.r2_public_url:
                public_root = settings.r2_public_url.rstrip("/")
                return f"{public_root}/{key}"
            logger.warning("R2_PUBLIC_URL is unset — returning API host URL "
                           "which will reject browser GETs. Set R2_PUBLIC_URL "
                           "to your r2.dev subdomain or custom domain.")
            return f"{endpoint_url}/{settings.r2_bucket}/{key}"
    except Exception as e:
        # boto3's ClientError.__str__ collapses to "An error occurred ()"
        # when error_code/message parsed off the response are empty, which
        # hides whatever R2 actually returned. Log the parsed response and
        # the full traceback so the next failure mode is debuggable.
        from botocore.exceptions import ClientError
        if isinstance(e, ClientError):
            logger.exception(
                "R2 PutObject failed — endpoint=%s bucket=%s key=%s response=%r",
                endpoint_url, settings.r2_bucket, key, e.response,
            )
        else:
            logger.exception(
                "R2 PutObject failed — endpoint=%s bucket=%s key=%s",
                endpoint_url, settings.r2_bucket, key,
            )
        return None
