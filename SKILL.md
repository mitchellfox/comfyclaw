---
name: comfyclaw
description: Manage ComfyUI servers and orchestrate workflows via CLI and dashboard. Use for adding/testing ComfyUI servers, importing workflow JSON, classifying inputs/outputs, and running prompts with custom parameters.
---

# ComfyClaw

**Source:** https://github.com/mitchellfox/comfyclaw

## Overview
ComfyClaw is an OpenClaw skill for managing ComfyUI servers and workflows. It provides a CLI for server/workflow management and a lightweight dashboard for visually editing workflow metadata, classifying inputs/outputs, and triggering runs.

## Quick Start
### 1) Add a server
```bash
python3 scripts/comfyclaw.py server add --name "ComfyUI" --url http://127.0.0.1:8188 --api-key "<key>" --default
python3 scripts/comfyclaw.py server list
python3 scripts/comfyclaw.py server test <server-id>
```

### 2) Import a workflow JSON
```bash
python3 scripts/comfyclaw.py workflow import /path/to/workflow.json --server <server-id> --title "My Workflow"
python3 scripts/comfyclaw.py workflow list
python3 scripts/comfyclaw.py workflow inspect <workflow-id>
```

### 3) Run a workflow with overrides
```bash
python3 scripts/comfyclaw.py workflow run <workflow-id> --input "3.text:hello" --input "5.seed:123"
```

## CLI Reference
### Servers
- `comfyclaw server add/edit/delete/list`
- `comfyclaw server test <id>`

### Workflows
- `comfyclaw workflow add/edit/delete/list`
- `comfyclaw workflow import <path-to-json>`
- `comfyclaw workflow inspect <id>`
- `comfyclaw workflow set-primary <workflow-id> <node-id>`
- `comfyclaw workflow set-secondary <workflow-id> <node-id>`
- `comfyclaw workflow run <id> [--input node.field:value ...]`
- `comfyclaw workflow nodes <id>`

## Dashboard
Start the dashboard server:
```bash
python3 scripts/server.py
```
Then open `http://127.0.0.1:8787`.

### Dashboard Features
- Manage servers (add/edit/delete/test connectivity)
- Manage workflows (metadata, server assignment)
- Import ComfyUI workflow JSON (auto-detect inputs/outputs)
- Classify inputs into primary/secondary
- Run workflow and poll status

## Workflow Management Notes
- Input nodes are detected from `inputs` fields on each ComfyUI node.
- Output nodes are detected by class types: `SaveImage`, `PreviewImage`, `VHS_VideoCombine`, `SaveVideo`, `SaveAnimatedWEBP`, `SaveAnimatedGIF`.
- When importing, all detected inputs are placed into secondary inputs by default so you can promote the important ones to primary.

## Files & Data
- Config file: `~/.openclaw/comfyclaw/config.json`
- CLI: `scripts/comfyclaw.py`
- Dashboard server: `scripts/server.py`
- Dashboard UI: `assets/dashboard/index.html`
