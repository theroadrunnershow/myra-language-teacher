"""
Budget kill-switch Lambda.

Triggered by SNS when the $50 monthly budget limit is hit.
Scales the ECS service to 0 tasks, stopping all compute costs immediately.

To restart the app after a kill:
  aws ecs update-service --cluster dino-app-cluster \
      --service dino-app-service --desired-count 1
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    cluster = os.environ["ECS_CLUSTER"]
    service = os.environ["ECS_SERVICE"]
    region  = os.environ["ECS_REGION"]

    logger.info("Budget kill-switch triggered.")
    logger.info(f"Scaling {cluster}/{service} to 0 in {region}.")

    for record in event.get("Records", []):
        msg = record.get("Sns", {}).get("Message", "")
        logger.info(f"SNS message: {msg[:500]}")

    ecs = boto3.client("ecs", region_name=region)
    response = ecs.update_service(
        cluster=cluster,
        service=service,
        desiredCount=0,
    )

    new_count = response["service"]["desiredCount"]
    logger.info(f"ECS service desired count is now {new_count}. App is offline.")

    return {"status": "ok", "desiredCount": new_count}
