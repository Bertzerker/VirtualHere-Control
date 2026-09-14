#!/usr/bin/env python3
import argparse
import base64
import datetime as dt
import html
import ipaddress
import json
import posixpath
import re
import secrets
import shlex
import socket
import textwrap
import time
from dataclasses import dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = BASE_DIR / "config"
STATIC_DIR = BASE_DIR / "static"
SETUP_CONFIG_FILE = CONFIG_DIR / "setup-config.json"
CERTS_DIR = BASE_DIR / "certs"
REPORTS_DIR = CONFIG_DIR / "setup-reports"

DEFAULT_AGENT_PORT = 9443
DEFAULT_CONTROLLER_PORT = 8080
DEFAULT_VH_HUB_PORT = 7575
DEFAULT_VH_SSL_PORT = 7574

DOWNLOADS = {
    ("server", "linux", "x86_64"): {
        "binary": "vhusbdx86_64",
        "url": "https://www.virtualhere.com/sites/default/files/usbserver/vhusbdx86_64",
    },
    ("server", "linux", "armhf"): {
        "binary": "vhusbdarm",
        "url": "https://www.virtualhere.com/sites/default/files/usbserver/vhusbdarm",
    },
    ("server", "linux", "aarch64"): {
        "binary": "vhusbdarm64",
        "url": "https://www.virtualhere.com/sites/default/files/usbserver/vhusbdarm64",
    },
    ("server", "openwrt", "armhf"): {
        "binary": "vhusbdarm",
        "url": "https://www.virtualhere.com/sites/default/files/usbserver/vhusbdarm",
    },
    ("server", "openwrt", "aarch64"): {
        "binary": "vhusbdarm64",
        "url": "https://www.virtualhere.com/sites/default/files/usbserver/vhusbdarm64",
    },
    ("server", "windows", "x86_64"): {
        "binary": "vhusbdwin64.exe",
        "url": "https://www.virtualhere.com/sites/default/files/usbserver/vhusbdwin64.exe",
    },
    ("client", "linux", "x86_64"): {
        "binary": "vhclientx86_64",
        "url": "https://www.virtualhere.com/sites/default/files/usbclient/vhclientx86_64",
    },
    ("client", "linux", "armhf"): {
        "binary": "vhclientarmhf",
        "url": "https://www.virtualhere.com/sites/default/files/usbclient/vhclientarmhf",
    },
    ("client", "linux", "aarch64"): {
        "binary": "vhclientaarch64",
        "url": "https://www.virtualhere.com/sites/default/files/usbclient/vhclientaarch64",
    },
    ("client", "windows", "x86_64"): {
        "binary": "vhui64.exe",
        "url": "https://www.virtualhere.com/sites/default/files/usbclient/vhui64.exe",
    },
}


class SetupError(RuntimeError):
    pass


@dataclass
class Target:
    role: str
    id: str
    name: str
    os_type: str
    arch: str
    os_variant: str
    ip: str
    login: str
    password: str
    install_dir: str
    ssh_port: int = 22
    agent_port: int = DEFAULT_AGENT_PORT
    artifact_stem: str = ""

    @property
    def download(self):
        try:
            return DOWNLOADS[(self.role, self.os_type, self.arch)]
        except KeyError as exc:
            raise SetupError(f"Unsupported {self.role} target: {self.os_type}/{self.arch}") from exc

    @property
    def binary_name(self):
        return self.download["binary"]

    @property
    def remote_binary(self):
        return remote_join(self, self.install_dir, self.binary_name)


def supported_targets():
    roles = {"server": [], "client": []}
    for role, os_type, arch in sorted(DOWNLOADS):
        roles[role].append({"os": os_type, "arch": arch, "binary": DOWNLOADS[(role, os_type, arch)]["binary"]})
    return roles


def build_plan(raw):
    spec = normalize_spec(raw)
    server = spec["server"]
    clients = spec["clients"]
    steps = [
        {"target": server.id, "action": "check-ssh", "label": f"Connect to server {server.ip}"},
        {"target": server.id, "action": "folders", "label": f"Create or reuse {server.install_dir}"},
        {"target": server.id, "action": "download", "label": f"Install VirtualHere server binary {server.binary_name} if missing"},
        {"target": server.id, "action": "certs", "label": "Generate CA and server certificate"},
        {"target": server.id, "action": "config", "label": "Write controller clients.json and optional vh-control-vhusbd.ini"},
        {"target": server.id, "action": "services", "label": server_service_label(server)},
    ]
    for client in clients:
        steps.extend([
            {"target": client.id, "action": "check-ssh", "label": f"Connect to client {client.ip}"},
            {"target": client.id, "action": "folders", "label": f"Create or reuse {client.install_dir}"},
            {"target": client.id, "action": "download", "label": f"Install VirtualHere client binary {client.binary_name} if missing"},
            {"target": client.id, "action": "token", "label": "Generate API token and agent TLS certificate"},
            {"target": client.id, "action": "vh-client-config", "label": "Load CA/client certificate and add manual hub"},
            {"target": client.id, "action": "services", "label": "Enable VirtualHere client and control agent autostart"},
        ])
    return {
        "ok": True,
        "server": public_target(server),
        "clients": [public_target(client) for client in clients],
        "options": public_options(spec["options"]),
        "steps": steps,
    }


