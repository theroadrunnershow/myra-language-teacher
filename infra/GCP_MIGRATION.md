# GCP Migration Plan

Migration from AWS to GCP due to account-level ELB restriction on new AWS accounts.
App code (Python/Docker) is unchanged — only infrastructure changes.

---

## Why GCP?

- No new-account service restrictions (unlike AWS blocking ALB creation)
- **Cloud Run** replaces ECS Fargate with less complexity: no VPC/subnet/NAT needed
- Scale-to-zero is **built-in** to Cloud Run (no nightly scheduler needed)
- Built-in HTTPS on `*.run.app` domain (no ALB + certificate management)
- Generous free tier: 2M requests/month, 360K vCPU-seconds/month

---

## AWS → GCP Service Mapping

| AWS Service | GCP Equivalent |
|---|---|
| ECS Fargate | Cloud Run |
| ECR | Artifact Registry |
| ALB | Global HTTPS Load Balancer |
| CloudFront | Cloud CDN |
| WAF | Cloud Armor |
| Lambda | Cloud Functions (Gen 2) |
| EventBridge Scheduler | Removed — Cloud Run scales to 0 natively |
| SNS | Pub/Sub |
| SSM Parameter Store | Secret Manager |
| CloudWatch Logs | Cloud Logging (built-in) |
| AWS Budgets | GCP Billing Budgets |
| S3 (tfstate) | Cloud Storage (GCS) |

---

## Architecture Comparison

**AWS (current):**
```
Internet → CloudFront → WAF → ALB → VPC → Private Subnet → ECS → NAT → internet
```

**GCP (new):**
```
Internet → Cloud Armor → Global HTTPS LB → Cloud CDN → Cloud Run (auto scale 0–2)
```

---

## Step 1: GCP Account Setup (One-Time Manual Steps)

### 1.1 Create GCP Project
1. Go to https://console.cloud.google.com
2. Create new project — suggested name: `myra-language-teacher`
3. Note your **Project ID** (e.g., `myra-language-teacher`)
4. Go to Billing → link a billing account (credit card required)

### 1.2 Install gcloud CLI
```bash
brew install --cask google-cloud-sdk
gcloud init
# Select your project when prompted
gcloud auth application-default login
```

### 1.3 Enable Required APIs
```bash
export PROJECT_ID=myra-language-teacher   # e.g. myra-language-teacher

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudfunctions.googleapis.com \
  pubsub.googleapis.com \
  billingbudgets.googleapis.com \
  compute.googleapis.com \
  storage.googleapis.com \
  --project=$PROJECT_ID
```

### 1.4 Create Terraform Service Account
```bash
gcloud iam service-accounts create terraform-deploy \
  --display-name="Terraform Deploy" \
  --project=$PROJECT_ID

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:terraform-deploy@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/owner"

gcloud iam service-accounts keys create ~/terraform-gcp-key.json \
  --iam-account=terraform-deploy@$PROJECT_ID.iam.gserviceaccount.com

export GOOGLE_APPLICATION_CREDENTIALS=~/terraform-gcp-key.json
```

### 1.5 Create GCS Bucket for Terraform State
```bash
gsutil mb -p $PROJECT_ID -l us-west1 gs://myra-tfstate/
gsutil versioning set on gs://myra-tfstate/
```

---

## Step 2: Terraform Rewrite

Replace all existing `infra/*.tf` files. The new structure is simpler — 9 files instead of 12.

### Files eliminated (not needed in GCP):
- `vpc.tf` — Cloud Run is serverless, no VPC/subnets/NAT needed
- `ecs.tf` — replaced by `cloud_run.tf`
- `ecr.tf` — replaced by `artifact_registry.tf`
- `alb.tf` — replaced by `load_balancer.tf`
- `scheduler.tf` — Cloud Run scales to zero natively
- `ssm.tf` — replaced by `secret_manager.tf`
- `iam.tf` — IAM defined inline per resource

