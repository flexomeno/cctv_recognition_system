#!/bin/sh
# Un contenedor kerberos/agent por cámara. El agente libre solo tiene un RTSP.
# Las claves viven en dvr_chN/agent.env y telegram.env, fuera de git.
set -eu
cd "$(dirname "$0")"
for camera_id in dvr_ch1 dvr_ch2 dvr_ch3 dvr_ch4 dvr_ch5; do
  if [ ! -f "$PWD/$camera_id/config/config.json" ]; then
    echo "Falta $PWD/$camera_id/config/config.json" >&2
    exit 1
  fi
done
docker network create kerberos-net >/dev/null 2>&1 || true
docker rm -f kerberos-dvr-ch2 kerberos-dvr-ch2-telegram kerberos-telegram kerberos-panel >/dev/null 2>&1 || true

start_agent() {
  camera_id=$1
  port=$2
  cpus=$3
  memory=$4
  name=$(printf '%s' "$camera_id" | tr '_' '-')
  docker rm -f "kerberos-$name" >/dev/null 2>&1 || true
  docker run -d \
    --name "kerberos-$name" \
    --restart unless-stopped \
    --network kerberos-net \
    -p "$port:80" \
    --env-file "$PWD/$camera_id/agent.env" \
    -v "$PWD/$camera_id/recordings:/home/agent/data/recordings" \
    -v "$PWD/$camera_id/config:/home/agent/data/config" \
    --memory "$memory" \
    --cpus "$cpus" \
    kerberos/agent:latest
}

start_agent dvr_ch1 8091 0.25 256m
start_agent dvr_ch2 8092 0.25 256m
start_agent dvr_ch3 8093 0.25 256m
start_agent dvr_ch4 8094 0.60 384m
start_agent dvr_ch5 8095 0.25 256m

docker rm -f kerberos-telegram >/dev/null 2>&1 || true
docker run -d \
  --name kerberos-telegram \
  --restart unless-stopped \
  --network kerberos-net \
  --env-file "$PWD/telegram.env" \
  -v "$PWD/dvr_ch1/recordings:/recordings/dvr_ch1:ro" \
  -v "$PWD/dvr_ch2/recordings:/recordings/dvr_ch2:ro" \
  -v "$PWD/dvr_ch3/recordings:/recordings/dvr_ch3:ro" \
  -v "$PWD/dvr_ch4/recordings:/recordings/dvr_ch4:ro" \
  -v "$PWD/dvr_ch5/recordings:/recordings/dvr_ch5:ro" \
  -v "$PWD/sent:/sent" \
  -v "$PWD/sender.py:/sender.py:ro" \
  --memory 128m \
  python:3.11-slim python -u /sender.py
docker rm -f kerberos-panel >/dev/null 2>&1 || true
docker run -d \
  --name kerberos-panel \
  --restart unless-stopped \
  -p 8090:8090 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$PWD:/kerberos:ro" \
  -v "$PWD/panel.py:/panel.py:ro" \
  --memory 64m \
  python:3.11-slim python -u /panel.py
