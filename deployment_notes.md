# Deployment Notes

## Part 1 — Run Locally on macOS

### Prerequisites (already installed)
- Python 3.9+ — `python3 --version`
- ffmpeg — `brew install ffmpeg`
- Homebrew — https://brew.sh

### Steps

```bash
# 1. Go to project root
cd ~/Downloads/claude_projects/myra-language-teacher

# 2. Activate the virtual environment
source venv/bin/activate

# 3. Install Python dependencies (first time only, takes 3-5 min)
pip install -r requirements.txt

# 4. Run the app
python main.py
```

Open **http://localhost:8000** in your browser.

> **First speech recognition call:** Whisper downloads `~/.cache/whisper/base.pt`
> (~140 MB) automatically. Wait ~30 seconds — then it works offline forever.

**To stop:** `Ctrl+C`

**To deactivate the venv when done:**
```bash
deactivate
```

---

## Part 2 — Deploy to AWS

### Phase A — Install required tools (one-time)

```bash
# AWS CLI
brew install awscli
aws --version          # aws-cli/2.x.x

# Terraform (requires >= 1.10)
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
terraform version      # Terraform v1.x.x

# Docker Desktop
# Download from https://www.docker.com/products/docker-desktop/
# Open Docker Desktop and wait for "Docker Desktop is running" in menu bar
docker --version       # Docker version 27.x.x
```

---

### Phase B — Create AWS credentials

#### Step 1 — AWS account
Sign up at https://aws.amazon.com if you don't have one (credit card required;
no charge until resources are created).

#### Step 2 — Create a deploy IAM user in the AWS Console
1. Go to **IAM → Users → Create user**
2. Username: `myra-deploy`
3. **Attach policies directly** → select `AdministratorAccess`
4. Click through to **Create user**
5. Open the user → **Security credentials** tab → **Create access key**
6. Use case: **Command Line Interface (CLI)**
7. Copy the **Access key ID** and **Secret access key** — shown only once

#### Step 3 — Configure the AWS CLI
```bash
aws configure --profile myra-deploy
# AWS Access Key ID:     <paste Access Key ID>
# AWS Secret Access Key: <paste Secret Access Key>
# Default region name:   us-west-2
# Default output format: json
```

Credentials are saved to `~/.aws/credentials` — never stored in the project.

#### Step 4 — Activate the profile
```bash
export AWS_PROFILE=myra-deploy
```

Add to `~/.zshrc` to persist across terminal sessions:
```bash
echo 'export AWS_PROFILE=myra-deploy' >> ~/.zshrc
```

#### Step 5 — Verify credentials
```bash
aws sts get-caller-identity
# Should print your Account ID, UserId, and ARN
```

> **Security note:** Never put credentials in `.env` or any file inside the
> project directory. If you do, rotate the keys immediately in the AWS Console
> (IAM → Users → myra-deploy → Security credentials → Deactivate + Delete).

---

### Phase C — Set your alert email

Open `infra/variables.tf` and replace the placeholder on line ~13:

```hcl
variable "alert_email" {
  default = "YOUR_ALERT_EMAIL@example.com"   # <- change this
}
```

You will receive a **"Subscription Confirmation"** email from AWS after
`terraform apply` — click the link or budget alerts won't be delivered.

---

### Phase D — Bootstrap Terraform state (one-time)

Creates the S3 bucket used by Terraform to store its state file remotely.

```bash
cd ~/Downloads/claude_projects/myra-language-teacher
./deploy/bootstrap.sh
```

Expected output:
```
=== Terraform S3 Backend Bootstrap ===
  Region:    us-west-2
  S3 bucket: dino-app-tfstate
...
  S3 bucket ready: s3://dino-app-tfstate
=== Bootstrap complete!
```

---

### Phase E — Deploy AWS infrastructure

```bash
cd ~/Downloads/claude_projects/myra-language-teacher/infra

# Download AWS + archive providers (~50 MB, one-time)
terraform init

# If you see "Backend configuration changed" error, run:
# terraform init -reconfigure

# Preview all resources to be created (~35 resources)
terraform plan

# Create everything — takes ~5 min
# CloudFront propagation takes an additional 10-15 min after apply completes
terraform apply
# Type 'yes' when prompted
```

**After apply, note the outputs:**
```
app_url              = "https://d1abc123xyz.cloudfront.net"
ecr_repository_url   = "123456789.dkr.ecr.us-west-2.amazonaws.com/dino-app"
restart_command      = "aws ecs update-service ..."
cloudwatch_logs_url  = "https://us-west-2.console.aws.amazon.com/..."
```

> **Check your email** for the SNS subscription confirmation and click it.

> **New AWS account?** If you get "This AWS account currently does not support
> creating load balancers", your account hasn't fully activated yet. Either
> wait 24 hours or contact AWS Support to enable ALB access.

---

### Phase F — Build and push the Docker image

```bash
cd ~/Downloads/claude_projects/myra-language-teacher

# Build image + push to ECR + force ECS deployment
./deploy/build-push.sh --deploy
```

> **First build takes 5-10 minutes** — Whisper base model (~140 MB) is
> downloaded and baked into the image during build.

Monitor the ECS rollout:
```bash
aws ecs wait services-stable \
  --region us-west-2 \
  --cluster dino-app-cluster \
  --services dino-app-service
```

---

### Phase G — Open the app

```bash
cd infra && terraform output app_url
```

Open the printed `https://d1abc...cloudfront.net` URL in your browser.

---

## Ongoing Operations

### Deploy a code change
```bash
export AWS_PROFILE=myra-deploy    # if not in ~/.zshrc
cd ~/Downloads/claude_projects/myra-language-teacher
./deploy/build-push.sh --deploy
```

### Manual scale controls
```bash
# Turn off (scale to 0)
aws ecs update-service --region us-west-2 \
  --cluster dino-app-cluster --service dino-app-service --desired-count 0

# Turn on (scale to 1)
aws ecs update-service --region us-west-2 \
  --cluster dino-app-cluster --service dino-app-service --desired-count 1
```

### View live logs
```bash
aws logs tail /ecs/dino-app --follow --region us-west-2
```

### Tear down everything (stops all AWS charges)
```bash
cd infra && terraform destroy
# Type 'yes' when prompted
```

---

## Architecture Summary

```
Browser --HTTPS--> CloudFront --> WAF --> ALB --> ECS Fargate (FastAPI + Whisper)
                       |                              |
                       v                              v
                   (caching)                    NAT Gateway --> Google (gTTS)
```

| Component | AWS Service | Detail |
|-----------|-------------|--------|
| CDN + DDoS | CloudFront + WAF | Rate limits: 10/30/100 req/min per endpoint |
| Load balancer | ALB | Only accepts traffic from CloudFront IPs |
| App runtime | ECS Fargate | 1 vCPU, 3 GB RAM; Whisper warm in memory |
| Container registry | ECR | Private; scan on push; keeps last 5 images |
| Outbound internet | NAT Gateway | Needed for gTTS (Google TTS) calls |
| Config | SSM Parameter Store | child_name, thresholds, languages |
| State file | S3 | `dino-app-tfstate` bucket, encrypted |

## Cost Guardrails

| Threshold | Action |
|-----------|--------|
| $40 / month | Email alert |
| $50 / month | Lambda scales ECS to 0; app goes offline |

**Nightly schedule (PST):** ECS scales to 0 at 8 PM, back to 1 at 7:30 AM — saves ~$15-18/month.

**To restart after a budget kill:**
```bash
aws ecs update-service --region us-west-2 \
  --cluster dino-app-cluster --service dino-app-service --desired-count 1
```
