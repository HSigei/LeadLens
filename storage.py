from __future__ import annotations

import os

import boto3


def storage_client():
    options = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    if endpoint_url := os.getenv("AWS_ENDPOINT_URL"):
        options["endpoint_url"] = endpoint_url
    return boto3.client("s3", **options)