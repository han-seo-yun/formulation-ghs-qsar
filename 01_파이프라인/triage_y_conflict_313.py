#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
triage_y_conflict_313.py

목적
----
`resolve_y_conflict()` (lib_tox11.py) 로직을 전체 1675 제형에 재적용한 결과
(`04_모델산출물/v4/y_conflict_audit.csv`)에서, **원래는 충돌로 표시되지 않았는데**
(`n_y_conflict == 0`) 재적용 시 조성 불일치가 감지된
(`pred_rule in {composition_mismatch, partial_composition}`) 행을 추려,
그 플래그가 실제로 y 라벨 신뢰도 위험인지 트리아지한다.

판정 기준
---------
조성 불일치 플래그는 "라벨 출처가 2개 이상"일 때만 실제 충돌 위험이 된다.
출처가 1개(또는 0개)면 서로 비교할 대상이 없으므로 라벨 자체가 모순될 수 없고,
플래그는 정보성(informational)에 그친다.

  - y_eye_n_src / y_skin_n_src / y_sens_n_src 중 하나라도 >= 2
        -> "genuine_multi_source_risk"
  - 전부 <= 1 (또는 결측)
        -> "single_source_no_real_conflict"

읽기 전용
---------
기존 파일/라벨을 절대 수정하지 않는다. 신규 CSV 1개만 생성한다.

출력
----
04_모델산출물/v4/y_conflict_313_triage.csv
  Formulation_ID, pred_rule, category, eye_n_src, skin_n_src, sens_n_src,
  endpoints_at_risk
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- 경로 설정
BASE = Path(__file__).resolve().parents[1]
AUDIT_CSV = BASE / "04_모델산출물" / "v4" / "y_conflict_audit.csv"
DATASET_XLSX = BASE / "04_모델산출물" / "input_dataset_v4.xlsx"
OUT_CSV = BASE / "04_모델산출물" / "v4" / "y_conflict_313_triage.csv"

TARGET_RULES = ("composition_mismatch", "partial_composition")

# 엔드포인트명 -> 출처 개수 컬럼
SRC_COLS = {
    "eye": "y_eye_n_src",
    "skin": "y_skin_n_src",
    "sens": "y_sens_n_src",
}

CAT_RISK = "genuine_multi_source_risk"
CAT_SAFE = "single_source_no_real_conflict"


def load_audit() -> pd.DataFrame:
    """감사 CSV 로드 (BOM 있음 -> utf-8-sig)."""
    df = pd.read_csv(AUDIT_CSV, encoding="utf-8-sig", dtype=str)
    df["n_y_conflict"] = pd.to_numeric(df["n_y_conflict"], errors="coerce")
    return df


def select_candidates(audit: pd.DataFrame) -> pd.DataFrame:
    """원래 충돌 플래그 없음(n_y_conflict==0) + 재적용 시 조성 불일치."""
    mask = (audit["n_y_conflict"] == 0) & (audit["pred_rule"].isin(TARGET_RULES))
    return audit.loc[mask, ["Formulation_ID", "pred_rule"]].copy()


def load_src_counts() -> pd.DataFrame:
    """formulation 시트에서 엔드포인트별 라벨 출처 개수만 추출."""
    form = pd.read_excel(DATASET_XLSX, sheet_name="formulation")
    keep = ["Formulation_ID"] + list(SRC_COLS.values())
    missing = [c for c in keep if c not in form.columns]
    if missing:
        raise KeyError(f"formulation 시트에 컬럼 없음: {missing}")
    out = form[keep].copy()
    out = out.rename(columns={v: f"{k}_n_src" for k, v in SRC_COLS.items()})
    return out


def classify(row: pd.Series) -> tuple[str, str]:
    """(category, endpoints_at_risk) 반환. 결측은 위험 아님(0 취급)."""
    at_risk = []
    for ep in SRC_COLS:
        val = row.get(f"{ep}_n_src")
        if pd.notna(val) and float(val) >= 2:
            at_risk.append(ep)
    category = CAT_RISK if at_risk else CAT_SAFE
    return category, ";".join(at_risk)


def main() -> None:
    audit = load_audit()
    cand = select_candidates(audit)
    print(f"[1] 감사 전체 행수                     : {len(audit)}")
    print(f"[2] 후보(n_y_conflict==0 & 조성 불일치): {len(cand)}")
    print("    pred_rule 분포:")
    for rule, n in cand["pred_rule"].value_counts().items():
        print(f"      - {rule}: {n}")

    src = load_src_counts()
    merged = cand.merge(src, on="Formulation_ID", how="left")

    unmatched = merged["eye_n_src"].isna().sum()
    if unmatched:
        print(f"    [경고] formulation 시트에서 미매칭 ID: {unmatched}건 (결측 -> 0 취급)")

    triaged = merged.apply(classify, axis=1, result_type="expand")
    merged["category"] = triaged[0]
    merged["endpoints_at_risk"] = triaged[1]

    out = merged[
        [
            "Formulation_ID",
            "pred_rule",
            "category",
            "eye_n_src",
            "skin_n_src",
            "sens_n_src",
            "endpoints_at_risk",
        ]
    ].sort_values(["category", "pred_rule", "Formulation_ID"], ascending=[True, True, True])

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # ------------------------------------------------------------ 요약 리포트
    print("\n[3] 트리아지 결과")
    for cat, n in out["category"].value_counts().items():
        print(f"      - {cat}: {n}")

    print("\n[4] 엔드포인트별 실제 위험(n_src >= 2) 건수")
    for ep in SRC_COLS:
        n = int((out[f"{ep}_n_src"].fillna(0) >= 2).sum())
        print(f"      - {ep}: {n}")

    print("\n[5] category x pred_rule 교차표")
    print(pd.crosstab(out["category"], out["pred_rule"]).to_string())

    print("\n[6] 위험 엔드포인트 조합 분포")
    risk = out.loc[out["category"] == CAT_RISK, "endpoints_at_risk"]
    for combo, n in risk.value_counts().items():
        print(f"      - {combo}: {n}")

    print(f"\n[출력] {OUT_CSV}")


if __name__ == "__main__":
    main()
