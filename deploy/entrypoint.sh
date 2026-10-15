#!/bin/sh
set -eu

mkdir -p /var/cctv/data /var/cctv/models
ln -sfn /var/cctv/data /app/data
ln -sfn /var/cctv/models /app/models

exec python -m app --config /app/config.yaml
