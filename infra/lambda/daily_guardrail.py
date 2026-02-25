"""
GCP Cloud Function: daily cost guardrail kill-switch.
Triggered by Cloud Monitoring alert when the 24-hour billing cost delta exceeds $20.
Only acts on OPEN incidents — ignores resolved/closed notifications.
"""
import os
import json
import base64
import functions_framework
from google.cloud import run_v2


@functions_framework.cloud_event
def handler(cloud_event):
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
    notification = json.loads(data)

    # Cloud Monitoring sends both "open" (firing) and "closed" (resolved) notifications.
    # Only kill Cloud Run when the alert fires (state="open").
    incident = notification.get("incident", {})
    state = incident.get("state", "")

    if state != "open":
        print(f"Alert state is '{state}' — no action taken.")
        return

    summary = incident.get("summary", "daily cost limit triggered")
    print(f"Daily cost guardrail OPEN: {summary}")

    project = os.environ["GCP_PROJECT"]
    region = os.environ["CLOUD_RUN_REGION"]
    service_name = os.environ["CLOUD_RUN_SERVICE"]

    client = run_v2.ServicesClient()
    service_path = f"projects/{project}/locations/{region}/services/{service_name}"

    service = client.get_service(name=service_path)
    service.template.scaling.max_instance_count = 0
    service.template.scaling.min_instance_count = 0

    client.update_service(service=service)
    print(f"Scaled {service_name} to 0 instances — daily $20 limit breached.")
