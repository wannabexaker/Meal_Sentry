#!/usr/bin/env bash
# MealSentry installer for Raspberry Pi (Debian/Raspbian). Idempotent.
# Creates the venv, installs deps, writes config + secrets env, and installs the
# hardened systemd units. Does NOT conflict with an existing NetSentry bot
# (distinct package, env vars, units, port).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
ENVFILE="${ROOT}/mealsentry.env"
PY="${ROOT}/.venv/bin/python"

echo "==> MealSentry install"
echo "    Repo root : ${ROOT}"
echo "    Run user  : ${RUN_USER}"

# --- 1. venv + dependencies ---
if [ ! -d "${ROOT}/.venv" ]; then
    echo "==> Creating virtualenv"
    python3 -m venv "${ROOT}/.venv"
fi
echo "==> Installing dependencies"
"${ROOT}/.venv/bin/pip" install --upgrade pip >/dev/null
"${ROOT}/.venv/bin/pip" install -e "${ROOT}" >/dev/null

# --- 2. config.yaml ---
if [ ! -f "${ROOT}/config.yaml" ]; then
    echo "==> Creating config.yaml from example (edit it afterwards)"
    cp "${ROOT}/config.yaml.example" "${ROOT}/config.yaml"
fi

# --- 3. secrets env file ---
if [ ! -f "${ENVFILE}" ]; then
    echo "==> Secrets setup (stored in ${ENVFILE}, chmod 600, NOT in git)"
    read -rp "    Telegram bot token (from @BotFather): " TOKEN
    read -rp "    Your Telegram numeric user id: " UID_IN
    umask 177
    cat > "${ENVFILE}" <<EOF
MEALSENTRY_TOKEN=${TOKEN}
MEALSENTRY_USER_ID=${UID_IN}
EOF
    chmod 600 "${ENVFILE}"
    echo "    Wrote ${ENVFILE}"
else
    echo "==> ${ENVFILE} already exists, leaving as-is"
fi

# --- 4. smoke check ---
echo "==> Import smoke test"
"${PY}" -c "import mealsentry.bot, mealsentry.api; print('   imports OK')"

# --- 5. systemd units ---
install_unit () {
    local src="$1" dst="/etc/systemd/system/$(basename "$1")"
    sed -e "s|__ROOT__|${ROOT}|g" \
        -e "s|__USER__|${RUN_USER}|g" \
        -e "s|__ENVFILE__|${ENVFILE}|g" \
        "${src}" | sudo tee "${dst}" >/dev/null
    echo "    Installed ${dst}"
}
echo "==> Installing systemd units (needs sudo)"
install_unit "${ROOT}/deploy/mealsentry.service"
install_unit "${ROOT}/deploy/mealsentry-api.service"
sudo systemctl daemon-reload
sudo systemctl enable mealsentry.service mealsentry-api.service

cat <<EOF

==> Done.
    Start:   sudo systemctl start mealsentry mealsentry-api
    Logs:    journalctl -u mealsentry -f
    API:     curl http://127.0.0.1:8787/health

    Edit ${ROOT}/config.yaml for tunables (age, targets, intensity, coach).
    Secrets live in ${ENVFILE} only.
EOF
