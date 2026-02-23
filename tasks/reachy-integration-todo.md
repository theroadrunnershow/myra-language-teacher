# Reachy Mini Integration — Remaining Steps

Work through these in order. Each section has a checkbox so you can track progress.

---

## Phase 1 — Prepare the Robot (one-time, local)

These are physical / SSH steps done once before any app testing.

- [ ] **Power on Reachy Mini** and wait for it to fully boot (~60 s)
- [ ] **Find the robot's IP** (see `reachy-mini-setup.md` Section 1)
  ```bash
  ping reachy.local          # try mDNS first
  # OR check your router's DHCP table
  ```
- [ ] **SSH in and confirm you can log in**
  ```bash
  ssh bedrock@<robot-ip>     # password: bedrock
  ```
- [ ] **Install mpg123 on the robot** (needed for audio playback)
  ```bash
  ssh bedrock@<robot-ip> "sudo apt update && sudo apt install -y mpg123"
  ```
- [ ] **Confirm gRPC port 50051 is listening** (needed for arm movements)
  ```bash
  ssh bedrock@<robot-ip> "ss -tlnp | grep 50051"
  # Should show a process listening on 0.0.0.0:50051
  ```
- [ ] **Change the default SSH password** (security)
  ```bash
  ssh bedrock@<robot-ip> "passwd"
  ```

---

## Phase 2 — Verify reachy2-sdk API (critical before testing arms)

The dance choreography in `reachy_service.py` uses the most likely API, but the exact
method signatures must be verified against the SDK version installed on this machine.

- [ ] **Install the SDK locally**
  ```bash
  source venv/bin/activate
  pip install reachy2-sdk
  ```

- [ ] **Check installed version and available methods**
  ```python
  # Run this in a Python shell (venv activated)
  import reachy2_sdk
  print(reachy2_sdk.__version__)

  from reachy2_sdk import ReachySDK
  r = ReachySDK(host="<robot-ip>")

  # Inspect what's available
  print(dir(r))
  print(dir(r.r_arm))
  ```

- [ ] **Verify the `goto` / `goto_joints` method name**
  ```python
  # Try both — one should work:
  help(r.r_arm.goto)
  # or:
  help(r.r_arm.goto_joints)
  ```
  Then update `reachy_service.py` lines ~130 and ~180 to use the correct method name.

- [ ] **Verify `turn_on` / `turn_off_smoothly` signatures**
  ```python
  help(r.turn_on)
  help(r.turn_off_smoothly)
  # If these don't exist, try:
  help(r.r_arm.turn_on)
  ```
  Update `_do_celebration_dance` and `_do_sad_dance` in `reachy_service.py` accordingly.

- [ ] **Run a minimal arm movement test** to confirm angles are safe
  ```python
  r.turn_on("r_arm")
  import time; time.sleep(0.5)
  # Small safe movement — just 10 degrees shoulder pitch
  r.r_arm.goto([10, 0, 0, 0, 0, 0, 0], duration=2.0)
  time.sleep(2.5)
  r.r_arm.goto([0, 0, 0, 0, 0, 0, 0], duration=2.0)
  time.sleep(2.5)
  r.turn_off_smoothly("r_arm")
  ```
  If this fails, note the error and adjust the API calls in `reachy_service.py`.

- [ ] **Adjust joint angles if needed** in `reachy_service.py`:
  - `_do_celebration_dance`: raise arms to ~`[-30, ±15, 0, -100, 0, 0, 0]`
  - `_do_sad_dance`: droop arms to ~`[20, ±5, 0, 30, 0, 0, 0]`
  - Stay within ±45° of rest position on first test to avoid hardware limits

---

## Phase 3 — Local end-to-end Test

- [ ] **Start the app locally**
  ```bash
  source venv/bin/activate
  python main.py
  # Open http://localhost:8000
  ```

- [ ] **Connect to robot from Settings page**
  1. Go to ⚙️ Settings → 🤖 Reachy Mini Robot
  2. Check "Enable Reachy Mini integration"
  3. Enter robot IP, username, password
  4. Click 🔌 Test Connection — should show **✅ Connected! (arm control, audio)**

- [ ] **Test audio output** — click 🔊 Hear It!
  - Word should play from **robot's speaker**, not your laptop

- [ ] **Test celebration dance** — say a word correctly
  - Robot arms should raise and wave
  - "yaaaaay" should come from robot speaker

- [ ] **Test sad dance** — say wrong word until attempts run out
  - Robot arms should droop and sway
  - "beeeep" should come from robot speaker

- [ ] **Test Stop button** — verify stopping mid-dance doesn't crash the server
  - Check console for any unhandled exceptions

---

## Phase 4 — GCP Deployment

### 4a. One-time GCP setup (if not already done)

- [ ] **Create GCP project** at https://console.cloud.google.com
  - Suggested name: `myra-language-teacher`
  - Note your **Project ID** (e.g. `myra-language-teacher-123456`)
- [ ] **Link a billing account** to the project
- [ ] **Install gcloud CLI**
  ```bash
  brew install --cask google-cloud-sdk
  gcloud init
  gcloud auth application-default login
  ```
- [ ] **Enable required APIs**
  ```bash
  export PROJECT_ID=your-project-id

  gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    cloudfunctions.googleapis.com \
    pubsub.googleapis.com \
    billingbudgets.googleapis.com \
    compute.googleapis.com \
    storage.googleapis.com \
    --project=$PROJECT_ID
  ```
