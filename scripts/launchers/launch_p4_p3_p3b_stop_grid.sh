#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PRODUCTS="CU,AL,ZN,SN,NI,AU,AG,EG,TA,MA,EB,SH,SA,FG,AO,SC,M,RM,B,P,RB,I,RU"
START_DATE="2022-01-01"

declare -a RUNS=(
  "s1_p4_p3_ledet_mainstream_stop20_2022_latest config_s1_p4_p3_ledet_mainstream_stop20.json"
  "s1_p4_p3_ledet_mainstream_stop25_2022_latest config_s1_p4_p3_ledet_mainstream_stop25.json"
  "s1_p4_p3_ledet_mainstream_stop30_2022_latest config_s1_p4_p3_ledet_mainstream_stop30.json"
  "s1_p4_p3_ledet_mainstream_nostop_2022_latest config_s1_p4_p3_ledet_mainstream_nostop.json"
  "s1_p4_p3b_ledet_term_pref_stop20_2022_latest config_s1_p4_p3b_ledet_term_pref_stop20.json"
  "s1_p4_p3b_ledet_term_pref_stop25_2022_latest config_s1_p4_p3b_ledet_term_pref_stop25.json"
  "s1_p4_p3b_ledet_term_pref_stop30_2022_latest config_s1_p4_p3b_ledet_term_pref_stop30.json"
  "s1_p4_p3b_ledet_term_pref_nostop_2022_latest config_s1_p4_p3b_ledet_term_pref_nostop.json"
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

echo "active P4 processes:"
ps -eo pid,etimes,pcpu,pmem,cmd | grep 'src/toolkit_minute_engine.py' | grep 's1_p4_' | grep -v grep || true
