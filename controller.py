#!/usr/bin/env python3
import argparse
import json
import re
import ssl
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
STATIC_DIR = BASE_DIR / "static"
CLIENTS_FILE = CONFIG_DIR / "clients.json"
ASSIGNMENTS_FILE = CONFIG_DIR / "assignments.json"


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def load_clients():
    data = load_json(CLIENTS_FILE, {"clients": []})
    clients = []
    seen = set()
    for client in data.get("clients", []):
        client_id = str(client.get("id", "")).strip()
        url = str(client.get("url", "")).rstrip("/")
        if not client_id or not url or client_id in seen:
            continue
        seen.add(client_id)
        item = dict(client)
        item["id"] = client_id
        item["url"] = url
        item.setdefault("name", client_id)
        item.setdefault("type", "client")
        item.setdefault("verify_tls", True)
        clients.append(item)
    return clients


def public_client(client):
    return {
        "id": client["id"],
        "name": client.get("name", client["id"]),
        "type": client.get("type", "client"),
        "os_variant": client.get("os_variant", ""),
        "url": client.get("url", ""),
    }


def ssl_context_for(client):
    if not str(client.get("url", "")).lower().startswith("https://"):
        return None
    if client.get("verify_tls", True) is False:
        return ssl._create_unverified_context()
    ca_bundle = client.get("ca_bundle")
    if ca_bundle:
        ca_path = Path(ca_bundle)
        if not ca_path.is_absolute():
            ca_path = BASE_DIR / ca_path
        context = ssl.create_default_context(cafile=str(ca_path))
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        context.check_hostname = False
        return context
    return ssl.create_default_context()


def call_agent(client, method, path, payload=None, timeout=8):
    url = client["url"] + path
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    token = client.get("token")
    if token:
        headers["Authorization"] = "Bearer " + str(token)
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context_for(client)) as response:
            raw = response.read().decode("utf-8")
            return {"ok": True, "status": response.status, "data": json.loads(raw)}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {}
        return {"ok": False, "status": exc.code, "error": raw or exc.reason, "data": data}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc)}


def summarize_agent_result(prefix, result):
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    command = data.get("command") or ""
    code = data.get("code")
    stdout = str(data.get("stdout") or "").strip()
    stderr = str(data.get("stderr") or "").strip()
    transport_error = str(result.get("error") or "").strip()

    parts = [prefix]
    if command:
        parts.append(f"command={command}")
    if code is not None:
        parts.append(f"code={code}")
    if stdout:
        parts.append(f"stdout={stdout[:500]}")
    if stderr:
        parts.append(f"stderr={stderr[:500]}")
    if transport_error and not stdout and not stderr:
        parts.append(f"agent={transport_error[:500]}")
    return "; ".join(parts)


def load_assignments():
    return load_json(ASSIGNMENTS_FILE, {"devices": {}})


def save_assignments(assignments):
    save_json(ASSIGNMENTS_FILE, assignments)


def status_words(status):
    return re.sub(r"[^a-z0-9]+", " ", str(status or "").lower()).strip()


def status_used_by_this_client(status):
    value = status_words(status)
    return "in use by you" in value or "in use by this client" in value


def device_used_by_this_client(device):
    return bool(device.get("local_use")) or status_used_by_this_client(device.get("status"))


def status_in_use(status):
    return "in use" in status_words(status)

def find_client_state(client_states, client_id):
    return next((state for state in client_states if state["id"] == client_id), None)


def find_device(devices, address):
    return next((device for device in devices if device.get("address") == address), None)


def collect_client_states(clients):
    client_states = []
    for client in clients:
        result = call_agent(client, "GET", "/api/state")
        state = {
            **public_client(client),
            "online": result["ok"],
            "error": None if result["ok"] else result.get("error", "unreachable"),
            "devices": [],
            "vh_ok": False,
        }
        if result["ok"]:
            data = result.get("data", {})
            state["vh_ok"] = bool(data.get("ok", False))
            state["agent"] = data.get("agent", {})
            state["devices"] = data.get("devices", [])
        client_states.append(state)
    return client_states


def cleanup_invalid_assignments(assignments, clients_by_id):
    assigned = assignments.setdefault("devices", {})
    stale = []
    for address, client_id in list(assigned.items()):
        if client_id in clients_by_id:
            continue
        stale.append({
            "address": address,
            "client_id": client_id,
            "reason": "assigned client no longer exists",
        })
        assigned.pop(address, None)
    return stale