def run_setup(raw):
    spec = normalize_spec(raw)
    dry_run = bool(raw.get("dry_run") or raw.get("dryRun"))
    if dry_run:
        return {**build_plan(raw), "dry_run": True, "log": [{"target": "local", "ok": True, "message": "Dry run only. No files changed and no SSH commands were run."}]}

    ensure_runtime_dependencies()

    context = SetupContext(spec)
    try:
        context.prepare_materials()
        context.build_controller_config()
        context.install_server()
        for client in context.clients:
            context.install_client(client)
        context.verify_server_can_reach_agents()
        context.write_report()
    except Exception as exc:
        context.add_log("setup", False, str(exc))
        context.write_report()
        raise SetupError(f"{exc}; report: {context.report_path}") from exc
    return {
        "ok": True,
        "dry_run": False,
        "report": str(context.report_path),
        "log": context.log,
        "clients": [public_target(client) for client in context.clients],
        "server": public_target(context.server),
    }


def normalize_spec(raw):
    if not isinstance(raw, dict):
        raise SetupError("Setup payload must be a JSON object")
    options = {
        "controller_port": int(raw.get("controller_port") or raw.get("controllerPort") or DEFAULT_CONTROLLER_PORT),
        "agent_port": int(raw.get("agent_port") or raw.get("agentPort") or DEFAULT_AGENT_PORT),
        "vh_hub_port": int(raw.get("vh_hub_port") or raw.get("vhHubPort") or DEFAULT_VH_HUB_PORT),
        "vh_ssl_port": int(raw.get("vh_ssl_port") or raw.get("vhSslPort") or DEFAULT_VH_SSL_PORT),
        "use_ssl": bool(raw.get("use_ssl", raw.get("useSsl", True))),
        "use_client_certs": bool(raw.get("use_client_certs", raw.get("useClientCerts", False))),
        "server_name": str(raw.get("server_name") or raw.get("serverName") or "virtualhere-server").strip(),
        "manage_vhusbd_config": bool(raw.get("manage_vhusbd_config", raw.get("manageVhusbdConfig", True))),
    }
    if not options["use_ssl"]:
        options["use_client_certs"] = False
    server = make_target("server", raw.get("server") or {}, options)
    clients = [make_target("client", item, options) for item in raw.get("clients") or []]
    if not clients:
        raise SetupError("At least one client is required")
    ids = set()
    for target in [server, *clients]:
        if target.id in ids:
            raise SetupError(f"Duplicate target id: {target.id}")
        ids.add(target.id)
        target.download
    return {"server": server, "clients": clients, "options": options}


def make_target(role, data, options):
    if not isinstance(data, dict):
        raise SetupError(f"{role} target must be an object")
    os_type = norm(data.get("os") or data.get("os_type") or data.get("type"))
    arch = norm(data.get("arch") or "x86_64")
    os_variant = norm(data.get("os_variant") or data.get("osVariant") or data.get("distro") or "")
    ip = str(data.get("ip") or data.get("host") or "").strip()
    login = str(data.get("login") or data.get("username") or "").strip()
    password = str(data.get("password") or "")
    install_dir = str(data.get("install_dir") or data.get("installDir") or default_install_dir(os_type)).strip()
    target_id = norm(data.get("id") or data.get("name") or (role if role == "server" else ip.replace(".", "-")))
    name = str(data.get("name") or target_id).strip()
    if not os_type or not ip or not login or not password or not install_dir:
        raise SetupError(f"{role} requires os, ip, login, password, and install folder")
    return Target(
        role=role,
        id=target_id,
        name=name,
        os_type=os_type,
        arch=arch,
        os_variant=os_variant,
        ip=ip,
        login=login,
        password=password,
        install_dir=install_dir.rstrip("\\/"),
        ssh_port=int(data.get("ssh_port") or data.get("sshPort") or 22),
        agent_port=int(data.get("agent_port") or data.get("agentPort") or options["agent_port"]),
    )


def ensure_runtime_dependencies():
    missing = []
    try:
        import paramiko  # noqa: F401
    except ImportError:
        missing.append("paramiko")
    try:
        import cryptography  # noqa: F401
    except ImportError:
        missing.append("cryptography")
    if missing:
        if Path("/etc/openwrt_release").exists():
            raise SetupError(
                "Setup dependencies are missing on OpenWrt. Run the guided setup from Windows or CachyOS, "
                "then run only the controller on OpenWrt. If you still want to try on OpenWrt, run "
                "python -m pip install -r /vh-server/requirements.txt from the actual install folder."
            )
        raise SetupError("Install setup dependencies first: python -m pip install -r requirements.txt (" + ", ".join(missing) + " missing)")


