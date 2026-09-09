#!/usr/bin/env python3
"""축 C-1 — pH 측정조건(dilution basis) 표준화 감사.

`lib_parse.parse_physchem_extract`에 추가된 `ph_basis`/`ph_basis_pct` 필드를
실제 배정 시트의 원문 추출정보 전량(특성 시트)에 적용해 커버리지를 검증한다.
GHS 비가산성 게이트(pH<=2 / pH>=11.5, 원액 기준)가 실제로는 몇 %가
'몇% 희석 상태'로 보고되는지를 드러낸다. 원본 라벨/공식은 바꾸지 않는다.

산출: 04_모델산출물/v4/ph_basis_audit.csv
"""
from pathlib import Path
import re
import sys

import pandas as pd

ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, f"{ROOT}/01_파이프라인")
from lib_parse import parse_physchem_extract

SRC = f"{ROOT}/03_입력데이터/dataset_배정_20260824.xlsx"
OUT = f"{ROOT}/04_모델산출물/v4/ph_basis_audit.csv"

phy = pd.read_excel(SRC, sheet_name="특성")

rows = []
ph_item_re = re.compile(r"\(\s*ph\s*\)\s*$", re.I)
for _, r in phy.iterrows():
    ext = r.get("추출정보")
    if pd.isna(ext):
        continue
    for it in str(ext).split("|"):
        it = it.strip()
        if not ph_item_re.search(it):
            continue
        out = parse_physchem_extract(it)
        rows.append({
            "Formulation_ID": r["Formulation_ID"], "raw_item": it,
            "ph_lo": out["pc2_ph_lo"], "ph_hi": out["pc2_ph_hi"],
            "ph_basis": out["ph_basis"], "ph_basis_pct": out["ph_basis_pct"],
            "strong_acid_gate": out["pc2_strong_acid"], "strong_base_gate": out["pc2_strong_base"],
        })

df = pd.DataFrame(rows)
n = len(df)
covered = (df["ph_basis"] != "unspecified").sum()
print(f"[AC3] pH 원문 항목 {n}건 중 측정조건 구조화 성공(neat/diluted/other_condition): "
      f"{covered}건 ({covered/n*100:.1f}%)")
print(df["ph_basis"].value_counts())

gate_rows = df[df["strong_acid_gate"] | df["strong_base_gate"]]
diluted_gate = gate_rows[gate_rows["ph_basis"] == "diluted"]
print(f"\n[핵심 리스크] GHS 비가산성 게이트(pH<=2/>=11.5) 대상 {len(gate_rows)}건 중, "
      f"'희석 상태'로 측정된 값이 게이트를 트리거한 경우: {len(diluted_gate)}건")
if len(diluted_gate):
    print(diluted_gate[["Formulation_ID", "raw_item", "ph_basis_pct"]].head(10).to_string())

df.to_csv(OUT, index=False, encoding="utf-8-sig")
print(f"\n저장: {OUT}  ({n}행)")