def sync_assignments_from_actual_use(assignments, client_states):
    assigned = assignments.setdefault("devices", {})
    actions = []
    local_uses = {}
    visible_devices = {}

    for state in client_states:
        if not state.get("online") or not state.get("vh_ok"):
            continue
        for device in state.get("devices", []):
            address = device.get("address")
            if not address:
                continue
            visible_devices.setdefault(address, []).append({"client_id": state["id"], "device": device})
            if device_used_by_this_client(device):
                local_uses.setdefault(address, []).append(state["id"])

    for address, client_ids in local_uses.items():
        client_id = client_ids[0]
        previous = assigned.get(address)
        if previous == client_id:
            continue
        assigned[address] = client_id
        actions.append({
            "address": address,
            "client_id": client_id,
            "previous_client_id": previous,
            "action": "detected-manual-use",
        })

    for address, client_id in list(assigned.items()):
        if address in local_uses:
            continue
        visible = visible_devices.get(address, [])
        if not visible:
            continue
        if any(status_in_use(item["device"].get("status")) for item in visible):
            continue
        assigned.pop(address, None)
        actions.append({
            "address": address,
            "client_id": client_id,
            "action": "detected-manual-release",
        })

    return actions


def enforce_assignments(clients_by_id, assignments, client_states):
    actions = []
    for address, client_id in list(assignments.setdefault("devices", {}).items()):
        client = clients_by_id.get(client_id)
        if not client:
            continue
        target_state = find_client_state(client_states, client_id)
        target_device = find_device(target_state.get("devices", []), address) if target_state else None

        if not target_state or not target_state.get("online") or not target_state.get("vh_ok"):
            continue
        if target_device and device_used_by_this_client(target_device):
            continue

        stop_results = []
        for state in client_states:
            if state["id"] == client_id or not state.get("online"):
                continue
            device = find_device(state.get("devices", []), address)
            if not device or not device_used_by_this_client(device):
                continue
            old_client = clients_by_id.get(state["id"])
            if not old_client:
                continue
            stopped = call_agent(old_client, "POST", "/api/stop", {"address": address})
            stop_results.append({"client": state["id"], "result": stopped})

        used = call_agent(client, "POST", "/api/use", {"address": address})
        action = {
            "address": address,
            "client_id": client_id,
            "action": "rebind-assigned-device",
            "ok": used["ok"] and used.get("data", {}).get("ok") is not False,
            "stop_results": stop_results,
            "use_result": used,
        }
        actions.append(action)

        if action["ok"]:
            refreshed = call_agent(client, "GET", "/api/state")
            if refreshed["ok"]:
                data = refreshed.get("data", {})
                target_state["vh_ok"] = bool(data.get("ok", False))
                target_state["devices"] = data.get("devices", target_state.get("devices", []))

    return actions


def refresh_assignments_from_agents(clients, assignments, enforce=True):
    clients_by_id = {client["id"]: client for client in clients}
    client_states = collect_client_states(clients)
    stale = cleanup_invalid_assignments(assignments, clients_by_id)
    sync_actions = sync_assignments_from_actual_use(assignments, client_states)
    actions = enforce_assignments(clients_by_id, assignments, client_states) if enforce else []
    if stale or sync_actions:
        save_assignments(assignments)
    return client_states, stale, [*sync_actions, *actions]


def aggregate_state():
    clients = load_clients()
    assignments = load_assignments()
    client_states, stale_assignments, assignment_actions = refresh_assignments_from_agents(clients, assignments)
    devices = {}

    for state in client_states:
        for device in state["devices"]:
            address = device.get("address")
            if not address:
                continue
            existing = devices.get(address, {})
            devices[address] = {
                "address": address,
                "name": device.get("name") or existing.get("name") or address,
                "hub": device.get("hub") or existing.get("hub") or "",
                "source_client_id": state["id"],
                "assigned_to": assignments.get("devices", {}).get(address),
                "status": device.get("status") or existing.get("status") or "",
            }

    public_clients = []
    for client in clients:
        public = public_client(client)
        public["online"] = next((s["online"] for s in client_states if s["id"] == client["id"]), False)
        public["error"] = next((s["error"] for s in client_states if s["id"] == client["id"]), None)
        public_clients.append(public)

    return {
        "time": int(time.time()),
        "clients": public_clients,
        "devices": sorted(devices.values(), key=lambda item: (item.get("hub", ""), item.get("name", ""))),
        "client_states": client_states,
        "assignments": assignments.get("devices", {}),
        "stale_assignments": stale_assignments,
        "assignment_actions": assignment_actions,
    }


