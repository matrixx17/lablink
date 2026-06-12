
import os, uuid
import boto3
from botocore.client import Config as BotoConfig

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin")
S3_BUCKET = os.getenv("S3_BUCKET", "lablink")
S3_SECURE = os.getenv("S3_SECURE", "false").lower() == "true"

session = boto3.session.Session()
s3 = session.client(
    "s3",
    region_name=S3_REGION,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    endpoint_url=S3_ENDPOINT,
    config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
)

def ensure_bucket():
    try:
        existing = s3.list_buckets()
        names = [b["Name"] for b in existing.get("Buckets", [])]
        if S3_BUCKET not in names:
            s3.create_bucket(Bucket=S3_BUCKET)
    except Exception:
        pass  # S3/MinIO unavailable — file upload features disabled

def get_presigned_post(filename: str, org_id: str = "default-org"):
    key = f"{org_id}/{uuid.uuid4()}_{os.path.basename(filename)}"
    resp = s3.generate_presigned_post(
        Bucket=S3_BUCKET,
        Key=key,
        Fields=None,
        Conditions=[["content-length-range", 0, 104857600]],  # 100 MB limit for demo
        ExpiresIn=3600,
    )
    return resp["url"], {"fields": resp["fields"], "key": key, "bucket": S3_BUCKET}