class SetupContext:
    def __init__(self, spec):
        self.server = spec["server"]
        self.clients = spec["clients"]
        self.options = spec["options"]
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.work_dir = REPORTS_DIR / self.run_id
        self.materials = {}
        self.log = []
        self.report_path = self.work_dir / "report.json"

    def add_log(self, target, ok, message, detail=None):
        item = {"time": int(time.time()), "target": target, "ok": ok, "message": message}
        if detail:
            item["detail"] = detail
        self.log.append(item)

    def prepare_materials(self):
        self.work_dir.mkdir(parents=True, exist_ok=True)
        certs = CertificateFactory(self.work_dir / "certs")
        self.materials["ca_pem"] = certs.create_ca()
        if self.options["use_ssl"]:
            self.materials["server_pem"] = certs.create_server_cert(self.server, self.options["server_name"])
        for client in self.clients:
            client.artifact_stem = make_artifact_stem(client)
            client.token = secrets.token_hex(32)
            self.materials[f"{client.id}_agent_cert"] = certs.create_leaf_cert(client, f"{client.id}-agent")
            if self.options["use_client_certs"]:
                self.materials[f"{client.id}_vh_client_pem"] = certs.create_client_pem(client)
        self.add_log("local", True, "Generated API tokens and certificates")

    def build_controller_config(self):
        clients_json = {"clients": []}
        for client in self.clients:
            clients_json["clients"].append({
                "id": client.id,
                "name": client.name,
                "type": client.os_type,
                "os_variant": client.os_variant,
                "url": f"https://{client.ip}:{client.agent_port}",
                "token": client.token,
                "verify_tls": True,
                "ca_bundle": "certs/setup-ca.pem",
            })
        self.materials["clients_json"] = json.dumps(clients_json, indent=2, sort_keys=True) + "\n"
        self.add_log("local", True, "Built controller clients.json for server upload")
    def install_server(self):
        try:
            self.add_log(self.server.id, True, f"Connecting to {self.server.ip}:{self.server.ssh_port}")
            with SSHSession(self.server) as ssh:
                self.ensure_base_tree(ssh, self.server)
                self.upload_controller_bundle(ssh)
                self.install_virtualhere_binary(ssh, self.server)
                self.write_server_config(ssh)
                self.install_server_services(ssh)
                self.add_log(self.server.id, True, "Server setup finished")
        except Exception as exc:
            raise SetupError(f"{self.server.id} setup failed: {exc}") from exc

    def install_client(self, client):
        try:
            self.add_log(client.id, True, f"Connecting to {client.ip}:{client.ssh_port}")
            with SSHSession(client) as ssh:
                self.ensure_base_tree(ssh, client)
                self.upload_client_bundle(ssh, client)
                self.install_virtualhere_binary(ssh, client)
                self.write_client_config(ssh, client)
                self.install_client_services(ssh, client)
                self.add_log(client.id, True, "Client setup finished")
        except Exception as exc:
            raise SetupError(f"{client.id} setup failed: {exc}") from exc

    def verify_server_can_reach_agents(self):
        if not is_unix_target(self.server):
            return
        with SSHSession(self.server) as ssh:
            for client in self.clients:
                url = f"https://{client.ip}:{client.agent_port}/health"
                result = ssh.run_linux(f"""
                url={shlex.quote(url)}
                if command -v python3 >/dev/null 2>&1; then
                  python3 -c 'import ssl, sys, urllib.request; ctx=ssl._create_unverified_context(); urllib.request.urlopen(sys.argv[1], context=ctx, timeout=8).read()' "$url"
                elif command -v python >/dev/null 2>&1; then
                  python -c 'import ssl, sys, urllib.request; ctx=ssl._create_unverified_context(); urllib.request.urlopen(sys.argv[1], context=ctx, timeout=8).read()' "$url"
                elif command -v curl >/dev/null 2>&1; then
                  curl -fsSk --max-time 8 "$url" >/dev/null
                else
                  wget --no-check-certificate -T 8 -qO- "$url" >/dev/null
                fi
                """, sudo=False, check=False)
                if result["code"] != 0:
                    detail = (result.get("stderr") or result.get("stdout") or "").strip()
                    raise SetupError(f"Server cannot reach {client.id} agent at {url}: {detail or 'connection failed'}")
                self.add_log(self.server.id, True, f"Server reached {client.id} agent at {url}")

    def ensure_base_tree(self, ssh, target):
        dirs = [
            target.install_dir,
            remote_join(target, target.install_dir, "config"),
            remote_join(target, target.install_dir, "certs"),
            remote_join(target, target.install_dir, "static"),
        ]
        for directory in dirs:
            ssh.mkdir(directory)
        self.add_log(target.id, True, f"Created or reused {target.install_dir}")

    def upload_controller_bundle(self, ssh):
        server = self.server
        ssh.upload_file(BASE_DIR / "controller.py", remote_join(server, server.install_dir, "controller.py"))
        ssh.upload_file(BASE_DIR / "setup_engine.py", remote_join(server, server.install_dir, "setup_engine.py"))
        if (BASE_DIR / "requirements.txt").exists():
            ssh.upload_file(BASE_DIR / "requirements.txt", remote_join(server, server.install_dir, "requirements.txt"))
        for path in (BASE_DIR / "static").glob("*"):
            if path.is_file():
                ssh.upload_file(path, remote_join(server, server.install_dir, "static", path.name))
        ssh.upload_text(self.materials["clients_json"], remote_join(server, server.install_dir, "config", "clients.json"))
        ssh.upload_text("{}", remote_join(server, server.install_dir, "config", "assignments.json"))
        ssh.upload_text(self.materials["ca_pem"], remote_join(server, server.install_dir, "certs", "setup-ca.pem"))
        if self.options["use_ssl"]:
            ssh.upload_text(self.materials["server_pem"], remote_join(server, server.install_dir, "certs", "server.pem"))
        self.add_log(server.id, True, "Uploaded controller files and certificates")

    def upload_client_bundle(self, ssh, client):
        agent_cert = self.materials[f"{client.id}_agent_cert"]
        paths = client_artifact_paths(client)
        ssh.upload_file(BASE_DIR / "agent.py", remote_join(client, client.install_dir, "agent.py"))
        ssh.upload_text(self.materials["ca_pem"], remote_join(client, client.install_dir, "certs", "ca.pem"))
        ssh.upload_text(agent_cert["cert"], paths["agent_cert"])
        ssh.upload_text(agent_cert["key"], paths["agent_key"])
        if self.options["use_client_certs"]:
            ssh.upload_text(self.materials[f"{client.id}_vh_client_pem"], paths["vh_client_pem"])
        agent_json = {
            "client_id": client.id,
            "client_name": client.name,
            "listen_host": "0.0.0.0",
            "listen_port": client.agent_port,
            "vh_binary": client.remote_binary,
            "api_token": client.token,
            "tls_cert": paths["agent_cert"],
            "tls_key": paths["agent_key"],
            "command_timeout_seconds": 7,
        }
        ssh.upload_text(json.dumps(agent_json, indent=2, sort_keys=True) + "\n", paths["agent_config"])
        self.add_log(client.id, True, f"Uploaded agent files as {client.artifact_stem}")

    def install_virtualhere_binary(self, ssh, target):
        url = target.download["url"]
        binary = target.remote_binary
        if is_unix_target(target):
            cmd = f"""
            set -eu
            if [ ! -f {shlex.quote(binary)} ]; then
              if command -v curl >/dev/null 2>&1; then
                curl -fsSL {shlex.quote(url)} -o {shlex.quote(binary)}
              else
                wget -O {shlex.quote(binary)} {shlex.quote(url)}
              fi
            fi
            chmod +x {shlex.quote(binary)}
            """
            ssh.run_linux(cmd, sudo=True)
        else:
            ps = f"""
            $ErrorActionPreference = 'Stop'
            $binary = '{ps_quote(binary)}'
            if (-not (Test-Path -LiteralPath $binary)) {{
              Invoke-WebRequest -Uri '{ps_quote(url)}' -OutFile $binary
            }}
            """
            ssh.run_windows(ps)
        self.add_log(target.id, True, f"Installed or reused {target.binary_name}")

    def write_server_config(self, ssh):
        server = self.server
        if self.options.get("manage_vhusbd_config") is False:
            self.add_log(server.id, True, "Skipped VirtualHere server config management")
            return
        lines = [f"ServerName={self.options['server_name']}"]
        if self.options["use_ssl"]:
            lines.extend([
                f"SSLCert={remote_join(server, server.install_dir, 'certs', 'server.pem')}",
                f"SSLPort={self.options['vh_ssl_port']}",
            ])
        if self.options["use_client_certs"]:
            lines.extend([
                "SSLUseClientCerts=1",
                f"SSLCAFile={remote_join(server, server.install_dir, 'certs', 'setup-ca.pem')}",
            ])
        ssh.upload_text("\n".join(lines) + "\n", server_generated_config_path(server))
        self.add_log(server.id, True, "Wrote generated VirtualHere server config without touching config.ini")

    def write_client_config(self, ssh, client):
        server_address = f"{self.server.ip}:{self.options['vh_hub_port']}"
        paths = client_artifact_paths(client)
        vh_config = render_vh_client_config(
            ca_path=remote_join(client, client.install_dir, "certs", "ca.pem") if self.options["use_ssl"] else "",
            client_cert=paths["vh_client_pem"] if self.options["use_client_certs"] else "",
            manual_hub=server_address,
            ssl_port=self.options["vh_ssl_port"],
        )
        ssh.upload_text(vh_config, paths["vh_config"])
        self.add_log(client.id, True, f"Wrote VirtualHere client config for {server_address}")

    def install_server_services(self, ssh):
        server = self.server
        if server.os_type == "openwrt":
            controller_cmd = (
                f"pgrep -f {shlex.quote('controller.py .*--port ' + str(self.options['controller_port']))} >/dev/null 2>&1 || "
                f"(cd {shlex.quote(server.install_dir)} && "
                f"nohup python3 controller.py --host {shlex.quote(server.ip)} --port {self.options['controller_port']} "
                f">/tmp/vh-control-web.log 2>&1 &)"
            )
            rc_block = rc_local_block([controller_cmd])
            ssh.upload_text(rc_block, "/tmp/vh-control-rc.block")
            ssh.run_linux("""
            touch /etc/rc.local
            chmod +x /etc/rc.local
            awk '
              /# BEGIN VH-CONTROL/ {skip=1; next}
              /# END VH-CONTROL/ {skip=0; next}
              skip != 1 {print}
            ' /etc/rc.local > /tmp/rc.local.clean
            awk '
              /^exit 0/ {
                while ((getline line < "/tmp/vh-control-rc.block") > 0) print line
                close("/tmp/vh-control-rc.block")
                inserted=1
                print
                next
              }
              {print}
              END {
                if (!inserted) {
                  while ((getline line < "/tmp/vh-control-rc.block") > 0) print line
                  print "exit 0"
                }
              }
            ' /tmp/rc.local.clean > /tmp/rc.local.new
            mv /tmp/rc.local.new /etc/rc.local
            rm -f /tmp/rc.local.clean /tmp/vh-control-rc.block
            chmod +x /etc/rc.local
            """ + "\n" + controller_cmd, sudo=True)
        elif server.os_type == "linux":
            vh_service = f"""
            [Unit]
            Description=VirtualHere USB Server
            After=network-online.target
            Wants=network-online.target

            [Service]
            Type=forking
            WorkingDirectory={server.install_dir}
            ExecStart={server.remote_binary} -b -c {server_generated_config_path(server)}
            Restart=always
            RestartSec=3

            [Install]
            WantedBy=multi-user.target
            """
            web_service = f"""
            [Unit]
            Description=VirtualHere Control Web
            After=network-online.target virtualhere-server.service
            Wants=network-online.target

            [Service]
            Type=simple
            WorkingDirectory={server.install_dir}
            ExecStart=/usr/bin/python3 {remote_join(server, server.install_dir, "controller.py")} --host 0.0.0.0 --port {self.options["controller_port"]}
            Restart=always
            RestartSec=3

            [Install]
            WantedBy=multi-user.target
            """
            ssh.upload_text(systemd_text(vh_service), "/tmp/virtualhere-server.service")
            ssh.upload_text(systemd_text(web_service), "/tmp/vh-control-web.service")
            ssh.run_linux("mv /tmp/virtualhere-server.service /etc/systemd/system/virtualhere-server.service\nmv /tmp/vh-control-web.service /etc/systemd/system/vh-control-web.service\nsystemctl daemon-reload\nsystemctl enable --now virtualhere-server.service vh-control-web.service", sudo=True)
        else:
            ps = f"""
            $ErrorActionPreference = 'Stop'
            & '{ps_quote(server.remote_binary)}' -b
            $python = (Get-Command python).Source
            $action = New-ScheduledTaskAction -Execute $python -Argument '{ps_quote(remote_join(server, server.install_dir, "controller.py"))} --host 0.0.0.0 --port {self.options["controller_port"]}'
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
            Register-ScheduledTask -TaskName 'VHControlWeb' -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force
            Start-ScheduledTask -TaskName 'VHControlWeb'
            """
            ssh.run_windows(ps)
        self.add_log(server.id, True, "Enabled server/controller autostart")

    def install_client_services(self, ssh, client):
        paths = client_artifact_paths(client)
        vh_config = paths["vh_config"]
        agent_config = paths["agent_config"]
        vh_log = remote_join(client, client.install_dir, "logs", f"{client.artifact_stem}-vhclient.log")
        agent_log = remote_join(client, client.install_dir, "logs", f"{client.artifact_stem}-agent.log")
        ssh.mkdir(remote_join(client, client.install_dir, "logs"))
        if client.os_type == "linux":
            client_service = f"""
            [Unit]
            Description=VirtualHere USB Client
            After=network-online.target
            Wants=network-online.target

            [Service]
            Type=forking
            WorkingDirectory={client.install_dir}
            ExecStartPre=/bin/sh -c 'modprobe vhci-hcd 2>/dev/null || modprobe vhci_hcd 2>/dev/null || true'
            ExecStart={client.remote_binary} --config={vh_config} -n -l={vh_log}
            Restart=always
            RestartSec=3

            [Install]
            WantedBy=multi-user.target
            """
            agent_service = f"""
            [Unit]
            Description=VirtualHere Control Agent
            After=network-online.target virtualhere-client.service
            Wants=network-online.target

            [Service]
            Type=simple
            WorkingDirectory={client.install_dir}
            ExecStart=/usr/bin/python3 {remote_join(client, client.install_dir, "agent.py")} --config {agent_config}
            Restart=always
            RestartSec=3
            StandardOutput=append:{agent_log}
            StandardError=append:{agent_log}

            [Install]
            WantedBy=multi-user.target
            """
            ssh.upload_text(systemd_text(client_service), "/tmp/virtualhere-client.service")
            ssh.upload_text(systemd_text(agent_service), "/tmp/vh-control-agent.service")
            ssh.run_linux("mv /tmp/virtualhere-client.service /etc/systemd/system/virtualhere-client.service\nmv /tmp/vh-control-agent.service /etc/systemd/system/vh-control-agent.service\nsystemctl daemon-reload\nsystemctl enable --now virtualhere-client.service vh-control-agent.service\nsleep 2\nsystemctl is-active --quiet virtualhere-client.service\nsystemctl is-active --quiet vh-control-agent.service", sudo=True)
            ssh.run_linux(f"{shlex.quote(client.remote_binary)} -t {shlex.quote('MANUAL HUB ADD,' + self.server.ip + ':' + str(self.options['vh_hub_port']))}", sudo=False, check=False)
            self.check_agent_health(ssh, client)
        else:
            server_port = self.options["vh_hub_port"]
            agent_cmd = remote_join(client, client.install_dir, "scripts", f"{client.artifact_stem}-agent.cmd")
            vh_task_name = f"VirtualHereClient-{client.id}"
            ps = f"""
            $ErrorActionPreference = 'Stop'
            $binary = '{ps_quote(client.remote_binary)}'
            $vhConfig = '{ps_quote(vh_config)}'
            $agentScript = '{ps_quote(remote_join(client, client.install_dir, "agent.py"))}'
            $agentConfig = '{ps_quote(agent_config)}'
            $agentLog = '{ps_quote(agent_log)}'
            $agentCmd = '{ps_quote(agent_cmd)}'
            $installDir = '{ps_quote(client.install_dir)}'
            $vhTaskName = '{ps_quote(vh_task_name)}'
            $taskName = 'VHControlAgent-{ps_quote(client.id)}'
            $taskUser = '{ps_quote(client.login)}'
            $taskPassword = '{ps_quote(client.password)}'
            $python = $null
            $pythonCandidates = @()
            $pythonCandidates += Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Python') -Filter python.exe -Recurse -ErrorAction SilentlyContinue | ForEach-Object {{ $_.FullName }}
            $pythonCandidates += Get-ChildItem -Path "$env:ProgramFiles\\Python*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {{ Join-Path $_.FullName 'python.exe' }}
            $pythonCandidates += Get-ChildItem -Path "${{env:ProgramFiles(x86)}}\\Python*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {{ Join-Path $_.FullName 'python.exe' }}
            $pythonCandidates += Get-Command python -All -ErrorAction SilentlyContinue | ForEach-Object {{ $_.Source }}
            foreach ($candidate in $pythonCandidates) {{
              if ($candidate -and $candidate -notlike '*\\WindowsApps\\*' -and (Test-Path -LiteralPath $candidate)) {{
                $python = $candidate
                break
              }}
            }}
            if (-not $python) {{
              throw "No usable Python executable found. The WindowsApps python alias cannot run as a scheduled task."
            }}
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $agentLog) | Out-Null
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $agentCmd) | Out-Null
            Remove-Item -LiteralPath $agentLog -Force -ErrorAction SilentlyContinue
            $cmdBody = @"
@echo off
cd /d "$installDir"
echo [%date% %time%] Starting VH Control Agent with "$python" >> "$agentLog"
"$python" "$agentScript" --config "$agentConfig" >> "$agentLog" 2>&1
"@
            Set-Content -LiteralPath $agentCmd -Value $cmdBody -Encoding ASCII
            $arguments = '/c "' + $agentCmd + '"'
            $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $arguments -WorkingDirectory $installDir
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
            if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {{
              Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
              Start-Sleep -Seconds 1
            }}
            if (Get-ScheduledTask -TaskName $vhTaskName -ErrorAction SilentlyContinue) {{
              Stop-ScheduledTask -TaskName $vhTaskName -ErrorAction SilentlyContinue
              Start-Sleep -Seconds 1
            }}
            $vhArguments = '"--config=' + $vhConfig + '" -b'
            $vhAction = New-ScheduledTaskAction -Execute $binary -Argument $vhArguments -WorkingDirectory $installDir
            $vhTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
            Register-ScheduledTask -TaskName $vhTaskName -Action $vhAction -Trigger $vhTrigger -Settings $settings -User $taskUser -Password $taskPassword -RunLevel Highest -Force
            Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User $taskUser -Password $taskPassword -RunLevel Highest -Force
            if (Get-Command New-NetFirewallRule -ErrorAction SilentlyContinue) {{
              New-NetFirewallRule -DisplayName "VH Control Agent {ps_quote(client.id)}" -Direction Inbound -Action Allow -Protocol TCP -LocalPort {client.agent_port} -ErrorAction SilentlyContinue | Out-Null
            }}
            Start-ScheduledTask -TaskName $vhTaskName
            Start-ScheduledTask -TaskName $taskName
            Start-Sleep -Seconds 2
            & $binary "--config=$vhConfig" -t 'MANUAL HUB ADD,{self.server.ip}:{server_port}'
            if (-not (Get-ScheduledTask -TaskName $vhTaskName -ErrorAction SilentlyContinue)) {{
              throw "Scheduled task $vhTaskName was not created"
            }}
            if (-not (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {{
              throw "Scheduled task $taskName was not created"
            }}
            $healthCode = "import ssl, urllib.request; ctx=ssl._create_unverified_context(); urllib.request.urlopen('https://127.0.0.1:{client.agent_port}/health', context=ctx, timeout=4).read()"
            $healthy = $false
            $lastHealthError = $null
            $deadline = (Get-Date).AddSeconds(45)
            while ((Get-Date) -lt $deadline) {{
              $healthOutput = & $python -c $healthCode 2>&1
              if ($LASTEXITCODE -eq 0) {{
                $healthy = $true
                break
              }}
              $lastHealthError = ($healthOutput | Out-String).Trim()
              Start-Sleep -Seconds 2
            }}
            if (-not $healthy) {{
              $taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
              $taskState = (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue).State
              $logTail = ''
              if (Test-Path -LiteralPath $agentLog) {{
                $logTail = (Get-Content -LiteralPath $agentLog -Tail 40 -ErrorAction SilentlyContinue | Out-String)
              }}
              throw "Agent health check failed on https://127.0.0.1:{client.agent_port}/health: $lastHealthError; task=$taskName state=$taskState lastResult=$($taskInfo.LastTaskResult); log=$logTail"
            }}
            """
            ssh.run_windows(ps)
        self.add_log(client.id, True, "Enabled client/agent autostart")

    def check_agent_health(self, ssh, client):
        url = f"https://127.0.0.1:{client.agent_port}/health"
        ssh.run_linux(f"""
        if command -v curl >/dev/null 2>&1; then
          curl -fsSk {shlex.quote(url)} >/dev/null
        else
          wget --no-check-certificate -qO- {shlex.quote(url)} >/dev/null
        fi
        """, sudo=False)
        self.add_log(client.id, True, f"Agent health check passed on {url}")

    def write_report(self):
        redacted = {
            "run_id": self.run_id,
            "server": public_target(self.server),
            "clients": [public_target(client) for client in self.clients],
            "options": public_options(self.options),
            "log": self.log,
        }
        save_json(self.report_path, redacted)


