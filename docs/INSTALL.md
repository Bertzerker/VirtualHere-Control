# Installation und Betrieb

## 0. Gefuehrter Installer

Der Controller enthaelt eine Setup-Seite unter `/setup.html`. Sie fragt folgende Daten ab:

- Server: OS, Architektur, IP/Host, SSH-Port, Login, Passwort und Installationsordner
- Clients: ID, Name, OS, Architektur, IP/Host, SSH-Port, Login, Passwort, Installationsordner und Agent-Port
- Optionen: Controller-Port, Agent-Port, VirtualHere-SSL-Port, SSL und optionale Client-Zertifikate

Vor der Ausfuehrung:

```powershell
python -m pip install -r requirements.txt
python controller.py --host 0.0.0.0 --port 8080
```

Dann `http://SERVER-IP:8080/setup.html` oeffnen.

Die Buttons `Config speichern` und `Config laden` schreiben bzw. lesen `config/setup-config.json` auf dem Controller. Gespeichert werden Server, Clients, Ports, OS/Architektur und Ordner. SSH-Passwoerter werden nicht gespeichert und muessen vor `Plan`, `Testlauf` oder `Start` neu eingegeben werden.

Unterstuetzte automatische Targets:

- Server: Linux `x86_64`, Linux `armhf`, Linux `aarch64`, OpenWrt `armhf`, OpenWrt `aarch64`, Windows `x86_64`
- Client: Linux `x86_64`, Linux `armhf`, Linux `aarch64`, Windows `x86_64`

Der Installer nutzt SSH/SFTP mit Passwort-Login. Linux-Ziele brauchen einen Benutzer mit `sudo`. OpenWrt-Ziele werden als root-artige SSH-Ziele behandelt und verwenden `/etc/rc.local` fuer Autostart. Windows-Ziele brauchen OpenSSH Server und Administratorrechte fuer Dienste/geplante Aufgaben.

Empfohlen: Den gefuehrten Setup-Lauf von Windows oder CachyOS starten. OpenWrt kann den fertigen Controller ausfuehren, ist aber kein guter Ort fuer den Installer, weil `paramiko`/`cryptography` dort oft nicht sauber per `pip` installierbar sind. Wenn der Installationsordner auf OpenWrt `/vh-server` ist, lautet der absolute Pfad:

```sh
python -m pip install -r /vh-server/requirements.txt
```

Aus `/` heraus funktioniert `python -m pip install -r requirements.txt` nicht, weil dort keine `requirements.txt` liegt.

Der Lauf erstellt oder aktualisiert:

- VirtualHere Server/Client Binary, falls im Zielordner nicht vorhanden
- optional eine eigene generierte VirtualHere-Server-Konfiguration unter `config/vh-control-vhusbd.ini`
- eindeutig benannte Agent-Konfiguration auf jedem Client, z.B. `config/081126-MN56-WIN-agent.json`
- `config/clients.json` fuer den Controller
- CA, Server-Zertifikat, eindeutig benannte Agent-Zertifikate und optional VirtualHere-Client-Zertifikate
- systemd Services auf Linux
- `/etc/rc.local` Autostart-Block auf OpenWrt
- VirtualHere Windows-Service und Windows-Aufgabenplanung fuer Agent/Controller auf Windows

Passwoerter werden nur fuer die SSH-Verbindung verwendet und nicht in die JSON-Konfigurationen oder Setup-Reports geschrieben.

Wichtig: Die Datei `config.ini` im VirtualHere-Server-Ordner gehoert VirtualHere selbst und kann Lizenzinformationen enthalten. Der Installer schreibt dort nicht hinein. Wenn das Setup eine eigene Server-Konfiguration braucht, nutzt es `config/vh-control-vhusbd.ini`.

## 1. VirtualHere Server auf ARM64-Linux

Fuer einen ARM64-Server wird der ARM64-Build des VirtualHere USB Servers verwendet. Der Server laeuft standardmaessig ueber TCP 7575, mit SSL ueber 7574.

Empfohlene `config.ini`-Werte auf dem VirtualHere Server:

```ini
ServerName=usb-server
SSLCert=/etc/virtualhere/server.pem
SSLPort=7574
SSLUseClientCerts=1
SSLCAFile=/etc/virtualhere/ca.pem
```

