# Reachy Pi Setup

This document describes how to structure Myra on the Reachy Raspberry Pi, which requirements to install, and how to run it in the two supported modes:

- `cloud`: Reachy runs only the robot controller and talks to the hosted Myra server
- `reachy_local`: Reachy runs both the robot controller and the Myra FastAPI server locally on the Pi

## Recommended Layout

Use this layout on the Pi:

```text
/home/pollen/
├── myra-language-teacher/              # git clone of this repo
│   ├── src/
│   ├── static/
│   ├── templates/
│   ├── data/
│   │   └── custom_words.runtime.v1.json
│   └── ...
├── myra-venv/                          # Python virtualenv for Myra
└── myra.env                            # optional env file for systemd/local server
```

Notes:

- Keep the repo checkout and the venv separate.
- The default local dynamic-words snapshot is inside the repo at `data/custom_words.runtime.v1.json`.
- Use `journalctl` or a systemd unit for logs instead of writing ad hoc log files into the repo.

## What Runs Where

### `cloud`

Runs on the Pi:

- `src/robot_teacher.py`
- Reachy SDK
- audio bridge and playback/recording logic

Runs remotely:

- `/api/word`
- `/api/recognize`
- `/api/tts`
- `/api/dino-voice`
- `/api/translate`

Use this when you want the simplest setup.

### `reachy_local`

Runs on the Pi:

- `src/robot_teacher.py`
- local uvicorn / FastAPI server from `src/main.py`
- local Whisper inference from `src/speech_service.py`
- local runtime words snapshot in `data/custom_words.runtime.v1.json`

Still uses internet when enabled:

- `gTTS`
- Google Translate fallback for unknown custom words
- optional GCS hydration/sync for dynamic words

Use this when you want speech recognition and API serving local to the robot.

## Requirements By Mode

### System Packages

Install these on the Pi in both modes:

```bash
sudo apt-get update
sudo apt-get install -y ffmpeg python3-venv
```

`ffmpeg` is required even in `cloud` mode because the robot decodes MP3 audio returned by `/api/tts`.

### Python Packages

#### `cloud`

Install only:

```bash
/home/pollen/myra-venv/bin/pip install -r /home/pollen/myra-language-teacher/requirements-robot.txt
```

This gives you:

- `reachy-mini`
- `requests`
- the shared runtime/audio stack from `requirements-common.txt` (`numpy`, `scipy`, `pydub`, `python-dotenv`)

#### `reachy_local`

Install both:

```bash
/home/pollen/myra-venv/bin/pip install \
  -r /home/pollen/myra-language-teacher/requirements.txt \
  -r /home/pollen/myra-language-teacher/requirements-robot.txt
```

This adds:

- `fastapi`
- `uvicorn`
- `faster-whisper`
- `gTTS`
- `google-cloud-translate`
- `google-cloud-storage`
- `rapidfuzz`
- `indic-transliteration`

Do not install `requirements-dev.txt` on the Pi unless you explicitly want to run the test suite there.

## Initial Pi Setup

From the Pi:

```bash
cd /home/pollen
git clone <your-repo-url> myra-language-teacher
python3 -m venv /home/pollen/myra-venv
source /home/pollen/myra-venv/bin/activate
```

Then install dependencies for the mode you want.

## Environment Variables

Create `/home/pollen/myra.env` if you want a stable local server configuration.

Example:

```bash
DISABLE_PASS1=true
WORDS_SYNC_TO_GCS=never
WORDS_OBJECT_BUCKET=myra-language-teacher-words
GCP_PROJECT=myra-language-teacher
```

Variables you should know:

- `DISABLE_PASS1=true`
  Recommended on the Pi. It skips the slower native-script Whisper pass.
- `WORDS_SYNC_TO_GCS=never|session_end|shutdown`
  Controls when custom words are uploaded back to GCS in `reachy_local`.
- `WORDS_OBJECT_BUCKET`
  Needed only if you want startup hydration from GCS and/or sync back to GCS.
- `WORDS_OBJECT_KEY`
  Leave unset unless you are deliberately changing internals. The code already defaults this to the fixed object path.
