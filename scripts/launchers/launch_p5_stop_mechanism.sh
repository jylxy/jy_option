#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PRODUCTS="CU,AL,ZN,SN,NI,AU,AG,EG,TA,MA,EB,SH,SA,FG,AO,SC,M,RM,B,P,RB,I,RU"
START_DATE="2022-01-01"

declare -a RUNS=(
  "s1_p5_p3b_a0_group_stop15_2022_latest config_s1_p5_p3b_a0_group_stop15.json"
  "s1_p5_p3b_a1_contract_stop15_2022_latest config_s1_p5_p3b_a1_contract_stop15.json"
  "s1_p5_p3b_a2_same_code_stop15_2022_latest config_s1_p5_p3b_a2_same_code_stop15.json"
  "s1_p5_p3b_a3_triggered_group_stop15_2022_latest config_s1_p5_p3b_a3_triggered_group_stop15.json"
  "s1_p5_p3b_b1_layer_reduce15_close25_2022_latest config_s1_p5_p3b_b1_layer_reduce15_close25.json"
  "s1_p5_p3b_b2_layer_reduce15_close20_group25_2022_latest config_s1_p5_p3b_b2_layer_reduce15_close20_group25.json"
  "s1_p5_p3b_b3_layer_warn15_reduce20_close25_2022_latest config_s1_p5_p3b_b3_layer_warn15_reduce20_close25.json"
  "s1_p5_p3b_b4_layer_close15_group25_2022_latest config_s1_p5_p3b_b4_layer_close15_group25.json"
)

mkdir -p logs

for item in "${RUNS[@]}"; do
  tag="${item%% *}"
  config="${item#* }"
  if pgrep -af "toolkit_minute_engine.py .*--tag ${tag} " >/dev/null; then
    echo "already running: ${tag}"
    continue
  fi
  if [[ -f "output/nav_${tag}.csv" ]]; then
    echo "output exists, skip: ${tag}"
    continue
  fi
  echo "launch ${tag} ${config}"
  nohup python3 src/toolkit_minute_engine.py \
    --start-date "${START_DATE}" \
    --tag "${tag}" \
    --config "${config}" \
    --products "${PRODUCTS}" \
    > "logs/${tag}.log" 2>&1 &
done

echo "active P5 processes:"
ps -eo pid,etimes,pcpu,pmem,cmd | grep 'src/toolkit_minute_engine.py' | grep 's1_p5_' | grep -v grep || true
