#!/usr/bin/env bash
# deploy/bootstrap.sh
#
# Creates the S3 bucket that Terraform uses for remote state.
# State locking uses S3 native locking (use_lockfile=true) — no DynamoDB needed.
# Run this ONCE before the first `terraform init`.
#
# Prerequisites: AWS CLI configured with a profile that has admin rights.
# Usage: ./deploy/bootstrap.sh

set -euo pipefail

REGION="us-west-2"
BUCKET="dino-app-tfstate"

echo "=== Terraform S3 Backend Bootstrap ==="
echo "  Region:    $REGION"
echo "  S3 bucket: $BUCKET"
echo ""

# ── S3 Bucket ─────────────────────────────────────────────────────────────────
echo "-> Creating S3 bucket..."
if aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  echo "  (bucket already exists, skipping)"
else
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$BUCKET" --region "$REGION"
  else
    aws s3api create-bucket \
      --bucket "$BUCKET" \
      --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
fi

echo "-> Enabling versioning..."
aws s3api put-bucket-versioning \
  --bucket "$BUCKET" \
  --versioning-configuration Status=Enabled

echo "-> Enabling server-side encryption..."
aws s3api put-bucket-encryption \
  --bucket "$BUCKET" \
  --server-side-encryption-configuration '{
    "Rules": [{
      "ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"},
      "BucketKeyEnabled": true
    }]
  }'

echo "-> Blocking all public access..."
aws s3api put-public-access-block \
  --bucket "$BUCKET" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

echo "  S3 bucket ready: s3://$BUCKET"
echo ""

echo "=== Bootstrap complete! Next steps: ==="
echo ""
echo "  1. Edit infra/variables.tf and set your alert_email"
echo "  2. cd infra"
echo "  3. terraform init"
echo "  4. terraform plan"
echo "  5. terraform apply"
echo ""
echo "  Then push your Docker image:"
echo "  6. ./deploy/build-push.sh --deploy"