- `WORDS_LOCAL_PATH`
  Optional override for the local runtime snapshot path. Default is `data/custom_words.runtime.v1.json`.
- `GCP_PROJECT`
  Needed only if you want `/api/translate` to fall back to Google Translate for unknown words.

Credentials:

- For Google Translate fallback and GCS sync from the Pi, the process must have Google application default credentials or a service account configured.
- If you set `WORDS_SYNC_TO_GCS=never` and do not rely on `/api/translate` fallback, you can avoid most Google credential usage in `reachy_local`.

## Run Commands

### `cloud`

From the repo root:

```bash
source /home/pollen/myra-venv/bin/activate
cd /home/pollen/myra-language-teacher
python src/robot_teacher.py \
  --runtime-mode cloud \
  --language both \
  --categories animals,colors,food,numbers \
  --words 10 \
  --child-name Myra
```

### `reachy_local`

Auto-start the local server:

```bash
source /home/pollen/myra-venv/bin/activate
cd /home/pollen/myra-language-teacher
WORDS_SYNC_TO_GCS=never python src/robot_teacher.py \
  --runtime-mode reachy_local \
  --language both \
  --categories animals,colors,food,numbers \
  --words 10 \
  --child-name Myra
```

Use an already-running local server:

```bash
source /home/pollen/myra-venv/bin/activate
cd /home/pollen/myra-language-teacher/src
DISABLE_PASS1=true WORDS_SYNC_TO_GCS=shutdown python -m uvicorn main:app --host 127.0.0.1 --port 8765 --workers 1
```

In a second shell:

```bash
source /home/pollen/myra-venv/bin/activate
cd /home/pollen/myra-language-teacher
python src/robot_teacher.py \
  --runtime-mode reachy_local \
  --no-server \
  --words-sync-to-gcs session_end \
  --language both \
  --words 10 \
  --child-name Myra
```

## Dynamic Words Behavior On Pi

The runtime words flow in `reachy_local` is:

1. Load the built-in words from `src/words_db.py`
2. If configured, hydrate custom words from the fixed GCS object
3. Merge them into the local runtime file at `data/custom_words.runtime.v1.json`
4. Serve lookups from the local runtime state on the Pi
5. Optionally sync custom words back to GCS based on `WORDS_SYNC_TO_GCS` / `--words-sync-to-gcs`

Important:

- Built-in words in `src/words_db.py` are read-only.
- Only dynamic/custom words are candidates for GCS sync.
- `session_end` is driven by the robot controller calling the local sync endpoint before teardown.
- `shutdown` is driven by the local server shutdown path.

## Recommended Mode Choices

### Best simple setup

Use `cloud` if you want:

- the smallest install on the Pi
- fewer local moving parts
- no local FastAPI server management

### Best low-latency robot setup

Use `reachy_local` if you want:

- Whisper running on the Pi
- no dependency on the hosted API for lesson flow
- optional GCS sync for custom words

Recommended default:

```bash
--runtime-mode reachy_local
--words-sync-to-gcs never
```

Then enable `session_end` or `shutdown` only after you confirm Google credentials and GCS access are working on the Pi.

## Optional systemd Server Unit

If you want the local server managed independently from the robot process, create `/etc/systemd/system/myra-server.service`:

```ini
[Unit]
Description=Myra FastAPI Server
After=network.target

[Service]
User=pollen
WorkingDirectory=/home/pollen/myra-language-teacher/src
EnvironmentFile=/home/pollen/myra.env
ExecStart=/home/pollen/myra-venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8765 --workers 1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable myra-server
sudo systemctl start myra-server
```

Then run the robot with:

```bash
python src/robot_teacher.py --runtime-mode reachy_local --no-server --child-name Myra
```

## Quick Checklist

- Reachy is on Wi-Fi
- `ffmpeg` is installed
- venv exists at `/home/pollen/myra-venv`
- repo exists at `/home/pollen/myra-language-teacher`
- use `requirements-robot.txt` for `cloud`
- use `requirements.txt` + `requirements-robot.txt` for `reachy_local`
- if using Google sync/translate on the Pi, credentials are configured
- if using `reachy_local`, port `8765` is free
