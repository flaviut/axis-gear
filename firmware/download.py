#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["boto3"]
# ///
"""Download all AXIS Gear firmware from the axisota S3 bucket."""

import os
import boto3

BUCKET = "axisota"
REGION = "us-east-1"
COGNITO_POOL = "us-east-1:7588162b-7715-4238-b1f4-5ac8406435c9"

dest = os.path.dirname(os.path.abspath(__file__))

cognito = boto3.client("cognito-identity", region_name=REGION)
identity = cognito.get_id(IdentityPoolId=COGNITO_POOL)
creds = cognito.get_credentials_for_identity(IdentityId=identity["IdentityId"])["Credentials"]

s3 = boto3.client(
    "s3",
    aws_access_key_id=creds["AccessKeyId"],
    aws_secret_access_key=creds["SecretKey"],
    aws_session_token=creds["SessionToken"],
    region_name=REGION,
)

objects = []
result = s3.list_objects_v2(Bucket=BUCKET)
objects.extend(result.get("Contents", []))
while result.get("IsTruncated"):
    result = s3.list_objects_v2(Bucket=BUCKET, ContinuationToken=result["NextContinuationToken"])
    objects.extend(result.get("Contents", []))

for i, obj in enumerate(sorted(objects, key=lambda x: x["Key"]), 1):
    key = obj["Key"]
    path = os.path.join(dest, key)
    if os.path.exists(path) and os.path.getsize(path) == obj["Size"]:
        print(f"[{i}/{len(objects)}] {key} (exists)")
        continue
    print(f"[{i}/{len(objects)}] {key}")
    s3.download_file(BUCKET, key, path)

print(f"\nDone. {len(objects)} files in {dest}")
