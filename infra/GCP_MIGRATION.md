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

**AWS (old):**
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
2. Create new project — name: `myra-language-teacher`
3. Note your **Project ID** (e.g., `myra-language-teacher`)
4. Go to Billing → link a billing account (credit card required)
5. Note your **Billing Account ID** (format `XXXXXX-XXXXXX-XXXXXX`) — needed for `terraform.tfvars`

### 1.2 Install gcloud CLI and authenticate
```bash
brew install --cask google-cloud-sdk
gcloud init                              # select project when prompted
gcloud auth application-default login   # ADC for Terraform

# REQUIRED: set quota project or Billing Budgets API will return 403
gcloud auth application-default set-quota-project myra-language-teacher
```

### 1.3 Enable Required APIs (bootstrap — one-time only)
```bash
export PROJECT_ID=myra-language-teacher

# cloudresourcemanager MUST be enabled first — Terraform's data.google_project depends on it
# This is a bootstrap prerequisite; all other APIs are then managed by Terraform (apis.tf)
gcloud services enable cloudresourcemanager.googleapis.com --project=$PROJECT_ID

gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudfunctions.googleapis.com \
  eventarc.googleapis.com \
  pubsub.googleapis.com \
  billingbudgets.googleapis.com \
  compute.googleapis.com \
  storage.googleapis.com \
  --project=$PROJECT_ID
```

> After the first `terraform apply`, all of the above are managed by `infra/apis.tf`.
> The `cloudresourcemanager` bootstrap stays manual — it's a chicken-and-egg requirement
> (Terraform needs it enabled to even read project metadata).

### 1.4 Create Terraform Service Account (for GitHub Actions CI/CD)
```bash
gcloud iam service-accounts create terraform-deploy \
  --display-name="Terraform Deploy" \
  --project=$PROJECT_ID

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:terraform-deploy@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/owner"

gcloud iam service-accounts keys create ~/terraform-gcp-key.json \
  --iam-account=terraform-deploy@$PROJECT_ID.iam.gserviceaccount.com
```

> For **local Terraform runs**, ADC (step 1.2) is used — no key file needed.
> The key file is only needed for GitHub Actions secrets.

### 1.5 Create GCS Bucket for Terraform State
```bash
gsutil mb -p $PROJECT_ID -l us-west1 gs://myra-language-teacher-tfstate/
gsutil versioning set on gs://myra-language-teacher-tfstate/
```

---

## Step 2: Terraform Files

All infra is in `infra/`. The structure is GCP-native — simpler than AWS (no VPC, no NAT, no scheduler).

### Files and their purpose:
| File | Purpose |
|---|---|
| `providers.tf` | Google provider (with `user_project_override`), GCS backend |
| `variables.tf` | `project_id`, `billing_account_id`, `region`, `budget_limit`, etc. |
| `terraform.tfvars` | Actual values for required vars — **gitignored**, create locally |
| `apis.tf` | All `google_project_service` resources — enables APIs via Terraform |
| `artifact_registry.tf` | Docker image repository |
| `cloud_run.tf` | Cloud Run service, auto scale 0–2, IAM for public access |
| `load_balancer.tf` | Global HTTPS LB + Cloud CDN + HTTP→HTTPS redirect |
| `cloud_armor.tf` | Rate limiting / WAF rules |
| `secret_manager.tf` | Secrets: `child-name`, `similarity-threshold`, `max-attempts`, `languages` |
| `budgets.tf` | Billing budget + Pub/Sub + Cloud Functions kill-switch |
| `outputs.tf` | `app_url`, `cloud_run_url`, `registry_url` |

### Create `infra/terraform.tfvars` (gitignored — do not commit)
```hcl
project_id         = "myra-language-teacher"
billing_account_id = "XXXXXX-XXXXXX-XXXXXX"   # from GCP Console → Billing
```

---

## Step 3: Build & Push Docker Image

> **All commands run from the project root** (`myra-language-teacher/`), not from `infra/`.

```bash
# Authenticate Docker to Artifact Registry (one-time)
gcloud auth configure-docker us-west1-docker.pkg.dev
```

### Apple Silicon Mac (M1/M2/M3) — use multi-arch build
Cloud Run requires `linux/amd64`. Build a multi-arch image so the same tag works both
locally (arm64) and on Cloud Run (amd64):

```bash
# One-time: create a buildx builder
docker buildx create --name multiarch --use

# Build and push in one step (includes both arm64 + amd64 variants)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  --push \
  -t us-west1-docker.pkg.dev/myra-language-teacher/myra-language-teacher/dino-app:latest \
  .
```

### Intel Mac / Linux
```bash
docker build -t us-west1-docker.pkg.dev/myra-language-teacher/myra-language-teacher/dino-app:latest .
docker push us-west1-docker.pkg.dev/myra-language-teacher/myra-language-teacher/dino-app:latest
```

### Why is the image ~3GB?
The large size is expected — it's dominated by:
- PyTorch (~1.5GB, pulled in by Whisper)
- Whisper `base.pt` model baked in (~140MB — avoids slow cold-start downloads)
- ffmpeg + Python runtime (~250MB)

> **Tip:** GitHub Actions (Step 5) builds natively on amd64 — no emulation, no multi-arch needed.
> For day-to-day deploys, just push to `main` and let CI handle it.

---

## Step 4: Deploy with Terraform

> All `terraform` commands must be run from the `infra/` directory.

```bash
cd infra/
terraform init      # connects to GCS backend
terraform plan      # review what will be created
terraform apply     # create all resources (~3-5 min)
```

To check the app URL after apply:
```bash
terraform output app_url          # from within infra/
# or from project root:
terraform -chdir=infra output app_url
```

