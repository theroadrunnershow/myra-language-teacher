# Mobile Testing Guide — Myra Language Teacher

How to test the app from your phone (e.g. when pushing PRs via mobile Claude Code).

The app (FastAPI + Whisper) can't run on a phone — you need a remote environment to run it.
Three options below, ordered by recommendation.

---

## Option 1: ngrok on your Mac (Quickest — No Cloud Setup)

Best when your Mac is already running the app locally.

```bash
# One-time install
brew install ngrok

# Start the app
source venv/bin/activate
python main.py

# In a second terminal — expose it to the internet
ngrok http 8000
# → Prints a public HTTPS URL like https://abc123.ngrok-free.app
```

Open that URL on your phone. Done.

**Limitations:**
- Mac must be on and app must be running
- Free tier URL changes every session (paid ngrok gives a stable domain)
- Not suitable for testing PRs independently of your Mac state

---

## Option 2: GCP VM with SSH from Mobile Claude Code (Full Dev Environment)

Set up a persistent GCP VM you can SSH into from mobile Claude Code, pull any branch, and run the app.

### One-time VM setup (run from Mac)

```bash
# Install gcloud CLI
brew install --cask google-cloud-sdk
gcloud init
gcloud auth application-default login

# Create a small VM (~$6/mo, e2-small)
export PROJECT_ID=YOUR_PROJECT_ID
gcloud compute instances create myra-dev \
  --machine-type=e2-small \
  --image-family=debian-12 \
  --image-project=debian-cloud \
  --zone=us-west1-a \
  --project=$PROJECT_ID

# SSH in and install dependencies
gcloud compute ssh myra-dev --zone=us-west1-a
# Inside VM:
sudo apt update && sudo apt install -y ffmpeg python3-pip git
git clone https://github.com/theroadrunnershow/myra-language-teacher.git
cd myra-language-teacher
pip install -r requirements.txt

# Open port 8000 in firewall (one-time)
gcloud compute firewall-rules create allow-myra-dev \
  --allow=tcp:8000 \
  --source-ranges=0.0.0.0/0 \
  --target-tags=myra-dev \
  --project=$PROJECT_ID
gcloud compute instances add-tags myra-dev --tags=myra-dev --zone=us-west1-a
```

### Testing a PR from mobile

```bash
# SSH into VM from mobile Claude Code terminal
gcloud compute ssh myra-dev --zone=us-west1-a

# Inside VM: pull the PR branch
cd myra-language-teacher
git fetch origin
git checkout -b pr-branch origin/pr-branch

# Run the app
pip install -r requirements.txt
python main.py
# → http://VM_EXTERNAL_IP:8000
```

Get the VM's external IP:
```bash
gcloud compute instances describe myra-dev --zone=us-west1-a \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
```

**Stop the VM when not in use to avoid charges:**
```bash
gcloud compute instances stop myra-dev --zone=us-west1-a
gcloud compute instances start myra-dev --zone=us-west1-a   # to restart
```

---

## Option 3: PR Preview Deployments via GitHub Actions (Best Long-Term)

Requires the GCP Cloud Run migration (`infra/GCP_MIGRATION.md`) to be complete first.

Once set up, the workflow is fully automatic:

```
Push PR → GitHub Action builds Docker image
        → deploys myra-pr-{number} to Cloud Run
        → posts preview HTTPS URL as PR comment
        → tap link on phone to test
        → merge PR → auto-cleanup deletes preview service
```

No SSH needed. No Mac required. Each PR gets its own isolated HTTPS URL.

**To implement:** Add `.github/workflows/pr-preview.yml` that:
1. Builds and pushes Docker image to Artifact Registry
2. Deploys `gcloud run deploy myra-pr-${{ github.event.number }}`
3. Comments the URL on the PR via `gh pr comment`
4. On PR close: runs `gcloud run services delete myra-pr-${{ github.event.number }}`

---

## Summary

| Option | Setup Time | Cost | Requires Mac On | Best For |
|--------|-----------|------|-----------------|----------|
| ngrok | 2 min | Free | Yes | Quick ad-hoc tests |
| GCP VM | 30 min | ~$6/mo (stop when idle) | No | Regular mobile dev |
| GCP PR Previews | 2–3 hrs | ~$0 (Cloud Run free tier) | No | Permanent solution |