Ohne Client-Zertifikate:

```ini
ServerName=usb-server
SSLCert=/etc/virtualhere/server.pem
SSLPort=7574
```

## 2. Zertifikate

Eigene CA:

```bash
openssl genrsa -out ca.key 2048
openssl req -new -sha256 -x509 -days 3650 -key ca.key -out ca.crt
openssl x509 -in ca.crt -out ca.pem -outform PEM
```

Server-Zertifikat:

```bash
openssl genrsa -out server.key 2048
openssl req -new -key server.key -out server.csr
openssl x509 -req -sha256 -days 3650 -in server.csr -CA ca.crt -CAkey ca.key -set_serial 02 -out server.crt
cat server.key server.crt > server.pem
```

Agent-Zertifikat fuer den Windows Control-Agent:

```bash
mkdir -p /vh-server/certs
cd /vh-server/certs

openssl genrsa -out ca.key 2048
openssl req -new -x509 -days 3650 -key ca.key -out ca.pem -subj "/CN=VH Control CA"

cat > windows-pc.ext <<EOF
subjectAltName = DNS:windows-pc, DNS:windows-pc.local, IP:192.168.8.125
EOF

openssl genrsa -out windows-pc.key 2048
openssl req -new -key windows-pc.key -out windows-pc.csr -subj "/CN=windows-pc"
openssl x509 -req -days 3650 -in windows-pc.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out windows-pc.crt -extfile windows-pc.ext
```

Wichtig: Das Agent-Zertifikat muss Subject Alternative Name Eintraege fuer die Adresse enthalten, ueber die der Controller oder PowerShell den Agent aufruft. Bei `https://192.168.8.125:9443` muss also `IP:192.168.8.125` enthalten sein.

Pruefen:

```bash
openssl x509 -in windows-pc.crt -noout -subject -issuer -ext subjectAltName
```

Separates VirtualHere-Client-Zertifikat:

```bash
openssl genrsa -out vh-client.key 2048
openssl req -new -key vh-client.key -out vh-client.csr
openssl x509 -req -days 3650 -in vh-client.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out vh-client.crt
cat vh-client.key vh-client.crt > vh-client.pem
```

Wichtig: Wenn der VirtualHere Client als Service laeuft, darf die PEM-Datei kein Passwort benoetigen.

## 3. VirtualHere Client als Service

### Windows

VirtualHere Client herunterladen, dann als Administrator:

```powershell
C:\vh-control\vhui64.exe -i
```

Danach kann die laufende Service-Instanz lokal gesteuert werden:

```powershell
C:\vh-control\vhui64.exe -t "LIST" -r=C:\vh-control\list-result.txt
Get-Content C:\vh-control\list-result.txt

C:\vh-control\vhui64.exe -t "USE,usb-server.114" -r=C:\vh-control\use-result.txt
Get-Content C:\vh-control\use-result.txt

C:\vh-control\vhui64.exe -t "STOP USING,usb-server.114" -r=C:\vh-control\stop-result.txt
Get-Content C:\vh-control\stop-result.txt
```

Wichtig: Ohne `-r=...` zeigt der Windows-GUI-Client die API-Antworten als Popup-Fenster an. Der Control-Agent nutzt unter Windows deshalb immer den `-r`-Modus.

Falls nach einem Verschieben oder Umbenennen des Ordners `StartService failed, error 2` erscheint, zeigt der Windows-Service noch auf den alten `vhui64.exe`-Pfad. Dann als Administrator reparieren:

```powershell
sc.exe query "VirtualHere Client USB Sharing"
sc.exe qc "VirtualHere Client USB Sharing"

C:\vh-control\vhui64.exe -u
Restart-Computer

C:\vh-control\vhui64.exe -i
sc.exe start "VirtualHere Client USB Sharing"
sc.exe qc "VirtualHere Client USB Sharing"
```

Wenn `-u` nicht mehr funktioniert, weil der alte Service-Pfad kaputt ist:

```powershell
sc.exe stop "VirtualHere Client USB Sharing"
sc.exe delete "VirtualHere Client USB Sharing"
Restart-Computer
C:\vh-control\vhui64.exe -i
```

### Linux x86_64

