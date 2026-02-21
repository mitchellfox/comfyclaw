#!/usr/bin/env python3
import datetime
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Tuple

CONFIG_DIR = os.path.expanduser("~/.openclaw/comfyclaw")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
GALLERY_PATH = os.path.join(CONFIG_DIR, "gallery.json")
OUTPUT_DIR = os.path.join(CONFIG_DIR, "outputs")
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "dashboard")
COMFYCLAW_SCRIPT = os.path.join(os.path.dirname(__file__), "comfyclaw.py")

# --- Network Connect Process Manager ---
_net_proc = None
_net_lock = threading.Lock()
_net_log = []
_net_log_lock = threading.Lock()
_NET_LOG_MAX = 200


def _net_reader(pipe, label):
    """Read subprocess output and store in log buffer."""
    for line in iter(pipe.readline, ''):
        line = line.rstrip('\n')
        if line:
            with _net_log_lock:
                _net_log.append({"ts": time.time(), "line": line})
                if len(_net_log) > _NET_LOG_MAX:
                    _net_log.pop(0)
    pipe.close()


_net_gateway_url = ""
_net_api_key = ""

def net_start(gateway_url: str, api_key: str, workflows: list = None) -> dict:
    global _net_proc, _net_gateway_url, _net_api_key
    _net_gateway_url = gateway_url
    _net_api_key = api_key
    # Persist to config for showcase uploads
    cfg = ensure_config()
    cfg.setdefault("network", {})["gateway_url"] = gateway_url
    cfg["network"]["api_key"] = api_key
    save_config(cfg)
    with _net_lock:
        if _net_proc and _net_proc.poll() is None:
            return {"status": "already_running", "pid": _net_proc.pid}
        with _net_log_lock:
            _net_log.clear()
        cmd = [sys.executable, COMFYCLAW_SCRIPT, "network", "connect",
               "--gateway", gateway_url, "--key", api_key]
        if workflows:
            cmd.extend(["--workflows"] + workflows)
        _net_proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        t = threading.Thread(target=_net_reader, args=(_net_proc.stdout, "net"), daemon=True)
        t.start()
        return {"status": "started", "pid": _net_proc.pid}


def net_stop() -> dict:
    global _net_proc
    with _net_lock:
        if _net_proc and _net_proc.poll() is None:
            _net_proc.terminate()
            try:
                _net_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _net_proc.kill()
            pid = _net_proc.pid
            _net_proc = None
            return {"status": "stopped", "pid": pid}
        _net_proc = None
        # No managed subprocess — try to find and kill external CLI process
        try:
            import signal
            result = subprocess.run(
                ["pgrep", "-f", "comfyclaw.py network connect"],
                capture_output=True, text=True
            )
            pids = [int(p) for p in result.stdout.strip().split("\n") if p.strip()]
            if pids:
                for pid in pids:
                    os.kill(pid, signal.SIGTERM)
                return {"status": "stopped", "pid": pids[0], "external": True}
        except Exception:
            pass
        return {"status": "not_running"}


def net_status() -> dict:
    global _net_proc
    with _net_lock:
        if _net_proc and _net_proc.poll() is None:
            running = True
            pid = _net_proc.pid
        else:
            running = False
            pid = None
            if _net_proc:
                _net_proc = None
    with _net_log_lock:
        log = list(_net_log[-50:])
    return {"running": running, "pid": pid, "log": log, "gateway_url": _net_gateway_url, "api_key": _net_api_key}


OUTPUT_TYPES = {
    "SaveImage",
    "PreviewImage",
    "VHS_VideoCombine",
    "SaveVideo",
    "SaveAnimatedWEBP",
    "SaveAnimatedGIF",
}

BATCHES: Dict[str, Dict[str, Any]] = {}
BATCH_LOCK = threading.Lock()
PIPELINES: Dict[str, Dict[str, Any]] = {}
PIPELINE_LOCK = threading.Lock()


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
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(GALLERY_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _json_response(handler: SimpleHTTPRequestHandler, status: int, payload: Any) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: SimpleHTTPRequestHandler) -> Any:
    length = int(handler.headers.get("Content-Length", "0"))
    raw = handler.rfile.read(length) if length else b""
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


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


def _request_raw(url: str, api_key: str = "") -> bytes:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def normalize_prompt(workflow_json: Dict[str, Any]) -> Dict[str, Any]:
    import copy
    if "prompt" in workflow_json and isinstance(workflow_json["prompt"], dict):
        return copy.deepcopy(workflow_json["prompt"])
    if isinstance(workflow_json, dict):
        return copy.deepcopy(workflow_json)
    raise ValueError("Unsupported workflow JSON format")


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


