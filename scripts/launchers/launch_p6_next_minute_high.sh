#!/usr/bin/env bash
set -euo pipefail

PRODUCTS="CU,AL,ZN,SN,NI,AU,AG,EG,TA,MA,EB,SH,SA,FG,AO,SC,M,RM,B,P,RB,I,RU"
TAG="s1_p6_a0_next_minute_high_2022_latest"
CFG="config_s1_p6_a0_next_minute_high.json"
LOG="logs/${TAG}.log"

mkdir -p logs

echo "launch ${TAG} with ${CFG}"
nohup python3 src/toolkit_minute_engine.py \
  --start-date 2022-01-01 \
  --tag "${TAG}" \
  --config "${CFG}" \
  --products "${PRODUCTS}" \
  > "${LOG}" 2>&1 &

echo "${TAG} pid=$! log=${LOG}"
