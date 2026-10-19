#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
image=cctv-recognition:local
config="$root/config.yaml"

if [ ! -f "$config" ]; then
  echo "Falta $config" >&2
  echo "Ese archivo no está en GitHub. Cópialo desde la Mac al clon del NUC." >&2
  exit 1
fi

kctl() {
  if command -v k3s >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then
      k3s kubectl "$@"
    else
      sudo k3s kubectl "$@"
    fi
    return
  fi
  if command -v kubectl >/dev/null 2>&1; then
    kubectl "$@"
    return
  fi
  echo "No hay k3s ni kubectl." >&2
  exit 1
}

imported=0
if command -v nerdctl >/dev/null 2>&1; then
  nerdctl --namespace k8s.io build --platform linux/amd64 -t "$image" "$root"
  imported=1
elif command -v docker >/dev/null 2>&1; then
  docker build --platform linux/amd64 -t "$image" "$root"
  if command -v k3s >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then
      docker save "$image" | k3s ctr images import -
    else
      docker save "$image" | sudo k3s ctr images import -
    fi
    imported=1
  fi
else
  echo "Hace falta Docker o nerdctl en el NUC para construir la imagen." >&2
  exit 1
fi

if [ "$imported" -eq 0 ]; then
  echo "La imagen quedó en Docker como $image y no se importó a k3s." >&2
  exit 1
fi

kctl create namespace cctv --dry-run=client -o yaml | kctl apply -f -
kctl -n cctv create secret generic cctv-config \
  --from-file=config.yaml="$config" \
  --dry-run=client -o yaml | kctl apply -f -
kctl apply -k "$root/deploy/k8s"
kctl -n cctv rollout restart deployment/cctv-monitor
kctl -n cctv rollout status deployment/cctv-monitor --timeout=180s
