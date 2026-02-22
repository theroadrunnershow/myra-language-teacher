#!/usr/bin/env bash
# deploy/build-push.sh
#
# Builds the Docker image for linux/amd64 (Fargate) and pushes it to ECR.
# Optionally forces a new ECS deployment so the cluster picks up the new image.
#
# Usage (run from project root):
#   ./deploy/build-push.sh            # build + push only
#   ./deploy/build-push.sh --deploy   # build + push + force ECS deployment

set -euo pipefail

REGION="us-west-2"
PREFIX="dino-app"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Myra Language Teacher — Build & Push ==="
echo "  Region:  $REGION"
echo "  Prefix:  $PREFIX"
echo "  Source:  $PROJECT_ROOT"
echo ""

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
ECR_REPO="${REGISTRY}/${PREFIX}"

echo "-> ECR repository: $ECR_REPO"
echo ""

echo "-> Authenticating Docker to ECR..."
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "$REGISTRY"

echo ""
echo "-> Building image (linux/amd64)..."
echo "   Note: Whisper base model (~140 MB) downloads during build."
echo "   First build takes ~5-10 minutes."
echo ""
docker build \
  --platform linux/amd64 \
  --tag "${PREFIX}:latest" \
  "$PROJECT_ROOT"

echo ""
echo "-> Tagging and pushing to ECR..."
docker tag "${PREFIX}:latest" "${ECR_REPO}:latest"
docker push "${ECR_REPO}:latest"
echo "   Pushed: ${ECR_REPO}:latest"

if [[ "${1:-}" == "--deploy" ]]; then
  echo ""
  echo "-> Forcing new ECS deployment..."
  aws ecs update-service \
    --region "$REGION" \
    --cluster "${PREFIX}-cluster" \
    --service "${PREFIX}-service" \
    --force-new-deployment \
    --output text --query "service.serviceName"

  echo ""
  echo "   Monitor rollout:"
  echo "   aws ecs wait services-stable --region $REGION --cluster ${PREFIX}-cluster --services ${PREFIX}-service"
fi

echo ""
echo "=== Done! ==="
