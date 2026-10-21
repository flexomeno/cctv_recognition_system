#!/usr/bin/env python3
"""Panel local para encender o apagar cada canal de Kerberos.

El umbral y la zona se editan en el tablero de cada agente. Aquí solo se
arranca o se detiene el contenedor, y se muestran el usuario y la clave
de ese tablero.
"""
import json
import socket
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path("/kerberos")
HOST = "192.168.110.144"
CHANNELS = (
    ("dvr_ch1", "kerberos-dvr-ch1", 8091),
    ("dvr_ch2", "kerberos-dvr-ch2", 8092),
    ("dvr_ch3", "kerberos-dvr-ch3", 8093),
    ("dvr_ch4", "kerberos-dvr-ch4", 8094),
    ("dvr_ch5", "kerberos-dvr-ch5", 8095),
)
BY_ID = {camera_id: (name, port) for camera_id, name, port in CHANNELS}

PAGE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Canales Kerberos</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem; color: #1c1917; background: #fafaf9; }
  h1 { font-size: 1.4rem; }
  p { max-width: 40rem; }
  table { border-collapse: collapse; background: white; }
  th, td { border-bottom: 1px solid #e7e5e4; padding: 0.7rem 0.9rem; text-align: left; vertical-align: middle; }
  button, a.btn { font: inherit; border: 1px solid #44403c; background: white; border-radius: 6px; padding: 0.35rem 0.7rem; cursor: pointer; text-decoration: none; color: inherit; }
  button.stop { background: #44403c; color: white; }
  .secret { display: flex; gap: 0.4rem; align-items: center; }
  input { font: inherit; border: 1px solid #d6d3d1; border-radius: 6px; padding: 0.3rem 0.45rem; width: 11rem; }
  .eye { padding: 0.3rem 0.5rem; }
  .off { color: #b91c1c; }
  .on { color: #15803d; }
</style>
</head>
<body>
<h1>Canales Kerberos</h1>
<p>Apagar un canal detiene su contenedor. Al reiniciar el NUC sigue apagado. El umbral y la zona se cambian en el tablero de ese canal.</p>
<table>
  <thead>
    <tr><th>Canal</th><th>Estado</th><th></th><th>Tablero</th><th>Usuario</th><th>Clave</th></tr>
  </thead>
  <tbody id="rows"></tbody>
</table>
<script>
function secretField(value) {
  const wrap = document.createElement("span");
  wrap.className = "secret";
  const input = document.createElement("input");
  input.type = "password";
  input.readOnly = true;
  input.value = value;
  const button = document.createElement("button");
  button.className = "eye";
  button.type = "button";
  button.textContent = "👁";
  button.setAttribute("aria-label", "Mostrar u ocultar");
  button.addEventListener("click", () => {
    const hidden = input.type === "password";
    input.type = hidden ? "text" : "password";
    button.setAttribute("aria-pressed", hidden ? "true" : "false");
  });
  wrap.append(input, button);
  return wrap;
}
function paint(data) {
  const body = document.getElementById("rows");
  body.replaceChildren();
  for (const ch of data) {
    const row = document.createElement("tr");
    const name = document.createElement("td");
    name.textContent = ch.id;
    const state = document.createElement("td");
    state.className = ch.running ? "on" : "off";
    state.textContent = ch.running ? "activo" : "apagado";
    const action = document.createElement("td");
    const toggle = document.createElement("button");
    toggle.textContent = ch.running ? "Apagar" : "Encender";
    if (ch.running) toggle.className = "stop";
    toggle.addEventListener("click", async () => {
      toggle.disabled = true;
      await fetch("/api/channels/" + ch.id + "/" + (ch.running ? "stop" : "start"), {method: "POST"});
      paint(await (await fetch("/api/channels")).json());
    });
    action.append(toggle);
    const linkCell = document.createElement("td");
    const link = document.createElement("a");
    link.className = "btn";
    link.href = ch.url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = "Abrir";
    linkCell.append(link);
    const user = document.createElement("td");
    user.append(secretField(ch.username));
    const pass = document.createElement("td");
    pass.append(secretField(ch.password));
    row.append(name, state, action, linkCell, user, pass);
    body.append(row);
  }
}
fetch("/api/channels").then(response => response.json()).then(paint);
</script>
</body>
</html>
"""


class DockerSocket(HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")


def docker(method, path):
    conn = DockerSocket("localhost")
    conn.request(method, path)
    response = conn.getresponse()
    body = response.read()
    status = response.status
    conn.close()
    if status >= 300:
        raise RuntimeError("docker %s %s -> %s" % (method, path, status))
    if not body:
        return None
    return json.loads(body)


def read_login(camera_id):
    username = ""
    password = ""
    path = ROOT / camera_id / "agent.env"
    for line in path.read_text().splitlines():
        if line.startswith("AGENT_USERNAME="):
            username = line.split("=", 1)[1]
        elif line.startswith("AGENT_PASSWORD="):
            password = line.split("=", 1)[1]
    return username, password


def running_names():
    found = set()
    for item in docker("GET", "/containers/json"):
        for name in item.get("Names") or []:
            found.add(name.lstrip("/"))
    return found


def channels():
    active = running_names()
    rows = []
    for camera_id, container, port in CHANNELS:
        username, password = read_login(camera_id)
        rows.append(
            {
                "id": camera_id,
                "running": container in active,
                "url": "http://%s:%s" % (HOST, port),
                "username": username,
                "password": password,
            }
        )
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/channels":
            self.send_json(channels())
            return
        self.send_json({"error": "no encontrado"}, 404)

    def do_POST(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        if len(parts) != 4 or parts[0] != "api" or parts[1] != "channels":
            self.send_json({"error": "no encontrado"}, 404)
            return
        camera_id, action = parts[2], parts[3]
        if camera_id not in BY_ID or action not in ("start", "stop"):
            self.send_json({"error": "canal desconocido"}, 404)
            return
        container = BY_ID[camera_id][0]
        docker("POST", "/containers/%s/%s?t=10" % (container, action))
        self.send_json({"id": camera_id, "running": action == "start"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
