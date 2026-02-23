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

## 4. Testing on AWS

### Important: network reachability

The app runs on **AWS ECS Fargate in a private subnet**. It reaches the internet
through a NAT Gateway (outbound only). This means:

| Scenario | Works? |
|----------|--------|
| Robot on same Wi-Fi as the ECS cluster | ❌ Not possible (ECS is in AWS cloud) |
| Robot with a **public static IP** | ✅ Yes |
| Robot behind a VPN with AWS VPC peering | ✅ Yes (advanced) |
| Robot accessible via a **tunnel** (e.g. ngrok, Tailscale, WireGuard) | ✅ Yes (recommended) |

The simplest approach for home use is a **Tailscale tunnel**.

---

### Option A — Tailscale (recommended for home use)

Tailscale gives the robot a stable private IP reachable from anywhere, including AWS.

**On the robot:**

```bash
ssh bedrock@<robot-local-ip>

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Note the Tailscale IP shown (e.g. 100.x.x.x)
tailscale ip -4
```

**On your AWS machine (or in the ECS task):** install Tailscale and join the same network.
The ECS task would need Tailscale sidecar — contact your DevOps team for this.

**Simpler alternative:** run the robot connection through a small **EC2 proxy instance** on the same Tailscale network, then have ECS call the proxy.

---

### Option B — Public IP on the robot (simplest, less secure)

> Only do this if your robot is behind a proper firewall that limits access.

1. Give your home router a static public IP (contact your ISP) or use a DDNS service
2. Forward ports **22** (SSH) and **50051** (gRPC/SDK) from your router to the robot
3. In the app Settings, enter your **public IP or DDNS hostname** as the robot IP

---

### Deploying the updated code to AWS

After merging the Reachy feature branch to `main`, rebuild and push the Docker image:

```bash
export AWS_PROFILE=myra-deploy
cd ~/Downloads/claude_projects/myra-language-teacher

# Build and push the new image, then force an ECS rolling deploy
./deploy/build-push.sh --deploy
```

Wait for the rollout:

```bash
aws ecs wait services-stable \
  --region us-west-2 \
  --cluster dino-app-cluster \
  --services dino-app-service
```

Then open the app URL:

```bash
cd infra && terraform output app_url
```

### Configuring the robot from the AWS-hosted app

1. Open the CloudFront URL (e.g. `https://d1abc123xyz.cloudfront.net`)
2. Go to **Settings → 🤖 Reachy Mini Robot**
3. Enter the robot's **public IP or Tailscale IP**, username, and password
4. Click **🔌 Test Connection**

Settings are saved in **your browser's sessionStorage**, so they are local to each
browser/device. Anyone using a different browser will need to enter the robot details
again.

### Viewing logs on AWS

If something isn't working, check the ECS logs:

```bash
aws logs tail /ecs/dino-app --follow --region us-west-2
```

Look for lines from `reachy_service` — they'll tell you whether the SDK or SSH
connection succeeded or what error occurred.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| "Not connected" after Test Connection | Wrong IP or robot is off | Verify robot is on and ping it |
| `SSH: Connection timed out` | Firewall blocking port 22 | Check robot's SSH service: `ssh bedrock@<ip>` from terminal |
| `SDK:` error, SSH works | `reachy2-sdk` not installed or gRPC port 50051 blocked | Run `pip install reachy2-sdk`; check firewall allows port 50051 |
| Audio plays on browser, not robot | Reachy not enabled in Settings | Enable the toggle in Settings and Save |
| No audio at all on robot | `mpg123` not installed on robot | `ssh bedrock@<ip> "sudo apt install -y mpg123"` |
| Arms don't move | Robot motors not homed / turned off | Power-cycle the robot; SDK will turn motors on before each dance |
| Dance crashes mid-move | Joint angle out of safe range | Check `reachy_service.py` `_do_celebration_dance` / `_do_sad_dance` and reduce joint angles |
| AWS: connection refused to robot | Robot not publicly reachable | Use Tailscale or set up port forwarding (see Section 4) |
