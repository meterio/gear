#!/bin/bash
set -eo pipefail
LISTEN_PORT=8545
LISTEN_HOST=0.0.0.0
METER_IP=0.0.0.0
METER_PROTOCOL=http
METER_PORT=8669
# if command starts with an option, prepend meter-gear
if [ "${1:0:1}" = '-' ]; then
	set -- meter-gear "$@"
fi

# Check if LISTEN_HOST is not set
if [[ -z "${LISTEN_HOST}" ]]; then
  LISTEN_HOST="0.0.0.0"
else
  LISTEN_HOST="${LISTEN_HOST}"
fi

# Check if LISTEN_PORT is not set
if [[ -z "${LISTEN_PORT}" ]]; then
  LISTEN_PORT="8545"
else
  LISTEN_PORT="${LISTEN_PORT}"
fi

# Check if METER_IP is not set
if [[ -z "${METER_IP}" ]]; then
  echo "Env variable needed: eg. METER_IP=127.0.0.1 or METER_IP=www.example.com"
  exit 1
fi

# check if METER_PORT is not set
if [[ -z "${METER_PORT}" ]]; then
  echo "Env variable needed: eg. METER_PORT=8669"
  exit 1
fi

# check if METER_PROTOCOL is not set
if [[ -z "${METER_PROTOCOL}" ]]; then
  METER_PROTOCOL='http'
else
  METER_PROTOCOL="${METER_PROTOCOL}"
fi

echo "Using meter point: ${METER_PROTOCOL}://${METER_IP}:${METER_PORT}"

LC_ALL="C.UTF-8" LANG="C.UTF-8" meter-gear --host ${LISTEN_HOST} --port ${LISTEN_PORT} --endpoint "${METER_PROTOCOL}://${METER_IP}:${METER_PORT}"