- [ ] **Create Terraform service account + key**
  ```bash
  gcloud iam service-accounts create terraform-deploy \
    --display-name="Terraform Deploy" --project=$PROJECT_ID

  gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:terraform-deploy@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/owner"

  gcloud iam service-accounts keys create ~/terraform-gcp-key.json \
    --iam-account=terraform-deploy@$PROJECT_ID.iam.gserviceaccount.com

  export GOOGLE_APPLICATION_CREDENTIALS=~/terraform-gcp-key.json
  ```
- [ ] **Create GCS bucket for Terraform state**
  ```bash
  gsutil mb -p $PROJECT_ID -l us-west1 gs://myra-tfstate/
  gsutil versioning set on gs://myra-tfstate/
  ```

### 4b. Set your project ID in Terraform

- [ ] **Edit `infra/variables.tf`** — add a default for `project_id` or set it via env var:
  ```bash
  # Option A: set in shell before every terraform command
  export TF_VAR_project_id=your-project-id

  # Option B: add a default to variables.tf
  variable "project_id" {
    default = "your-project-id"   # ← fill this in
  }
  ```

### 4c. Deploy infrastructure

- [ ] **Run Terraform**
  ```bash
  cd infra/
  terraform init
  terraform plan    # review — should show ~10 resources
  terraform apply   # type 'yes' when prompted
  ```
- [ ] **Note the outputs**
  ```bash
  terraform output app_url          # Global HTTPS LB URL
  terraform output cloud_run_url    # Direct Cloud Run URL (for quick testing)
  terraform output logs_url         # Cloud Logging URL
  ```

### 4d. Build and push Docker image

- [ ] **Build and push**
  ```bash
  export REGION=us-west1

  gcloud auth configure-docker ${REGION}-docker.pkg.dev

  docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest .
  docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest
  ```
  > First build takes 5–10 min (Whisper base model is baked in).

- [ ] **Verify Cloud Run service is running**
  ```bash
  gcloud run services describe dino-app --region=us-west1 --project=$PROJECT_ID
  # Status should show "Ready"
  ```
- [ ] **Open the Cloud Run URL** (`terraform output cloud_run_url`) and confirm the app loads

---

## Phase 5 — Robot Connectivity from GCP

Cloud Run containers cannot reach your home network directly. Pick one option:

- [ ] **Choose a tunnel method** (see `reachy-mini-setup.md` Section 4 for full instructions):
  - [ ] **Option A: Tailscale Funnel** (most stable — robot gets a permanent `*.ts.net` hostname)
  - [ ] **Option B: ngrok TCP tunnel** (quick setup, free tier)
  - [ ] **Option C: Public IP + port forwarding** (requires static IP from ISP)

- [ ] **Install and start the tunnel on the robot**

- [ ] **Note the public hostname/IP** the tunnel gives you

- [ ] **Test connectivity from your laptop first** (simulates what Cloud Run will do):
  ```bash
  # Replace with your tunnel hostname and port
  ssh -p <tunnel-port> bedrock@<tunnel-host> "echo SSH OK"
  nc -zv <tunnel-host> <grpc-port>       # gRPC port check
  ```

---

## Phase 6 — GCP end-to-end Test

- [ ] **Open the GCP app URL** (`terraform output app_url`)
- [ ] **Configure robot in Settings** using the tunnel hostname/IP from Phase 5
- [ ] **Click 🔌 Test Connection** — should show green
- [ ] **Test audio** — 🔊 Hear It! — word plays from robot speaker
- [ ] **Test celebration** — say a word correctly — arms wave, "yaaaaay" from robot
- [ ] **Test sad dance** — fail until out of attempts — arms droop, "beeeep" from robot
- [ ] **Check GCP logs** for any errors:
  ```bash
  gcloud logging tail \
    "resource.type=cloud_run_revision AND resource.labels.service_name=dino-app" \
    --project=$PROJECT_ID --format="value(textPayload)"
  ```

---

## Phase 7 — Wrap Up

- [ ] **Merge PR** for `claude/reachy-mini-integration-Q1sbf` into `main`
- [ ] **Redeploy to GCP** after merge:
  ```bash
  docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest .
  docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest
  gcloud run services update dino-app --region=${REGION} \
    --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest \
    --project=${PROJECT_ID}
  ```
- [ ] **Share the app URL with Myra** 🦕

---

## Quick reference — file locations for any fixes needed

| What to fix | File | Where |
|---|---|---|
| Dance arm movements / API calls | `reachy_service.py` | `_do_celebration_dance()` line ~130, `_do_sad_dance()` line ~180 |
| Audio playback command on robot | `reachy_service.py` | `play_audio_on_robot()` line ~85 — change `mpg123` to `aplay` if needed |
| Robot settings UI | `templates/config.html` | Reachy Mini section ~line 101 |
| When dances trigger | `static/js/app.js` | `handleResult()` — search for `reachyDance(` |
| API endpoints | `main.py` | Search for `/api/reachy/` |
| GCP infrastructure | `infra/cloud_run.tf` | CPU/memory/timeout settings |
| GCP project ID | `infra/variables.tf` | `project_id` variable |
