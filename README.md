# ⚡ ComfyClaw

A skill for [OpenClaw](https://openclaw.ai) that manages ComfyUI servers and workflows through a CLI and web dashboard — plus a decentralized GPU marketplace where anyone can share and monetize their workflows.

![License](https://img.shields.io/badge/license-MIT-blue)

## Features

- **Multi-server management** — Add, edit, test, and remove ComfyUI servers
- **Workflow orchestration** — Import ComfyUI workflow JSON, classify inputs/outputs, run with overrides
- **File uploads** — Upload images and audio to ComfyUI servers (with subfolder routing for audio)
- **Gallery** — Track all generation outputs with metadata, filter by workflow, download, delete
- **Web dashboard** — Premium dark UI with server management, workflow run panel, gallery viewer, and network tab
- **CLI parity** — Everything the dashboard can do, the CLI can too
- **🌐 ComfyClaw Network** — Share your workflows on [comfyclaw.app](https://comfyclaw.app), earn money when people run them on your GPU

## Quick Start

### 1. Add a ComfyUI server

```bash
comfyclaw server add \
  --name "My GPU" \
  --url http://127.0.0.1:8188 \
  --api-key "your-key" \
  --default
```

### 2. Import a workflow

```bash
comfyclaw workflow import /path/to/workflow_api.json \
  --server <server-id> \
  --title "FLUX Text to Image" \
  --emoji "🖼️"
```

### 3. Promote important inputs

```bash
# See all detected nodes
comfyclaw workflow inspect <workflow-id>

# Promote the ones you want as primary inputs
comfyclaw workflow set-primary <workflow-id> <node-id>
```

### 4. Generate

```bash
# Fire and forget
comfyclaw workflow run <workflow-id> \
  --input "13.text:a cat in space"

# Wait for completion + save to gallery
comfyclaw workflow run <workflow-id> \
  --input "13.text:a cat in space" \
  --wait --timeout 120
```

### 5. Browse outputs

```bash
comfyclaw gallery list
comfyclaw gallery list --workflow <workflow-id> --limit 10
comfyclaw gallery download <output-id> --output ./my-image.png
```

## 🌐 ComfyClaw Network

Turn your GPU into a business. Share your workflows on [comfyclaw.app](https://comfyclaw.app) — a marketplace where anyone can pay to run them on your hardware.

### How It Works

1. **Sign up** at [comfyclaw.app/provider/signup](https://comfyclaw.app/provider/signup) to get your API key
2. **Enter your API key** in the local dashboard's 💰 Network tab
3. **Toggle workflows on** — they appear on the marketplace instantly
4. **Your GPU connects out** — no port forwarding needed (WebSocket reverse connection)
5. **Users pay, you earn** — optional Stripe Connect for payouts

### Provider Quick Start

```bash
# Start the dashboard
python3 scripts/server.py

# Open http://localhost:8787, go to 💰 Network tab
# Enter your API key — GPU connection starts automatically
# Toggle workflows on/off — marketplace updates in real-time
```

### Or use the CLI directly:

```bash
# Publish workflows for remote access
comfyclaw workflow publish <workflow-id>

# Connect to the network
comfyclaw network connect --gateway https://comfyclaw.app --key ccn_sk_your_key
```

### Architecture

```
Your Machine                          comfyclaw.app
┌─────────────┐                      ┌─────────────┐
│  ComfyUI    │◄─── local API ──────►│   Gateway    │◄──── Users
│  (GPU)      │                      │   Server     │      (Web UI)
│             │    WebSocket (out)    │              │
│  ComfyClaw  │─────────────────────►│  Job Queue   │
│  Dashboard  │     reverse conn     │  Wallet/Auth │
└─────────────┘                      └─────────────┘
```

- **Your workflow JSON never leaves your machine** — only the input schema is exposed
- **WebSocket reverse connection** — your machine connects out, no firewall config needed
- **Free by default** — share workflows for $0.00, connect Stripe to set prices
- **Real-time updates** — toggle workflows on/off from the dashboard, marketplace updates instantly

### Pricing

- Providers set their own per-run prices (or share for free)
- Platform adds 40% markup for consumers
- Payouts via Stripe Connect (Express accounts)

### Consumer Features (comfyclaw.app)

- Browse and search workflows with category filters (Image, Video)
- Sort by popularity, price
- ⭐ Save favorite workflows (server-side, persists across devices)
- Dollar-based wallet with Stripe Checkout
- Real-time generation progress
- 1-hour output download window

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
| **💰 Network** | Provider dashboard: API key entry, GPU connection, workflow toggle, earnings, payout setup |

### Run Panel Input Types

The dashboard auto-detects input types and renders appropriate controls:

- **Text/prompt fields** → text input
- **Image fields** → file upload (uploads directly to ComfyUI)
- **Audio fields** → file upload (routes to ComfyUI `audio/` subfolder)
- **Aspect ratio** → dropdown with all standard ratios
- **Base resolution** → stepper with ◀/▶ arrows (increments of 128, min 512)
- **Numeric fields** → number input
- **Fields with options** → dropdown select

### Toast Notifications

The dashboard shows toast notifications for key events:
- GPU connection started/stopped
- Workflow toggled live/offline
- Generation complete (single and batch)
- Provider account connected/disconnected

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
| `workflow set-primary <wf-id> <node-id>` | Promote input to primary |
| `workflow set-secondary <wf-id> <node-id>` | Demote input to secondary |
| `workflow run <id> [--input k:v ...] [--wait] [--timeout N]` | Run workflow |
| `workflow status <wf-id> <prompt-id>` | Check generation status |
| `workflow publish <id>` | Enable for network sharing |
| `workflow unpublish <id>` | Disable network sharing |

### Network Commands

| Command | Description |
|---------|-------------|
| `network connect --gateway <url> --key <key>` | Connect GPU to network |

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
│   ├── comfyclaw.py      # CLI tool (includes network commands)
│   ├── server.py         # Dashboard REST API + static server
│   ├── gateway/          # Network gateway (marketplace backend)
│   │   ├── __init__.py
│   │   └── server.py     # Gateway server (API, auth, jobs, wallets, providers)
│   └── web/
│       └── index.html    # Consumer marketplace UI (comfyclaw.app)
└── assets/
    └── dashboard/
        └── index.html    # Local dashboard (HTML + CSS + JS)
```

- **Zero dependencies** — Pure Python stdlib (no pip install needed)
- **Single-file UIs** — No build step, no node_modules
- **Config-driven** — All state in JSON files, easy to backup/migrate

## Running as a Service

```bash
# Dashboard (local management)
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

# Gateway (marketplace API — for hosting comfyclaw.app)
cat > ~/.config/systemd/user/comfyclaw-gateway.service << 'EOF'
[Unit]
Description=ComfyClaw Gateway - Public Workflow API
[Service]
Type=simple
ExecStart=/usr/bin/python3 -m scripts.gateway.server
WorkingDirectory=/path/to/comfyclaw
Restart=always
RestartSec=3
[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now comfyclaw comfyclaw-gateway
```

## OpenClaw Integration

ComfyClaw is designed as an [OpenClaw skill](https://docs.openclaw.ai). Install it:

```bash
# Copy to skills directory
cp -r comfyclaw/ ~/.openclaw/skills/comfyclaw/

# Or install from ClawHub
clawhub install comfyclaw
```

## License

MIT
