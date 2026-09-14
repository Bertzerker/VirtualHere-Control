# VirtualHere USB Control

Webinterface zur zentralen Zuweisung von VirtualHere-USB-Geraeten an feste Clients.

## Architektur

VirtualHere stellt die Client-Steuerung lokal am Client bereit. Deshalb besteht diese Loesung aus zwei Teilen:

- `controller.py`: zentrales Webinterface und API auf dem ARM64-Linux-Server.
- `agent.py`: kleiner HTTPS/API-Service auf jedem Client. Der Agent ruft lokal den VirtualHere Client mit `-t` auf.

Damit werden keine hinterlegten Use-/Release-Skripte auf den Clients gebraucht. Das Webinterface sendet API-Befehle an eindeutig konfigurierte Clients.

## Client-Unterscheidung

Jeder Client bekommt eine feste `id` in `config/clients.json`, z.B.:

- `windows-pc`
- `linux-pc`
- `pi-zero-2w`

Fuer den produktiven Betrieb sollte jeder Agent ueber HTTPS laufen und einen eigenen API-Token verwenden. Optional koennen die Agent-Zertifikate ueber eine eigene CA validiert werden.

## Schnellstart

### Gefuehrter Installer

1. Setup-Abhaengigkeiten installieren:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Controller starten:

   ```powershell
   python controller.py --host 0.0.0.0 --port 8080
   ```

3. Im Browser oeffnen:

   ```text
   http://SERVER-IP:8080/setup.html
   ```

Der Installer fragt Server und Clients mit OS, Architektur, IP/Host, SSH-Login, SSH-Passwort und Zielordner ab. Danach erstellt er einen Plan oder fuehrt die Einrichtung per SSH aus. Passwoerter werden nicht in `config/clients.json` oder den Setup-Reports gespeichert.

Die Setup-Seite kann die Eingaben als `config/setup-config.json` speichern und wieder laden. SSH-Passwoerter werden dabei absichtlich nicht gespeichert und muessen vor einem neuen Lauf erneut eingetragen werden.

Beim Ausfuehren erzeugt der Installer API-Tokens, eine CA, Server-/Agent-Zertifikate und optional VirtualHere-Client-Zertifikate. Er schreibt eindeutig benannte Agent-Dateien wie `081126-MN56-WIN-agent.json`, `clients.json`, VirtualHere-Konfigurationen, laedt fehlende VirtualHere-Binaries herunter und richtet Autostart fuer Server, Client, Agent und Controller ein.

OpenWrt kann den fertigen Controller ausfuehren. Der eigentliche Setup-Lauf sollte aber von Windows oder CachyOS gestartet werden, weil die Setup-Abhaengigkeiten `paramiko` und `cryptography` auf OpenWrt oft nicht per `pip` verfuegbar sind.

Die VirtualHere-eigene `config.ini` im Server-Ordner wird vom Installer nicht beschrieben, weil sie Lizenzinformationen enthalten kann. Eine vom Installer erzeugte Server-Konfiguration liegt separat unter `config/vh-control-vhusbd.ini`.

## Manueller Schnellstart

1. Beispielkonfiguration kopieren:

   ```powershell
   Copy-Item config\clients.example.json config\clients.json
   Copy-Item config\agent.example.json config\agent.json
   ```

2. Auf jedem Client `config/agent.json` anpassen:

   - `client_id`
   - `client_name`
   - `vh_binary`
   - `api_token`
   - optional `tls_cert` und `tls_key`

3. Auf dem Server `config/clients.json` anpassen:

   - URL jedes Agents
   - passender Token
   - TLS-Verifikation

4. Agent auf jedem Client starten:

   ```bash
   python3 agent.py --config config/agent.json
   ```

5. Controller auf dem Server starten:

   ```bash
   python3 controller.py --host 0.0.0.0 --port 8080
   ```

6. Webinterface oeffnen:

   ```text
   http://SERVER-IP:8080
   ```

## Bedienung

- USB-Geraet per Drag and Drop auf einen Client ziehen.
- Auf Touch-Geraeten ein Geraet antippen und dann den Ziel-Client antippen.
- `Freigeben` loest die aktuelle Zuordnung.

## Wichtige VirtualHere-Befehle

Der Agent nutzt die offizielle Client-API:

- `LIST`
- `USE,<address>`
- `STOP USING,<address>`
- `GET CLIENT STATE`

Details zur Installation stehen in [docs/INSTALL.md](docs/INSTALL.md).