### New files:
| File | Replaces | Purpose |
|---|---|---|
| `providers.tf` | `providers.tf` | Google provider, GCS backend |
| `variables.tf` | `variables.tf` | project_id, region, alert_email, budget_limit |
| `artifact_registry.tf` | `ecr.tf` | Container image repository |
| `cloud_run.tf` | `ecs.tf` + `vpc.tf` + `scheduler.tf` | Serverless container, auto scale 0–2 |
| `load_balancer.tf` | `alb.tf` + `cloudfront.tf` | Global HTTPS LB + Cloud CDN |
| `cloud_armor.tf` | WAF in `cloudfront.tf` | Rate limiting rules |
| `secret_manager.tf` | `ssm.tf` | child_name, thresholds config |
| `budgets.tf` | `budgets.tf` | Billing budget + Pub/Sub + Cloud Functions kill-switch |
| `outputs.tf` | `outputs.tf` | app URL, registry URL |

---

## Step 3: Build & Push Docker Image to GCP

```bash
# Authenticate Docker to Artifact Registry
gcloud auth configure-docker us-west1-docker.pkg.dev

# Build image
docker build -t us-west1-docker.pkg.dev/$PROJECT_ID/myra/dino-app:latest .

# Push to Artifact Registry
docker push us-west1-docker.pkg.dev/$PROJECT_ID/myra/dino-app:latest
```

---

## Step 4: Deploy with Terraform

```bash
cd infra/
terraform init
terraform plan
terraform apply
```

---

## Step 5: CI/CD Setup (GitHub Actions)

CI/CD is handled by GitHub Actions — free, no GCP cost, 2,000 min/month included.
Workflow file: `.github/workflows/deploy.yml`

**Trigger**: Push to `main` → build Docker image → push to Artifact Registry → deploy to Cloud Run.

### One-time GitHub Secrets setup
Go to: GitHub repo → Settings → Secrets and variables → Actions → New repository secret

| Secret | Value |
|---|---|
| `GCP_SA_KEY` | Full JSON contents of `~/terraform-gcp-key.json` |
| `GCP_PROJECT_ID` | Your GCP project ID (e.g. `myra-language-teacher-123456`) |
| `GCP_REGION` | `us-west1` |

> Cloud Run service name (`dino-app`) is hardcoded in the workflow since it's fixed by Terraform.

### What happens on each push to `main`
1. Checkout code
2. Authenticate to GCP via service account key
3. Configure Docker for `us-west1-docker.pkg.dev`
4. Build image tagged with both `:latest` and `:<git-sha>`
5. Push both tags to Artifact Registry
6. Deploy new revision to Cloud Run using the SHA-tagged image

### Future upgrade: Workload Identity Federation (optional)
Replace the JSON key with keyless OIDC tokens — more secure, no long-lived credentials.
Requires one-time `gcloud iam workload-identity-pools create` setup when ready.

---

## Step 6: Verification Checklist

- [ ] `terraform init` succeeds with GCS backend
- [ ] `terraform plan` shows expected resources, no errors
- [ ] `terraform apply` completes without errors
- [ ] Cloud Run URL (from `terraform output app_url`) returns HTTP 200
- [ ] TTS works: click a word, hear audio in Telugu/Assamese
- [ ] STT works: record speech, get recognition result
- [ ] Cloud Armor rate limits visible in GCP Console → Cloud Armor
- [ ] Scale-to-zero: wait 15 min idle → Cloud Run instances drop to 0
- [ ] Billing budget alert configured in GCP Console → Billing → Budgets

---

## App Code Changes

**None.** The following files are unchanged:
- `main.py`, `words_db.py`, `speech_service.py`, `tts_service.py`
- `templates/`, `static/`
- `Dockerfile` (same image, different registry destination)

---

## Cost Estimate (GCP Free Tier)

| Service | Free Tier | Notes |
|---|---|---|
| Cloud Run | 2M req/mo, 360K vCPU-sec, 180K GB-sec | Likely free for personal use |
| Artifact Registry | 0.5 GB free | Image is ~500MB, may incur small cost |
| Cloud Load Balancing | 5 rules free | Global HTTPS LB |
| Cloud CDN | First 10GB egress free/mo | |
| Secret Manager | 6 active versions free | |
| Cloud Functions | 2M invocations free | Budget kill-switch |
| Cloud Storage (tfstate) | 5GB free | |

**Estimated monthly cost: $0–$5** for light personal use.
