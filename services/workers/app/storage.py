"""S3-compatible storage client (Supabase Storage in dev, ADR-0002)."""

from typing import Any

import aioboto3
from botocore.config import Config

from app.config import Settings


def s3_client(settings: Settings) -> Any:
    """Async context manager yielding an S3 client: `async with s3_client(settings) as s3: ...`."""
    session = aioboto3.Session()
    return session.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
