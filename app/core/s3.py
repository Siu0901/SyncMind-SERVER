from typing import BinaryIO, Optional

import aioboto3

from app.core.config import get_settings


settings = get_settings()


class S3Client:
    def __init__(self):
        self.bucket = settings.AWS_S3_BUCKET

        self.session = aioboto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY.get_secret_value(),
            region_name=settings.AWS_REGION,
        )


    async def upload_file(
        self,
        file: BinaryIO,
        key: str,
        content_type: Optional[str] = None,
    ):
        extra_args = {}

        if content_type:
            extra_args["ContentType"] = content_type

        async with self.session.client("s3") as s3_client:
            await s3_client.upload_fileobj(
                Fileobj=file,
                Bucket=self.bucket,
                Key=key,
                ExtraArgs=extra_args,
            )


    async def download_file(self, key: str) -> bytes:
        async with self.session.client("s3") as s3_client:
            response = await s3_client.get_object(
                Bucket=self.bucket,
                Key=key,
            )

            async with response["Body"] as body:
                return await body.read()


    async def delete_file(self, key: str):
        async with self.session.client("s3") as s3_client:
            await s3_client.delete_object(
                Bucket=self.bucket,
                Key=key,
            )