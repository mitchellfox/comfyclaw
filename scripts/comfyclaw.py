#!/usr/bin/env python3
import argparse
import base64
import datetime
import json
import os
import random
import struct
import sys
import threading
import time
import uuid
import urllib.parse
import urllib.request
import urllib.error
from typing import Any, Dict, List, Tuple

PIPELINES_ENDPOINT = "http://localhost:8787/api/pipelines"
PIPELINES_RUN_ENDPOINT = "http://localhost:8787/api/pipelines/run"

CONFIG_DIR = os.path.expanduser("~/.openclaw/comfyclaw")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

OUTPUT_TYPES = {
    "SaveImage",
    "PreviewImage",
    "VHS_VideoCombine",
    "SaveVideo",
    "SaveAnimatedWEBP",
    "SaveAnimatedGIF",
}


def ensure_config() -> Dict[str, Any]:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        data = {"servers": [], "workflows": [], "templates": [], "pipelines": []}
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("templates", [])
    data.setdefault("pipelines", [])
    return data


def save_config(data: Dict[str, Any]) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def find_server(data: Dict[str, Any], server_id: str) -> Dict[str, Any]:
    for server in data.get("servers", []):
        if server["id"] == server_id:
            return server
    raise SystemExit(f"Server not found: {server_id}")


