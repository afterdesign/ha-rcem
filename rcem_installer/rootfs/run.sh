#!/bin/bash
# Copies the bundled RCEm integration into Home Assistant's custom_components
# and asks for a restart when the files changed.
set -euo pipefail

SRC=/rcem
DEST=/homeassistant/custom_components/rcem
VERSION=$(jq -r .version "$SRC/manifest.json")

notify() {
  curl -fsS -X POST \
    -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg t "$1" --arg m "$2" '{title: $t, message: $m, notification_id: "rcem_installer"}')" \
    http://supervisor/core/api/services/persistent_notification/create >/dev/null \
    || echo "[rcem] Could not create notification"
}

if [ -d "$DEST" ] && diff -rq -x __pycache__ "$SRC" "$DEST" >/dev/null 2>&1; then
  echo "[rcem] Integration ${VERSION} already installed, nothing to do."
  exit 0
fi

echo "[rcem] Installing integration ${VERSION} to ${DEST}"
mkdir -p /homeassistant/custom_components
rm -rf "${DEST}.new"
cp -r "$SRC" "${DEST}.new"
rm -rf "$DEST"
mv "${DEST}.new" "$DEST"

notify "RCEm ${VERSION} zainstalowany" \
  "Zrestartuj Home Assistant, a potem dodaj integrację: [Dodaj RCEm](/config/integrations/dashboard/add?domain=rcem). Po aktualizacji wystarczy restart."
echo "[rcem] Done. Restart Home Assistant to load it."