Console-Client verwenden und als Daemon starten:

```bash
chmod +x ./vhclientx86_64
sudo ./vhclientx86_64 -n
./vhclientx86_64 -t "LIST"
```

Systemd-Beispiel fuer den VirtualHere Client:

```ini
[Unit]
Description=VirtualHere USB Client
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
ExecStart=/home/robert/vh-control/vhclientx86_64 -n
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Der grafische Linux-Client `vhuit64` ist fuer die Desktop-Bedienung gedacht. Fuer einen stabilen Hintergrundbetrieb und fuer die Agent-Steuerung sollte der Console-Client `vhclientx86_64` verwendet werden.

### Raspberry Pi Zero 2 W

Der Pi Zero 2 W laeuft meist mit 32-bit Raspberry Pi OS. Dann den `armhf` Console-Client verwenden. Bei 64-bit OS den `aarch64` Console-Client verwenden.

```bash
sudo ./vhclientarmhf -n
./vhclientarmhf -t "LIST"
```

## 4. VirtualHere Client SSL konfigurieren

Auf jedem Client die CA-Datei in der VirtualHere-Konfiguration setzen:

- Windows: `C:\Users\<Username>\AppData\Roaming\vhui.ini`
- Linux: `~/.vhui`

Beispiel:

```ini
[General]
SSLClientCert=/etc/virtualhere/client.pem
```

Die CA-Datei wird im VirtualHere Client unter `USB Hubs -> Advanced Settings -> SSL -> Certificate Authority File` eingetragen. Ohne Auto-Find den Hub manuell mit Port 7574 eintragen.

## 5. Control-Agent als Service

### Linux systemd

`/etc/systemd/system/vh-control-agent.service`:

```ini
[Unit]
Description=VirtualHere Control Agent
After=network-online.target virtualhereclient.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vh-control
ExecStart=/usr/bin/python3 /opt/vh-control/agent.py --config /opt/vh-control/config/agent.json
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Aktivieren:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vh-control-agent.service
```

### Windows

Ein normales Python-Skript kann nicht direkt mit `New-Service` als Windows-Service registriert werden. Windows erwartet bei `New-Service` einen servicefaehigen Prozess. Verwende deshalb entweder die Windows-Aufgabenplanung oder einen echten Service-Wrapper wie NSSM/WinSW.

Empfohlene Variante ohne Zusatztools: Aufgabenplanung als `SYSTEM`.

Python-Pfad ermitteln:

```powershell
(Get-Command python).Source
```

Geplante Aufgabe erstellen:

```powershell
$python = (Get-Command python).Source
$action = New-ScheduledTaskAction -Execute $python -Argument 'C:\vh-control\agent.py --config C:\vh-control\config\agent.json'
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0
Register-ScheduledTask -TaskName "VHControlAgent" -Action $action -Trigger $trigger -Settings $settings -User "SYSTEM" -RunLevel Highest
Start-ScheduledTask -TaskName "VHControlAgent"
```

Status pruefen:

```powershell
Get-ScheduledTask -TaskName "VHControlAgent"
Get-ScheduledTaskInfo -TaskName "VHControlAgent"
```

API-Test:

```powershell
Invoke-RestMethod https://192.168.8.125:9443/health
Invoke-RestMethod https://192.168.8.125:9443/api/state -Headers @{Authorization = "Bearer DEIN_TOKEN"}
```

## 6. Controller als Service auf dem ARM64-Server

`/etc/systemd/system/vh-control-web.service`:

```ini
[Unit]
Description=VirtualHere Control Webinterface
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vh-control
ExecStart=/usr/bin/python3 /opt/vh-control/controller.py --host 0.0.0.0 --port 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Aktivieren:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vh-control-web.service
```

## 7. Zuweisungslogik

Beim Verschieben eines USB-Geraets auf einen neuen Client:

1. Controller prueft die bisherige Zuordnung.
2. Falls belegt: alter Agent bekommt `STOP USING,<address>`.
3. Neuer Agent bekommt `USE,<address>`.
4. Controller speichert die neue Zuordnung in `config/assignments.json`.

Beim Freigeben:

1. Controller sucht den zugeordneten Client.
2. Agent bekommt `STOP USING,<address>`.
3. Zuordnung wird geloescht.
