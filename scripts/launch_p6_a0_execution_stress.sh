#!/usr/bin/env bash
set -euo pipefail

PRODUCTS="CU,AL,ZN,SN,NI,AU,AG,EG,TA,MA,EB,SH,SA,FG,AO,SC,M,RM,B,P,RB,I,RU"
START_DATE="2022-01-01"

declare -A CONFIGS=(
  [s1_p6_a0_group_stop15_codefix_2022_latest]="config_s1_p6_a0_group_stop15_codefix.json"
  [s1_p6_a0_live_mild_2022_latest]="config_s1_p6_a0_live_mild.json"
  [s1_p6_a0_live_conservative_2022_latest]="config_s1_p6_a0_live_conservative.json"
  [s1_p6_a0_live_severe_2022_latest]="config_s1_p6_a0_live_severe.json"
)

mkdir -p logs

for TAG in "${!CONFIGS[@]}"; do
  CFG="${CONFIGS[$TAG]}"
  LOG="logs/${TAG}.log"
  echo "launch ${TAG} with ${CFG}"
  nohup python3 src/toolkit_minute_engine.py \
    --start-date "${START_DATE}" \
    --tag "${TAG}" \
    --config "${CFG}" \
    --products "${PRODUCTS}" \
    > "${LOG}" 2>&1 &
  echo "${TAG} pid=$! log=${LOG}"
done