def detect_nodes(workflow_json: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
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
                    "options": node.get("input_options", {}).get(field),
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
    return {"inputs": input_nodes, "outputs": output_nodes}


class ComfyClawHandler(SimpleHTTPRequestHandler):
    def _infer_output_type(self, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".png":
            return "image/png"
        if ext in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if ext == ".webp":
            return "image/webp"
        if ext == ".mp4":
            return "video/mp4"
        if ext == ".wav":
            return "audio/wav"
        if ext == ".gif":
            return "image/gif"
        return "application/octet-stream"

    def _extract_outputs(self, outputs: Dict[str, Any]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for node_id, node_output in outputs.items():
            for key in ("images", "videos", "gifs", "audio"):
                entries = node_output.get(key)
                if isinstance(entries, list):
                    for entry in entries:
                        if not isinstance(entry, dict):
                            continue
                        filename = entry.get("filename")
                        if not filename:
                            continue
                        items.append({
                            "nodeId": node_id,
                            "filename": filename,
                            "type": entry.get("type", "output"),
                            "subfolder": entry.get("subfolder", ""),
                        })
        return items

    def _process_outputs(self, wf: Dict[str, Any], server: Dict[str, Any], prompt_id: str, outputs: Dict[str, Any]) -> Any:
        gallery = ensure_gallery()
        existing = next((o for o in gallery.get("outputs", []) if o.get("promptId") == prompt_id), None)
        if existing and existing.get("outputPath"):
            return existing
        output_items = self._extract_outputs(outputs)
        if not output_items:
            return None
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        wf_dir = os.path.join(OUTPUT_DIR, wf.get("id", "workflow"))
        os.makedirs(wf_dir, exist_ok=True)
        created = None
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        input_values = {
            f"{node.get('nodeId')}.{node.get('fieldPath')}": node.get("currentValue")
            for node in (wf.get("primaryInputNodes") or [])
        }
        for item in output_items:
            params = {
                "filename": item["filename"],
                "type": item.get("type", "output"),
            }
            if item.get("subfolder"):
                params["subfolder"] = item["subfolder"]
            view_url = server["url"].rstrip("/") + "/view?" + urllib.parse.urlencode(params)
            raw = _request_raw(view_url, server.get("apiKey", ""))
            ext = os.path.splitext(item["filename"])[1].lower() or ".png"
            safe_prompt = prompt_id.replace("/", "-")
            file_name = f"{timestamp}-{safe_prompt}{ext}"
            file_path = os.path.join(wf_dir, file_name)
            with open(file_path, "wb") as f:
                f.write(raw)
            output_type = self._infer_output_type(file_path)
            record = {
                "id": os.urandom(8).hex(),
                "workflowId": wf.get("id"),
                "workflowTitle": wf.get("title"),
                "promptId": prompt_id,
                "timestamp": timestamp,
                "outputPath": file_path,
                "outputType": output_type,
                "inputValues": input_values,
                "status": "complete",
            }
            if existing:
                for key in ("batchId", "variationIndex", "batchCount", "pipelineId", "stepIndex", "stepCount"):
                    if key in existing:
                        record[key] = existing.get(key)
                existing.update(record)
                created = existing
            else:
                gallery["outputs"].append(record)
                created = created or record
            if record.get("batchId"):
                with BATCH_LOCK:
                    batch = BATCHES.get(record.get("batchId"))
                    if batch:
                        for run in batch.get("runs", []):
                            if run.get("promptId") == prompt_id:
                                run["status"] = "complete"
                        if all(r.get("status") == "complete" for r in batch.get("runs", [])):
                            batch["status"] = "complete"
        save_gallery(gallery)
        return created

    def _safe(self, fn):
        try:
            fn()
        except BrokenPipeError:
            pass
        except Exception as exc:
            import traceback
            traceback.print_exc()
            try:
                _json_response(self, 500, {"error": str(exc)})
            except Exception:
                pass

    def _run_batch(self, batch_id: str, wf: Dict[str, Any], server: Dict[str, Any],
                   runs: List[Dict[str, Any]], seed_fields: List[Tuple[str, str]], vary_seed: bool) -> None:
        for run in runs:
            prompt = normalize_prompt(wf.get("workflowJson", {}))
            for key, val in run.get("inputs", {}).items():
                if "." not in key:
                    continue
                node_id, field = key.split(".", 1)
                if node_id in prompt:
                    prompt[node_id].setdefault("inputs", {})[field] = val
            # Randomize seed if -1/0 to prevent ComfyUI caching identical prompts
            for _nid, _node in prompt.items():
                _ni = _node.get("inputs", {})
                if "seed" in _ni and _ni["seed"] in (-1, 0, "-1", "0"):
                    _ni["seed"] = random.randint(1, 2**32 - 1)
                if "noise_seed" in _ni and _ni["noise_seed"] in (-1, 0, "-1", "0"):
                    _ni["noise_seed"] = random.randint(1, 2**32 - 1)
            try:
                url = server["url"].rstrip("/") + "/prompt"
                resp = _request_json("POST", url, server.get("apiKey", ""), {"prompt": prompt})
            except Exception as exc:
                run["status"] = "error"
                run["error"] = str(exc)
                continue
            prompt_id = resp.get("prompt_id") if isinstance(resp, dict) else None
            if not prompt_id:
                run["status"] = "error"
                run["error"] = "no prompt_id"
                continue
            run["promptId"] = prompt_id
            run["status"] = "queued"
            gallery = ensure_gallery()
            timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            input_values = dict(run.get("inputs", {}))
            gallery["outputs"].append({
                "id": os.urandom(8).hex(),
                "workflowId": wf.get("id"),
                "workflowTitle": wf.get("title"),
                "promptId": prompt_id,
                "timestamp": timestamp,
                "outputPath": "",
                "outputType": "",
                "inputValues": input_values,
                "status": "queued",
                "batchId": batch_id,
                "variationIndex": run.get("index"),
                "batchCount": len(runs),
            })
            save_gallery(gallery)
        with BATCH_LOCK:
            if batch_id in BATCHES:
                BATCHES[batch_id]["status"] = "queued"

    def _run_pipeline(self, pipeline_id: str, steps: List[Dict[str, Any]]) -> None:
        data = ensure_config()
        prev_output = None
        completed = []
        for idx, step in enumerate(steps):
            workflow_id = step.get("workflowId")
            wf = next((w for w in data.get("workflows", []) if w.get("id") == workflow_id), None)
            if not wf:
                completed.append({"stepIndex": idx + 1, "status": "error", "error": "workflow not found"})
                break
            server = next((s for s in data.get("servers", []) if s.get("id") == wf.get("serverRef")), None)
            if not server:
                completed.append({"stepIndex": idx + 1, "status": "error", "error": "workflow has no server"})
                break
            prompt = normalize_prompt(wf.get("workflowJson", {}))
            inputs = dict(step.get("inputs", {}))
            for key, val in inputs.items():
                if val == "__prev__" and prev_output:
                    inputs[key] = prev_output
            for key, val in inputs.items():
                if "." not in key:
                    continue
                node_id, field = key.split(".", 1)
                if node_id in prompt:
                    prompt[node_id].setdefault("inputs", {})[field] = val
            try:
                url = server["url"].rstrip("/") + "/prompt"
                resp = _request_json("POST", url, server.get("apiKey", ""), {"prompt": prompt})
            except Exception as exc:
                completed.append({"stepIndex": idx + 1, "status": "error", "error": str(exc)})
                break
            prompt_id = resp.get("prompt_id") if isinstance(resp, dict) else None
            if not prompt_id:
                completed.append({"stepIndex": idx + 1, "status": "error", "error": "no prompt_id"})
                break
            gallery = ensure_gallery()
            timestamp = datetime.datetime.utcnow().isoformat() + "Z"
            input_values = dict(inputs)
            gallery["outputs"].append({
                "id": os.urandom(8).hex(),
                "workflowId": wf.get("id"),
                "workflowTitle": wf.get("title"),
                "promptId": prompt_id,
                "timestamp": timestamp,
                "outputPath": "",
                "outputType": "",
                "inputValues": input_values,
                "status": "queued",
                "pipelineId": pipeline_id,
                "stepIndex": idx + 1,
                "stepCount": len(steps),
            })
            save_gallery(gallery)
            # poll for completion
            output_record = None
            completed_flag = False
            for _ in range(240):
                try:
                    url = server["url"].rstrip("/") + "/history/" + prompt_id
                    resp = _request_json("GET", url, server.get("apiKey", ""))
                except Exception:
                    import time
                    time.sleep(2)
                    continue
                if resp and prompt_id in resp:
                    entry = resp[prompt_id]
                    status = entry.get("status", {})
                    if status.get("completed"):
                        outputs = entry.get("outputs", {})
                        output_items = self._extract_outputs(outputs)
                        if output_items:
                            first = output_items[0]
                            sub = first.get("subfolder") or ""
                            filename = first.get("filename")
                            if filename:
                                prev_output = f"{sub}/{filename}" if sub else filename
                        output_record = self._process_outputs(wf, server, prompt_id, outputs)
                        completed.append({
                            "stepIndex": idx + 1,
                            "status": "complete",
                            "promptId": prompt_id,
                            "outputPath": prev_output,
                        })
                        completed_flag = True
                        break
                import time
                time.sleep(2)
            with PIPELINE_LOCK:
                pipeline = PIPELINES.get(pipeline_id)
                if pipeline:
                    pipeline["currentStep"] = idx + 1
                    pipeline["completed"] = completed
            if not completed_flag:
                completed.append({"stepIndex": idx + 1, "status": "timeout", "promptId": prompt_id})
                break
        with PIPELINE_LOCK:
            pipeline = PIPELINES.get(pipeline_id)
            if pipeline:
                pipeline["status"] = "complete" if len(completed) == len(steps) else "error"
                pipeline["completed"] = completed

    def do_GET(self) -> None:
        if self.path.startswith("/api/"):
            return self._safe(self.handle_api_get)
        if self.path == "/" or self.path.startswith("/index.html"):
            self.path = "/index.html"
        try:
            return super().do_GET()
        except BrokenPipeError:
            pass

    def do_POST(self) -> None:
        if self.path.startswith("/api/"):
            return self._safe(self.handle_api_post)
        return super().do_POST()

    def do_PUT(self) -> None:
        if self.path.startswith("/api/"):
            return self._safe(self.handle_api_put)
        self.send_error(405)

    def do_DELETE(self) -> None:
        if self.path.startswith("/api/"):
            return self._safe(self.handle_api_delete)
        self.send_error(405)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def handle_api_get(self) -> None:
        data = ensure_config()
        if self.path == "/api/network/status":
            return _json_response(self, 200, net_status())
        if self.path == "/api/servers":
            return _json_response(self, 200, data.get("servers", []))
        if self.path == "/api/workflows":
            return _json_response(self, 200, data.get("workflows", []))
        if self.path.startswith("/api/gallery"):
            gallery = ensure_gallery()
            match = re.match(r"/api/gallery/([^/]+)$", self.path)
            if match:
                output_id = match.group(1)
                item = next((o for o in gallery.get("outputs", []) if o.get("id") == output_id), None)
                if not item:
                    return _json_response(self, 404, {"error": "output not found"})
                return _json_response(self, 200, item)
            workflow_id = None
            if "?" in self.path:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                workflow_id = params.get("workflowId", [None])[0]
            outputs = gallery.get("outputs", [])
            if workflow_id:
                outputs = [o for o in outputs if o.get("workflowId") == workflow_id]
            outputs = sorted(outputs, key=lambda o: o.get("timestamp", ""), reverse=True)
            return _json_response(self, 200, outputs)
        match = re.match(r"/api/batch/([^/]+)$", self.path)
        if match:
            batch_id = match.group(1)
            with BATCH_LOCK:
                batch = BATCHES.get(batch_id)
            if not batch:
                return _json_response(self, 404, {"error": "batch not found"})
            return _json_response(self, 200, {
                "batchId": batch_id,
                "status": batch.get("status", "running"),
                "variations": batch.get("variations", 0),
                "runs": batch.get("runs", []),
            })
        match = re.match(r"/api/pipelines/run/([^/]+)$", self.path)
        if match:
            pipeline_id = match.group(1)
            with PIPELINE_LOCK:
                pipeline = PIPELINES.get(pipeline_id)
            if not pipeline:
                return _json_response(self, 404, {"error": "pipeline not found"})
            return _json_response(self, 200, pipeline)
        if self.path.startswith("/api/pipelines"):
            return _json_response(self, 200, data.get("pipelines", []))
        if self.path.startswith("/api/templates"):
            templates = data.get("templates", [])
            workflow_id = None
            if "?" in self.path:
                query = urllib.parse.urlparse(self.path).query
                params = urllib.parse.parse_qs(query)
                workflow_id = params.get("workflowId", [None])[0]
            if workflow_id:
                templates = [t for t in templates if t.get("workflowId") in ("", workflow_id)]
            return _json_response(self, 200, templates)
        if self.path.startswith("/api/outputs/"):
            encoded_path = self.path.replace("/api/outputs/", "", 1)
            file_path = urllib.parse.unquote(encoded_path)
            if not file_path.startswith(OUTPUT_DIR):
                return _json_response(self, 403, {"error": "invalid path"})
            if not os.path.exists(file_path):
                return _json_response(self, 404, {"error": "file not found"})
            mime = "application/octet-stream"
            if file_path.endswith(".png"):
                mime = "image/png"
            elif file_path.endswith(".jpg") or file_path.endswith(".jpeg"):
                mime = "image/jpeg"
            elif file_path.endswith(".webp"):
                mime = "image/webp"
            elif file_path.endswith(".mp4"):
                mime = "video/mp4"
            elif file_path.endswith(".wav"):
                mime = "audio/wav"
            with open(file_path, "rb") as f:
                data_bytes = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data_bytes)))
            self.end_headers()
            self.wfile.write(data_bytes)
            return
        match = re.match(r"/api/workflows/([^/]+)/status/([^/]+)", self.path)
        if match:
            workflow_id, prompt_id = match.groups()
            wf = next((w for w in data.get("workflows", []) if w["id"] == workflow_id), None)
            if not wf:
                return _json_response(self, 404, {"error": "workflow not found"})
            server = next((s for s in data.get("servers", []) if s["id"] == wf.get("serverRef")), None)
            if not server:
                return _json_response(self, 400, {"error": "workflow has no server"})
            try:
                url = server["url"].rstrip("/") + "/history/" + prompt_id
                resp = _request_json("GET", url, server.get("apiKey", ""))
                if resp and prompt_id in resp:
                    outputs = resp[prompt_id].get("outputs", {})
                    gallery_item = self._process_outputs(wf, server, prompt_id, outputs)
                    if gallery_item:
                        return _json_response(self, 200, {"status": "complete", "output": gallery_item})
                return _json_response(self, 200, resp)
            except Exception as exc:
                return _json_response(self, 500, {"error": str(exc)})
        return _json_response(self, 404, {"error": "not found"})

    def handle_api_post(self) -> None:
        data = ensure_config()

        # Showcase: upload a gallery image as showcase to the gateway
        if self.path == "/api/showcase/upload":
            payload = _read_json(self)
            output_path = payload.get("outputPath", "")
            workflow_id = payload.get("workflowId", "")
            if not output_path or not os.path.isfile(output_path):
                return _json_response(self, 400, {"error": "File not found"})
            if not workflow_id:
                return _json_response(self, 400, {"error": "workflowId required"})
            # Get gateway URL and API key from network status or config
            net = net_status()
            gateway_url = net.get("gateway_url", "").rstrip("/")
            api_key = net.get("api_key", "")
            if not gateway_url or not api_key:
                # Try to get from localStorage-equivalent (stored in config by dashboard)
                gateway_url = data.get("network", {}).get("gateway_url", "https://comfyclaw.app")
                api_key = data.get("network", {}).get("api_key", "")
            if not api_key:
                return _json_response(self, 400, {"error": "Not connected to network. Enter API key first."})
            # Resize to 800px wide JPEG thumbnail using convert (ImageMagick)
            import tempfile
            tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
            tmp.close()
            try:
                subprocess.run([
                    "convert", output_path,
                    "-resize", "800x800>",
                    "-quality", "85",
                    tmp.name
                ], check=True, capture_output=True, timeout=10)
                with open(tmp.name, "rb") as f:
                    thumb_data = f.read()
            except Exception as e:
                # Fallback: send original
                with open(output_path, "rb") as f:
                    thumb_data = f.read()
            finally:
                try: os.unlink(tmp.name)
                except: pass
            # Upload to gateway
            fname = os.path.basename(output_path)
            if not fname.lower().endswith(".jpg"):
                fname = os.path.splitext(fname)[0] + ".jpg"
            boundary = os.urandom(8).hex()
            body = (
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{fname}\"\r\nContent-Type: image/jpeg\r\n\r\n"
            ).encode() + thumb_data + f"\r\n--{boundary}--\r\n".encode()
            up_url = f"{gateway_url}/api/v1/showcase/{workflow_id}"
            req = urllib.request.Request(up_url, data=body, method="POST")
            req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            req.add_header("Authorization", f"Bearer {api_key}")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read())
                return _json_response(self, 200, result)
            except Exception as e:
                return _json_response(self, 500, {"error": f"Upload failed: {e}"})

        # Showcase: remove all showcase images for a workflow
        if self.path == "/api/showcase/clear":
            payload = _read_json(self)
            workflow_id = payload.get("workflowId", "")
            net = net_status()
            gateway_url = (net.get("gateway_url") or data.get("network", {}).get("gateway_url", "https://comfyclaw.app")).rstrip("/")
            api_key = net.get("api_key") or data.get("network", {}).get("api_key", "")
            if not api_key:
                return _json_response(self, 400, {"error": "Not connected to network"})
            try:
                clear_url = f"{gateway_url}/api/v1/showcase/{workflow_id}/clear"
                req = urllib.request.Request(clear_url, method="POST", data=b"{}")
                req.add_header("Authorization", f"Bearer {api_key}")
                req.add_header("Content-Type", "application/json")
                urllib.request.urlopen(req, timeout=10)
                return _json_response(self, 200, {"status": "cleared"})
            except Exception as e:
                return _json_response(self, 500, {"error": f"Clear failed: {e}"})

        if self.path == "/api/network/start":
            payload = _read_json(self)
            gateway_url = payload.get("gateway_url", "").strip()
            api_key = payload.get("api_key", "").strip()
            if not gateway_url or not api_key:
                return _json_response(self, 400, {"error": "gateway_url and api_key required"})
            workflows = payload.get("workflows")
            result = net_start(gateway_url, api_key, workflows)
            return _json_response(self, 200, result)
        if self.path == "/api/network/stop":
            return _json_response(self, 200, net_stop())
        if self.path == "/api/network/refresh-workflows":
            # Restart connection so updated workflow list is sent to gateway
            with _net_lock:
                if not _net_proc or _net_proc.poll() is not None:
                    return _json_response(self, 200, {"status": "not_running"})
            # Read current connection params from the running process args
            payload = _read_json(self)
            gw = payload.get("gateway_url", "").strip()
            key = payload.get("api_key", "").strip()
            if gw and key:
                net_stop()
                import time as _time; _time.sleep(0.5)
                result = net_start(gw, key)
                return _json_response(self, 200, {"status": "refreshed", **result})
            return _json_response(self, 400, {"error": "gateway_url and api_key required"})
        if self.path == "/api/servers":
            payload = _read_json(self)
            payload.setdefault("id", os.urandom(6).hex())
            payload.setdefault("name", "ComfyUI")
            payload.setdefault("url", "")
            payload.setdefault("apiKey", "")
            payload.setdefault("isDefault", False)
            if payload.get("isDefault"):
                for s in data["servers"]:
                    s["isDefault"] = False
            data["servers"].append(payload)
            save_config(data)
            return _json_response(self, 201, payload)
        if self.path == "/api/pipelines":
            payload = _read_json(self)
            payload.setdefault("id", os.urandom(6).hex())
            payload.setdefault("name", "Pipeline")
            payload.setdefault("steps", [])
            payload.setdefault("created", datetime.datetime.utcnow().isoformat() + "Z")
            data.setdefault("pipelines", []).append(payload)
            save_config(data)
            return _json_response(self, 201, payload)
        if self.path == "/api/pipelines/run":
            payload = _read_json(self)
            steps = payload.get("steps") or []
            pipeline_id = os.urandom(6).hex()
            pipeline = {
                "pipelineId": pipeline_id,
                "status": "running",
                "steps": steps,
                "currentStep": 0,
                "completed": [],
            }
            with PIPELINE_LOCK:
                PIPELINES[pipeline_id] = pipeline
            threading.Thread(
                target=self._run_pipeline,
                args=(pipeline_id, steps),
                daemon=True,
            ).start()
            return _json_response(self, 200, {"pipelineId": pipeline_id})
        if self.path == "/api/workflows":
            payload = _read_json(self)
            payload.setdefault("id", os.urandom(6).hex())
            payload.setdefault("title", "Workflow")
            payload.setdefault("emoji", "🧩")
            payload.setdefault("description", "")
            payload.setdefault("serverRef", "")
            payload.setdefault("workflowJson", {})
            payload.setdefault("primaryInputNodes", [])
            payload.setdefault("secondaryInputNodes", [])
            payload.setdefault("primaryOutputNodes", [])
            payload.setdefault("secondaryOutputNodes", [])
            data["workflows"].append(payload)
            save_config(data)
            return _json_response(self, 201, payload)
        if self.path == "/api/templates":
            payload = _read_json(self)
            payload.setdefault("id", os.urandom(6).hex())
            payload.setdefault("name", "Template")
            payload.setdefault("workflowId", "")
            payload.setdefault("inputs", {})
            payload.setdefault("created", datetime.datetime.utcnow().isoformat() + "Z")
            data["templates"].append(payload)
            save_config(data)
            return _json_response(self, 201, payload)
        if self.path == "/api/batch":
            payload = _read_json(self)
            workflow_id = payload.get("workflowId")
            variations = int(payload.get("variations") or 1)
            vary_seed = bool(payload.get("varySeed"))
            wf = next((w for w in data.get("workflows", []) if w["id"] == workflow_id), None)
            if not wf:
                return _json_response(self, 404, {"error": "workflow not found"})
            server = next((s for s in data.get("servers", []) if s["id"] == wf.get("serverRef")), None)
            if not server:
                return _json_response(self, 400, {"error": "workflow has no server"})
            inputs = payload.get("inputs") or {}
            seed_fields = _find_seed_fields(wf.get("workflowJson", {}))
            batch_id = os.urandom(6).hex()
            variations = max(1, variations)
            runs = []
            for idx in range(variations):
                run_inputs = dict(inputs)
                if vary_seed and seed_fields:
                    seed_value = random.randint(1, 2**31 - 1)
                    for node_id, field in seed_fields:
                        run_inputs[f"{node_id}.{field}"] = seed_value
                runs.append({
                    "index": idx + 1,
                    "promptId": None,
                    "status": "queued",
                    "inputs": run_inputs,
                })
            with BATCH_LOCK:
                BATCHES[batch_id] = {
                    "batchId": batch_id,
                    "workflowId": workflow_id,
                    "variations": variations,
                    "varySeed": vary_seed,
                    "status": "running",
                    "runs": runs,
                }
            threading.Thread(
                target=self._run_batch,
                args=(batch_id, wf, server, runs, seed_fields, vary_seed),
                daemon=True,
            ).start()
            return _json_response(self, 200, {"batchId": batch_id, "count": variations})
        match = re.match(r"/api/workflows/([^/]+)/run", self.path)
        if match:
            workflow_id = match.group(1)
            wf = next((w for w in data.get("workflows", []) if w["id"] == workflow_id), None)
            if not wf:
                return _json_response(self, 404, {"error": "workflow not found"})
            server = next((s for s in data.get("servers", []) if s["id"] == wf.get("serverRef")), None)
            if not server:
                return _json_response(self, 400, {"error": "workflow has no server"})
            payload = _read_json(self)
            prompt = normalize_prompt(wf.get("workflowJson", {}))
            overrides = payload.get("inputs", [])
            for override in overrides:
                node_id = override.get("nodeId")
                field = override.get("fieldPath")
                if node_id in prompt:
                    prompt[node_id].setdefault("inputs", {})[field] = override.get("currentValue")
            wf["primaryInputNodes"] = payload.get("primaryInputs", wf.get("primaryInputNodes", []))
            wf["secondaryInputNodes"] = payload.get("secondaryInputs", wf.get("secondaryInputNodes", []))
            save_config(data)
            try:
                url = server["url"].rstrip("/") + "/prompt"
                resp = _request_json("POST", url, server.get("apiKey", ""), {"prompt": prompt})
                if "prompt_id" in resp:
                    gallery = ensure_gallery()
                    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
                    input_values = {
                        f"{node.get('nodeId')}.{node.get('fieldPath')}": node.get("currentValue")
                        for node in (wf.get("primaryInputNodes") or [])
                    }
                    gallery["outputs"].append({
                        "id": os.urandom(8).hex(),
                        "workflowId": wf.get("id"),
                        "workflowTitle": wf.get("title"),
                        "promptId": resp["prompt_id"],
                        "timestamp": timestamp,
                        "outputPath": "",
                        "outputType": "",
                        "inputValues": input_values,
                        "status": "queued",
                    })
                    save_gallery(gallery)
                return _json_response(self, 200, resp)
            except Exception as exc:
                return _json_response(self, 500, {"error": str(exc)})
        if self.path == "/api/workflows/import":
            payload = _read_json(self)
            wf_json = payload.get("workflowJson") or {}
            detected = detect_nodes(wf_json)
            wf = {
                "id": os.urandom(6).hex(),
                "title": payload.get("title", "Imported workflow"),
                "emoji": payload.get("emoji", "🧩"),
                "description": payload.get("description", "Imported"),
                "serverRef": payload.get("serverRef", ""),
                "workflowJson": wf_json,
                "primaryInputNodes": [],
                "secondaryInputNodes": detected["inputs"],
                "primaryOutputNodes": detected["outputs"],
                "secondaryOutputNodes": [],
            }
            data["workflows"].append(wf)
            save_config(data)
            return _json_response(self, 201, wf)
        match = re.match(r"/api/workflows/([^/]+)/import", self.path)
        if match:
            payload = _read_json(self)
            wf_json = payload.get("workflowJson") or {}
            detected = detect_nodes(wf_json)
            wf = {
                "id": os.urandom(6).hex(),
                "title": payload.get("title", "Imported workflow"),
                "emoji": payload.get("emoji", "🧩"),
                "description": payload.get("description", "Imported"),
                "serverRef": payload.get("serverRef", ""),
                "workflowJson": wf_json,
                "primaryInputNodes": [],
                "secondaryInputNodes": detected["inputs"],
                "primaryOutputNodes": detected["outputs"],
                "secondaryOutputNodes": [],
            }
            data["workflows"].append(wf)
            save_config(data)
            return _json_response(self, 201, wf)
        match = re.match(r"/api/servers/([^/]+)/test", self.path)
        if match:
            server_id = match.group(1)
            server = next((s for s in data.get("servers", []) if s["id"] == server_id), None)
            if not server:
                return _json_response(self, 404, {"error": "server not found"})
            try:
                url = server["url"].rstrip("/") + "/system_stats"
                _request_json("GET", url, server.get("apiKey", ""))
                return _json_response(self, 200, {"ok": True})
            except Exception as exc:
                return _json_response(self, 500, {"ok": False, "error": str(exc)})
        # Upload file to ComfyUI server (proxy) — supports ?subfolder=audio
        match = re.match(r"/api/servers/([^/]+)/upload", self.path)
        if match:
            server_id = match.group(1)
            server = next((s for s in data.get("servers", []) if s["id"] == server_id), None)
            if not server:
                return _json_response(self, 404, {"error": "server not found"})
            content_type = self.headers.get("Content-Type", "")
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            try:
                url = server["url"].rstrip("/") + "/upload/image"
                req = urllib.request.Request(url, data=body, method="POST")
                req.add_header("Content-Type", content_type)
                if server.get("apiKey"):
                    req.add_header("Authorization", "Bearer " + server["apiKey"])
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                return _json_response(self, 200, result)
            except Exception as exc:
                return _json_response(self, 500, {"error": str(exc)})
        return _json_response(self, 404, {"error": "not found"})

    def handle_api_put(self) -> None:
        data = ensure_config()
        if self.path.startswith("/api/servers/"):
            server_id = self.path.split("/")[-1]
            payload = _read_json(self)
            server = next((s for s in data.get("servers", []) if s["id"] == server_id), None)
            if not server:
                return _json_response(self, 404, {"error": "server not found"})
            server.update(payload)
            if server.get("isDefault"):
                for s in data["servers"]:
                    if s["id"] != server_id:
                        s["isDefault"] = False
            save_config(data)
            return _json_response(self, 200, server)
        if self.path.startswith("/api/pipelines/"):
            pipeline_id = self.path.split("/")[-1]
            payload = _read_json(self)
            pipeline = next((p for p in data.get("pipelines", []) if p.get("id") == pipeline_id), None)
            if not pipeline:
                return _json_response(self, 404, {"error": "pipeline not found"})
            pipeline.update(payload)
            save_config(data)
            return _json_response(self, 200, pipeline)
        if self.path.startswith("/api/workflows/"):
            workflow_id = self.path.split("/")[-1]
            payload = _read_json(self)
            wf = next((w for w in data.get("workflows", []) if w["id"] == workflow_id), None)
            if not wf:
                return _json_response(self, 404, {"error": "workflow not found"})
            wf.update(payload)
            save_config(data)
            return _json_response(self, 200, wf)
        return _json_response(self, 404, {"error": "not found"})

    def handle_api_delete(self) -> None:
        data = ensure_config()
        if self.path.startswith("/api/servers/"):
            server_id = self.path.split("/")[-1]
            data["servers"] = [s for s in data.get("servers", []) if s["id"] != server_id]
            save_config(data)
            return _json_response(self, 200, {"ok": True})
        if self.path.startswith("/api/workflows/"):
            workflow_id = self.path.split("/")[-1]
            data["workflows"] = [w for w in data.get("workflows", []) if w["id"] != workflow_id]
            save_config(data)
            return _json_response(self, 200, {"ok": True})
        if self.path.startswith("/api/gallery/"):
            output_id = self.path.split("/")[-1]
            gallery = ensure_gallery()
            outputs = gallery.get("outputs", [])
            item = next((o for o in outputs if o.get("id") == output_id), None)
            if not item:
                return _json_response(self, 404, {"error": "output not found"})
            if item.get("outputPath") and os.path.exists(item.get("outputPath")):
                try:
                    os.remove(item.get("outputPath"))
                except OSError:
                    pass
            gallery["outputs"] = [o for o in outputs if o.get("id") != output_id]
            save_gallery(gallery)
            return _json_response(self, 200, {"ok": True})
        if self.path.startswith("/api/templates/"):
            template_id = self.path.split("/")[-1]
            data["templates"] = [t for t in data.get("templates", []) if t.get("id") != template_id]
            save_config(data)
            return _json_response(self, 200, {"ok": True})
        if self.path.startswith("/api/pipelines/"):
            pipeline_id = self.path.split("/")[-1]
            data["pipelines"] = [p for p in data.get("pipelines", []) if p.get("id") != pipeline_id and p.get("name") != pipeline_id]
            save_config(data)
            return _json_response(self, 200, {"ok": True})
        return _json_response(self, 404, {"error": "not found"})

    def translate_path(self, path: str) -> str:
        if path.startswith("/api/"):
            return path
        path = urllib.parse.urlparse(path).path
        path = path.lstrip("/")
        if not path:
            path = "index.html"
        return os.path.join(ASSETS_DIR, path)



def run(host: str = "0.0.0.0", port: int = 8787) -> None:
    os.makedirs(ASSETS_DIR, exist_ok=True)
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, ComfyClawHandler)
    print(f"ComfyClaw dashboard running at http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    host = os.environ.get("COMFYCLAW_HOST", "0.0.0.0")
    port = int(os.environ.get("COMFYCLAW_PORT", "8787"))
    run(host, port)
