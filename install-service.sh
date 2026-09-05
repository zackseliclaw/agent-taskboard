#!/usr/bin/env bash
set -euo pipefail

readonly NAME="agent-taskboard"
readonly PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
readonly SOURCE="${PROJECT_ROOT}/systemd/${NAME}.service"
readonly TARGET="/etc/systemd/system/${NAME}.service"
readonly DATA_DIR="${PROJECT_ROOT}/data"
readonly ARTIFACTS_DIR="${PROJECT_ROOT}/artifacts"

if [[ -n "${TASKBOARD_USER:-}" ]]; then
  readonly SERVICE_USER="${TASKBOARD_USER}"
elif [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != "root" ]]; then
  readonly SERVICE_USER="${SUDO_USER}"
else
  readonly SERVICE_USER="$(id -un)"
fi

readonly SERVICE_GROUP="${TASKBOARD_GROUP:-$(id -gn "${SERVICE_USER}")}"
readonly PYTHON="$(command -v python3 || command -v python)"

if [[ ! -f "${SOURCE}" ]]; then
  echo "Could not find the systemd template: ${SOURCE}" >&2
  exit 1
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "Service user does not exist: ${SERVICE_USER}" >&2
  exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this uninstaller with sudo." >&2
    exit 1
  fi

  systemctl disable --now "${NAME}.service" 2>/dev/null || true
  rm -f "${TARGET}"
  systemctl daemon-reload
  echo "Uninstalled ${NAME}; data and artifacts were preserved."
  exit 0
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

install -d -m 0700 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${DATA_DIR}"
install -d -m 0750 -o "${SERVICE_USER}" -g "${SERVICE_GROUP}" "${ARTIFACTS_DIR}"

UNIT_TMP="$(mktemp)"
trap 'rm -f "${UNIT_TMP}"' EXIT

awk \
  -v project_root="${PROJECT_ROOT}" \
  -v service_user="${SERVICE_USER}" \
  -v service_group="${SERVICE_GROUP}" \
  -v python="${PYTHON}" \
  -v data_dir="${DATA_DIR}" \
  -v artifacts_dir="${ARTIFACTS_DIR}" '
    /^Documentation=/ { print "Documentation=file:" project_root "/README.md"; next }
    /^User=/ { print "User=" service_user; next }
    /^Group=/ { print "Group=" service_group; next }
    /^WorkingDirectory=/ { print "WorkingDirectory=" project_root; next }
    /^ExecStart=/ { print "ExecStart=" python " " project_root "/taskboard.py"; next }
    /^ReadWriteDirectories=__DATA_DIR__$/ { print "ReadWriteDirectories=" data_dir; next }
    /^ReadWriteDirectories=__ARTIFACTS_DIR__$/ { print "ReadWriteDirectories=" artifacts_dir; next }
    { print }
  ' "${SOURCE}" >"${UNIT_TMP}"

install -m 0644 "${UNIT_TMP}" "${TARGET}"
systemctl daemon-reload
systemctl enable "${NAME}.service"
systemctl restart "${NAME}.service"
systemctl --no-pager --full status "${NAME}.service"