def find_workflow(data: Dict[str, Any], workflow_id: str) -> Dict[str, Any]:
    for wf in data.get("workflows", []):
        if wf["id"] == workflow_id:
            return wf
    # Try prefix match
    matches = [wf for wf in data.get("workflows", []) if wf["id"].startswith(workflow_id)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(f"Ambiguous workflow prefix '{workflow_id}': {[m['id'][:12] for m in matches]}")
    raise SystemExit(f"Workflow not found: {workflow_id}")


def find_template(data: Dict[str, Any], template_id: str) -> Dict[str, Any]:
    for tmpl in data.get("templates", []):
        if tmpl["id"] == template_id:
            return tmpl
    raise SystemExit(f"Template not found: {template_id}")


def parse_workflow_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_prompt(workflow_json: Dict[str, Any]) -> Dict[str, Any]:
    if "prompt" in workflow_json and isinstance(workflow_json["prompt"], dict):
        return workflow_json["prompt"]
    if isinstance(workflow_json, dict):
        return workflow_json
    raise SystemExit("Unsupported workflow JSON format")


def detect_nodes(workflow_json: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    prompt = normalize_prompt(workflow_json)
    input_nodes: List[Dict[str, Any]] = []
    output_nodes: List[Dict[str, Any]] = []
    for node_id, node in prompt.items():
        class_type = node.get("class_type", "")
        inputs = node.get("inputs", {})
        if isinstance(inputs, dict) and inputs:
            for field, value in inputs.items():
                input_nodes.append({
                    "nodeId": str(node_id),
                    "fieldPath": str(field),
                    "label": f"{node_id}.{field}",
                    "type": type(value).__name__,
                    "currentValue": value,
                    "description": class_type or "Input",
                })
        if class_type in OUTPUT_TYPES:
            output_nodes.append({
                "nodeId": str(node_id),
                "fieldPath": "output",
                "label": f"{node_id}.output",
                "type": "output",
                "currentValue": None,
                "description": class_type,
            })
    return input_nodes, output_nodes


def _find_seed_fields(workflow_json: Dict[str, Any]) -> List[Tuple[str, str]]:
    prompt = normalize_prompt(workflow_json)
    matches: List[Tuple[str, str]] = []
    for node_id, node in prompt.items():
        inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
        if not isinstance(inputs, dict):
            continue
        for field in inputs.keys():
            field_lower = str(field).lower()
            if "noise_seed" in field_lower or "seed" in field_lower:
                matches.append((str(node_id), str(field)))
    return matches


def server_add(args: argparse.Namespace) -> None:
    data = ensure_config()
    if args.default:
        for s in data["servers"]:
            s["isDefault"] = False
    server = {
        "id": str(uuid.uuid4()),
        "name": args.name,
        "url": args.url.rstrip("/"),
        "apiKey": args.api_key or "",
        "isDefault": bool(args.default),
    }
    data["servers"].append(server)
    save_config(data)
    print(json.dumps(server, indent=2))


def server_edit(args: argparse.Namespace) -> None:
    data = ensure_config()
    server = find_server(data, args.id)
    if args.name:
        server["name"] = args.name
    if args.url:
        server["url"] = args.url.rstrip("/")
    if args.api_key is not None:
        server["apiKey"] = args.api_key
    if args.default is not None:
        if args.default:
            for s in data["servers"]:
                s["isDefault"] = False
        server["isDefault"] = bool(args.default)
    save_config(data)
    print(json.dumps(server, indent=2))


def server_delete(args: argparse.Namespace) -> None:
    data = ensure_config()
    data["servers"] = [s for s in data["servers"] if s["id"] != args.id]
    save_config(data)
    print(f"Deleted server {args.id}")


def server_list(_: argparse.Namespace) -> None:
    data = ensure_config()
    print(json.dumps(data.get("servers", []), indent=2))


def _request_json(method: str, url: str, api_key: str = "", body: Any = None) -> Any:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        text = resp.read().decode("utf-8")
        if text:
            return json.loads(text)
        return {}


def server_test(args: argparse.Namespace) -> None:
    data = ensure_config()
    server = find_server(data, args.id)
    try:
        url = server["url"].rstrip("/") + "/system_stats"
        _request_json("GET", url, server.get("apiKey", ""))
        print("ok")
    except Exception as exc:
        print(f"failed: {exc}")
        sys.exit(1)


def workflow_add(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = {
        "id": str(uuid.uuid4()),
        "title": args.title,
        "emoji": args.emoji or "✨",
        "description": args.description or "",
        "serverRef": args.server,
        "workflowJson": {},
        "primaryInputNodes": [],
        "secondaryInputNodes": [],
        "primaryOutputNodes": [],
        "secondaryOutputNodes": [],
    }
    data["workflows"].append(wf)
    save_config(data)
    print(json.dumps(wf, indent=2))


def workflow_edit(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = find_workflow(data, args.id)
    if args.title:
        wf["title"] = args.title
    if args.emoji:
        wf["emoji"] = args.emoji
    if args.description is not None:
        wf["description"] = args.description
    if args.server:
        wf["serverRef"] = args.server
    save_config(data)
    print(json.dumps(wf, indent=2))


def workflow_delete(args: argparse.Namespace) -> None:
    data = ensure_config()
    data["workflows"] = [w for w in data["workflows"] if w["id"] != args.id]
    save_config(data)
    print(f"Deleted workflow {args.id}")


def workflow_list(_: argparse.Namespace) -> None:
    data = ensure_config()
    print(json.dumps(data.get("workflows", []), indent=2))


def workflow_import(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf_json = parse_workflow_json(args.path)
    input_nodes, output_nodes = detect_nodes(wf_json)
    wf = {
        "id": str(uuid.uuid4()),
        "title": args.title or os.path.basename(args.path),
        "emoji": args.emoji or "🧩",
        "description": args.description or "Imported workflow",
        "serverRef": args.server,
        "workflowJson": wf_json,
        "primaryInputNodes": [],
        "secondaryInputNodes": input_nodes,
        "primaryOutputNodes": output_nodes,
        "secondaryOutputNodes": [],
        "published": False,
    }
    data["workflows"].append(wf)
    save_config(data)
    print(json.dumps(wf, indent=2))


def workflow_inspect(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = find_workflow(data, args.id)
    print(json.dumps({
        "primaryInputNodes": wf.get("primaryInputNodes", []),
        "secondaryInputNodes": wf.get("secondaryInputNodes", []),
        "primaryOutputNodes": wf.get("primaryOutputNodes", []),
        "secondaryOutputNodes": wf.get("secondaryOutputNodes", []),
    }, indent=2))


def _find_node_index(nodes: List[Dict[str, Any]], node_id: str) -> int:
    for i, n in enumerate(nodes):
        if n["nodeId"] == node_id or n.get("label") == node_id:
            return i
        if n.get("label") == node_id:
            return i
        if f"{n['nodeId']}.{n['fieldPath']}" == node_id:
            return i
    return -1


def workflow_set_primary(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = find_workflow(data, args.workflow_id)
    idx = _find_node_index(wf.get("secondaryInputNodes", []), args.node_id)
    if idx >= 0:
        node = wf["secondaryInputNodes"].pop(idx)
        wf["primaryInputNodes"].append(node)
        save_config(data)
        print(f"Moved to primary: {node['label']}")
        return
    print("Node not found in secondary inputs")


def workflow_set_secondary(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = find_workflow(data, args.workflow_id)
    idx = _find_node_index(wf.get("primaryInputNodes", []), args.node_id)
    if idx >= 0:
        node = wf["primaryInputNodes"].pop(idx)
        wf["secondaryInputNodes"].append(node)
        save_config(data)
        print(f"Moved to secondary: {node['label']}")
        return
    print("Node not found in primary inputs")


def workflow_nodes(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = find_workflow(data, args.id)
    nodes = wf.get("primaryInputNodes", []) + wf.get("secondaryInputNodes", [])
    print(json.dumps(nodes, indent=2))


def _coerce_value(val: str) -> Any:
    try:
        return json.loads(val)
    except Exception:
        return val


def _parse_kv_items(items: List[str]) -> Dict[str, Any]:
    values: Dict[str, Any] = {}
    for item in items:
        if ":" not in item:
            raise SystemExit(f"Invalid input: {item}")
        key, val = item.split(":", 1)
        if "." not in key:
            raise SystemExit(f"Use nodeId.field for input: {key}")
        values[key] = _coerce_value(val)
    return values


def _parse_overrides(items: List[str]) -> List[Dict[str, Any]]:
    overrides = []
    for item in items:
        if ":" not in item:
            raise SystemExit(f"Invalid input override: {item}")
        node_key, val = item.split(":", 1)
        if "." not in node_key:
            raise SystemExit(f"Use nodeId.field for override: {node_key}")
        node_id, field = node_key.split(".", 1)
        overrides.append({"nodeId": node_id, "fieldPath": field, "value": _coerce_value(val)})
    return overrides


def _poll_and_save(server: Dict[str, Any], wf: Dict[str, Any], prompt_id: str,
                    input_values: Dict[str, Any], timeout: int = 300,
                    batch_meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Poll ComfyUI history until complete, then download output and save to gallery."""
    import time
    import datetime as _dt
    import shutil

    start = time.time()
    print(f"Waiting for {prompt_id}...", end="", flush=True)
    while time.time() - start < timeout:
        time.sleep(3)
        print(".", end="", flush=True)
        try:
            url = server["url"].rstrip("/") + "/history/" + prompt_id
            resp = _request_json("GET", url, server.get("apiKey", ""))
        except Exception:
            continue
        if prompt_id not in resp:
            continue
        entry = resp[prompt_id]
        status = entry.get("status", {})
        if not status.get("completed", False):
            continue
        # Complete — find outputs
        outputs = entry.get("outputs", {})
        print(" done!")
        for node_id, node_out in outputs.items():
            images = node_out.get("images", []) + node_out.get("gifs", [])
            for img in images:
                fname = img.get("filename", "")
                subfolder = img.get("subfolder", "")
                img_type = img.get("type", "output")
                # Download the file
                view_url = server["url"].rstrip("/") + f"/view?filename={fname}&type={img_type}"
                if subfolder:
                    view_url += f"&subfolder={subfolder}"
                req = urllib.request.Request(view_url)
                if server.get("apiKey"):
                    req.add_header("Authorization", f"Bearer {server['apiKey']}")
                # Determine output type
                ext = os.path.splitext(fname)[1].lower()
                if ext in (".mp4", ".webm", ".avi"):
                    output_type = "video/mp4"
                elif ext in (".wav", ".mp3", ".ogg", ".flac"):
                    output_type = "audio/" + ext.lstrip(".")
                else:
                    output_type = "image/png"
                # Save to outputs dir
                ts = _dt.datetime.utcnow().isoformat() + "Z"
                wf_dir = os.path.join(OUTPUT_DIR, wf["id"])
                os.makedirs(wf_dir, exist_ok=True)
                out_name = f"{ts}-{fname}"
                out_path = os.path.join(wf_dir, out_name)
                try:
                    with urllib.request.urlopen(req, timeout=30) as dl_resp:
                        with open(out_path, "wb") as fp:
                            fp.write(dl_resp.read())
                except Exception as exc:
                    print(f"Failed to download output: {exc}")
                    continue
                # Save gallery entry
                gallery = ensure_gallery()
                gal_entry = {
                    "id": os.urandom(8).hex(),
                    "workflowId": wf.get("id"),
                    "workflowTitle": wf.get("title"),
                    "promptId": prompt_id,
                    "timestamp": ts,
                    "outputPath": out_path,
                    "outputType": output_type,
                    "inputValues": input_values,
                    "status": "complete",
                }
                if batch_meta:
                    gal_entry.update(batch_meta)
                gallery["outputs"].append(gal_entry)
                save_gallery(gallery)
                print(f"Saved: {out_path}")
                return gal_entry
        # No downloadable outputs found
        print("Complete but no downloadable outputs found.")
        return {"status": "complete", "prompt_id": prompt_id}
    print(" timed out!")
    return {"status": "timeout", "prompt_id": prompt_id}


def workflow_run(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = find_workflow(data, args.id)
    server = find_server(data, wf["serverRef"]) if wf.get("serverRef") else None
    if not server:
        raise SystemExit("Workflow has no serverRef")
    prompt = normalize_prompt(wf.get("workflowJson", {}))
    input_values = {}
    overrides = _parse_overrides(args.input or [])
    for override in overrides:
        node_id = override["nodeId"]
        field = override["fieldPath"]
        if node_id not in prompt:
            raise SystemExit(f"Node not found: {node_id}")
        if "inputs" not in prompt[node_id]:
            prompt[node_id]["inputs"] = {}
        prompt[node_id]["inputs"][field] = override["value"]
        input_values[f"{node_id}.{field}"] = override["value"]
    # Also capture primary input current values for gallery
    for node in wf.get("primaryInputNodes", []):
        key = f"{node['nodeId']}.{node['fieldPath']}"
        if key not in input_values and node.get("currentValue") is not None:
            input_values[key] = node["currentValue"]
    try:
        url = server["url"].rstrip("/") + "/prompt"
        resp = _request_json("POST", url, server.get("apiKey", ""), {"prompt": prompt})
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            print(json.dumps(resp, indent=2))
            sys.exit(1)
        print(f"Queued: {prompt_id}")
        if args.wait:
            timeout = args.timeout or 300
            result = _poll_and_save(server, wf, prompt_id, input_values, timeout)
            print(json.dumps(result, indent=2, default=str))
        else:
            print(json.dumps({"prompt_id": prompt_id}, indent=2))
    except Exception as exc:
        print(f"failed: {exc}")
        sys.exit(1)


def workflow_batch(args: argparse.Namespace) -> None:
    data = ensure_config()
    wf = find_workflow(data, args.id)
    server = find_server(data, wf["serverRef"]) if wf.get("serverRef") else None
    if not server:
        raise SystemExit("Workflow has no serverRef")
    base_prompt = normalize_prompt(wf.get("workflowJson", {}))
    overrides = _parse_overrides(args.input or [])
    override_values = {f"{o['nodeId']}.{o['fieldPath']}": o["value"] for o in overrides}
    for override in overrides:
        node_id = override["nodeId"]
        field = override["fieldPath"]
        if node_id not in base_prompt:
            raise SystemExit(f"Node not found: {node_id}")
        base_prompt.setdefault(node_id, {}).setdefault("inputs", {})[field] = override["value"]
    variations = max(1, args.variations or 1)
    seed_fields = _find_seed_fields(wf.get("workflowJson", {}))
    if args.vary_seed and not seed_fields:
        print("Warning: No seed field found; running identical copies.")
    batch_id = os.urandom(6).hex()
    results = []
    for idx in range(variations):
        prompt = json.loads(json.dumps(base_prompt))
        input_values = dict(override_values)
        if args.vary_seed and seed_fields:
            seed_value = random.randint(1, 2**31 - 1)
            for node_id, field in seed_fields:
                prompt.setdefault(node_id, {}).setdefault("inputs", {})[field] = seed_value
                input_values[f"{node_id}.{field}"] = seed_value
        for node in wf.get("primaryInputNodes", []):
            key = f"{node['nodeId']}.{node['fieldPath']}"
            if key not in input_values and node.get("currentValue") is not None:
                input_values[key] = node["currentValue"]
        try:
            url = server["url"].rstrip("/") + "/prompt"
            resp = _request_json("POST", url, server.get("apiKey", ""), {"prompt": prompt})
            prompt_id = resp.get("prompt_id")
            if not prompt_id:
                print(json.dumps(resp, indent=2))
                sys.exit(1)
            print(f"Queued: {prompt_id} ({idx + 1}/{variations})")
            if args.wait:
                timeout = args.timeout or 300
                meta = {"batchId": batch_id, "variationIndex": idx + 1, "batchCount": variations}
                result = _poll_and_save(server, wf, prompt_id, input_values, timeout, meta)
                results.append(result)
            else:
                results.append({"prompt_id": prompt_id, "variationIndex": idx + 1})
        except Exception as exc:
            print(f"failed: {exc}")
            sys.exit(1)
        if args.wait and idx + 1 < variations:
            import time
            time.sleep(1)
    print(json.dumps({"batchId": batch_id, "count": variations, "results": results}, indent=2, default=str))


GALLERY_PATH = os.path.join(CONFIG_DIR, "gallery.json")
OUTPUT_DIR = os.path.join(CONFIG_DIR, "outputs")


def ensure_gallery() -> Dict[str, Any]:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if not os.path.exists(GALLERY_PATH):
        data = {"outputs": []}
        with open(GALLERY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        return data
    with open(GALLERY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_gallery(data: Dict[str, Any]) -> None:
    with open(GALLERY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def server_upload(args: argparse.Namespace) -> None:
    """Upload an image or audio file to a ComfyUI server."""
    data = ensure_config()
    server = find_server(data, args.id)
    file_path = args.file
    if not os.path.isfile(file_path):
        raise SystemExit(f"File not found: {file_path}")

    import mimetypes
    boundary = uuid.uuid4().hex
    filename = os.path.basename(file_path)
    mime = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    with open(file_path, "rb") as f:
        file_data = f.read()

    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode())
    parts.append(file_data)
    parts.append(f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput\r\n".encode())
    subfolder = args.subfolder or ""
    if subfolder:
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"subfolder\"\r\n\r\n{subfolder}\r\n".encode())
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)

    url = server["url"].rstrip("/") + "/upload/image"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if server.get("apiKey"):
        req.add_header("Authorization", f"Bearer {server['apiKey']}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(f"Upload failed: {exc}")
        sys.exit(1)


def workflow_status(args: argparse.Namespace) -> None:
    """Check the status of a queued generation by prompt_id."""
    data = ensure_config()
    wf = find_workflow(data, args.workflow_id)
    server = find_server(data, wf["serverRef"]) if wf.get("serverRef") else None
    if not server:
        raise SystemExit("Workflow has no serverRef")
    try:
        url = server["url"].rstrip("/") + "/history/" + args.prompt_id
        resp = _request_json("GET", url, server.get("apiKey", ""))
        if args.prompt_id in resp:
            entry = resp[args.prompt_id]
            outputs = entry.get("outputs", {})
            status = entry.get("status", {})
            completed = status.get("completed", False)
            print(json.dumps({
                "completed": completed,
                "status": status,
                "output_nodes": list(outputs.keys()),
            }, indent=2))
        else:
            print(json.dumps({"completed": False, "status": "pending"}))
    except Exception as exc:
        print(f"Status check failed: {exc}")
        sys.exit(1)


def template_save(args: argparse.Namespace) -> None:
    data = ensure_config()
    inputs = _parse_kv_items(args.input or [])
    workflow_id = args.workflow
    if workflow_id:
        find_workflow(data, workflow_id)
    tmpl = {
        "id": str(uuid.uuid4()),
        "name": args.name,
        "workflowId": workflow_id or "",
        "inputs": inputs,
        "created": datetime.datetime.utcnow().isoformat() + "Z",
    }
    data["templates"].append(tmpl)
    save_config(data)
    print(json.dumps(tmpl, indent=2))


def template_list(args: argparse.Namespace) -> None:
    data = ensure_config()
    templates = data.get("templates", [])
    if args.workflow:
        templates = [t for t in templates if t.get("workflowId") == args.workflow]
    print(json.dumps(templates, indent=2))


def template_delete(args: argparse.Namespace) -> None:
    data = ensure_config()
    before = len(data.get("templates", []))
    data["templates"] = [t for t in data.get("templates", []) if t.get("id") != args.id]
    after = len(data.get("templates", []))
    save_config(data)
    if before == after:
        raise SystemExit(f"Template not found: {args.id}")
    print(f"Deleted template {args.id}")


def template_run(args: argparse.Namespace) -> None:
    data = ensure_config()
    tmpl = find_template(data, args.id)
    workflow_id = tmpl.get("workflowId") or args.workflow
    if not workflow_id:
        raise SystemExit("Template is missing workflowId. Provide --workflow.")
    wf = find_workflow(data, workflow_id)
    server = find_server(data, wf["serverRef"]) if wf.get("serverRef") else None
    if not server:
        raise SystemExit("Workflow has no serverRef")
    prompt = normalize_prompt(wf.get("workflowJson", {}))
    input_values = dict(tmpl.get("inputs", {}))
    overrides = _parse_overrides(args.override or [])
    for override in overrides:
        input_values[f"{override['nodeId']}.{override['fieldPath']}"] = override["value"]
    for node_key, val in input_values.items():
        if "." not in node_key:
            continue
        node_id, field = node_key.split(".", 1)
        if node_id not in prompt:
            raise SystemExit(f"Node not found: {node_id}")
        prompt[node_id].setdefault("inputs", {})[field] = val
    # Also capture primary input current values for gallery
    for node in wf.get("primaryInputNodes", []):
        key = f"{node['nodeId']}.{node['fieldPath']}"
        if key not in input_values and node.get("currentValue") is not None:
            input_values[key] = node["currentValue"]
    try:
        url = server["url"].rstrip("/") + "/prompt"
        resp = _request_json("POST", url, server.get("apiKey", ""), {"prompt": prompt})
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            print(json.dumps(resp, indent=2))
            sys.exit(1)
        print(f"Queued: {prompt_id}")
        if args.wait:
            timeout = args.timeout or 300
            result = _poll_and_save(server, wf, prompt_id, input_values, timeout)
            print(json.dumps(result, indent=2, default=str))
        else:
            print(json.dumps({"prompt_id": prompt_id}, indent=2))
    except Exception as exc:
        print(f"failed: {exc}")
        sys.exit(1)


def _parse_pipeline_step(step: str) -> Dict[str, Any]:
    if ":" not in step:
        raise SystemExit(f"Invalid pipeline step: {step}")
    workflow_id, rest = step.split(":", 1)
    inputs: Dict[str, Any] = {}
    if rest.strip():
        parts = [p for p in rest.split(",") if p]
        for item in parts:
            if "=" not in item:
                raise SystemExit(f"Invalid pipeline input: {item}")
            key, val = item.split("=", 1)
            if "." not in key:
                raise SystemExit(f"Use nodeId.field for input: {key}")
            inputs[key] = _coerce_value(val)
    return {"workflowId": workflow_id, "inputs": inputs}


def pipeline_run(args: argparse.Namespace) -> None:
    steps = [_parse_pipeline_step(step) for step in (args.steps or [])]
    payload = {"steps": steps}
    try:
        resp = _request_json("POST", PIPELINES_RUN_ENDPOINT, body=payload)
        pipeline_id = resp.get("pipelineId")
        if not pipeline_id:
            print(json.dumps(resp, indent=2))
            sys.exit(1)
        print(json.dumps({"pipelineId": pipeline_id}, indent=2))
        if args.wait:
            timeout = args.timeout or 300
            start = datetime.datetime.utcnow()
            while (datetime.datetime.utcnow() - start).total_seconds() < timeout:
                status = _request_json("GET", f"{PIPELINES_RUN_ENDPOINT}/{pipeline_id}")
                if status.get("status") == "complete":
                    print(json.dumps(status, indent=2))
                    return
                print(".", end="", flush=True)
                import time
                time.sleep(2)
            print(" timed out!")
    except Exception as exc:
        print(f"failed: {exc}")
        sys.exit(1)


def pipeline_list(_: argparse.Namespace) -> None:
    try:
        resp = _request_json("GET", PIPELINES_ENDPOINT)
        print(json.dumps(resp, indent=2))
    except Exception as exc:
        print(f"failed: {exc}")
        sys.exit(1)


def pipeline_save(args: argparse.Namespace) -> None:
    steps = [_parse_pipeline_step(step) for step in (args.steps or [])]
    payload = {"name": args.name, "steps": steps}
    try:
        resp = _request_json("POST", PIPELINES_ENDPOINT, body=payload)
        print(json.dumps(resp, indent=2))
    except Exception as exc:
        print(f"failed: {exc}")
        sys.exit(1)


def pipeline_delete(args: argparse.Namespace) -> None:
    try:
        resp = _request_json("DELETE", f"{PIPELINES_ENDPOINT}/{args.name}")
        print(json.dumps(resp, indent=2))
    except Exception as exc:
        print(f"failed: {exc}")
        sys.exit(1)


def gallery_list(args: argparse.Namespace) -> None:
    """List gallery outputs, optionally filtered by workflow."""
    gallery = ensure_gallery()
    outputs = gallery.get("outputs", [])
    if args.workflow:
        outputs = [o for o in outputs if o.get("workflowId") == args.workflow]
    if args.limit:
        outputs = outputs[-args.limit:]
    for o in outputs:
        status = o.get("status", "unknown")
        title = o.get("workflowTitle", "?")
        ts = o.get("timestamp", "")[:19]
        oid = o.get("id", "?")
        path = o.get("outputPath", "")
        print(f"{oid}  {status:<10}  {title:<30}  {ts}  {path}")


def gallery_delete(args: argparse.Namespace) -> None:
    """Delete a gallery entry and its output file."""
    gallery = ensure_gallery()
    outputs = gallery.get("outputs", [])
    entry = next((o for o in outputs if o.get("id") == args.id), None)
    if not entry:
        raise SystemExit(f"Gallery entry not found: {args.id}")
    # Remove output file if it exists
    if entry.get("outputPath"):
        full = os.path.join(OUTPUT_DIR, entry["outputPath"])
        if os.path.isfile(full):
            os.remove(full)
            print(f"Removed file: {full}")
    gallery["outputs"] = [o for o in outputs if o.get("id") != args.id]
    save_gallery(gallery)
    print(f"Deleted gallery entry {args.id}")


def gallery_download(args: argparse.Namespace) -> None:
    """Download a gallery output to a local path."""
    gallery = ensure_gallery()
    entry = next((o for o in gallery.get("outputs", []) if o.get("id") == args.id), None)
    if not entry:
        raise SystemExit(f"Gallery entry not found: {args.id}")
    if not entry.get("outputPath"):
        raise SystemExit("No output file for this entry")
    src = os.path.join(OUTPUT_DIR, entry["outputPath"])
    if not os.path.isfile(src):
        raise SystemExit(f"Output file not found: {src}")
    dest = args.output or os.path.basename(entry["outputPath"])
    import shutil
    shutil.copy2(src, dest)
    print(f"Downloaded to: {dest}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="comfyclaw", description="Manage ComfyUI servers and workflows")
    sub = parser.add_subparsers(dest="command")

    server = sub.add_parser("server")
    server_sub = server.add_subparsers(dest="sub")

    s_add = server_sub.add_parser("add")
    s_add.add_argument("--name", required=True)
    s_add.add_argument("--url", required=True)
    s_add.add_argument("--api-key", default="")
    s_add.add_argument("--default", action="store_true")
    s_add.set_defaults(func=server_add)

    s_edit = server_sub.add_parser("edit")
    s_edit.add_argument("id")
    s_edit.add_argument("--name")
    s_edit.add_argument("--url")
    s_edit.add_argument("--api-key")
    s_edit.add_argument("--default", type=lambda x: x.lower() == "true")
    s_edit.set_defaults(func=server_edit)

    s_del = server_sub.add_parser("delete")
    s_del.add_argument("id")
    s_del.set_defaults(func=server_delete)

    s_list = server_sub.add_parser("list")
    s_list.set_defaults(func=server_list)

    s_test = server_sub.add_parser("test")
    s_test.add_argument("id")
    s_test.set_defaults(func=server_test)

    s_upload = server_sub.add_parser("upload")
    s_upload.add_argument("id", help="Server ID")
    s_upload.add_argument("file", help="Path to image or audio file")
    s_upload.add_argument("--subfolder", default="", help="Subfolder (e.g. 'audio' for audio files)")
    s_upload.set_defaults(func=server_upload)

    workflow = sub.add_parser("workflow")
    wf_sub = workflow.add_subparsers(dest="sub")

    wf_add = wf_sub.add_parser("add")
    wf_add.add_argument("--title", required=True)
    wf_add.add_argument("--emoji")
    wf_add.add_argument("--description")
    wf_add.add_argument("--server", required=True)
    wf_add.set_defaults(func=workflow_add)

    wf_edit = wf_sub.add_parser("edit")
    wf_edit.add_argument("id")
    wf_edit.add_argument("--title")
    wf_edit.add_argument("--emoji")
    wf_edit.add_argument("--description")
    wf_edit.add_argument("--server")
    wf_edit.set_defaults(func=workflow_edit)

    wf_del = wf_sub.add_parser("delete")
    wf_del.add_argument("id")
    wf_del.set_defaults(func=workflow_delete)

    wf_list = wf_sub.add_parser("list")
    wf_list.set_defaults(func=workflow_list)

    wf_import = wf_sub.add_parser("import")
    wf_import.add_argument("path")
    wf_import.add_argument("--title")
    wf_import.add_argument("--emoji")
    wf_import.add_argument("--description")
    wf_import.add_argument("--server", required=True)
    wf_import.set_defaults(func=workflow_import)

    wf_inspect = wf_sub.add_parser("inspect")
    wf_inspect.add_argument("id")
    wf_inspect.set_defaults(func=workflow_inspect)

    wf_setp = wf_sub.add_parser("set-primary")
    wf_setp.add_argument("workflow_id")
    wf_setp.add_argument("node_id")
    wf_setp.set_defaults(func=workflow_set_primary)

    wf_sets = wf_sub.add_parser("set-secondary")
    wf_sets.add_argument("workflow_id")
    wf_sets.add_argument("node_id")
    wf_sets.set_defaults(func=workflow_set_secondary)

    wf_run = wf_sub.add_parser("run")
    wf_run.add_argument("id")
    wf_run.add_argument("--input", action="append", help="Override input as nodeId.field:value")
    wf_run.add_argument("--wait", action="store_true", help="Wait for completion and save to gallery")
    wf_run.add_argument("--timeout", type=int, default=300, help="Max seconds to wait (default 300)")
    wf_run.set_defaults(func=workflow_run)

    wf_nodes = wf_sub.add_parser("nodes")
    wf_nodes.add_argument("id")
    wf_nodes.set_defaults(func=workflow_nodes)

    wf_status = wf_sub.add_parser("status")
    wf_status.add_argument("workflow_id")
    wf_status.add_argument("prompt_id")
    wf_status.set_defaults(func=workflow_status)

    wf_batch = wf_sub.add_parser("batch")
    wf_batch.add_argument("id")
    wf_batch.add_argument("--input", action="append", help="Override input as nodeId.field:value")
    wf_batch.add_argument("--variations", type=int, default=1)
    wf_batch.add_argument("--vary-seed", action="store_true")
    wf_batch.add_argument("--wait", action="store_true", help="Wait for completion and save to gallery")
    wf_batch.add_argument("--timeout", type=int, default=300, help="Max seconds to wait (default 300)")
    wf_batch.set_defaults(func=workflow_batch)

    # Template commands
    template = sub.add_parser("template")
    tmpl_sub = template.add_subparsers(dest="sub")

    tmpl_save = tmpl_sub.add_parser("save")
    tmpl_save.add_argument("--name", required=True)
    tmpl_save.add_argument("--workflow", help="Workflow ID (optional for global templates)")
    tmpl_save.add_argument("--input", action="append", help="Input as nodeId.field:value")
    tmpl_save.set_defaults(func=template_save)

    tmpl_list = tmpl_sub.add_parser("list")
    tmpl_list.add_argument("--workflow", help="Filter by workflow ID")
    tmpl_list.set_defaults(func=template_list)

    tmpl_delete = tmpl_sub.add_parser("delete")
    tmpl_delete.add_argument("id")
    tmpl_delete.set_defaults(func=template_delete)

    tmpl_run = tmpl_sub.add_parser("run")
    tmpl_run.add_argument("id")
    tmpl_run.add_argument("--workflow", help="Workflow ID if template is global")
    tmpl_run.add_argument("--override", action="append", help="Override as nodeId.field:value")
    tmpl_run.add_argument("--wait", action="store_true", help="Wait for completion and save to gallery")
    tmpl_run.add_argument("--timeout", type=int, default=300, help="Max seconds to wait (default 300)")
    tmpl_run.set_defaults(func=template_run)

    # Pipeline commands
    pipeline = sub.add_parser("pipeline")
    pipe_sub = pipeline.add_subparsers(dest="sub")

    pipe_run = pipe_sub.add_parser("run")
    pipe_run.add_argument("--steps", action="append", required=True,
                          help="Step definition workflowId:node.field=value,node.field=value")
    pipe_run.add_argument("--wait", action="store_true", help="Wait for pipeline completion")
    pipe_run.add_argument("--timeout", type=int, default=300, help="Max seconds to wait (default 300)")
    pipe_run.set_defaults(func=pipeline_run)

    pipe_list = pipe_sub.add_parser("list")
    pipe_list.set_defaults(func=pipeline_list)

    pipe_save = pipe_sub.add_parser("save")
    pipe_save.add_argument("name")
    pipe_save.add_argument("--steps", action="append", required=True,
                           help="Step definition workflowId:node.field=value,node.field=value")
    pipe_save.set_defaults(func=pipeline_save)

    pipe_del = pipe_sub.add_parser("delete")
    pipe_del.add_argument("name")
    pipe_del.set_defaults(func=pipeline_delete)

    # Gallery commands
    gallery = sub.add_parser("gallery")
    gal_sub = gallery.add_subparsers(dest="sub")

    gal_list = gal_sub.add_parser("list")
    gal_list.add_argument("--workflow", help="Filter by workflow ID")
    gal_list.add_argument("--limit", type=int, help="Max entries to show")
    gal_list.set_defaults(func=gallery_list)

    gal_del = gal_sub.add_parser("delete")
    gal_del.add_argument("id")
    gal_del.set_defaults(func=gallery_delete)

    gal_dl = gal_sub.add_parser("download")
    gal_dl.add_argument("id")
    gal_dl.add_argument("--output", help="Output path (default: filename)")
    gal_dl.set_defaults(func=gallery_download)

    # Gateway commands
    gw = sub.add_parser("gateway")
    gw_sub = gw.add_subparsers(dest="sub")

    gw_start = gw_sub.add_parser("start")
    gw_start.add_argument("--host", default="0.0.0.0")
    gw_start.add_argument("--port", type=int, default=8788)
    gw_start.set_defaults(func=gateway_start)

    gw_stop = gw_sub.add_parser("stop")
    gw_stop.set_defaults(func=gateway_stop)

    gw_key = gw_sub.add_parser("key")
    gw_key_sub = gw_key.add_subparsers(dest="key_sub")

    gw_key_create = gw_key_sub.add_parser("create")
    gw_key_create.add_argument("--label", required=True)
    gw_key_create.set_defaults(func=gateway_key_create)

    gw_key_list = gw_key_sub.add_parser("list")
    gw_key_list.set_defaults(func=gateway_key_list)

    gw_key_revoke = gw_key_sub.add_parser("revoke")
    gw_key_revoke.add_argument("key")
    gw_key_revoke.set_defaults(func=gateway_key_revoke)

    # Network commands
    net = sub.add_parser("network")
    net_sub = net.add_subparsers(dest="net_cmd")
    net_connect = net_sub.add_parser("connect")
    net_connect.add_argument("--gateway", required=True, help="Gateway URL (e.g. https://comfyclaw.app)")
    net_connect.add_argument("--key", required=True, help="Provider API key (ccn_sk_...)")
    net_connect.add_argument("--workflows", nargs="*", help="Workflow IDs to offer (default: all published)")
    net_connect.set_defaults(func=network_connect)

    # Workflow publish/unpublish
    wf_publish = wf_sub.add_parser("publish")
    wf_publish.add_argument("id")
    wf_publish.set_defaults(func=workflow_publish)

    wf_unpublish = wf_sub.add_parser("unpublish")
    wf_unpublish.add_argument("id")
    wf_unpublish.set_defaults(func=workflow_unpublish)

    return parser


# Gateway CLI functions

def gateway_start(args: argparse.Namespace) -> None:
    """Start the ComfyClaw gateway server."""
    from gateway.server import run
    print(f"Starting ComfyClaw gateway on {args.host}:{args.port}")
    run(args.host, args.port)


def gateway_stop(args: argparse.Namespace) -> None:
    """Stop the gateway (placeholder — use systemd or Ctrl-C)."""
    print("Stop the gateway with Ctrl-C or `systemctl --user stop comfyclaw-gateway`")


def gateway_key_create(args: argparse.Namespace) -> None:
    """Create a new API key."""
    from gateway.server import create_api_key
    key = create_api_key(args.label)
    print(f"🔑 Key: {key}")
    print(f"   Label: {args.label}")


def gateway_key_list(args: argparse.Namespace) -> None:
    """List API keys."""
    from gateway.server import list_api_keys
    keys = list_api_keys()
    if not keys:
        print("No API keys. Gateway runs in open mode (no auth).")
        return
    for k in keys:
        status = "✅" if k.get("enabled") else "❌"
        print(f"{status} {k['key'][:16]}...  label={k.get('label', '')}  enabled={k.get('enabled')}")


def gateway_key_revoke(args: argparse.Namespace) -> None:
    """Revoke an API key."""
    from gateway.server import revoke_api_key
    revoke_api_key(args.key)
    print(f"Revoked: {args.key}")


def network_connect(args: argparse.Namespace) -> None:
    """Connect to a ComfyClaw Network gateway as a GPU provider."""
    import hashlib
    import struct
    import socket as _socket

    gateway_url = args.gateway.rstrip("/")
    api_key = args.key
    data = ensure_config()

    # Determine which workflows to offer
    if args.workflows:
        workflows = args.workflows
    else:
        workflows = [w["id"] for w in data.get("workflows", []) if w.get("published")]
    if not workflows:
        print("❌ No published workflows found. Publish workflows first with: comfyclaw workflow publish <id>")
        sys.exit(1)

    # Detect GPU info
    gpu_info = {"name": "Unknown GPU", "vram_gb": 0}
    try:
        server = next((s for s in data.get("servers", []) if s.get("isDefault")), None)
        if not server and data.get("servers"):
            server = data["servers"][0]
        if server:
            url = server["url"].rstrip("/") + "/system_stats"
            req = urllib.request.Request(url)
            if server.get("apiKey"):
                req.add_header("Authorization", f"Bearer {server['apiKey']}")
            with urllib.request.urlopen(req, timeout=5) as resp:
                stats = json.loads(resp.read())
            devices = stats.get("devices", [])
            if devices:
                gpu = devices[0]
                gpu_info = {
                    "name": gpu.get("name", "Unknown GPU"),
                    "vram_gb": round(gpu.get("vram_total", 0) / (1024**3), 1),
                    "vram_free_gb": round(gpu.get("vram_free", 0) / (1024**3), 1),
                }
    except Exception:
        pass

    print(f"🖥️  ComfyClaw Network Provider")
    print(f"   Gateway: {gateway_url}")
    print(f"   GPU: {gpu_info.get('name')} ({gpu_info.get('vram_gb', '?')}GB)")
    print(f"   Workflows: {len(workflows)}")
    for wf_id in workflows:
        wf = next((w for w in data.get("workflows", []) if w["id"] == wf_id), None)
        if wf:
            print(f"     • {wf.get('emoji', '')} {wf.get('title', wf_id)}")
    print()

    def _recv_all(sock, n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def ws_recv(sock):
        header = _recv_all(sock, 2)
        if not header or len(header) < 2:
            return None
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if opcode == 0x8:
            return None
        if opcode == 0x9:  # ping → send pong
            payload = _recv_all(sock, length) if length else b""
            # Send masked pong
            mask = os.urandom(4)
            masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload or b""))
            frame = bytes([0x8A, 0x80 | len(masked)]) + mask + masked
            sock.sendall(frame)
            return ws_recv(sock)
        if length == 126:
            length = struct.unpack(">H", _recv_all(sock, 2))[0]
        elif length == 127:
            length = struct.unpack(">Q", _recv_all(sock, 8))[0]
        payload = _recv_all(sock, length) if length else b""
        if payload is None:
            return None
        return payload

    def ws_send(sock, msg_dict):
        payload = json.dumps(msg_dict).encode("utf-8")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        length = len(masked)
        header = bytes([0x81])  # FIN + text
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        sock.sendall(header + mask + masked)

    def _track_comfyui_progress(server, prompt_id, job_id, progress_cb):
        """Connect to ComfyUI WS and relay progress updates."""
        import socket as _sock
        parsed = urllib.parse.urlparse(server["url"].rstrip("/"))
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        try:
            s = _sock.create_connection((host, port), timeout=10)
            api_key = server.get("apiKey", "")
            qs = f"clientId=provider-{job_id[:8]}"
            if api_key:
                qs += f"&token={api_key}"
            import base64 as _b64
            ws_key = _b64.b64encode(os.urandom(16)).decode()
            handshake = (
                f"GET /ws?{qs} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n\r\n"
            )
            s.send(handshake.encode())
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(1024)
                if not chunk:
                    return
                resp += chunk
            if b"101" not in resp.split(b"\r\n")[0]:
                return
            s.settimeout(2)
            start = time.time()
            while time.time() - start < 600:
                try:
                    header = s.recv(2)
                    if len(header) < 2:
                        break
                    opcode = header[0] & 0x0F
                    if opcode == 0x8:
                        break
                    length = header[1] & 0x7F
                    if opcode == 0x9:
                        payload = s.recv(length) if length else b""
                        s.send(bytes([0x8A, len(payload)]) + payload)
                        continue
                    if length == 126:
                        length = struct.unpack(">H", s.recv(2))[0]
                    elif length == 127:
                        length = struct.unpack(">Q", s.recv(8))[0]
                    payload = b""
                    while len(payload) < length:
                        chunk = s.recv(length - len(payload))
                        if not chunk:
                            break
                        payload += chunk
                    if not payload:
                        continue
                    try:
                        text = payload.decode("utf-8", errors="replace").strip()
                        if not text.startswith("{"):
                            continue
                        msg = json.loads(text)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    msg_data = msg.get("data", {})
                    if msg_data.get("prompt_id") and msg_data["prompt_id"] != prompt_id:
                        continue
                    if msg.get("type") == "progress":
                        val = msg_data.get("value", 0)
                        mx = msg_data.get("max", 1)
                        progress_cb(val / mx if mx > 0 else 0)
                    elif msg.get("type") == "executed":
                        break
                except _sock.timeout:
                    continue
                except Exception:
                    break
            s.close()
        except Exception:
            pass

    def execute_job(job_msg, progress_cb=None):
        """Execute a job on local ComfyUI and return result."""
        job_id = job_msg["job_id"]
        workflow_id = job_msg["workflow_id"]
        inputs = job_msg.get("inputs", {})

        wf = next((w for w in data.get("workflows", []) if w["id"] == workflow_id), None)
        if not wf:
            return {"type": "failed", "job_id": job_id, "error": "workflow not found locally"}

        server = next((s for s in data.get("servers", []) if s.get("id") == wf.get("serverRef")), None)
        if not server:
            return {"type": "failed", "job_id": job_id, "error": "no ComfyUI server"}

        # Build prompt
        import copy
        wf_json = wf.get("workflowJson", {})
        prompt = wf_json.get("prompt", wf_json) if isinstance(wf_json, dict) else wf_json
        if isinstance(prompt, dict) and "prompt" in prompt:
            prompt = prompt["prompt"]
        prompt = copy.deepcopy(prompt)  # Don't mutate the original workflow

        for key, val in inputs.items():
            if "." not in key:
                continue
            node_id, field = key.split(".", 1)
            if node_id in prompt:
                prompt[node_id].setdefault("inputs", {})[field] = val

        # Randomize seed if -1 or 0 to prevent ComfyUI caching identical prompts
        resolved_seeds = {}
        for node_id, node in prompt.items():
            node_inputs = node.get("inputs", {})
            if "seed" in node_inputs and node_inputs["seed"] in (-1, 0, "-1", "0"):
                resolved = random.randint(1, 2**32 - 1)
                node_inputs["seed"] = resolved
                resolved_seeds[f"{node_id}.seed"] = resolved
            if "noise_seed" in node_inputs and node_inputs["noise_seed"] in (-1, 0, "-1", "0"):
                resolved = random.randint(1, 2**32 - 1)
                node_inputs["noise_seed"] = resolved
                resolved_seeds[f"{node_id}.noise_seed"] = resolved

        # Submit to ComfyUI
        submit_url = server["url"].rstrip("/") + "/prompt"
        req_data = json.dumps({"prompt": prompt}).encode()
        req = urllib.request.Request(submit_url, data=req_data, method="POST")
        req.add_header("Content-Type", "application/json")
        if server.get("apiKey"):
            req.add_header("Authorization", f"Bearer {server['apiKey']}")

        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                result = json.loads(resp.read())
            prompt_id = result.get("prompt_id")
            if not prompt_id:
                return {"type": "failed", "job_id": job_id, "error": "no prompt_id from ComfyUI"}
        except Exception as e:
            return {"type": "failed", "job_id": job_id, "error": f"ComfyUI submit error: {e}"}

        # Start progress tracking thread
        if progress_cb:
            t = threading.Thread(target=_track_comfyui_progress, args=(server, prompt_id, job_id, progress_cb), daemon=True)
            t.start()

        # Poll for completion
        history_url = server["url"].rstrip("/") + "/history/" + prompt_id
        for _ in range(300):  # 10 min max
            time.sleep(2)
            try:
                req = urllib.request.Request(history_url)
                if server.get("apiKey"):
                    req.add_header("Authorization", f"Bearer {server['apiKey']}")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    history = json.loads(resp.read())
                if prompt_id in history:
                    entry = history[prompt_id]
                    status_info = entry.get("status", {})
                    if status_info.get("completed"):
                        outputs = entry.get("outputs", {})
                        # Find output file
                        for node_id, node_out in outputs.items():
                            for key in ("images", "videos", "gifs"):
                                items = node_out.get(key, [])
                                for item in items:
                                    filename = item.get("filename")
                                    if not filename:
                                        continue
                                    # Download the output
                                    params = urllib.parse.urlencode({
                                        "filename": filename,
                                        "type": item.get("type", "output"),
                                        "subfolder": item.get("subfolder", ""),
                                    })
                                    dl_url = server["url"].rstrip("/") + "/view?" + params
                                    dl_req = urllib.request.Request(dl_url)
                                    if server.get("apiKey"):
                                        dl_req.add_header("Authorization", f"Bearer {server['apiKey']}")
                                    with urllib.request.urlopen(dl_req, timeout=60) as dl_resp:
                                        output_bytes = dl_resp.read()
                                    ext = os.path.splitext(filename)[1].lower()
                                    output_type = "image/png"
                                    if ext in (".jpg", ".jpeg"): output_type = "image/jpeg"
                                    elif ext == ".webp": output_type = "image/webp"
                                    elif ext == ".mp4": output_type = "video/mp4"
                                    elif ext == ".gif": output_type = "image/gif"
                                    import base64 as _b64
                                    return {
                                        "type": "complete",
                                        "job_id": job_id,
                                        "output": _b64.b64encode(output_bytes).decode(),
                                        "output_type": output_type,
                                        "resolved_seeds": resolved_seeds,
                                    }
                        return {"type": "failed", "job_id": job_id, "error": "no output files"}
            except Exception:
                pass
        return {"type": "failed", "job_id": job_id, "error": "timeout waiting for ComfyUI"}

    # Main connection loop with auto-reconnect
    while True:
        try:
            # Parse gateway URL
            parsed = urllib.parse.urlparse(gateway_url)
            use_ssl = parsed.scheme == "https"
            host = parsed.hostname
            port = parsed.port or (443 if use_ssl else 80)
            ws_path = f"/ws/provider?key={api_key}"

            print(f"⚡ Connecting to {gateway_url}...")
            sock = _socket.create_connection((host, port), timeout=15)

            if use_ssl:
                import ssl
                ctx = ssl.create_default_context()
                sock = ctx.wrap_socket(sock, server_hostname=host)

            # WebSocket handshake
            ws_key = base64.b64encode(os.urandom(16)).decode()
            handshake = (
                f"GET {ws_path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {ws_key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"\r\n"
            )
            sock.sendall(handshake.encode())

            # Read response
            response = b""
            while b"\r\n\r\n" not in response:
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError("No handshake response")
                response += chunk

            status_line = response.split(b"\r\n")[0]
            if b"101" not in status_line:
                print(f"❌ Handshake failed: {status_line.decode()}")
                sock.close()
                time.sleep(5)
                continue

            # Send ready message
            ws_send(sock, {
                "type": "ready",
                "workflows": workflows,
                "gpu_info": gpu_info,
            })
            print(f"✅ Connected! Listening for jobs...")
            print(f"   Press Ctrl+C to disconnect\n")

            # Main loop
            sock.settimeout(10)
            while True:
                try:
                    frame = ws_recv(sock)
                    if frame is None:
                        print("🔌 Connection closed by gateway")
                        break
                    msg = json.loads(frame.decode("utf-8"))

                    if msg.get("type") == "ping":
                        ws_send(sock, {"type": "pong"})

                    elif msg.get("type") == "job":
                        job_id = msg["job_id"]
                        wf_id = msg["workflow_id"]
                        wf_title = next((w.get("title", wf_id) for w in data.get("workflows", []) if w["id"] == wf_id), wf_id)
                        print(f"📥 Job {job_id[:12]}... → {wf_title}")

                        # Send initial progress
                        ws_send(sock, {"type": "progress", "job_id": job_id, "progress": 0.05})

                        # Execute with live progress relay
                        def _relay_progress(p):
                            try:
                                ws_send(sock, {"type": "progress", "job_id": job_id, "progress": round(p, 3)})
                            except Exception:
                                pass
                        result = execute_job(msg, progress_cb=_relay_progress)
                        ws_send(sock, result)

                        status_emoji = "✅" if result["type"] == "complete" else "❌"
                        print(f"   {status_emoji} {result['type']}")

                except _socket.timeout:
                    continue
                except Exception as e:
                    print(f"⚠️  Error: {e}")
                    break

            sock.close()

        except KeyboardInterrupt:
            print("\n👋 Disconnecting...")
            try:
                sock.close()
            except:
                pass
            break
        except Exception as e:
            print(f"⚠️  Connection error: {e}")
            print("   Reconnecting in 5 seconds...")
            time.sleep(5)


def workflow_publish(args: argparse.Namespace) -> None:
    """Publish a workflow for remote access."""
    data = ensure_config()
    wf = find_workflow(data, args.id)
    wf["published"] = True
    save_config(data)
    print(f"✅ Published: {wf.get('emoji', '')} {wf.get('title', args.id)}")
    primary = wf.get("primaryInputNodes", [])
    print(f"   {len(primary)} inputs exposed, workflow JSON protected")


def workflow_unpublish(args: argparse.Namespace) -> None:
    """Unpublish a workflow."""
    data = ensure_config()
    wf = find_workflow(data, args.id)
    wf["published"] = False
    save_config(data)
    print(f"❌ Unpublished: {wf.get('emoji', '')} {wf.get('title', args.id)}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)
    args.func(args)


if __name__ == "__main__":
    main()
