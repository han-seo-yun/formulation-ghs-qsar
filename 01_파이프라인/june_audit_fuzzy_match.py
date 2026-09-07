#!/usr/bin/env python3
"""6/30 성분 정밀감사 <-> 현재(v4) ingredient 시트 퍼지(fuzzy) 성분명 매칭.

배경
----
이전 회수 시도는 "정규화된 성분명 완전일치"만 사용해서 놓친 건이 많았다.
(원인 추정: "Technical" 같은 부가설명, 퍼센트 표기, 성분 나열 순서 차이, 특수문자)
이 스크립트는 difflib.SequenceMatcher.ratio() 기반 유사도 매칭으로 회수 후보를
다시 뽑는다.

대상
----
04_모델산출물/v4/june_audit_reconciliation_remaining_after_v4.csv 에서
recommendation 이 아래 5종인 81건의 Formulation_ID 만 본다.
  RECOVER_CAS_HIGH_CONFIDENCE / RECOVER_CAS_REVIEW / RECOVER_CAS_LOW_CONFIDENCE
  RECOVER_CAS_CHECK_STATUS / RECOVER_SMILES_ONLY
(MANUAL_REVIEW_CONFLICTING_CAS 79건, NO_ACTION 나머지는 대상 제외)

원칙
----
* 읽기 전용. 기존 xlsx / parquet / 파이프라인 스크립트를 절대 수정하지 않는다.
* 산출물은 신규 CSV 1개(회수 "후보" 목록)뿐이며, 실제 반영은 사람이 결정한다.
* match_score < 0.75 는 low_confidence_flag=True 로 표시 -> 자동신뢰 금지, 사람 재검토.

주의: 6/30 감사자료의 인덱스 정렬 문제
------------------------------------
Formulation_Ingredients / _CAS / _SMILES 는 "같은 순서 파이프구분"이 전제지만
실제로는 대상 81건 중 46건에서 길이가 어긋난다(예: PID195 = 성분 5개 / CAS 10개,
MIX1026 = 성분 20개 / CAS 22개). CAS 열에 SDS OCR 잔재(가짜 CAS)가 끼거나 SMILES 가
구조해석 성공분만 부분수록된 탓이다. 인덱스만 믿고 회수하면 엉뚱한 물질이 붙는다
(검증 중 실제 발생: NMP -> BHT SMILES, Toluene -> 371-47-1).
그래서 CAS/SMILES 회수는 인덱스 정렬이 방어가능한 경우로만 제한하고, 근거를
alignment_basis 컬럼에 남긴다. 정렬 불가건은 회수하지 않고 사람에게 넘긴다.

산출: 04_모델산출물/v4/june_audit_fuzzy_recovery_candidates.csv
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

import pandas as pd

BASE = "/Users/hanseoyun/Desktop/260830"
CUR_XLSX = f"{BASE}/04_모델산출물/input_dataset_v4.xlsx"
JUNE_MASTER = (f"{BASE}/formulation_audit_team_share_highlighted_only_20260630/"
               "formulation_ingredients_master_audited.xlsx")
REMAINING_CSV = f"{BASE}/04_모델산출물/v4/june_audit_reconciliation_remaining_after_v4.csv"
OUT_CSV = f"{BASE}/04_모델산출물/v4/june_audit_fuzzy_recovery_candidates.csv"

TARGET_RECOMMENDATIONS = [
    "RECOVER_CAS_HIGH_CONFIDENCE",
    "RECOVER_CAS_REVIEW",
    "RECOVER_CAS_LOW_CONFIDENCE",
    "RECOVER_CAS_CHECK_STATUS",
    "RECOVER_SMILES_ONLY",
]

MATCH_THRESHOLD = 0.60        # 채택 하한
LOW_CONF_THRESHOLD = 0.75     # 이 아래면 사람 재검토 플래그
# 6/30 Formulation_Ingredients 에는 성분명 대신 SDS 본문이 그대로 들어간 행이 섞여
# 있다. 그런 blob 은 성분명으로 볼 수 없어 매칭 후보에서 제외한다(길이 기준).
MAX_JUNE_NAME_LEN = 120
# 성분명 1개짜리 제형에서 CAS 가 여러 개일 때, 몇 개까지 그 성분 귀속으로 볼지.
# (MIX1069 처럼 성분 1개에 CAS 45개인 행은 SDS 전체 추출물이라 귀속 불가로 본다)
MAX_CAS_FOR_SINGLE_INGREDIENT = 3

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")
_SMILES_BAD_RE = re.compile(r"^(nan|none|-|n/?a)$", re.I)

# 성분명 정규화 시 떼어낼 부가설명/역할 표기
_NOISE_WORDS = [
    "technical", "tech grade", "tech", "active ingredient", "active substance",
    "inert ingredient", "inert ingredients", "other ingredients", "ingredient",
    "iso", "iupac", "cas", "cas no", "cas number", "weight", "hazard code",
    "component", "solvent", "surfactant", "emulsifier", "carrier", "diluent",
    "preservative", "antifoam", "biocide", "grade", "pure", "purified",
    "anhydrous", "aqueous solution", "solution", "mixture", "as is",
    "or equivalent", "and isomers", "isomer mixture", "mixed isomers",
    "trade secret", "proprietary", "confidential", "not applicable",
]


# --------------------------------------------------------------------------- #
# 유틸
# --------------------------------------------------------------------------- #
def cas_is_valid(tok: str) -> bool:
    """CAS 형식 + 체크디짓 검증.

    6/30 감사자료의 CAS 열에는 SDS OCR 잔재(예: '44-00-6', '21-00-3')가 섞여 있다.
    형식만 보면 통과하므로 체크디짓까지 확인해 가짜 CAS 를 걸러낸다.
    """
    if not _CAS_RE.match(tok):
        return False
    digits = tok.replace("-", "")
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (i + 1) for i, d in enumerate(reversed(body)))
    return total % 10 == check


def normalize_cas(tok: str) -> str:
    """앞자리 0 패딩 제거 후 표준형으로."""
    t = tok.strip()
    parts = t.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        t = f"{int(parts[0])}-{parts[1]}-{parts[2]}"
    return t


def split_pipe(value) -> list[str]:
    """파이프 구분 문자열 -> 리스트(공백 항목도 인덱스 유지를 위해 ''로 보존)."""
    if pd.isna(value):
        return []
    return [tok.strip() for tok in str(value).split("|")]


def has_value(value) -> bool:
    """현재 데이터에서 '값이 있다'고 볼 수 있는지."""
    if value is None or pd.isna(value):
        return False
    s = str(value).strip()
    return bool(s) and not _SMILES_BAD_RE.match(s)


def normalize_name(name: str) -> str:
    """성분명 정규화: 소문자화 + 퍼센트/범위/부가설명/특수문자 제거."""
    if name is None or pd.isna(name):
        return ""
    s = str(name).lower()
    s = re.sub(r"\s+", " ", s).strip()
    # 퍼센트/범위 표기 제거: "3-5", ">95", "0.005 - <", "38-43", "<= 1", "1 – <"
    s = re.sub(r"[<>=~]+\s*\d*\.?\d*\s*%?", " ", s)
    s = re.sub(r"\d+\.?\d*\s*[-–~]\s*\d+\.?\d*\s*%?", " ", s)
    s = re.sub(r"\d+\.?\d*\s*%", " ", s)
    s = re.sub(r"\bppm\b|\bw/w\b|\bw/v\b|\bv/v\b", " ", s)
    # 괄호 안 부가설명 제거 (화학명 일부일 수도 있어 짧은 것만 제거)
    s = re.sub(r"\(([^()]{0,25})\)", lambda m: " " if _is_noise(m.group(1)) else f"({m.group(1)})", s)
    # 역할/등급 부가설명 단어 제거
    for w in _NOISE_WORDS:
        s = re.sub(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", " ", s)
    # 특수문자 정리 (화학명 구분에 필요한 문자는 남긴다)
    s = re.sub(r"[^a-z0-9\+\-,'\(\)\[\]\.]+", " ", s)
    s = re.sub(r"\s*,\s*", ",", s)
    s = re.sub(r"[\.\-,]+$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_noise(text: str) -> bool:
    t = text.strip().lower()
    return (not t) or (t in _NOISE_WORDS) or bool(re.fullmatch(r"[\d\.\s%<>=~\-–]+", t))


def similarity(a: str, b: str) -> float:
    """정규화된 성분명 간 difflib 유사도(0.0~1.0).

    difflib 의 ratio() 는 순서에 민감해서 'A, B' vs 'B A' 같은 어순 차이를 못 잡는다.
    토큰 정렬본과의 ratio() 도 함께 계산해 더 높은 쪽을 쓴다.
    """
    if not a or not b:
        return 0.0
    direct = SequenceMatcher(None, a, b).ratio()
    a_sorted = " ".join(sorted(re.split(r"[\s,]+", a)))
    b_sorted = " ".join(sorted(re.split(r"[\s,]+", b)))
    token = SequenceMatcher(None, a_sorted, b_sorted).ratio()
    return max(direct, token)


# --------------------------------------------------------------------------- #
# 1) 로딩
# --------------------------------------------------------------------------- #
print("[1/4] 파일 로딩")
remaining = pd.read_csv(REMAINING_CSV)
targets = remaining[remaining["recommendation"].isin(TARGET_RECOMMENDATIONS)].copy()
target_fids = list(dict.fromkeys(targets["Formulation_ID"].astype(str)))
print(f"  대상 recommendation 분포:\n{targets['recommendation'].value_counts().to_string()}")
print(f"  대상 Formulation_ID: {len(target_fids)}건")

cur = pd.read_excel(CUR_XLSX, sheet_name="ingredient")
cur["Formulation_ID"] = cur["Formulation_ID"].astype(str)
cur = cur[cur["Formulation_ID"].isin(target_fids)][
    ["Formulation_ID", "ing_idx", "ingredient_name", "ing_name_best", "ing_cas_best", "smiles"]
].copy()
print(f"  현재 ingredient 시트에서 대상 제형 행: {len(cur)}행 "
      f"({cur['Formulation_ID'].nunique()}개 제형)")

june = pd.read_excel(JUNE_MASTER, sheet_name="Formulations")
june["Formulation_ID"] = june["Formulation_ID"].astype(str)
june = june[june["Formulation_ID"].isin(target_fids)].copy()
print(f"  6/30 감사자료에서 대상 제형 행: {len(june)}행")

rec_by_fid = dict(zip(targets["Formulation_ID"].astype(str), targets["recommendation"]))
june_by_fid = {r["Formulation_ID"]: r for _, r in june.iterrows()}


# --------------------------------------------------------------------------- #
# 2) 제형별 퍼지 매칭
# --------------------------------------------------------------------------- #
print("[2/4] 제형별 fuzzy 매칭 (difflib SequenceMatcher.ratio)")
rows: list[dict] = []
diag = {
    "fid_no_current_name": [],   # 현재 ing_name_best 가 전부 비어 매칭 자체가 불가
    "fid_no_june_name": [],      # 6/30 Formulation_Ingredients 에 쓸 성분명이 없음
    "fid_matched": set(),        # ratio>=0.6 매칭이 하나라도 성립
    "fid_recovered": set(),      # 회수할 CAS/SMILES 가 실제로 생긴 제형
    "fid_nothing_to_recover": set(),  # 매칭은 됐지만 회수할 값이 없음
    "fid_unaligned_blocked": set(),   # 이름은 매칭됐는데 6/30 CAS 정렬불가로 회수 보류
}

for fid in target_fids:
    jrow = june_by_fid.get(fid)
    if jrow is None:
        continue
    crows = cur[cur["Formulation_ID"] == fid]

    june_names_raw = split_pipe(jrow["Formulation_Ingredients"])
    june_cas_raw = split_pipe(jrow["Formulation_Ingredients_CAS"])
    june_smi_raw = split_pipe(jrow["Formulation_Ingredients_SMILES"])
    audit_status = jrow["Audit_Status"]

    # --- CAS/SMILES 인덱스 정렬 가능성 판정 ---
    june_names_nonempty = [n for n in june_names_raw if n]
    single_named = len(june_names_nonempty) == 1
    valid_cas = [normalize_cas(c) for c in june_cas_raw if cas_is_valid(normalize_cas(c))]
    smi_nonempty = [s for s in june_smi_raw if has_value(s)]

    n_name = len(june_names_raw)
    if n_name and n_name == len(june_cas_raw):
        cas_mode, cas_list = "INDEX_ALIGNED", june_cas_raw
    elif n_name and n_name == len(valid_cas):
        # 가짜 CAS 토큰만 끼어든 경우: 제거 후 순서가 맞아떨어지면 정렬로 인정
        cas_mode, cas_list = "VALID_FILTERED_ALIGNED", valid_cas
    elif single_named and 1 <= len(valid_cas) <= MAX_CAS_FOR_SINGLE_INGREDIENT:
        cas_mode, cas_list = "SINGLE_INGREDIENT", None
    else:
        cas_mode, cas_list = "UNALIGNED_CAS_NOT_RECOVERED", None

    if n_name and n_name == len(june_smi_raw):
        smi_mode, smi_list = "INDEX_ALIGNED", june_smi_raw
    elif single_named and len(smi_nonempty) == 1:
        smi_mode, smi_list = "SINGLE_INGREDIENT", None
    else:
        smi_mode, smi_list = "UNALIGNED_SMILES_NOT_RECOVERED", None

    usable_june = [
        (i, n) for i, n in enumerate(june_names_raw)
        if n and len(n) <= MAX_JUNE_NAME_LEN
    ]
    cur_named = [
        (idx, r) for idx, r in crows.iterrows() if normalize_name(r["ing_name_best"])
    ]

    if not cur_named:
        diag["fid_no_current_name"].append(fid)
        continue
    if not usable_june:
        diag["fid_no_june_name"].append(fid)
        continue

    # 모든 (현재행 x 6/30성분) 쌍의 유사도 -> 점수 내림차순 그리디 1:1 배정
    pairs = []
    for cidx, crow in cur_named:
        cn = normalize_name(crow["ing_name_best"])
        for jidx, jname in usable_june:
            score = similarity(cn, normalize_name(jname))
            if score >= MATCH_THRESHOLD:
                pairs.append((score, cidx, jidx))
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))

    used_cur: set = set()
    used_june: set = set()
    for score, cidx, jidx in pairs:
        if cidx in used_cur or jidx in used_june:
            continue
        used_cur.add(cidx)
        used_june.add(jidx)
        diag["fid_matched"].add(fid)

        crow = cur.loc[cidx]
        jname = june_names_raw[jidx]

        # --- CAS 회수 (정렬 근거가 있을 때만) ---
        rec_cas = ""
        if not has_value(crow["ing_cas_best"]):
            if cas_mode == "SINGLE_INGREDIENT":
                rec_cas = " | ".join(dict.fromkeys(valid_cas))
            elif cas_list is not None and jidx < len(cas_list):
                c = normalize_cas(cas_list[jidx])
                if cas_is_valid(c):
                    rec_cas = c

        # --- SMILES 회수 (정렬 근거가 있을 때만) ---
        rec_smi = ""
        if not has_value(crow["smiles"]):
            if smi_mode == "SINGLE_INGREDIENT":
                rec_smi = smi_nonempty[0]
            elif smi_list is not None and jidx < len(smi_list) and has_value(smi_list[jidx]):
                rec_smi = smi_list[jidx]

        if not rec_cas and not rec_smi:
            # 이름은 매칭됐지만 회수할 값이 없음(이미 값 보유 or 정렬불가) -> 후보 아님
            if cas_mode.startswith("UNALIGNED") and not has_value(crow["ing_cas_best"]):
                diag["fid_unaligned_blocked"].add(fid)
            continue

        basis = []
        if rec_cas:
            basis.append(f"CAS:{cas_mode}")
        if rec_smi:
            basis.append(f"SMILES:{smi_mode}")

        diag["fid_recovered"].add(fid)
        rows.append({
            "Formulation_ID": fid,
            "original_recommendation": rec_by_fid.get(fid, ""),
            "current_ing_name": crow["ing_name_best"],
            "matched_june_name": jname,
            "match_score": round(float(score), 4),
            "recovered_cas": rec_cas,
            "recovered_smiles": rec_smi,
            "june_audit_status": audit_status,
            "low_confidence_flag": bool(score < LOW_CONF_THRESHOLD),
            "alignment_basis": "; ".join(basis),
        })

    if fid in diag["fid_matched"] and fid not in diag["fid_recovered"]:
        diag["fid_nothing_to_recover"].add(fid)


# --------------------------------------------------------------------------- #
# 3) 저장
# --------------------------------------------------------------------------- #
print("[3/4] 산출물 저장")
out_cols = ["Formulation_ID", "original_recommendation", "current_ing_name",
            "matched_june_name", "match_score", "recovered_cas", "recovered_smiles",
            "june_audit_status", "low_confidence_flag",
            # 아래는 회수값의 인덱스정렬 근거(요청 스펙 외 추가). 사람 재검토용.
            "alignment_basis"]
out = pd.DataFrame(rows, columns=out_cols)
if not out.empty:
    out = out.sort_values(["low_confidence_flag", "match_score", "Formulation_ID"],
                          ascending=[True, False, True])
out.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
print(f"  저장: {OUT_CSV}  ({len(out)}행)")


# --------------------------------------------------------------------------- #
# 4) 요약
# --------------------------------------------------------------------------- #
print("[4/4] 요약")
n_target = len(target_fids)
n_recovered_fid = len(diag["fid_recovered"])
print(f"  대상 제형              : {n_target}건")
print(f"  fuzzy 매칭 성립(회수후보 존재): {n_recovered_fid}건")
print(f"  이름매칭 성립(제형 단위) : {len(diag['fid_matched'])}건")
print(f"  매칭됐지만 회수값 없음  : {len(diag['fid_nothing_to_recover'])}건")
print(f"  매칭됐지만 6/30 CAS 정렬불가로 보류: {len(diag['fid_unaligned_blocked'])}건 "
      f"-> {sorted(diag['fid_unaligned_blocked'])}")
print(f"  현재 성분명 자체가 비어 매칭불가: {len(diag['fid_no_current_name'])}건")
print(f"  6/30 성분명이 없음/본문blob     : {len(diag['fid_no_june_name'])}건")

if not out.empty:
    lo = out[out["low_confidence_flag"]]
    hi = out[~out["low_confidence_flag"]]
    print(f"\n  후보 행 {len(out)}개 중 low_confidence(0.60~0.75): {len(lo)}행 / "
          f"신뢰(>=0.75): {len(hi)}행")
    print(f"  제형 단위: low_confidence만 있는 제형 "
          f"{len(set(lo['Formulation_ID']) - set(hi['Formulation_ID']))}건, "
          f">=0.75 후보 보유 제형 {hi['Formulation_ID'].nunique()}건")
    print(f"\n  CAS 회수 후보 행: {(out['recovered_cas'] != '').sum()}  "
          f"SMILES 회수 후보 행: {(out['recovered_smiles'] != '').sum()}")
    print("\n  original_recommendation 별 회수 제형 수:")
    print(out.groupby("original_recommendation")["Formulation_ID"].nunique().to_string())
    print("\n  match_score 분포:")
    print(out["match_score"].describe().to_string())
    print("\n  score 구간별 행수:")
    print(pd.cut(out["match_score"], [0.599, 0.75, 0.9, 0.9999, 1.0],
                 labels=["0.60-0.75(low)", "0.75-0.90", "0.90-1.00", "1.00(exact)"]
                 ).value_counts().sort_index().to_string())
    print("\n  alignment_basis 분포:")
    print(out["alignment_basis"].value_counts().to_string())