### If resources already exist (409 conflicts)
If `terraform apply` fails with `already exists` errors, import the conflicting resources
rather than trying to delete them:

```bash
# Examples — adjust resource names to match error output
terraform import google_artifact_registry_repository.app \
  "projects/myra-language-teacher/locations/us-west1/repositories/myra-language-teacher"

terraform import google_compute_security_policy.app \
  "projects/myra-language-teacher/global/securityPolicies/dino-app-armor"

terraform import google_compute_global_address.app \
  "projects/myra-language-teacher/global/addresses/dino-app-ip"

terraform import google_secret_manager_secret.child_name \
  "projects/myra-language-teacher/secrets/child-name"
```

---

## Step 5: CI/CD Setup (GitHub Actions)

CI/CD is handled by GitHub Actions — free, no GCP cost, 2,000 min/month included.
Workflow file: `.github/workflows/deploy.yml`

**Trigger**: Push to `main` → build Docker image (amd64, native) → push to Artifact Registry → deploy to Cloud Run.

### One-time GitHub Secrets setup
Go to: GitHub repo → Settings → Secrets and variables → Actions → New repository secret

| Secret | Value |
|---|---|
| `GCP_SA_KEY` | Full JSON contents of `~/terraform-gcp-key.json` |
| `GCP_PROJECT_ID` | `myra-language-teacher` |
| `GCP_REGION` | `us-west1` |

> Cloud Run service name (`dino-app`) is hardcoded in the workflow since it's fixed by Terraform.

### What happens on each push to `main`
1. Checkout code
2. Authenticate to GCP via service account key
3. Configure Docker for `us-west1-docker.pkg.dev`
4. Build image tagged with both `:latest` and `:<git-sha>` (native amd64 — no emulation)
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
- [ ] `terraform output app_url` returns the nip.io URL
- [ ] Navigating to the URL returns HTTP 200 (SSL cert takes up to 15 min to provision)
- [ ] TTS works: click a word, hear audio in Telugu/Assamese
- [ ] STT works: record speech, get recognition result
- [ ] Cloud Armor rate limits visible in GCP Console → Cloud Armor
- [ ] Scale-to-zero: wait 15 min idle → Cloud Run instances drop to 0
- [ ] Billing budget visible in GCP Console → Billing → Budgets & alerts

---

## Step 7: Custom Domain (Optional)

The default setup uses a `{ip}.nip.io` URL. To use a real domain (e.g. `myra-language-teacher.ai`):

### 7.1 Buy the domain

Register at any registrar. Recommendations:
- **Cloudflare Registrar** — at-cost pricing, no markup
- `.app` / `.dev` — ~$12/yr, HTTPS-enforced TLDs
- `.ai` — ~$70/yr

### 7.2 Get the static IP

The global IP is already provisioned by Terraform:
```bash
gcloud compute addresses describe dino-app-ip --global --format='value(address)'
# or
cd infra && terraform output
```

### 7.3 Add DNS A record at your registrar

| Type | Name | Value |
|------|------|-------|
| A | `@` (root) | `<IP from 7.2>` |
| A | `www` | `<IP from 7.2>` (optional) |

### 7.4 Set the domain in Terraform

Add to `infra/terraform.tfvars`:
```hcl
domain = "myra-language-teacher.ai"
```

### 7.5 Apply

```bash
cd infra
terraform apply -var="project_id=myra-language-teacher"
```

This updates the managed SSL certificate to use your domain instead of nip.io. GCP verifies ownership via the A record and auto-provisions the cert.

### 7.6 Wait and verify

SSL cert provisioning takes **15–30 minutes** after DNS propagates.

```bash
terraform output app_url   # → https://myra-language-teacher.ai
curl -I https://myra-language-teacher.ai   # → HTTP/2 200
```

> **Note:** Once `domain` is set, the nip.io URL stops working — the managed cert is single-domain.
> Add `www.your-domain.com` to the cert domains list in `load_balancer.tf` if you want both.

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
| Artifact Registry | 0.5 GB free | Image is ~3GB — expect ~$0.30/mo storage |
| Cloud Load Balancing | 5 rules free | Global HTTPS LB |
| Cloud CDN | First 10GB egress free/mo | |
| Secret Manager | 6 active versions free | |
| Cloud Functions | 2M invocations free | Budget kill-switch |
| Cloud Storage (tfstate) | 5GB free | |

**Estimated monthly cost: $0–$5** for light personal use.

---

## Known Issues & Fixes Applied

| Issue | Fix |
|---|---|
| `terraform init` backend changed error | Run `terraform init -reconfigure` when switching backends |
| `cloudresourcemanager` 403 on `terraform plan` | Enable manually first: `gcloud services enable cloudresourcemanager.googleapis.com` |
| `billing_account` missing on budget resource | Use `var.billing_account_id` in `variables.tf` instead of `data.google_project` lookup |
| Billing Budgets API 403 quota project error | Add `user_project_override = true` + `billing_project` to provider; run `gcloud auth application-default set-quota-project` |
| Eventarc API 403 on Cloud Function create | Enable manually: `gcloud services enable eventarc.googleapis.com`; now also in `apis.tf` with `depends_on` on the function |
| Cloud Function build fails: `missing main.py` | Cloud Functions Gen2 requires the source file be named `main.py`. Fixed in `archive_file` by using `source { filename = "main.py" }` to rename `kill_run.py` inside the zip |
| Cloud Run rejects ARM64 image | Use `docker buildx --platform linux/amd64,linux/arm64` on Apple Silicon |
| `terraform output` returns no outputs | Must run from `infra/` directory, not project root |
| `docker build` can't find Dockerfile | Must run from project root, not `infra/` |