class ControllerHandler(SimpleHTTPRequestHandler):
    server_version = "VirtualHereControl/0.1"

    def translate_path(self, path):
        parsed = urlparse(path)
        clean = parsed.path.lstrip("/")
        if not clean:
            clean = "index.html"
        if clean in {"setup.html", "setup.js"}:
            clean = "__not_found__"
        return str(STATIC_DIR / clean)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, status, data):
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/clients":
            self.send_json(200, {"clients": [public_client(client) for client in load_clients()]})
            return
        if parsed.path == "/api/state":
            self.send_json(200, aggregate_state())
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/assign":
            self.assign_device()
            return
        if parsed.path == "/api/release":
            self.release_device()
            return
        self.send_json(404, {"ok": False, "error": "unknown endpoint"})

    def assign_device(self):
        body = self.read_json_body()
        device_address = str(body.get("deviceAddress", "")).strip()
        target_id = str(body.get("clientId", "")).strip()
        clients = {client["id"]: client for client in load_clients()}
        if not device_address or target_id not in clients:
            self.send_json(400, {"ok": False, "error": "deviceAddress and valid clientId are required"})
            return

        assignments = load_assignments()
        _, stale_assignments, assignment_actions = refresh_assignments_from_agents(list(clients.values()), assignments)
        current_id = assignments.get("devices", {}).get(device_address)
        steps = []
        if stale_assignments:
            steps.append({"action": "cleanup-stale-assignments", "result": stale_assignments})
        if assignment_actions:
            steps.append({"action": "enforce-existing-assignments", "result": assignment_actions})

        if current_id and current_id != target_id and current_id in clients:
            released = call_agent(clients[current_id], "POST", "/api/stop", {"address": device_address})
            steps.append({"client": current_id, "action": "stop", "result": released})
            if not released["ok"] or released.get("data", {}).get("ok") is False:
                self.send_json(502, {
                    "ok": False,
                    "error": summarize_agent_result("old client did not release device", released),
                    "steps": steps,
                })
                return

        used = call_agent(clients[target_id], "POST", "/api/use", {"address": device_address})
        steps.append({"client": target_id, "action": "use", "result": used})
        if not used["ok"] or used.get("data", {}).get("ok") is False:
            self.send_json(502, {
                "ok": False,
                "error": summarize_agent_result("target client did not bind device", used),
                "steps": steps,
            })
            return

        assignments.setdefault("devices", {})[device_address] = target_id
        save_assignments(assignments)
        self.send_json(200, {"ok": True, "assigned_to": target_id, "device": device_address, "steps": steps})

    def release_device(self):
        body = self.read_json_body()
        device_address = str(body.get("deviceAddress", "")).strip()
        client_id = str(body.get("clientId", "")).strip()
        clients = {client["id"]: client for client in load_clients()}
        assignments = load_assignments()
        _, stale_assignments, _ = refresh_assignments_from_agents(list(clients.values()), assignments, enforce=False)
        target_id = client_id or assignments.get("devices", {}).get(device_address)

        if not device_address or target_id not in clients:
            if device_address:
                assignments.setdefault("devices", {}).pop(device_address, None)
                save_assignments(assignments)
                self.send_json(200, {
                    "ok": True,
                    "released_from": None,
                    "device": device_address,
                    "stale_assignments": stale_assignments,
                })
                return
            self.send_json(400, {"ok": False, "error": "deviceAddress and known assignment/clientId are required"})
            return

        stopped = call_agent(clients[target_id], "POST", "/api/stop", {"address": device_address})
        if not stopped["ok"] or stopped.get("data", {}).get("ok") is False:
            assignments.setdefault("devices", {}).pop(device_address, None)
            save_assignments(assignments)
            self.send_json(200, {
                "ok": True,
                "released_from": target_id,
                "device": device_address,
                "warning": summarize_agent_result("client command failed but controller assignment was cleared", stopped),
                "result": stopped,
            })
            return

        assignments.setdefault("devices", {}).pop(device_address, None)
        save_assignments(assignments)
        self.send_json(200, {"ok": True, "released_from": target_id, "device": device_address, "result": stopped})


def main():
    parser = argparse.ArgumentParser(description="VirtualHere USB Control web interface")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), ControllerHandler)
    if args.tls_cert and args.tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.tls_cert, args.tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)

    scheme = "https" if args.tls_cert else "http"
    print(f"VirtualHere Control listening on {scheme}://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
