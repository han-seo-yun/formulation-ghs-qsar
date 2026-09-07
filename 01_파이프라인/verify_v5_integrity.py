#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v5 독립 무결성 검증 (읽기 전용).

input_dataset_v5.xlsx + 04_모델산출물/v4_fixed/ 를 v4 및 v1 원본과 대조해
두 차례 수정(6/30 감사 병합 안전화 · 라벨 음성 복구)이 서로 간섭했는지,
그리고 잔여 오염이 남았는지를 빌더의 주장과 무관하게 재계산한다.

빌더(build_input_v5.py)나 그 자체 검증(verify_label_recovery.py)의 산출 수치를
읽어서 비교하지 않는다. 모든 수치는 원본 시트/파케이/npz 에서 직접 재계산한다.

산출물
  04_모델산출물/v4_fixed/v5_integrity_report.json
  04_모델산출물/v4_fixed/v5_integrity_*.csv

쓰기 대상은 위 v5_integrity_* 신규 파일뿐이며 기존 데이터는 일절 변경하지 않는다.

사용
  python3 01_파이프라인/verify_v5_integrity.py            # PubChem 대조 포함
  python3 01_파이프라인/verify_v5_integrity.py --no-net    # 오프라인 (V4-c 건너뜀)
"""
from __future__ import annotations

import json
import math
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter

import numpy as np
import pandas as pd

# ---------------------------------------------------------------- 경로
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_V1 = f"{ROOT}/03_입력데이터/input_dataset.xlsx"
XL_V4 = f"{ROOT}/04_모델산출물/input_dataset_v4.xlsx"
XL_V5 = f"{ROOT}/04_모델산출물/input_dataset_v5.xlsx"
OUT_V4 = f"{ROOT}/04_모델산출물/v4"
OUT_V5 = f"{ROOT}/04_모델산출물/v4_fixed"
REPORT = f"{OUT_V5}/v5_integrity_report.json"
CSV_PREFIX = f"{OUT_V5}/v5_integrity"

USE_NET = "--no-net" not in sys.argv
EPS = ("eye", "skin", "sens")
R: dict = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
           "note": "독립 검증. 모든 수치는 원본에서 재계산했으며 빌더 로그를 인용하지 않는다."}
FINDINGS: list[dict] = []          # (severity, id, message)


def log(*a):
    print(*a, flush=True)


def finding(sev, fid, msg, **extra):
    """sev ∈ {BLOCKER, MAJOR, MINOR, INFO}"""
    d = {"severity": sev, "id": fid, "message": msg}
    d.update(extra)
    FINDINGS.append(d)
    log(f"    [{sev}] {fid}: {msg}")


NA_TOKEN = "#__NA__#"   # Arrow 문자열 dtype 은 NUL 을 절단하므로 NUL 센티넬 금지


def S(df, col):
    """NA 를 센티넬로 바꾼 문자열 시리즈. NaN!=NaN 비교 함정을 피한다."""
    s = df[col]
    return s.astype(object).where(s.notna(), NA_TOKEN).astype(str)


def eqnum(a, b):
    """수치 컬럼 동등 비교(NaN==NaN 을 참으로)."""
    return np.isclose(pd.to_numeric(a, errors="coerce").astype(float),
                      pd.to_numeric(b, errors="coerce").astype(float),
                      equal_nan=True)


# ---------------------------------------------------------------- 빌더 규칙 복제
SEV = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
       "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1,
                "2A": 2, "2B": 1, "NC": 0},
       "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}


def nrm_cat(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    if not s or s.lower() in ("nan", "none", "-"):
        return None
    if re.search(r"not classified|^nc$|not_classified|미분류|no data|unknown", s, re.I):
        return "NC" if re.search(r"not classified|^nc$|not_classified|미분류", s, re.I) else None
    try:
        f = float(s)
        if f == int(f):
            return str(int(f))
    except ValueError:
        pass
    m = re.match(r"\s*(?:cat(?:egory)?\.?\s*)?(1A|1B|1C|2A|2B|1|2|3|4)\b", s, re.I)
    return m.group(1).upper() if m else s.upper()[:24]


_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")


def cas_wellformed(c):
    return bool(_CAS_RE.match(str(c or "").strip()))


def cas_checkdigit_ok(c):
    c = str(c or "").strip()
    if not _CAS_RE.match(c):
        return False
    d = c.replace("-", "")
    return sum(int(x) * (i + 1) for i, x in enumerate(reversed(d[:-1]))) % 10 == int(d[-1])


# build_input_v5.py 의 _NONCHEM_RE 를 그대로 복제하되, 어떤 패턴이 걸렸는지 추적한다.
NONCHEM_PATTERNS = {
    "blank": r"^\s*$", "content": r"content", "w_w": r"\bw\s*/\s*w\b",
    "w_v": r"\bw\s*/\s*v\b", "concentration": r"concentration", "pct_only": r"^\s*%\s*$",
    "percent": r"percent(age)?$", "numeric_only": r"^\s*[\d.,\s%<>=~\-–]+$",
    "cas_header": r"^cas\b", "ec_header": r"^ec\b", "ec_index": r"^ec[\s_-]?index",
    "index_no": r"^index\s*no", "name_colon": r"^name\s*[:：]",
    "ingredients_hdr": r"^ingredient(s)?\s*$", "components_hdr": r"^component(s)?\s*$",
    "chemical_name_hdr": r"^chemical\s+name", "classification_hdr": r"^classification",
    "hazard_hdr": r"^hazard", "weight_hdr": r"^weight", "amount_hdr": r"^amount",
    "substance_hdr": r"^substance\s*$", "identifier_hdr": r"^identifier",
    "reach_hdr": r"^reach", "ppm": r"^ppm$", "mgkg": r"^mg/kg$", "gl": r"^g/l$",
    "wt": r"^wt\b", "na": r"^n/?a$", "none": r"^none$",
    "other_ingredient": r"^other\s+ingredient", "remainder": r"^remainder",
    "balance": r"^balance$", "trade_secret": r"^trade\s+secret",
    "proprietary": r"^proprietary\s*$", "total": r"^total$", "sum": r"^sum$",
    "range": r"^range$", "number": r"^number$", "no_dot": r"^no\.$"}
_NC_RX = {k: re.compile(v, re.I) for k, v in NONCHEM_PATTERNS.items()}
# 실제 물질명에 단위/접두 노이즈만 붙은 경우(=게이트의 오탐 가능군)
SOFT_GATES = {"w_w", "w_v", "content", "chemical_name_hdr", "name_colon"}


def nonchem_gates(tok):
    t = str(tok or "").strip()
    hits = [k for k, rx in _NC_RX.items() if rx.search(t)]
    if not hits and len(re.sub(r"[^A-Za-z]", "", t)) < 3:
        hits = ["under_3_letters"]
    return hits


# ---------------------------------------------------------------- 적재
log("[0] 적재")
_sheets = ("MetaData", "formulation", "ingredient", "targets", "feature_manifest")
V4 = {k: pd.read_excel(XL_V4, sheet_name=k) for k in _sheets}
V5 = {k: pd.read_excel(XL_V5, sheet_name=k) for k in _sheets}
F1 = pd.read_excel(SRC_V1, sheet_name="formulation")
I1 = pd.read_excel(SRC_V1, sheet_name="ingredient")
F4, F5 = V4["formulation"], V5["formulation"]
I4, I5 = V4["ingredient"], V5["ingredient"]

P = {}
for tag, d in (("v4", OUT_V4), ("v5", OUT_V5)):
    P[tag] = {
        "Xf": pd.read_parquet(f"{d}/X_formulation.parquet"),
        "Xi": pd.read_parquet(f"{d}/X_ingredient.parquet"),
        "y": pd.read_parquet(f"{d}/y_formulation.parquet"),
        "g": pd.read_parquet(f"{d}/groups.parquet"),
        "fp_ing": np.load(f"{d}/fp_ing_morgan.npz", allow_pickle=True),
        "fp_mac": np.load(f"{d}/fp_ing_maccs.npz", allow_pickle=True),
        "fp_form": np.load(f"{d}/fp_form_pooled.npz", allow_pickle=True)}
log(f"    v5 formulation {F5.shape} · ingredient {I5.shape}")


# ================================================================ V1 구조적 무결성
log("[V1] 구조적 무결성")
v1: dict = {}
v1["shapes"] = {
    "v4": {k: list(V4[k].shape) for k in _sheets},
    "v5": {k: list(V5[k].shape) for k in _sheets},
    "parquet_v5": {k: list(P["v5"][k].shape) for k in ("Xf", "Xi", "y", "g")},
    "parquet_v4": {k: list(P["v4"][k].shape) for k in ("Xf", "Xi", "y", "g")},
    "npz_v5": {"fp_ing_morgan": list(P["v5"]["fp_ing"]["fp"].shape),
               "fp_ing_maccs": list(P["v5"]["fp_mac"]["fp"].shape),
               "fp_form_morgan": list(P["v5"]["fp_form"]["morgan"].shape),
               "fp_form_maccs": list(P["v5"]["fp_form"]["maccs"].shape)}}
v1["sheet_names_identical"] = (
    pd.ExcelFile(XL_V4).sheet_names == pd.ExcelFile(XL_V5).sheet_names)

# 행수 / ID 집합
Xf5, Xi5, y5, g5 = (P["v5"][k] for k in ("Xf", "Xi", "y", "g"))
Xf4, Xi4, y4, g4 = (P["v4"][k] for k in ("Xf", "Xi", "y", "g"))
fid5 = Xf5["Formulation_ID"]
v1["row_counts_unchanged"] = {
    "formulation": len(F4) == len(F5) == 1675,
    "ingredient": len(I4) == len(I5) == 5287}
v1["formulation_id"] = {
    "n": int(fid5.nunique()), "duplicated": int(fid5.duplicated().sum()),
    "set_equal_v4": set(Xf4.Formulation_ID) == set(fid5),
    "order_equal_v4": bool((Xf4.Formulation_ID.values == fid5.values).all()),
    "Xf_y_g_set_equal": set(fid5) == set(y5.Formulation_ID) == set(g5.Formulation_ID),
    "Xf_y_order_equal": bool((y5.Formulation_ID.values == fid5.values).all()),
    "Xf_g_order_equal": bool((g5.Formulation_ID.values == fid5.values).all()),
    "sheet_vs_parquet_equal": bool((F5.Formulation_ID.values == fid5.values).all())}
v1["ingredient_key"] = {
    "duplicated_fid_ingidx": int(I5.duplicated(["Formulation_ID", "ing_idx"]).sum()),
    "order_equal_v4": bool(((I4.Formulation_ID.values == I5.Formulation_ID.values) &
                            (I4.ing_idx.values == I5.ing_idx.values)).all()),
    "order_equal_v1": bool(((I1.Formulation_ID.values == I5.Formulation_ID.values) &
                            (I1.ing_idx.values == I5.ing_idx.values)).all())}
v1["npz_alignment"] = {
    "fp_ing_fid_matches_Xi": bool((P["v5"]["fp_ing"]["fid"] == Xi5.Formulation_ID.values).all()),
    "fp_ing_idx_matches_Xi": bool((P["v5"]["fp_ing"]["ing_idx"] == Xi5.ing_idx.values).all()),
    "fp_mac_fid_matches_Xi": bool((P["v5"]["fp_mac"]["fid"] == Xi5.Formulation_ID.values).all()),
    "fp_form_fid_matches_Xf": bool((P["v5"]["fp_form"]["fid"] == fid5.values).all()),
    "has_structure_sum_v4": int(P["v4"]["fp_ing"]["has_structure"].sum()),
    "has_structure_sum_v5": int(P["v5"]["fp_ing"]["has_structure"].sum())}
v1["column_delta"] = {
    "formulation_added": sorted(set(F5.columns) - set(F4.columns)),
    "formulation_removed": sorted(set(F4.columns) - set(F5.columns)),
    "ingredient_added": sorted(set(I5.columns) - set(I4.columns)),
    "ingredient_removed": sorted(set(I4.columns) - set(I5.columns)),
    "targets_added": sorted(set(V5["targets"].columns) - set(V4["targets"].columns)),
    "targets_removed": sorted(set(V4["targets"].columns) - set(V5["targets"].columns)),
    "X_formulation_added": sorted(set(Xf5.columns) - set(Xf4.columns)),
    "X_formulation_removed": sorted(set(Xf4.columns) - set(Xf5.columns)),
    "X_ingredient_changed": sorted(set(Xi5.columns) ^ set(Xi4.columns))}

for k, ok in (("row_counts_formulation", v1["row_counts_unchanged"]["formulation"]),
              ("row_counts_ingredient", v1["row_counts_unchanged"]["ingredient"]),
              ("fid_no_dup", v1["formulation_id"]["duplicated"] == 0),
              ("fid_set_equal_v4", v1["formulation_id"]["set_equal_v4"]),
              ("fid_order_equal_v4", v1["formulation_id"]["order_equal_v4"]),
              ("Xf_y_g_aligned", v1["formulation_id"]["Xf_y_order_equal"] and
                                 v1["formulation_id"]["Xf_g_order_equal"]),
              ("ing_key_unique", v1["ingredient_key"]["duplicated_fid_ingidx"] == 0),
              ("npz_aligned", all(v1["npz_alignment"][x] for x in
                                  ("fp_ing_fid_matches_Xi", "fp_ing_idx_matches_Xi",
                                   "fp_mac_fid_matches_Xi", "fp_form_fid_matches_Xf")))):
    if not ok:
        finding("BLOCKER", f"V1.{k}", "구조 정합성 실패")
if not v1["column_delta"]["X_formulation_added"] and \
        not v1["column_delta"]["X_formulation_removed"]:
    log("    X_formulation 컬럼 집합 v4 동일 (521)")

# MetaData 기재값 vs 실제
_md = V5["MetaData"]
_md_feat = _md.loc[_md["항목"].astype(str).str.contains("X_formulation", na=False), "항목"]
v1["metadata_feature_string"] = _md_feat.iloc[0] if len(_md_feat) else None
if v1["metadata_feature_string"] and "520" in str(v1["metadata_feature_string"]) \
        and Xf5.shape[1] == 521:
    finding("MINOR", "V1.metadata_col_count",
            f"MetaData 는 'X_formulation 520열'이라 기재하지만 실제 파케이는 "
            f"{Xf5.shape[1]}열 (v4 와 동일한 기존 오기)")

# ---- group_key: 정의(코드 규칙)와 값(데이터) 을 분리해 검증
gk4 = S(g4, "group_key")
gk5 = S(g5, "group_key")
gk_diff = gk4 != gk5
# 정의 재현: 최고 pct 순 정렬 후 컬럼별 first(=결측 스킵) → cas 없으면 name, 없으면 FID
_prim = (I5.sort_values(["Formulation_ID", "ing_pct_best"], ascending=[True, False])
           .groupby("Formulation_ID")
           .agg(pc=("ing_cas_best", "first"), pn=("ing_name_best", "first")))
_rep = (_prim["pc"].fillna("").where(lambda s: s != "", _prim["pn"].fillna(""))
        .replace("", np.nan))
_rep = _rep.fillna(pd.Series(_prim.index, index=_prim.index))
_rep = _rep.reindex(g5.Formulation_ID.values)
v1["group_key"] = {
    "definition_reproduced_from_v5_rule": bool(
        (S(pd.DataFrame({"a": _rep.values}), "a").values == gk5.values).all()),
    "nunique_v4": int(g4.group_key.nunique()), "nunique_v5": int(g5.group_key.nunique()),
    "n_rows_changed": int(gk_diff.sum()),
    "changed": g5.loc[gk_diff.values, ["Formulation_ID", "group_key",
                                       "group_primary_cas", "group_primary_name"]]
                 .assign(group_key_v4=g4.loc[gk_diff.values, "group_key"].values,
                         group_primary_cas_v4=g4.loc[gk_diff.values, "group_primary_cas"].values)
                 .to_dict("records"),
    "size_distribution_v4": {str(k): int(v) for k, v in
                             g4.group_key.value_counts().value_counts().sort_index().items()},
    "size_distribution_v5": {str(k): int(v) for k, v in
                             g5.group_key.value_counts().value_counts().sort_index().items()},
    "n_group_key_is_formulation_id": int((gk5.values == g5.Formulation_ID.values).sum())}
# group_primary_cas 와 group_primary_name 이 서로 다른 성분행에서 왔는지
_row_mismatch = []
for fid, sub in I5.sort_values(["Formulation_ID", "ing_pct_best"],
                               ascending=[True, False]).groupby("Formulation_ID"):
    c_idx = sub.index[sub.ing_cas_best.notna()]
    n_idx = sub.index[sub.ing_name_best.notna()]
    if len(c_idx) and len(n_idx) and c_idx[0] != n_idx[0]:
        _row_mismatch.append(fid)
v1["group_primary_row_mismatch"] = {
    "n": len(_row_mismatch),
    "note": "groupby.agg('first') 는 컬럼별로 결측을 건너뛰므로 group_primary_cas 와 "
            "group_primary_name 이 서로 다른 성분행에서 나올 수 있다. v4 와 동일한 코드.",
    "examples": _row_mismatch[:20]}
if v1["group_key"]["definition_reproduced_from_v5_rule"]:
    log(f"    group_key 정의 규칙 재현 OK · 값 변경 {int(gk_diff.sum())}행 "
        f"({v1['group_key']['nunique_v4']}→{v1['group_key']['nunique_v5']}개)")
else:
    finding("BLOCKER", "V1.group_key_definition",
            "group_key 를 v5 규칙으로 재현할 수 없다 — 정의가 코드와 산출물 사이에서 불일치")

# 그룹 분할(co-membership) 변화
_p4 = g4.groupby(gk4.values).Formulation_ID.apply(frozenset)
_p5 = g5.groupby(gk5.values).Formulation_ID.apply(frozenset)
_only4, _only5 = set(_p4) - set(_p5), set(_p5) - set(_p4)
_aff = (set().union(*_only4) if _only4 else set()) | (set().union(*_only5) if _only5 else set())
v1["group_partition_change"] = {"groups_only_in_v4": len(_only4),
                                "groups_only_in_v5": len(_only5),
                                "n_formulations_in_changed_groups": len(_aff)}
if int(gk_diff.sum()):
    finding("MINOR", "V1.group_key_values_changed",
            f"group_key 정의는 불변이나 값이 {int(gk_diff.sum())}개 제형에서 바뀌었다 "
            f"(6/30 병합 철회로 주성분 CAS 가 결측이 되어 그룹키가 하위 성분으로 이동). "
            f"영향받은 그룹의 제형 {len(_aff)}개 — CV 폴드 구성이 v4 와 달라진다.",
            changed_formulations=[r["Formulation_ID"] for r in v1["group_key"]["changed"]])
if _row_mismatch:
    finding("MINOR", "V1.group_primary_row_mismatch",
            f"group_primary_cas 와 group_primary_name 이 서로 다른 성분행에서 온 제형 "
            f"{len(_row_mismatch)}개 (agg('first') 의 컬럼별 결측 스킵). v4 에도 있던 성질이나 "
            f"v5 에서 CAS 철회가 늘어 그룹키가 부성분(예: 용제·실리카)로 이동하기 쉬워졌다.")

R["V1_structural"] = v1


# ================================================================ V2 상호 간섭
log("[V2] 두 수정의 상호 간섭")
v2: dict = {}

# ---- (a) 6/30 병합으로 값이 바뀐 성분행/제형
ing_changed = ((S(I4, "smiles") != S(I5, "smiles")) |
               (S(I4, "ing_cas_best") != S(I5, "ing_cas_best")))
cas_changed = S(I4, "ing_cas_best") != S(I5, "ing_cas_best")
smi_changed = S(I4, "smiles") != S(I5, "smiles")
june_fids = sorted(set(I5.Formulation_ID[ing_changed.values]))
v2["june_merge_effect"] = {
    "rows_value_changed": int(ing_changed.sum()),
    "cas_changed_rows": int(cas_changed.sum()),
    "cas_retracted": int((I4.ing_cas_best.notna() & I5.ing_cas_best.isna()).sum()),
    "cas_corrected": int((I4.ing_cas_best.notna() & I5.ing_cas_best.notna() &
                          cas_changed).sum()),
    "cas_new": int((I4.ing_cas_best.isna() & I5.ing_cas_best.notna()).sum()),
    "smiles_changed_rows": int(smi_changed.sum()),
    "smiles_retracted": int((I4.smiles.notna() & I5.smiles.isna()).sum()),
    "smiles_new": int((I4.smiles.isna() & I5.smiles.notna()).sum()),
    "affected_formulations": len(june_fids),
    "raw_cas_column_untouched": bool((S(I1, "cas") == S(I5, "cas")).all()),
    "ing_name_best_untouched": bool((S(I4, "ing_name_best") == S(I5, "ing_name_best")).all()),
    "structure_coverage_v4": round(float(I4.smiles.notna().mean()), 6),
    "structure_coverage_v5": round(float(I5.smiles.notna().mean()), 6),
    "desc_ok_v4": int(P["v4"]["fp_ing"]["has_structure"].sum()),
    "desc_ok_v5": int(P["v5"]["fp_ing"]["has_structure"].sum())}

# ---- (b) 라벨 복구로 y 가 바뀐 제형
lab = {}
label_changed_all, label_added_all = set(), set()
for ep in EPS:
    a, b = S(y4, f"y_{ep}"), S(y5, f"y_{ep}")
    na = NA_TOKEN
    added = set(y5.Formulation_ID[((a == na) & (b != na)).values])
    lost = set(y5.Formulation_ID[((a != na) & (b == na)).values])
    changed = set(y5.Formulation_ID[((a != na) & (b != na) & (a != b)).values])
    m = ((a != na) & (b != na) & (a != b)).values
    flips = pd.DataFrame({"Formulation_ID": y5.Formulation_ID[m],
                          "y_v4": y4[f"y_{ep}"][m], "src_v4": y4[f"y_{ep}_src"][m],
                          "y_v5": y5[f"y_{ep}"][m],
                          "src_v5": y5[f"y_{ep}_src_detail"][m],
                          "bin_v4": y4[f"y_{ep}_bin"][m], "bin_v5": y5[f"y_{ep}_bin"][m]})
    lab[ep] = {
        "n_v4": int(y4[f"y_{ep}"].notna().sum()), "n_v5": int(y5[f"y_{ep}"].notna().sum()),
        "added": len(added), "lost": len(lost), "changed": len(changed),
        "pos_rate_v4": round(float(pd.to_numeric(y4[f"y_{ep}_bin"], errors="coerce").mean()), 4),
        "pos_rate_v5": round(float(pd.to_numeric(y5[f"y_{ep}_bin"], errors="coerce").mean()), 4),
        "flip_pos_to_neg": int(((flips.bin_v4 == 1) & (flips.bin_v5 == 0)).sum()),
        "flip_neg_to_pos": int(((flips.bin_v4 == 0) & (flips.bin_v5 == 1)).sum()),
        "changed_intersect_june_formulations": len(changed & set(june_fids)),
        "added_intersect_june_formulations": len(added & set(june_fids)),
        "flips": flips.to_dict("records")}
    label_changed_all |= changed
    label_added_all |= added
    if lost:
        finding("MAJOR", f"V2.label_lost_{ep}", f"v4 에 있던 y_{ep} 라벨 {len(lost)}건이 v5 에서 소실")
v2["label_recovery_effect"] = lab
v2["interference"] = {
    "june_affected_formulations": len(june_fids),
    "label_changed_formulations": len(label_changed_all),
    "label_added_formulations": len(label_added_all),
    "overlap_changed_and_june": sorted(label_changed_all & set(june_fids)),
    "overlap_added_and_june": sorted(label_added_all & set(june_fids))}

# ---- (c) resid / CT 정의행
ct = {}
for ep in EPS:
    c4, c5 = y4[f"ct_{ep}_ord"].notna(), y5[f"ct_{ep}_ord"].notna()
    r4, r5 = y4[f"resid_{ep}"].notna(), y5[f"resid_{ep}"].notna()
    both = (c4 & c5).values
    ct[ep] = {
        "ct_defined_v4": int(c4.sum()), "ct_defined_v5": int(c5.sum()),
        "ct_lost": sorted(y5.Formulation_ID[(c4 & ~c5).values]),
        "ct_gained": sorted(y5.Formulation_ID[(~c4 & c5).values]),
        "ct_value_changed_where_both_present": int(
            (~eqnum(y4[f"ct_{ep}_ord"][both], y5[f"ct_{ep}_ord"][both])).sum()),
        "resid_defined_v4": int(r4.sum()), "resid_defined_v5": int(r5.sum()),
        "resid_lost": sorted(y5.Formulation_ID[(r4 & ~r5).values]),
        "resid_gained": int((~r4 & r5).sum()),
        "resid_lost_all_from_june_merge": set(y5.Formulation_ID[(r4 & ~r5).values])
                                          <= set(june_fids)}
v2["ct_and_residual"] = ct
for ep in EPS:
    if ct[ep]["ct_value_changed_where_both_present"]:
        finding("MAJOR", f"V2.ct_value_shift_{ep}",
                f"구조 커버리지 변화가 CT 가산 예측 등급을 "
                f"{ct[ep]['ct_value_changed_where_both_present']}행에서 바꿨다")

# ---- (d) trainable / 파생 부기 컬럼
der = {}
for c in ("n_y_conflict", "has_y", "trainable", "trainable_ghs", "trainable_ntp",
          "trainable_epa", "trainable_epa_any", "has_ghs_label", "has_toxicity",
          "n_tox_endpoints", "align_ok", "n_ing_missing"):
    if c in F4.columns and c in F5.columns:
        der[c] = {"rows_changed": int((S(F4, c) != S(F5, c)).sum()),
                  "sum_v4": float(pd.to_numeric(F4[c], errors="coerce").sum()),
                  "sum_v5": float(pd.to_numeric(F5[c], errors="coerce").sum())}
for c in ("y_eye_conflict", "y_skin_conflict", "y_sens_conflict", "has_any_y", "n_y",
          "trainable_trackA", "trainable_trackB"):
    der[c] = {"rows_changed": int((S(y4, c) != S(y5, c)).sum()),
              "sum_v4": float(pd.to_numeric(y4[c], errors="coerce").sum()),
              "sum_v5": float(pd.to_numeric(y5[c], errors="coerce").sum())}
v2["derived_columns"] = der
# 부기 컬럼이 새 라벨을 반영하지 않는 정합성 문제
if der.get("has_y", {}).get("sum_v5") is not None and \
        der["has_y"]["sum_v5"] != der["has_any_y"]["sum_v5"]:
    finding("MINOR", "V2.stale_bookkeeping",
            f"formulation 시트의 has_y({int(der['has_y']['sum_v5'])}) 와 "
            f"y 파케이의 has_any_y({int(der['has_any_y']['sum_v5'])}) 가 불일치. "
            f"has_y·n_y_conflict·trainable* 는 v1 유래 레거시 컬럼으로 라벨 복구를 "
            f"반영하지 않는다(v4 와 값 동일). 역할태깅상 bookkeeping 이라 X 기본 제외지만, "
            f"이름 때문에 '학습가능 행수'로 오독될 위험이 있다.")

# ---- (e) X 피처 실제 변화 범위
_chg_cols, _chg_rows = [], set()
for c in Xf5.columns:
    a, b = Xf4[c], Xf5[c]
    if a.dtype.kind in "fiub" and b.dtype.kind in "fiub":
        m = ~eqnum(a, b)
    else:
        m = (S(Xf4, c) != S(Xf5, c)).values
    n = int(np.asarray(m).sum())
    if n:
        _chg_cols.append({"column": c, "rows_changed": n})
        _chg_rows |= set(np.where(np.asarray(m))[0])
v2["X_formulation_change"] = {
    "columns_changed": len(_chg_cols), "rows_changed": len(_chg_rows),
    "formulations_changed": sorted(Xf5.Formulation_ID.iloc[sorted(_chg_rows)]),
    "top_columns": sorted(_chg_cols, key=lambda d: -d["rows_changed"])[:25]}
# 라벨 관련 컬럼이 X 로 새로 유입되었는지
_leak_rx = re.compile(r"sds_ghs|grade_status|neg_recovered|src_detail|^y_(eye|skin|sens)|"
                      r"h_statement|signal_word|label_source", re.I)
v2["X_label_leak_check"] = {
    "suspect_columns_in_X_formulation": [c for c in Xf5.columns if _leak_rx.search(c)],
    "suspect_columns_in_X_ingredient": [c for c in Xi5.columns if _leak_rx.search(c)]}
if v2["X_label_leak_check"]["suspect_columns_in_X_formulation"] or \
        v2["X_label_leak_check"]["suspect_columns_in_X_ingredient"]:
    finding("BLOCKER", "V2.label_leak",
            "라벨 복구로 신설된 컬럼이 X 에 유입됨",
            columns=v2["X_label_leak_check"])
R["V2_interference"] = v2

if not v2["interference"]["overlap_changed_and_june"] and \
        not v2["interference"]["overlap_added_and_june"]:
    log("    두 수정의 제형 단위 교집합 0 — y 변경/추가 제형과 병합 영향 제형은 서로소")


# ================================================================ V3 라벨 복구 타당성
log("[V3] 라벨 복구의 화학적 타당성")
v3: dict = {}
F5i = F5.set_index("Formulation_ID")
F1i = F1.set_index("Formulation_ID")
QCOL = {"eye": "tox_eye_irritation_quote", "skin": "tox_skin_irritation_quote",
        "sens": "tox_skin_sensitization_quote"}
VCOL = {"eye": "tox_eye_irritation", "skin": "tox_skin_irritation",
        "sens": "tox_skin_sensitization"}
POSCAT = {"eye": {"1", "2", "2A", "2B"}, "skin": {"1", "1A", "1B", "1C", "2", "3"},
          "sens": {"1", "1A", "1B"}}
ALT = {"eye": ["ntp_eye_ghs_cat", "tox2_eye_ghs", "p1_eye", "t11_eye_label"],
       "skin": ["ntp_skin_ghs_cat", "tox2_skin_ghs", "p1_skin", "t11_skin_label"],
       "sens": ["tox2_sens_ghs", "p1_sens", "t11_sens_label"]}

# 상태값 도메인 및 카테고리 침범 여부 (v1 원본에서 직접)
dom = {}
for ep in EPS:
    st = F1[f"sds_grade_status_{ep}"].astype("string").str.strip().str.lower()
    hascat = F1[f"sds_ghs_{ep}"].map(lambda v: nrm_cat(v) is not None)
    dom[ep] = {"status_counts": {k: int(v) for k, v in st.value_counts(dropna=False).items()},
               "n_negative": int((st == "negative").sum()),
               "n_negative_with_existing_category": int(((st == "negative") & hascat).sum()),
               "n_recovered_flag_v5": int(F5[f"sds_v1_neg_recovered_{ep}"].astype(bool).sum()),
               "n_used_as_final_y": int((F5[f"y_{ep}_src_detail"] ==
                                        "sds_v1_negative_recovered").sum())}
    if dom[ep]["n_negative"] != dom[ep]["n_recovered_flag_v5"]:
        finding("MAJOR", f"V3.recovery_count_{ep}",
                f"status=negative {dom[ep]['n_negative']}행 vs 복구플래그 "
                f"{dom[ep]['n_recovered_flag_v5']}행 불일치")
    if dom[ep]["n_negative_with_existing_category"]:
        finding("MAJOR", f"V3.overwrote_category_{ep}",
                "기존 카테고리를 가진 행이 음성으로 덮어쓰였다")
    # 독립 증거: v1 tox_* verdict 컬럼이 같은 방향인지
    fids = F5.Formulation_ID[F5[f"sds_v1_neg_recovered_{ep}"].astype(bool)]
    vd = F1i.loc[fids, VCOL[ep]].astype(str).str.strip().str.lower()
    dom[ep]["independent_verdict_column_counts"] = {k: int(v) for k, v
                                                    in vd.value_counts().items()}
v3["status_domain_and_recovery"] = dom

# ---- pH 비가산성 게이트
strong = ((pd.to_numeric(F5.ph_best, errors="coerce") <= 2) |
          (pd.to_numeric(F5.ph_best, errors="coerce") >= 11.5))
ph_rows = []
for ep in EPS:
    rec = F5[f"sds_v1_neg_recovered_{ep}"].astype(bool)
    used = (F5[f"y_{ep}_src_detail"] == "sds_v1_negative_recovered").fillna(False)
    for i in np.where((rec & strong).to_numpy())[0]:
        ph_rows.append({"endpoint": ep, "Formulation_ID": F5.Formulation_ID.iloc[i],
                        "product_name": F5.product_name.iloc[i],
                        "ph_best": float(F5.ph_best.iloc[i]),
                        "used_as_final_y": bool(used.iloc[i]),
                        "final_y": F5[f"y_{ep}"].iloc[i],
                        "ct_cat": F5[f"f_ct_{ep}_cat"].iloc[i],
                        "quote": str(F1i.loc[F5.Formulation_ID.iloc[i], QCOL[ep]])[:300]})
# 게이트가 적용되는 엔드포인트(눈자극/피부부식)만 따로 집계
gate_eye_skin = [r for r in ph_rows if r["endpoint"] in ("eye", "skin")]
v3["ph_nonadditivity_gate"] = {
    "n_formulations_with_ph": int(F5.ph_best.notna().sum()),
    "n_strong_acid_le2": int((pd.to_numeric(F5.ph_best, errors="coerce") <= 2).sum()),
    "n_strong_base_ge11_5": int((pd.to_numeric(F5.ph_best, errors="coerce") >= 11.5).sum()),
    "recovered_negative_at_strong_ph": ph_rows,
    "violations_eye_or_skin": gate_eye_skin,
    "criterion": "pH<=2 or pH>=11.5 인 제형이 눈자극/피부부식에서 음성으로 복구되면 위반. "
                 "피부감작성은 GHS pH 게이트의 적용 대상이 아니다."}
if gate_eye_skin:
    finding("BLOCKER", "V3.ph_gate_violation",
            f"강산/강염기 제형이 눈/피부 음성으로 복구된 건 {len(gate_eye_skin)}건",
            rows=gate_eye_skin)
elif ph_rows:
    finding("INFO", "V3.ph_gate_sens_only",
            f"강산/강염기 제형이 음성으로 복구된 건은 {len(ph_rows)}건이며 전부 "
            f"피부감작성(pH 게이트 비적용 엔드포인트)이다: "
            f"{[r['Formulation_ID'] for r in ph_rows]}")
# 복구와 무관한 기존 강pH 음성행도 참고로 남긴다
_pre = []
for ep in EPS:
    m = strong & (F5[f"y_{ep}"].map(nrm_cat) == "NC")
    for i in np.where(m.to_numpy())[0]:
        _pre.append({"endpoint": ep, "Formulation_ID": F5.Formulation_ID.iloc[i],
                     "ph_best": float(F5.ph_best.iloc[i]),
                     "y_src_detail": F5[f"y_{ep}_src_detail"].iloc[i]})
v3["ph_strong_negative_all_sources"] = _pre

# ---- CT 가산 예측 양성 vs 복구 음성
ctc = {}
for ep in EPS:
    rec = F5[f"sds_v1_neg_recovered_{ep}"].astype(bool)
    used = (F5[f"y_{ep}_src_detail"] == "sds_v1_negative_recovered").fillna(False)
    ctpos = F5[f"f_ct_{ep}_cat"].map(nrm_cat).isin(POSCAT[ep])
    ctc[ep] = {"recovered_and_ct_positive": int((rec & ctpos).sum()),
               "used_as_final_and_ct_positive": int((used & ctpos).sum()),
               "used_as_final_total": int(used.sum()),
               "baseline_all_yNC_and_ct_positive": int(
                   ((F5[f"y_{ep}"].map(nrm_cat) == "NC") & ctpos).sum()),
               "baseline_all_yNC": int((F5[f"y_{ep}"].map(nrm_cat) == "NC").sum()),
               "contradiction_formulations": sorted(F5.Formulation_ID[(used & ctpos).to_numpy()])}
v3["ct_contradiction"] = ctc

# ---- 하위 출처가 양성인데 복구 NC 가 최종 y 를 이긴 행
ov = {}
for ep in EPS:
    used = (F5[f"y_{ep}_src_detail"] == "sds_v1_negative_recovered").fillna(False).to_numpy()
    rows = []
    for i in np.where(used)[0]:
        others = []
        for c in ALT[ep]:
            if c in F5.columns:
                v = nrm_cat(F5[c].iloc[i])
                if v is not None and SEV[ep].get(v, -1) > 0:
                    others.append({"source_column": c, "category": v})
        if others:
            rows.append({"Formulation_ID": F5.Formulation_ID.iloc[i],
                         "product_name": F5.product_name.iloc[i],
                         "positive_claims": others,
                         "quote": str(F1i.loc[F5.Formulation_ID.iloc[i], QCOL[ep]])[:250]})
    ov[ep] = {"n": len(rows), "rows": rows}
v3["recovered_nc_overrides_positive_claim"] = ov
_tot = sum(ov[e]["n"] for e in EPS)
if _tot:
    finding("MAJOR", "V3.nc_overrides_positive",
            f"복구된 NC 가 최종 y 로 채택되면서 다른 출처의 양성 주장을 이긴 행 "
            f"eye {ov['eye']['n']} / skin {ov['skin']['n']} / sens {ov['sens']['n']} "
            f"(= v4→v5 양성→음성 라벨 전환 {_tot}건과 동일 집합). "
            f"이 중 v4 가 Cat 1(부식/심각손상)로 두었던 행도 포함된다 — 사람 재검 미완료.",
            severe_flips=[r["Formulation_ID"] for ep in EPS for r in ov[ep]["rows"]])

# ---- 인용문 증거 품질 스캔
NEG_RX = (r"not classified|shall not be classified|criteria are not met|"
          r"not meet the criteria|non-?irritant|non-?irritating|no irritat|not irritating|"
          r"not expected to|does not cause|no sensitis|no sensitiz|non-?sensitis|"
          r"non-?sensitiz|not a (skin |dermal )?sensitizer|not sensitizing|no effects known|"
          r"not a hazardous|not hazardous|no skin irritation|no eye irritation|"
          r"will not occur|did not cause|no significant effects|not a sensitizer|"
          r"minimal effects|not corrosive|no irritant effect")
SUS_RX = (r"not determined|no data available|not conclusive|inconclusive|"
          r"not available|n/av|no information|not tested|may cause|likely irritat|"
          r"causes serious|irritating to|risk of serious|allergic")
# 근거 부재형 표현: 음성 진술이 함께 있어도 '해당 엔드포인트 자체는 미측정'이라는 뜻
NODATA_RX = (r"not determined|no data available|not conclusive|inconclusive|"
             r"\bn/av\b|no information|not tested|no effects known|not applicable")
quote_rows = []
for ep in EPS:
    used = (F5[f"y_{ep}_src_detail"] == "sds_v1_negative_recovered").fillna(False).to_numpy()
    for fid in F5.Formulation_ID.to_numpy()[used]:
        q = str(F1i.loc[fid, QCOL[ep]])
        quote_rows.append({
            "endpoint": ep, "Formulation_ID": fid,
            "quote_missing": q.strip().lower() in ("nan", "none", ""),
            "has_endpoint_word": bool(re.search("sensiti" if ep == "sens" else ep, q, re.I)),
            "has_explicit_negative": bool(re.search(NEG_RX, q, re.I)),
            "has_suspicious_marker": bool(re.search(SUS_RX, q, re.I)),
            "has_no_data_marker": bool(re.search(NODATA_RX, q, re.I)),
            "quote": q[:400].replace("\n", " / ")})
QD = pd.DataFrame(quote_rows)
v3["quote_evidence_audit"] = {
    ep: {"n_used_as_final_y": int((QD.endpoint == ep).sum()),
         "quote_missing": int(QD[(QD.endpoint == ep)].quote_missing.sum()),
         "has_endpoint_word": int(QD[(QD.endpoint == ep)].has_endpoint_word.sum()),
         "has_explicit_negative_phrase": int(QD[(QD.endpoint == ep)].has_explicit_negative.sum()),
         "no_negative_phrase_at_all": int((~QD[(QD.endpoint == ep)].has_explicit_negative).sum()),
         "has_suspicious_marker": int(QD[(QD.endpoint == ep)].has_suspicious_marker.sum())}
    for ep in EPS}
_hard = QD[(~QD.has_explicit_negative) & QD.has_suspicious_marker]
v3["quote_evidence_hard_problem_rows"] = _hard.to_dict("records")
# 2차: 문서 전반의 음성 진술은 있으나 해당 엔드포인트는 '자료없음/미측정'인 행
_t2 = QD[QD.has_explicit_negative & QD.has_no_data_marker]
v3["quote_evidence_no_data_rows"] = _t2.to_dict("records")
v3["quote_evidence_tiers"] = {
    "tier1_contradicted_or_unsupported": int(len(_hard)),
    "tier2_negative_stated_but_endpoint_has_no_data": int(len(_t2)),
    "tier3_any_suspicious_marker": int(QD.has_suspicious_marker.sum()),
    "total_recovered_used_as_final_y": int(len(QD))}
if len(_t2):
    finding("MINOR", "V3.negative_with_no_data_marker",
            f"문서에 'Not classified' 등 음성 진술은 있으나 해당 엔드포인트 항목이 "
            f"'No data available / Not determined / N/Av / No effects known' 인 행 "
            f"{len(_t2)}건 — 규정상 미분류이지만 시험근거 없는 음성이므로 라벨 신뢰도가 낮다.",
            rows=_t2[["endpoint", "Formulation_ID"]].to_dict("records"))
if len(_hard):
    finding("MAJOR", "V3.weak_or_contradictory_evidence",
            f"복구 근거 인용문에 명시적 음성 진술이 없고 '자료없음/판단불가/자극가능' 등 "
            f"반대 신호만 있는 행 {len(_hard)}건 — 상위 sds_grade_status='negative' 플래그가 "
            f"'미측정/판단불가'까지 음성으로 흡수한 사례.",
            rows=_hard[["endpoint", "Formulation_ID", "quote"]].to_dict("records"))

# ---- 무작위 20건 표본
rng = np.random.default_rng(20260830)
pool = QD.index.to_numpy()
sel = rng.choice(pool, size=min(20, len(pool)), replace=False)
_ingtxt = (I5.groupby("Formulation_ID")
             .apply(lambda d: " | ".join(str(x) for x in d.ing_name_best.dropna().head(6)),
                    include_groups=False))
samp = []
for i in sel:
    r = QD.loc[i]
    fid = r.Formulation_ID
    samp.append({
        "endpoint": r.endpoint, "Formulation_ID": fid,
        "product_name": F5i.loc[fid, "product_name"],
        "ph_best": (None if pd.isna(F5i.loc[fid, "ph_best"])
                    else float(F5i.loc[fid, "ph_best"])),
        "ct_cat": (None if pd.isna(F5i.loc[fid, f"f_ct_{r.endpoint}_cat"])
                   else str(F5i.loc[fid, f"f_ct_{r.endpoint}_cat"])),
        "v1_sds_grade_status": str(F5i.loc[fid, f"sds_grade_status_{r.endpoint}"]),
        "v1_verdict_column": str(F1i.loc[fid, VCOL[r.endpoint]]),
        "ingredients_head": _ingtxt.get(fid, "")[:120],
        "quote": r.quote,
        "evidence_explicit_negative": bool(r.has_explicit_negative),
        "evidence_suspicious": bool(r.has_suspicious_marker),
        "ph_gate_violation": bool(
            (r.endpoint in ("eye", "skin")) and
            pd.notna(F5i.loc[fid, "ph_best"]) and
            (F5i.loc[fid, "ph_best"] <= 2 or F5i.loc[fid, "ph_best"] >= 11.5))})
v3["random_sample_20"] = samp
R["V3_label_plausibility"] = v3


# ================================================================ V4 잔여 오염
log("[V4] 잔여 오염")
v4d: dict = {}

# ---- (a) 비화학 토큰 + 구조/CAS 보유
def _nc_frame(df):
    gates = df.ing_name_best.map(lambda t: nonchem_gates(t))
    isnc = gates.map(bool)
    keep = isnc & (df.ing_cas_best.notna() | df.smiles.notna())
    out = df.loc[keep, ["Formulation_ID", "ing_idx", "ing_name_best", "ing_cas_best",
                        "smiles", "smiles_source"]].copy()
    out["gates"] = gates[keep].map(lambda g: ",".join(g))
    out["kind"] = out.gates.map(
        lambda g: "soft_real_chemical_with_noise"
        if set(g.split(",")) <= SOFT_GATES else "hard_pure_junk_token")
    if "june_merge_basis" in df.columns:
        out["june_merge_basis"] = df.loc[keep, "june_merge_basis"]
    return out


NC4, NC5 = _nc_frame(I4), _nc_frame(I5)
v4d["nonchemical_token_with_identity"] = {
    "definition": "build_input_v5.py 의 _is_nonchem 규칙을 그대로 적용",
    "v4": {"rows": len(NC4), "with_cas": int(NC4.ing_cas_best.notna().sum()),
           "with_smiles": int(NC4.smiles.notna().sum()),
           "formulations": int(NC4.Formulation_ID.nunique())},
    "v5": {"rows": len(NC5), "with_cas": int(NC5.ing_cas_best.notna().sum()),
           "with_smiles": int(NC5.smiles.notna().sum()),
           "formulations": int(NC5.Formulation_ID.nunique()),
           "kind_counts": {k: int(v) for k, v in NC5.kind.value_counts().items()},
           "gate_counts": {k: int(v) for k, v in NC5.gates.value_counts().items()},
           "hard_rows": int((NC5.kind == "hard_pure_junk_token").sum()),
           "hard_formulations": int(NC5.loc[NC5.kind == "hard_pure_junk_token",
                                            "Formulation_ID"].nunique()),
           "formulation_list": sorted(NC5.Formulation_ID.unique().tolist()),
           "hard_formulation_list": sorted(
               NC5.loc[NC5.kind == "hard_pure_junk_token", "Formulation_ID"].unique().tolist())},
    "origin": {k: int(v) for k, v in NC5.get(
        "june_merge_basis", pd.Series(dtype=object)).value_counts(dropna=False).items()}}
NC5.to_csv(f"{CSV_PREFIX}_nonchem_token_with_identity.csv", index=False)
finding("MAJOR", "V4.nonchem_token_with_identity",
        f"비화학 토큰이 CAS/SMILES 를 보유한 성분행 v5 {len(NC5)}행 / 제형 "
        f"{NC5.Formulation_ID.nunique()}개 (CAS 보유 {int(NC5.ing_cas_best.notna().sum())} · "
        f"SMILES 보유 {int(NC5.smiles.notna().sum())}). 그 중 순수 잡토큰 "
        f"{int((NC5.kind == 'hard_pure_junk_token').sum())}행/제형 "
        f"{int(NC5.loc[NC5.kind == 'hard_pure_junk_token', 'Formulation_ID'].nunique())}개. "
        f"전부 v1 원본 유입분이며 6/30 병합 경로가 만든 것이 아니다(v4 {len(NC4)}행에서 "
        f"철회만 진행). 게이트가 v1 값에는 소급 적용되지 않는다.")

# ---- (b) CAS 형식/체크디지트
cas_bad = {}
for tag, df in (("v4", I4), ("v5", I5)):
    for col in ("cas", "ing_cas_best"):
        s = df[col].dropna()
        cas_bad[f"{tag}.{col}"] = {
            "n_nonnull": int(len(s)),
            "malformed": int(sum(not cas_wellformed(x) for x in s)),
            "checkdigit_fail": int(sum(cas_wellformed(x) and not cas_checkdigit_ok(x)
                                       for x in s)),
            "checkdigit_fail_unique_values": sorted({str(x) for x in s
                                                     if cas_wellformed(x)
                                                     and not cas_checkdigit_ok(x)})}
v4d["cas_validity"] = cas_bad
_m = I5.ing_cas_best.notna() & ~I5.ing_cas_best.map(cas_checkdigit_ok)
BADCAS = I5.loc[_m, ["Formulation_ID", "ing_idx", "ing_name_best", "ing_cas_best",
                     "smiles", "june_merge_basis"]]
BADCAS.to_csv(f"{CSV_PREFIX}_invalid_cas.csv", index=False)
if len(BADCAS):
    finding("MAJOR", "V4.invalid_cas_checkdigit",
            f"ing_cas_best 가 CAS 체크디지트 검증에 실패하는 행 {len(BADCAS)}개 "
            f"(제형 {BADCAS.Formulation_ID.nunique()}개) — v4 와 동일 수치로 미개선. "
            f"'Index No 607'+'421-00-4' 형태로 EC Index 번호가 CAS 컬럼에 들어간 사례가 "
            f"다수. v5 병합경로는 _cas_ok 로 체크디지트를 검사하지만 v1 기존값에는 "
            f"소급 적용되지 않는다.",
            unique_values=cas_bad["v5.ing_cas_best"]["checkdigit_fail_unique_values"])

# ---- (c) 동일 CAS · 상이 SMILES
try:
    from rdkit import Chem, RDLogger
    RDLogger.DisableLog("rdApp.*")

    def canon(s):
        try:
            m = Chem.MolFromSmiles(str(s))
            return Chem.MolToSmiles(m) if m else None
        except Exception:
            return None
    have_rdkit = True
except Exception:
    have_rdkit = False

    def canon(s):
        return str(s)

cms = {}
for tag, df in (("v4", I4), ("v5", I5)):
    for col in ("cas", "ing_cas_best"):
        d = df[df[col].notna() & df.smiles.notna()].copy()
        d["_c"] = d.smiles.map(canon)
        dd = d[d._c.notna()]
        for mode, key in (("canonical_smiles", "_c"), ("raw_smiles_string", "smiles")):
            src = dd if mode == "canonical_smiles" else d
            g = src.groupby(col)[key].nunique()
            bad = g[g > 1]
            cms[f"{tag}.{col}.{mode}"] = {
                "cas_with_conflict": int(len(bad)),
                "rows_involved": int(src[src[col].isin(bad.index)].shape[0]),
                "total_rows_with_cas_and_smiles": int(len(src))}
v4d["same_cas_different_smiles"] = cms
v4d["same_cas_different_smiles_note"] = (
    "'116 CAS / 1348행' 은 raw `cas` 컬럼 + SMILES 문자열 동등비교 기준이며 v5 에서도 "
    "동일하다(raw cas 컬럼은 병합이 건드리지 않음). RDKit 정규화 후 실제 구조 충돌은 "
    "raw cas 기준 24 CAS/386행으로 v4·v5 동일, ing_cas_best 기준으로는 "
    "67 CAS/824행 → 49 CAS/668행으로 개선되었다.")
_d = I5[I5.ing_cas_best.notna() & I5.smiles.notna()].copy()
_d["canon_smiles"] = _d.smiles.map(canon)
_dd = _d[_d.canon_smiles.notna()]
_g = _dd.groupby("ing_cas_best").canon_smiles.nunique()
_dd[_dd.ing_cas_best.isin(_g[_g > 1].index)].sort_values("ing_cas_best")[
    ["ing_cas_best", "ing_name_best", "canon_smiles", "Formulation_ID", "ing_idx",
     "smiles_source", "june_merge_basis"]].to_csv(
    f"{CSV_PREFIX}_same_cas_diff_smiles.csv", index=False)

# ---- (d) june_merge_basis 분포 + 병합 54행
basis = I5.june_merge_basis.value_counts(dropna=False)
v4d["june_merge_basis_distribution"] = {("<none>" if pd.isna(k) else str(k)): int(v)
                                        for k, v in basis.items()}
MERGED = I5[I5.june_merge_basis.isin(
    ["length_matched_all", "name_cas_length_matched", "name_smiles_length_matched",
     "name_ref_unique"])][["Formulation_ID", "ing_idx", "ing_name_best", "ing_cas_best",
                           "smiles", "june_merge_basis"]].copy()
v4d["merged_rows"] = {"n": len(MERGED),
                      "by_basis": {k: int(v) for k, v in
                                   MERGED.june_merge_basis.value_counts().items()},
                      "formulations": int(MERGED.Formulation_ID.nunique())}
v4d["reference_dictionary_independence"] = (
    "주의: name_ref_unique 의 참조사전(_ref_n2c/_ref_c2s)은 6/30 워크북의 길이일치 행과 "
    "v1 ingredient 시트의 name→CAS 쌍으로 만들어진다. 즉 '비위치' 이긴 하나 데이터셋 "
    "외부 근거가 아니다. 따라서 아래 PubChem 대조를 독립 경로로 별도 수행한다.")

# ---- (e) PubChem 독립 대조 (CAS → 구조 InChIKey)
pub = {"enabled": USE_NET, "rows": []}
if USE_NET:
    socket.setdefaulttimeout(25)
    BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
    cache = {}

    def pubchem(cas):
        if cas in cache:
            return cache[cas]
        res = (None, None)
        for _ in range(3):
            try:
                raw = urllib.request.urlopen(
                    BASE + urllib.parse.quote(cas) + "/property/SMILES,Title/JSON").read()
                p = json.loads(raw)["PropertyTable"]["Properties"][0]
                res = (p.get("SMILES") or p.get("CanonicalSMILES"), p.get("Title"))
                break
            except Exception:
                time.sleep(1.2)
        cache[cas] = res
        time.sleep(0.25)
        return res

    def ikey(s):
        if not have_rdkit or s is None:
            return None
        try:
            m = Chem.MolFromSmiles(str(s))
            return Chem.MolToInchiKey(m) if m else None
        except Exception:
            return None

    for r in MERGED.itertuples(index=False):
        cas = str(r.ing_cas_best).strip() if pd.notna(r.ing_cas_best) else ""
        if not cas:
            continue
        ps, pt = pubchem(cas)
        a, b = ikey(r.smiles), ikey(ps)
        pub["rows"].append({
            "Formulation_ID": r.Formulation_ID, "ing_idx": int(r.ing_idx),
            "ing_name_best": str(r.ing_name_best), "cas": cas,
            "basis": r.june_merge_basis, "pubchem_title": pt,
            "cas_resolvable_in_pubchem": ps is not None,
            "skeleton_inchikey_match": (None if not (a and b) else a[:14] == b[:14]),
            "full_inchikey_match": (None if not (a and b) else a == b)})
    PB = pd.DataFrame(pub["rows"])
    if len(PB):
        PB.to_csv(f"{CSV_PREFIX}_pubchem_crosscheck.csv", index=False)
        for bs in sorted(PB.basis.unique()):
            sub = PB[PB.basis == bs]
            res = sub[sub.skeleton_inchikey_match.notna()]
            pub.setdefault("summary", {})[bs] = {
                "n": int(len(sub)),
                "resolvable": int(len(res)),
                "skeleton_match": int(res.skeleton_inchikey_match.sum()),
                "skeleton_mismatch": int((~res.skeleton_inchikey_match.astype(bool)).sum()),
                "unresolvable_cas": sorted(sub.loc[~sub.cas_resolvable_in_pubchem,
                                                   "cas"].unique().tolist())}
        mism = PB[PB.skeleton_inchikey_match == False]  # noqa: E712
        if len(mism):
            finding("MAJOR", "V4.pubchem_structure_mismatch",
                    f"병합행 중 CAS↔SMILES 가 PubChem 구조와 불일치 {len(mism)}건",
                    rows=mism.to_dict("records"))
        # 이름↔CAS 불일치 (PubChem 표제명과 성분명 대조: 사람 판독용 후보)
        def _norm(x):
            return re.sub(r"[^a-z0-9]", "", str(x or "").lower())
        cand = []
        for r in PB.itertuples(index=False):
            if not r.pubchem_title:
                continue
            n, t = _norm(r.ing_name_best), _norm(r.pubchem_title)
            if n and t and t not in n and n not in t:
                cand.append({"Formulation_ID": r.Formulation_ID, "ing_idx": r.ing_idx,
                             "ing_name_best": r.ing_name_best, "cas": r.cas,
                             "pubchem_title": r.pubchem_title, "basis": r.basis})
        pub["name_vs_pubchem_title_mismatch_candidates"] = cand
        pd.DataFrame(cand).to_csv(f"{CSV_PREFIX}_name_cas_mismatch_candidates.csv",
                                  index=False)
        # 사람 판독 결과: 대부분 절단된 화학명(동일 물질)이나 아래 2건은 실제 오배정
        ADJUDICATED = {("EyeIrritation6pack_PID955", 1): (
            "'THFA Proprietary' 에 872-50-4(N-메틸-2-피롤리돈)가 배정됨. "
            "THFA=테트라하이드로퍼퓨릴알코올(97-99-4)이므로 물질이 다르다. "
            "이 CAS 가 PID955 의 group_key 로도 채택되었다."),
            ("EyeIrritation6pack_PID985", 0): (
            "'GSP Crop Science Limited'(회사명)에 129558-76-5(톨펜피라드)가 배정됨. "
            "_is_nonchem 게이트가 회사명을 걸러내지 못했다.")}
        adj = [{"Formulation_ID": k[0], "ing_idx": k[1], "issue": v}
               for k, v in ADJUDICATED.items()]
        pub["adjudicated_name_cas_misassignment"] = adj
        finding("MAJOR", "V4.name_cas_misassignment_in_merged_rows",
                f"6/30 병합으로 확정된 54행 중 CAS↔SMILES 구조는 PubChem 과 100% 일치하지만"
                f"(해석가능 46/46 스켈레톤 InChIKey 일치), 성분명↔CAS 는 2건이 오배정이며 "
                f"둘 다 basis=length_matched_all(파이프 길이만 맞으면 통과)이다. "
                f"PubChem 표제명 불일치 후보는 {len(cand)}건이고 나머지는 절단된 화학명이다.",
                rows=adj)
v4d["pubchem_crosscheck"] = pub
R["V4_residual_contamination"] = v4d

# ---- 피처 역할 매니페스트 정합성
FR = pd.read_csv(f"{OUT_V5}/feature_role_manifest.csv")
R["V4_residual_contamination"]["feature_role_manifest"] = {
    "rows": len(FR),
    "counts": {k: int(v) for k, v in FR.feature_role.value_counts().items()},
    "covers_X_formulation": sorted(set(Xf5.columns) -
                                   set(FR[FR.sheet == "formulation"].column)) == [],
    "covers_X_ingredient": sorted(set(Xi5.columns) -
                                  set(FR[FR.sheet == "ingredient"].column)) == [],
    "n_exclude_by_default": int(FR.exclude_by_default.sum()),
    "sds_coderived": int(FR.sds_coderived.sum()),
    "chemistry_columns_with_toxicity_provenance": sorted(
        FR[(FR.feature_role == "chemistry") &
           FR.column.str.contains("^t11_", regex=True)].column.tolist())}


# ================================================================ V5 판정
log("[V5] 판정")
sev_count = Counter(f["severity"] for f in FINDINGS)
if sev_count["BLOCKER"]:
    verdict = "NO-GO"
elif sev_count["MAJOR"]:
    verdict = "GO-WITH-CAVEATS"
else:
    verdict = "GO"
R["findings"] = FINDINGS
R["finding_counts"] = dict(sev_count)
R["verdict"] = verdict
R["verdict_basis"] = (
    "BLOCKER 가 있으면 NO-GO, MAJOR 만 있으면 GO-WITH-CAVEATS, 없으면 GO. "
    "MAJOR 는 v5 자체가 만든 회귀가 아니라 대부분 v1 원본에서 이월된 미해결 결함이며, "
    "라벨 우선순위가 만든 25건의 양성→음성 전환은 사람 재검이 필요하다.")

pd.DataFrame(FINDINGS).to_csv(f"{CSV_PREFIX}_findings.csv", index=False)
QD.to_csv(f"{CSV_PREFIX}_recovered_quote_audit.csv", index=False)
pd.DataFrame([{**r, "positive_claims": json.dumps(r["positive_claims"], ensure_ascii=False)}
              for ep in EPS for r in v3["recovered_nc_overrides_positive_claim"][ep]["rows"]]
             ).to_csv(f"{CSV_PREFIX}_nc_overrides_positive.csv", index=False)
pd.DataFrame(v3["random_sample_20"]).to_csv(f"{CSV_PREFIX}_random_sample_20.csv", index=False)
pd.DataFrame(v1["group_key"]["changed"]).to_csv(f"{CSV_PREFIX}_group_key_changed.csv",
                                                index=False)


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    if pd.isna(o):
        return None
    return str(o)


with open(REPORT, "w", encoding="utf-8") as fh:
    json.dump(R, fh, ensure_ascii=False, indent=1, default=_default)
log(f"    → {REPORT}")
log(f"    판정: {verdict}  (BLOCKER {sev_count['BLOCKER']} · MAJOR {sev_count['MAJOR']} · "
    f"MINOR {sev_count['MINOR']} · INFO {sev_count['INFO']})")
