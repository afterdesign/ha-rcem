#!/bin/bash
# Bundle custom_components/rcem into the installer app and align versions.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf rcem_installer/rootfs/rcem
cp -r custom_components/rcem rcem_installer/rootfs/rcem
find rcem_installer/rootfs/rcem -name __pycache__ -prune -exec rm -rf {} +
VERSION=$(python3 -c 'import json;print(json.load(open("custom_components/rcem/manifest.json"))["version"])')
sed -i.bak -E "s/^version: .*/version: \"${VERSION}\"/" rcem_installer/config.yaml && rm rcem_installer/config.yaml.bak
echo "Installer synced to ${VERSION}"
