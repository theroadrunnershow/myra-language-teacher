"""
GCP Cloud Function: budget kill-switch.
Triggered by Pub/Sub when monthly spend hits 100% of budget.
Scales Cloud Run service to 0 max instances to stop all compute cost.
"""
import os
import json
import base64
import functions_framework
from google.cloud import run_v2


@functions_framework.cloud_event
def handler(cloud_event):
    data = base64.b64decode(cloud_event.data["message"]["data"]).decode("utf-8")
    budget_data = json.loads(data)

    cost_amount = float(budget_data.get("costAmount", 0))
    budget_amount = float(budget_data.get("budgetAmount", 0))
    print(f"Budget alert: ${cost_amount:.2f} spent of ${budget_amount:.2f} budget")

    project = os.environ["GCP_PROJECT"]
    region = os.environ["CLOUD_RUN_REGION"]
    service_name = os.environ["CLOUD_RUN_SERVICE"]

    client = run_v2.ServicesClient()
    service_path = f"projects/{project}/locations/{region}/services/{service_name}"

    service = client.get_service(name=service_path)
    service.template.scaling.max_instance_count = 0
    service.template.scaling.min_instance_count = 0

    client.update_service(service=service)
    print(f"Scaled {service_name} to 0 instances due to budget limit.")
