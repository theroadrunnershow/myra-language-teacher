# Reachy Mini Robot Setup Guide

This guide covers:
1. [How to find your robot's details](#1-finding-your-robot-details)
2. [How to update robot settings in the app](#2-updating-robot-settings-in-the-app)
3. [Testing locally (on your Mac/PC)](#3-testing-locally)
4. [Testing on AWS](#4-testing-on-aws)

---

## 1. Finding Your Robot Details

### Robot IP Address

The Reachy Mini must be on the **same Wi-Fi network** as the computer running the app (for local testing) or reachable over the internet (for AWS — see [Section 4](#4-testing-on-aws)).

**Method A — Check the robot's screen / status page**

Pollen Robotics Reachy Mini displays its IP address on its face screen during boot.
If it has scrolled away, reboot the robot and watch the screen.

**Method B — Router admin panel**

1. Open your router's admin page (usually `http://192.168.1.1` or `http://192.168.0.1`)
2. Log in with your router credentials
3. Find the **DHCP clients** or **Connected devices** list
4. Look for a device named **reachy** or **reachy-mini**
5. Note the IP address (e.g. `192.168.1.42`)

**Method C — Network scan from your terminal**

```bash
# macOS / Linux
ping reachy.local           # works if mDNS is enabled on the robot

# Or scan the network (replace 192.168.1 with your subnet)
arp -a | grep -i reachy
```

**Method D — SSH into the robot and check**

If you already know the IP, SSH in and confirm:

```bash
ssh bedrock@<robot-ip>
hostname -I        # prints all IP addresses assigned to the robot
```

---

### SSH Credentials

Pollen Robotics ships Reachy Mini with these **default SSH credentials**:

| Field    | Default value |
|----------|---------------|
| Username | `bedrock`     |
| Password | `bedrock`     |

> **Security tip:** Change the password after first use:
> ```bash
> ssh bedrock@<robot-ip>
> passwd          # follow the prompts to set a new password
> ```

---

### Verifying the robot is reachable

Before configuring the app, confirm both the SDK port and SSH are open:

```bash
# SSH test (port 22) — should ask for password
ssh bedrock@<robot-ip>

# SDK test (gRPC port 50051) — should connect without hanging
nc -zv <robot-ip> 50051
```

Both must succeed for full functionality (audio + arm movements).
If only SSH works, audio will still play but arm dances will be skipped.

---

## 2. Updating Robot Settings in the App

All robot settings are saved in the app's **Settings page** and stored locally in your browser. No config file editing is required.

### Steps

1. Open the app in your browser (e.g. `http://localhost:8000`)
2. Click **⚙️ Settings** (top-right)
3. Scroll down to the **🤖 Reachy Mini Robot** section
4. Check **Enable Reachy Mini integration**
5. Fill in the fields:

   | Field | What to enter |
   |-------|--------------|
   | **Robot IP address or hostname** | e.g. `192.168.1.42` or `reachy.local` |
   | **SSH username** | `bedrock` (default) |
   | **SSH password** | `bedrock` (default, or your new password) |

6. Click **🔌 Test Connection** — you should see:
   ```
   ✅ Connected! (arm control, audio)
   ```
7. Click **💾 Save Settings**

### What each part controls

| Connection | Used for |
|------------|----------|
| **SSH** | Playing TTS audio through the robot's speaker |
| **SDK (gRPC)** | Celebration dance (correct word) and sad dance (wrong word) |

If only one connection succeeds, the app still works — it will use whichever feature is available and silently skip the rest.

---

## 3. Testing Locally

### Prerequisites

Make sure the following are installed on your machine:

```bash
# Python 3.9+
python3 --version

# ffmpeg (required for audio processing)
brew install ffmpeg          # macOS
sudo apt install ffmpeg      # Ubuntu/Debian

# mpg123 must be installed ON THE ROBOT for audio playback
ssh bedrock@<robot-ip> "sudo apt install -y mpg123"
```

### Install dependencies

```bash
cd ~/Downloads/claude_projects/myra-language-teacher

# Activate virtual environment
source venv/bin/activate     # macOS/Linux
# or: venv\Scripts\activate  # Windows

# Install all dependencies (including Reachy packages)
pip install -r requirements.txt
```

> `reachy2-sdk` and `paramiko` are listed in `requirements.txt`.
> The app starts and runs **without** them installed — Reachy features are simply
> disabled and you'll see a warning in the console.

### Run the app

```bash
python main.py
```

Open **http://localhost:8000** in your browser.

### End-to-end test

1. Go to **Settings → 🤖 Reachy Mini Robot**
2. Enter your robot's IP, username, and password
3. Click **🔌 Test Connection** — confirm green status
4. Save settings and go back to the home screen
5. Click **🔊 Hear It!** — you should hear the word from the **robot's speaker**
6. Click **🎤 Say It!** and speak the word correctly
   - The robot should perform the **celebration dance** (arms up and wave)
   - You should hear "yaaaaay" from the **robot's speaker**
7. Deliberately say the wrong word until attempts are exhausted
   - The robot should perform the **sad dance** (arms droop and sway)
   - You should hear "beeeep" from the **robot's speaker**

### Console logs to watch

When the robot is connected, you'll see lines like:

```
INFO:reachy_service:Reachy SDK connected to 192.168.1.42
INFO:reachy_service:SSH connected to Reachy at 192.168.1.42
INFO:reachy_service:Audio dispatched to robot speaker (24680 bytes, mp3)
```

If the SDK is not installed, you'll see (app still works):

```
WARNING:reachy_service:reachy2-sdk not installed – arm movements will be skipped.
```

---

## 4. Testing on GCP (Cloud Run)

### Important: network reachability

The app runs on **GCP Cloud Run** — a fully managed serverless platform. Each request
spins up a container that makes outbound calls through Google's network. There is no
fixed IP or VPC, so the robot must be reachable over the public internet or via a tunnel.

| Scenario | Works? |
|----------|--------|
| Robot on same local Wi-Fi (no public access) | ❌ Cloud Run can't reach your home network |
| Robot with a **public static IP** + port forwarding | ✅ Yes |
| Robot reachable via **Tailscale** (recommended) | ✅ Yes — simplest for home use |
| Robot reachable via **ngrok TCP tunnel** | ✅ Yes — quick to set up, free tier available |

---

### Option A — Tailscale (recommended)

Tailscale gives the robot a stable private IP reachable from Cloud Run via a Tailscale
subnet router or funnel.

**Step 1 — Install Tailscale on the robot:**

```bash
ssh bedrock@<robot-local-ip>

curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Note the Tailscale IP (e.g. 100.x.y.z)
tailscale ip -4
```

**Step 2 — Expose the robot publicly via Tailscale Funnel** (makes ports reachable from Cloud Run):

```bash
# On the robot — expose SSH (port 22) and gRPC (port 50051) via Tailscale Funnel
sudo tailscale funnel 22
sudo tailscale funnel 50051
```

> Tailscale Funnel gives you a stable `https://<machine>.ts.net` hostname.
> Use that hostname in the app Settings as the robot IP.

---

### Option B — ngrok TCP tunnel (quick setup, no account needed for basic use)

```bash
ssh bedrock@<robot-local-ip>

# Install ngrok on the robot
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | sudo tee /etc/apt/sources.list.d/ngrok.list
sudo apt update && sudo apt install ngrok

# Expose SSH (run this; note the hostname + port it assigns)
ngrok tcp 22
# → Forwarding tcp://0.tcp.ngrok.io:12345 -> localhost:22
```

Use `0.tcp.ngrok.io` as the robot hostname and `12345` as the SSH port in settings.
Repeat for port 50051 (gRPC) in a second terminal. Note: free ngrok tunnels reset on restart.

---

### Option C — Public IP + port forwarding (simplest, less secure)

> Only do this on a network with a proper firewall. Never expose SSH to the internet
> without a strong password or key-based auth.

1. Get a static public IP from your ISP, or set up a DDNS service (e.g. DuckDNS, No-IP)
2. Log into your router and forward ports **22** (SSH) and **50051** (gRPC) to the robot's local IP
3. In the app Settings, enter your **public IP or DDNS hostname** as the robot IP

---

### Deploying the updated code to GCP

After merging the Reachy feature branch to `main`, build and push a new Docker image
to Artifact Registry, which triggers Cloud Run to pick up the change:

```bash
export PROJECT_ID=your-gcp-project-id   # e.g. myra-language-teacher-123456
export REGION=us-west1

# Authenticate Docker to Artifact Registry (one-time)
gcloud auth configure-docker ${REGION}-docker.pkg.dev

# Build and push
docker build -t ${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest .
docker push ${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest

# Force Cloud Run to pick up the new image immediately
gcloud run services update dino-app \
  --region=${REGION} \
  --image=${REGION}-docker.pkg.dev/${PROJECT_ID}/myra/dino-app:latest \
  --project=${PROJECT_ID}
```

Get the live app URL:

```bash
cd infra && terraform output app_url
# or for the direct Cloud Run URL (no CDN):
terraform output cloud_run_url
```

### Configuring the robot from the GCP-hosted app

1. Open the app URL (from `terraform output app_url`)
2. Go to **Settings → 🤖 Reachy Mini Robot**
3. Enter the robot's **Tailscale hostname, ngrok host, or public IP**, username, and password
4. Click **🔌 Test Connection** — confirm green status

Settings are stored in **your browser's sessionStorage** (local to each browser/device).
Anyone accessing from a different browser will need to enter their own robot details.

### Viewing logs on GCP

```bash
# Stream live logs from Cloud Run
gcloud logging tail "resource.type=cloud_run_revision AND resource.labels.service_name=dino-app" \
  --project=${PROJECT_ID} --format="value(textPayload)"

# Or open in the console (URL printed by terraform output logs_url)
cd infra && terraform output logs_url
```

Look for lines from `reachy_service` — they'll show whether the SDK or SSH connection
succeeded or what error occurred.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Not connected" after Test Connection | Wrong IP or robot is off | Verify robot is on and `ping <robot-ip>` from your laptop first |
| `SSH: Connection timed out` | Firewall or tunnel not running | Check tunnel is active; try `ssh bedrock@<ip>` from your terminal |
| `SDK:` error, SSH works | `reachy2-sdk` not installed or gRPC port 50051 blocked | Run `pip install reachy2-sdk`; ensure port 50051 is forwarded/tunnelled |
| Audio plays on browser, not robot | Reachy not enabled in Settings | Enable the toggle in Settings and Save |
| No audio at all on robot | `mpg123` not installed on robot | `ssh bedrock@<ip> "sudo apt install -y mpg123"` |
| Arms don't move | Robot motors off or SDK API mismatch | Power-cycle robot; check console for SDK errors; see todo doc for API verification steps |
| Dance crashes mid-move | Joint angle out of safe range | Reduce angles in `reachy_service.py` `_do_celebration_dance` / `_do_sad_dance` |
| GCP: connection refused to robot | Robot not publicly reachable from Cloud Run | Set up Tailscale Funnel or ngrok tunnel (see Option A/B above) |
