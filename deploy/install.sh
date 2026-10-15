#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
image=cctv-recognition:local
config="$root/config.yaml"

if [ ! -f "$config" ]; then
  echo "Falta $config" >&2
  exit 1
fi

docker build --platform linux/amd64 -t "$image" "$root"

if command -v k3s >/dev/null 2>&1; then
  if [ "$(id -u)" -eq 0 ]; then
    docker save "$image" | k3s ctr images import -
  else
    docker save "$image" | sudo k3s ctr images import -
  fi
elif [ -n "${K3S_IMPORT_HOST:-}" ]; then
  docker save "$image" | ssh "$K3S_IMPORT_HOST" "sudo k3s ctr images import -"
else
  echo "La imagen quedó en Docker como $image."
  echo "En el NUC impórtala antes de aplicar el manifiesto:"
  echo "  docker save $image | ssh <nuc> 'sudo k3s ctr images import -'"
fi

kubectl create namespace cctv --dry-run=client -o yaml | kubectl apply -f -
kubectl -n cctv create secret generic cctv-config \
  --from-file=config.yaml="$config" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -k "$root/deploy/k8s"
kubectl -n cctv rollout status deployment/cctv-monitor --timeout=180s
