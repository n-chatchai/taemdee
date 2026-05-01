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
            config=Config(signature_version="s3v4"),
            region_name="auto",
        ) as s3:
            await s3.put_object(
                Bucket=settings.r2_bucket,
                Key=key,
                Body=file_data,
                ContentType=content_type,
            )
            return f"{endpoint_url}/{settings.r2_bucket}/{key}"
    except Exception as e:
        logger.error(f"Failed to upload to R2: {e}")
        return None
