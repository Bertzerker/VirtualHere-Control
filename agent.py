#!/usr/bin/env python3
import argparse
import hmac
import json
import os
import re
import socket
import ssl
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "config" / "agent.json"
DEVICE_ADDRESS_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

def normalize_identity(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def local_identity_values(config):
    values = {
        socket.gethostname(),
        socket.getfqdn(),
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("HOSTNAME", ""),
        config.get("client_id", ""),
        config.get("client_name", ""),
    }
    return {normalized for normalized in (normalize_identity(value) for value in values) if normalized}


def status_words(status):
    return re.sub(r"[^a-z0-9]+", " ", str(status or "").lower()).strip()


def is_local_use_status(status, config):
    words = status_words(status)
    if "in use by you" in words or "in use by this client" in words:
        return True
    if "in use by" not in words:
        return False
    normalized = normalize_identity(status)
    return any(identity in normalized for identity in local_identity_values(config))


def annotate_local_devices(devices, config):
    for device in devices:
        device["local_use"] = is_local_use_status(device.get("status"), config)
    return devices

def load_config(path):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def parse_virtualhere_list(text):
    devices = []
    current_hub = ""
    current_hub_address = ""
    hub_re = re.compile(r"^\s*(?!-+>)(.+?)\s+\(([^()]+:\d+)\)\s*$")
    device_re = re.compile(r"^\s*(?:-+>|=>)?\s*(.+?)\s+\(([^()]+)\)(.*)$")

    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.lower().startswith("virtualhere ipc"):
            continue
        hub_match = hub_re.match(clean)
        if hub_match:
            current_hub = hub_match.group(1).strip()
            current_hub_address = hub_match.group(2).strip()
            continue
        device_match = device_re.match(clean)
        if not device_match:
            continue
        address = device_match.group(2).strip()
        if ":" in address:
            continue
        name = device_match.group(1).replace("*", "").strip()
        status = device_match.group(3).strip()
        devices.append({
            "name": name,
            "address": address,
            "hub": current_hub,
            "hub_address": current_hub_address,
            "status": status,
        })
    return devices


class AgentHandler(BaseHTTPRequestHandler):
    server_version = "VirtualHereAgent/0.1"

    def send_json(self, status, data):
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def is_authorized(self):
        token = str(self.server.config.get("api_token", ""))
        if not token:
            return False
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):], token)

    def require_auth(self):
        if self.is_authorized():
            return True
        self.send_json(401, {"ok": False, "error": "unauthorized"})
        return False

    def vh(self, command):
        return run_vh_command(self.server.config, command)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self.send_json(200, {"ok": True, "client_id": self.server.config.get("client_id")})
            return
        if parsed.path == "/api/state":
            if not self.require_auth():
                return
            listed = self.vh("LIST")
            devices = parse_virtualhere_list(listed.get("stdout", "")) if listed.get("ok") else []
            annotate_local_devices(devices, self.server.config)
            state = self.vh("GET CLIENT STATE")
            self.send_json(200, {
                "ok": listed["ok"],
                "agent": {
                    "client_id": self.server.config.get("client_id"),
                    "client_name": self.server.config.get("client_name"),
                    "time": int(time.time()),
                    "local_identity": sorted(local_identity_values(self.server.config)),
                },
                "devices": devices,
                "list": listed,
                "client_state": state,
            })
            return
        self.send_json(404, {"ok": False, "error": "unknown endpoint"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if not self.require_auth():
            return
        if parsed.path == "/api/use":
            self.device_command("USE")
            return
        if parsed.path == "/api/stop":
            self.device_command("STOP USING")
            return
        if parsed.path == "/api/command":
            self.raw_command()
            return
        self.send_json(404, {"ok": False, "error": "unknown endpoint"})

    def device_command(self, verb):
        body = self.read_json_body()
        address = str(body.get("address", "")).strip()
        if not DEVICE_ADDRESS_RE.match(address):
            self.send_json(400, {"ok": False, "error": "invalid device address"})
            return
        result = self.vh(f"{verb},{address}")
        self.send_json(200 if result["ok"] else 502, result)

    def raw_command(self):
        body = self.read_json_body()
        command = str(body.get("command", "")).strip()
        allowed = (
            command == "LIST" or
            command == "GET CLIENT STATE" or
            command == "STOP USING ALL LOCAL" or
            command.startswith("MANUAL HUB ADD,") or
            command.startswith("MANUAL HUB REMOVE,")
        )
        if not allowed:
            self.send_json(400, {"ok": False, "error": "command not allowed"})
            return
        result = self.vh(command)
        self.send_json(200 if result["ok"] else 502, result)


def run_vh_command(config, command):
    binary = str(config.get("vh_binary", "")).strip()
    timeout = int(config.get("command_timeout_seconds", 7))
    if not binary:
        return {"ok": False, "code": 127, "stdout": "", "stderr": "vh_binary is not configured", "command": command}

    if os.name == "nt":
        return run_windows_with_result_file(binary, command, timeout)

    args = [binary, "-t", command]
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        return {
            "ok": completed.returncode == 0 and not stdout.startswith(("FAILED", "ERROR")),
            "code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "command": command,
        }
    except FileNotFoundError:
        return {"ok": False, "code": 127, "stdout": "", "stderr": f"not found: {binary}", "command": command}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": 124, "stdout": "", "stderr": "VirtualHere command timed out", "command": command}


def run_windows_with_result_file(binary, command, timeout):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as handle:
        result_path = handle.name
    try:
        completed = subprocess.run(
            [binary, "-t", command, f"-r={result_path}"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = Path(result_path).read_text(encoding="utf-8", errors="replace").strip()
        stderr = completed.stderr.strip()
        return {
            "ok": completed.returncode == 0 and not stdout.startswith(("FAILED", "ERROR")),
            "code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "command": command,
        }
    finally:
        try:
            Path(result_path).unlink()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="VirtualHere local control agent")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()
    config = load_config(args.config)

    host = config.get("listen_host", "0.0.0.0")
    port = int(config.get("listen_port", 9443))
    server = ThreadingHTTPServer((host, port), AgentHandler)
    server.config = config

    cert = config.get("tls_cert")
    key = config.get("tls_key")
    if cert and key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        scheme = "https"
    else:
        scheme = "http"

    print(f"VirtualHere Agent {config.get('client_id')} listening on {scheme}://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
