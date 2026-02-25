"""
GCP Cloud Function: daily restore.
Triggered by Cloud Scheduler at midnight UTC every day.
Restores Cloud Run max instances so the service is available at the start of each new billing day.
"""
import os
import functions_framework
from google.cloud import run_v2


@functions_framework.cloud_event
def handler(cloud_event):
    max_instances = int(os.environ.get("MAX_INSTANCES", "2"))
    project = os.environ["GCP_PROJECT"]
    region = os.environ["CLOUD_RUN_REGION"]
    service_name = os.environ["CLOUD_RUN_SERVICE"]

    client = run_v2.ServicesClient()
    service_path = f"projects/{project}/locations/{region}/services/{service_name}"

    service = client.get_service(name=service_path)

    if service.template.scaling.max_instance_count == 0:
        service.template.scaling.max_instance_count = max_instances
        service.template.scaling.min_instance_count = 0
        client.update_service(service=service)
        print(f"Restored {service_name} to {max_instances} max instances for new day.")
    else:
        print(f"{service_name} already running at {service.template.scaling.max_instance_count} max instances — no restore needed.")
