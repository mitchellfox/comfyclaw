# ⚡ ComfyClaw

A skill for [OpenClaw](https://openclaw.ai) that manages ComfyUI servers and workflows through a CLI and web dashboard.

![License](https://img.shields.io/badge/license-MIT-blue)

## Features

- **Multi-server management** — Add, edit, test, and remove ComfyUI servers
- **Workflow orchestration** — Import ComfyUI workflow JSON, classify inputs/outputs, run with overrides
- **File uploads** — Upload images and audio to ComfyUI servers (with subfolder routing for audio)
- **Gallery** — Track all generation outputs with metadata, filter by workflow, download, delete
- **Web dashboard** — Premium dark UI with server management, workflow run panel, and gallery viewer
- **CLI parity** — Everything the dashboard can do, the CLI can too

## Quick Start

### 1. Add a ComfyUI server

```bash
python3 scripts/comfyclaw.py server add \
  --name "My GPU" \
  --url http://127.0.0.1:8188 \
  --api-key "your-key" \
  --default
```

### 2. Import a workflow

```bash
python3 scripts/comfyclaw.py workflow import /path/to/workflow_api.json \
  --server <server-id> \
  --title "FLUX Text to Image" \
  --emoji "🖼️"
```

### 3. Promote important inputs

```bash
# See all detected nodes
python3 scripts/comfyclaw.py workflow inspect <workflow-id>

# Promote the ones you want as primary inputs
python3 scripts/comfyclaw.py workflow set-primary <workflow-id> <node-id>
```

### 4. Generate

```bash
# Fire and forget
python3 scripts/comfyclaw.py workflow run <workflow-id> \
  --input "13.text:a cat in space"

# Wait for completion + save to gallery
python3 scripts/comfyclaw.py workflow run <workflow-id> \
  --input "13.text:a cat in space" \
  --wait --timeout 120
```

### 5. Browse outputs

```bash
python3 scripts/comfyclaw.py gallery list
python3 scripts/comfyclaw.py gallery list --workflow <workflow-id> --limit 10
python3 scripts/comfyclaw.py gallery download <output-id> --output ./my-image.png
```

## Dashboard

Start the dashboard server:

```bash
python3 scripts/server.py
# Runs on http://0.0.0.0:8787
```

### Dashboard Tabs

| Tab | Features |
|-----|----------|
| **Servers** | Add/edit/delete servers, test connectivity |
| **Workflows** | Import JSON, classify inputs, run generations, per-workflow gallery |
| **Gallery** | All outputs, filter by workflow, preview modal, download/delete |

### Run Panel Input Types

The dashboard auto-detects input types and renders appropriate controls:

- **Text/prompt fields** → text input
- **Image fields** → file upload (uploads directly to ComfyUI)
- **Audio fields** → file upload (routes to ComfyUI `audio/` subfolder)
- **Aspect ratio** → dropdown with all standard ratios (1:1, landscape, portrait)
- **Base resolution** → stepper with ◀/▶ arrows (increments of 128, min 512)
- **Numeric fields** → number input
- **Fields with options** → dropdown select

## CLI Reference

### Server Commands

| Command | Description |
|---------|-------------|
| `server add --name --url [--api-key] [--default]` | Add a ComfyUI server |
| `server edit <id> [--name] [--url] [--api-key] [--default]` | Edit server settings |
| `server delete <id>` | Remove a server |
| `server list` | List all servers |
| `server test <id>` | Test server connectivity |
| `server upload <id> <file> [--subfolder audio]` | Upload image/audio to server |

### Workflow Commands

| Command | Description |
|---------|-------------|
| `workflow add --title --server [--emoji] [--description]` | Create empty workflow |
| `workflow edit <id> [--title] [--emoji] [--description] [--server]` | Edit workflow metadata |
| `workflow delete <id>` | Remove a workflow |
| `workflow list` | List all workflows |
| `workflow import <path> --server <id> [--title] [--emoji]` | Import ComfyUI JSON |
| `workflow inspect <id>` | Show all classified nodes |
| `workflow nodes <id>` | List input nodes |
| `workflow set-primary <workflow-id> <node-id>` | Promote input to primary |
| `workflow set-secondary <workflow-id> <node-id>` | Demote input to secondary |
| `workflow run <id> [--input k:v ...] [--wait] [--timeout N]` | Run workflow |
| `workflow status <workflow-id> <prompt-id>` | Check generation status |

### Gallery Commands

| Command | Description |
|---------|-------------|
| `gallery list [--workflow <id>] [--limit N]` | List outputs |
| `gallery delete <id>` | Delete output + file |
| `gallery download <id> [--output path]` | Download output locally |

## Data & Configuration

All data is stored under `~/.openclaw/comfyclaw/`:

| File | Purpose |
|------|---------|
| `config.json` | Servers, workflows, input/output node classifications |
| `gallery.json` | Generation output tracking |
| `outputs/<workflow-id>/` | Downloaded output files |

## Architecture

```
comfyclaw/
├── SKILL.md              # OpenClaw skill manifest
├── README.md             # This file
├── scripts/
│   ├── comfyclaw.py      # CLI tool
│   └── server.py         # Dashboard REST API + static server
└── assets/
    └── dashboard/
        └── index.html    # Single-file dashboard (HTML + CSS + JS)
```

- **Zero dependencies** — Pure Python stdlib (no pip install needed)
- **Single-file dashboard** — No build step, no node_modules
- **Config-driven** — All state in JSON files, easy to backup/migrate
- **ComfyUI API proxy** — Dashboard proxies uploads and status checks through the server

## Running as a Service

For production use, set up a systemd user service:

```bash
cat > ~/.config/systemd/user/comfyclaw.service << 'EOF'
[Unit]
Description=ComfyClaw Dashboard Server

[Service]
Type=simple
WorkingDirectory=/path/to/comfyclaw/assets/dashboard
ExecStart=/usr/bin/python3 /path/to/comfyclaw/scripts/server.py
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now comfyclaw
```

## OpenClaw Integration

ComfyClaw is designed as an [OpenClaw skill](https://docs.openclaw.ai). Install it:

```bash
# Copy to skills directory
cp -r comfyclaw/ ~/.openclaw/skills/comfyclaw/

# Or install from ClawHub (when published)
clawhub install comfyclaw
```

Agents can then use the CLI to generate images/video programmatically:

```bash
# Agent generates an image
python3 ~/.openclaw/skills/comfyclaw/scripts/comfyclaw.py workflow run <id> \
  --input "13.text:portrait of a wizard" --wait

# Output path is printed and saved to gallery
```

## License

MIT