class CertificateFactory:
    def __init__(self, cert_dir):
        self.cert_dir = Path(cert_dir)
        self.cert_dir.mkdir(parents=True, exist_ok=True)
        self.ca_key = None
        self.ca_cert = None

    def create_ca(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        self.ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VirtualHere Control CA")])
        self.ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(self.ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5))
            .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )
        pem = self.ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        (self.cert_dir / "ca.pem").write_text(pem, encoding="utf-8")
        return pem

    def create_server_cert(self, target, common_name):
        leaf = self.create_leaf_cert(target, common_name)
        return leaf["key"] + leaf["cert"]

    def create_client_pem(self, target):
        leaf = self.create_leaf_cert(target, f"{target.id}-vh-client")
        return leaf["key"] + leaf["cert"]

    def create_leaf_cert(self, target, common_name):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID

        if not self.ca_key or not self.ca_cert:
            raise SetupError("CA must be created before leaf certificates")
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        names = san_names(target, x509)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self.ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5))
            .not_valid_after(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .sign(self.ca_key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode("ascii")
        cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
        return {"key": key_pem, "cert": cert_pem}


class SSHSession:
    def __init__(self, target):
        self.target = target
        self.client = None
        self.sftp = None

    def __enter__(self):
        import paramiko

        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.client.connect(
                hostname=self.target.ip,
                port=self.target.ssh_port,
                username=self.target.login,
                password=self.target.password,
                timeout=15,
                banner_timeout=15,
                auth_timeout=15,
            )
            self.sftp = self.client.open_sftp()
        except socket.timeout as exc:
            raise SetupError(f"{self.target.id} SSH connection to {self.target.ip}:{self.target.ssh_port} timed out") from exc
        except Exception as exc:
            raise SetupError(f"{self.target.id} SSH connection to {self.target.ip}:{self.target.ssh_port} failed: {exc}") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()

    def mkdir(self, path):
        if self.target.os_type == "windows":
            self.run_windows(f"New-Item -ItemType Directory -Force -Path '{ps_quote(path)}' | Out-Null")
            return
        self.run_linux(f"mkdir -p {shlex.quote(path)}", sudo=True)

    def upload_file(self, local_path, remote_path):
        self.upload_bytes(Path(local_path).read_bytes(), remote_path)

    def upload_text(self, text, remote_path):
        data = text.encode("utf-8")
        self.upload_bytes(data, remote_path)

    def upload_bytes(self, data, remote_path):
        self.mkdir(remote_dirname(self.target, remote_path))
        if is_unix_target(self.target):
            tmp_path = f"/tmp/vhsetup-{secrets.token_hex(8)}"
            with self.sftp.file(tmp_path, "wb") as handle:
                handle.write(data)
            self.run_linux(f"mv {shlex.quote(tmp_path)} {shlex.quote(remote_path)}", sudo=True)
            return
        with self.sftp.file(remote_path, "wb") as handle:
            handle.write(data)

    def run_linux(self, script, sudo=False, check=True):
        script_text = "set -eu\n" + textwrap.dedent(script).strip() + "\n"
        if sudo and self.target.os_type != "openwrt" and self.target.login != "root":
            quoted = shlex.quote(script_text)
            command = f"printf '%s\\n' {shlex.quote(self.target.password)} | sudo -S sh -c {quoted}"
        else:
            command = f"sh -c {shlex.quote(script_text)}"
        return self.run(command, check=check)

    def run_windows(self, script, check=True):
        command_text = "$ProgressPreference='SilentlyContinue';$InformationPreference='SilentlyContinue';" + textwrap.dedent(script)
        encoded = base64.b64encode(command_text.encode("utf-16le")).decode("ascii")
        script_path = ""
        if len(encoded) > 6000 and self.sftp:
            script_path = f"C:/Users/Public/vhsetup-{secrets.token_hex(8)}.ps1"
            with self.sftp.file(script_path, "wb") as handle:
                handle.write(command_text.encode("utf-8-sig"))
            cmd_path = script_path.replace("/", "\\")
            command = f'cmd.exe /c powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{cmd_path}"'
        else:
            command = f"powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"
        try:
            return self.run(command, check=check)
        finally:
            if script_path and self.sftp:
                try:
                    self.sftp.remove(script_path)
                except OSError:
                    pass

    def run(self, command, check=True):
        try:
            stdin, stdout, stderr = self.client.exec_command(command, timeout=120)
            code = stdout.channel.recv_exit_status()
            out = clean_remote_text(stdout.read().decode("utf-8", errors="replace"))
            err = clean_remote_text(stderr.read().decode("utf-8", errors="replace"))
        except socket.timeout as exc:
            raise SetupError(f"{self.target.id} command timed out after 120 seconds") from exc
        if check and code != 0:
            raise SetupError(f"{self.target.id} command failed ({code}): {err.strip() or out.strip()}")
        return {"code": code, "stdout": out, "stderr": err}


def render_vh_client_config(ca_path, client_cert, manual_hub, ssl_port=DEFAULT_VH_SSL_PORT):
    lines = ["[General]", "AutoFind=0"]
    if ca_path:
        lines.append(f"SSLCAFile={ca_path}")
        lines.append(f"SSLPort={ssl_port}")
    if client_cert:
        lines.append(f"SSLClientCert={client_cert}")
    lines.extend(["", "[Settings]", f"ManualHubs={manual_hub}", ""])
    return "\n".join(lines)


def load_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def sanitize_setup_config(data):
    config = dict(data)
    server = dict(config.get("server") or {})
    server["password"] = ""
    config["server"] = server
    clients = []
    for client in config.get("clients") or []:
        item = dict(client)
        item["password"] = ""
        clients.append(item)
    config["clients"] = clients
    config["saved_at"] = int(time.time())
    config["version"] = 1
    config.pop("dry_run", None)
    config.pop("dryRun", None)
    return config


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def clean_remote_text(value):
    if not value.startswith("#< CLIXML"):
        return value
    messages = re.findall(r'<S S="(?:Error|Warning|Verbose|Information)">(.*?)</S>', value, flags=re.DOTALL)
    if not messages:
        return value
    cleaned = []
    for message in messages:
        text = html.unescape(message)
        text = text.replace("_x000D__x000A_", "\n").replace("_x000D_", "\r").replace("_x000A_", "\n")
        cleaned.append(text)
    return "\n".join(cleaned)


def san_names(target, x509_module):
    names = []
    seen = set()
    for value in (target.id, target.name, target.ip):
        value = str(value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        try:
            names.append(x509_module.IPAddress(ipaddress.ip_address(value)))
            continue
        except ValueError:
            pass
        if re.fullmatch(r"[A-Za-z0-9.-]+", value):
            names.append(x509_module.DNSName(value))
    if not names:
        names.append(x509_module.DNSName(target.id or "virtualhere"))
    return names


def public_target(target):
    return {
        "id": target.id,
        "name": target.name,
        "role": target.role,
        "os": target.os_type,
        "arch": target.arch,
        "os_variant": target.os_variant,
        "ip": target.ip,
        "login": target.login,
        "ssh_port": target.ssh_port,
        "install_dir": target.install_dir,
        "agent_port": target.agent_port,
        "binary": target.binary_name,
    }


def public_options(options):
    return dict(options)


def make_artifact_stem(target):
    date_code = dt.datetime.now().strftime("%m%d%y")
    target_id = re.sub(r"[^A-Za-z0-9]+", "", target.id).upper() or "TARGET"
    return f"{date_code}-{target_id}-{os_code(target.os_type)}"


def os_code(os_type):
    codes = {
        "windows": "WIN",
        "linux": "LIN",
        "openwrt": "OWRT",
    }
    return codes.get(os_type, re.sub(r"[^A-Za-z0-9]+", "", os_type).upper()[:4] or "OS")


def client_artifact_paths(client):
    stem = client.artifact_stem or make_artifact_stem(client)
    return {
        "agent_config": remote_join(client, client.install_dir, "config", f"{stem}-agent.json"),
        "agent_cert": remote_join(client, client.install_dir, "certs", f"{stem}-agent.crt"),
        "agent_key": remote_join(client, client.install_dir, "certs", f"{stem}-agent.key"),
        "vh_client_pem": remote_join(client, client.install_dir, "certs", f"{stem}-vh-client.pem"),
        "vh_config": remote_join(client, client.install_dir, "config", f"{stem}-vhui.ini"),
    }


def server_generated_config_path(server):
    return remote_join(server, server.install_dir, "config", "vh-control-vhusbd.ini")


def is_unix_target(target):
    return target.os_type in ("linux", "openwrt")


def server_service_label(server):
    if server.os_type == "openwrt":
        return "Enable VirtualHere server and controller autostart through /etc/rc.local"
    return "Enable VirtualHere server and controller autostart"


def rc_local_block(commands):
    lines = ["", "# BEGIN VH-CONTROL"]
    lines.extend(commands)
    lines.append("# END VH-CONTROL")
    return "\n".join(lines) + "\n"


def norm(value):
    return str(value or "").strip().lower().replace(" ", "-")


def default_install_dir(os_type):
    return "C:/vh-control" if os_type == "windows" else "/opt/vh-control"


def remote_join(target, *parts):
    clean = [str(part).strip("\\/") for part in parts if str(part)]
    if target.os_type == "windows":
        if parts and str(parts[0]).startswith(("C:/", "C:\\")):
            root = str(parts[0]).replace("\\", "/").rstrip("/")
            rest = [str(part).strip("\\/") for part in parts[1:]]
            return "/".join([root, *rest])
        return "/".join(clean)
    if parts and str(parts[0]).startswith("/"):
        return posixpath.join(str(parts[0]), *[str(part).strip("/") for part in parts[1:]])
    return posixpath.join(*clean)


def remote_dirname(target, path):
    normalized = path.replace("\\", "/")
    dirname = normalized.rsplit("/", 1)[0]
    if target.os_type == "windows":
        return dirname
    return dirname or "/"


def ps_quote(value):
    return str(value).replace("'", "''")


def systemd_text(value):
    return textwrap.dedent(value).strip() + "\n"

class SetupWebHandler(SimpleHTTPRequestHandler):
    server_version = "VirtualHereSetup/0.1"
    allowed_static = {
        "setup.html",
        "setup.js",
        "styles.css",
        "virtualhere-icon.png",
        "cursor-hand.png",
        "cursor-usb.png",
    }

    def translate_path(self, path):
        parsed = urlparse(path)
        clean = parsed.path.lstrip("/") or "setup.html"
        if clean not in self.allowed_static:
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
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/setup/capabilities":
            self.send_json(200, {"ok": True, "targets": supported_targets()})
            return
        if parsed.path == "/api/setup/config":
            exists = SETUP_CONFIG_FILE.exists()
            self.send_json(200, {
                "ok": True,
                "exists": exists,
                "path": str(SETUP_CONFIG_FILE),
                "config": load_json(SETUP_CONFIG_FILE, {}) if exists else {},
            })
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/setup/plan":
            try:
                self.send_json(200, build_plan(self.read_json_body()))
            except SetupError as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/setup/run":
            try:
                self.send_json(200, run_setup(self.read_json_body()))
            except SetupError as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
            return
        if parsed.path == "/api/setup/config":
            try:
                config = sanitize_setup_config(self.read_json_body())
                save_json(SETUP_CONFIG_FILE, config)
                self.send_json(200, {"ok": True, "path": str(SETUP_CONFIG_FILE), "config": config})
            except Exception as exc:
                self.send_json(500, {"ok": False, "error": str(exc)})
            return
        self.send_json(404, {"ok": False, "error": "unknown endpoint"})


def serve_setup(hostname, port):
    server = ThreadingHTTPServer((hostname, port), SetupWebHandler)
    print(f"VirtualHere Setup listening on http://{hostname}:{port}")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="VirtualHere guided setup web interface")
    parser.add_argument("--hostname", "--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    serve_setup(args.hostname, args.port)


if __name__ == "__main__":
    main()

