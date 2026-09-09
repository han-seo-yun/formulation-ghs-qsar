#!/usr/bin/env python3
"""input_dataset_v5 빌더 — v4와 동일하나 6/30 감사 워크북 병합부(§4x)를 안전화했다.

v4 → v5 변경점 (그 외 로직·컬럼은 전부 동일)
  v4 는 워크북의 이름/CAS/SMILES 파이프리스트를 `range(max(len...))` 로 **위치조인**했다.
  대상 195 제형 중 163(83.6%)에서 세 리스트 길이가 달라 인덱스가 어긋났고, 그 결과
  오프바이원 CAS 오배정과 "Content (W/W)" 같은 표 헤더 토큰에 실제 화학구조가 붙는
  오염이 v4 산출물(112행)로 전파되었다. (근거: 04_모델산출물/v4_fixed/
  june_merge_defect_evidence.csv, 생성기 audit_june_merge_defect.py)

  v5 의 병합 규칙 — 위치조인은 **리스트 길이가 같을 때만** 허용한다. 실증 결과
  len(names)==len(cas) 인 경우 위치쌍은 참조사전 대조 232/232 정확(오류 0),
  len(names)==len(smiles) 인 경우 71/71 정확이었다. 즉 길이 일치는 워크북 자신의
  정렬 계약이 지켜졌다는 신호이고, 오류는 전부 길이 불일치 행에서만 발생했다.
    basis=length_matched_all      이름/CAS/SMILES 3개 길이 동일 → CAS+SMILES 병합
    basis=name_cas_length_matched 이름/CAS 길이만 동일        → CAS 만 병합
    basis=name_smiles_length_matched 이름/SMILES 길이만 동일   → SMILES 만 병합
    basis=name_ref_unique         길이 불일치 → 위치조인 대신 비위치 참조사전
                                  (이름→CAS 가 프로젝트 전체에서 유일할 때만) 로 복구
    basis=skipped_*               위 어느 조건도 못 만족 → 병합 스킵(사유 기록)
  추가 게이트(모든 tier 공통): 이름 토큰이 화학물질명일 것(SDS 표 헤더/단위/숫자 배제),
  CAS 가 정규형식 + 체크디지트 통과, SMILES 가 RDKit 파싱 통과, 제형 내 동일 정규화
  이름의 중복 항목이 상충하지 않을 것.
  v4 의 "기존 값이 있으면 덮어쓰지 않는다" 원칙은 그대로 유지한다.

--- 이하 v4 원문 ---
input_dataset_v2 빌더 — 2차 배정 수집분 + 검증결과 + 성분/제형 2층 디스크립터.

산출
  input_dataset_v2.xlsx            사람이 읽는 통합본(8시트)
  out/v2/X_ingredient.parquet      성분 단위 피처행렬 (5287 × N)
  out/v2/X_formulation.parquet     제형 단위 피처행렬 (1675 × M)
  out/v2/y_formulation.parquet     타깃 + 출처 + 충돌플래그 + Track B 잔차타깃
  out/v2/groups.parquet            GroupKFold 용 그룹키(주성분 기준 — 누출 방지)
  out/v2/fp_ing_morgan.npz         성분 Morgan(ECFP4, 2048bit)
  out/v2/fp_ing_maccs.npz          성분 MACCS(167bit)
  out/v2/fp_form_pooled.npz        제형 단위 pooling 지문(max / 농도가중 mean)
  out/v2/feature_manifest.csv      컬럼별 역할(feature/target/label_source/provenance/id)

설계 원칙
  - **누출 차단**: 라벨이 SDS H코드에서 유도된 행이 있으므로 H코드·신호어 계열 컬럼은
    feature 가 아니라 label_source 로 분류하고 X 행렬에서 제외한다.
  - **결측을 지우지 않는다**: 결측 자체가 정보이므로 *_isna 플래그와 커버리지 피처를 남긴다.
  - **집계는 농도가중**: 제형 블록은 성분 블록의 농도가중 모멘트로 만든다(가산성 가정과 정합).
"""
import json
import math
import os
from pathlib import Path
import re
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_desc import DESC_NAMES, desc_for_smiles, guess_role   # noqa: E402
from lib_parse import (ct_predict, num_range, parse_form_extract,   # noqa: E402
                       parse_ingredient_extract, parse_physchem_extract,
                       parse_tox_extract)
from lib_tox11 import (T11_CODERIVED, T11_LABEL_SOURCE, parse_tox11)   # noqa: E402

ROOT = str(Path(__file__).resolve().parent.parent)
BASE = ROOT
INPUT_DIR = f"{BASE}/03_입력데이터"
DEPS_DIR = f"{BASE}/02_의존데이터"
MODEL_DIR = f"{BASE}/04_모델산출물"
SRC_V1 = f"{INPUT_DIR}/input_dataset.xlsx"
SRC_2ND = f"{INPUT_DIR}/dataset_배정_20260824.xlsx"
CIPAC_J = f"{DEPS_DIR}/formulation_harness/cipac_codes.json"
TOXRUN = f"{DEPS_DIR}/formulation_harness/_run_tox"
RESULTS = f"{DEPS_DIR}/results"
OUT_XLSX = f"{MODEL_DIR}/input_dataset_v5.xlsx"
OUTDIR = f"{MODEL_DIR}/v4_fixed"
JUNE_MASTER = (f"{BASE}/formulation_audit_team_share_highlighted_only_20260630/"
               "formulation_ingredients_master_audited.xlsx")
JUNE_RECON = f"{MODEL_DIR}/v4/june_audit_reconciliation.csv"
os.makedirs(OUTDIR, exist_ok=True)

CAT_ORDER = {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 3, "2A": 3, "2B": 2, "3": 1,
             "NC": 0, "NOT CLASSIFIED": 0}
# 순서형 인코딩. 눈/피부 자극 강도의 서열을 보존한다(Track A 의 ordinal loss 용).
SEV = {"eye": {"1": 3, "2A": 2, "2B": 1, "2": 2, "NC": 0},
       "skin": {"1": 4, "1A": 4, "1B": 4, "1C": 4, "2": 2, "3": 1, "2A": 2, "2B": 1, "NC": 0},
       "sens": {"1": 1, "1A": 1, "1B": 1, "NC": 0}}


def nrm_cat(v):
    """'Not classified'/1.0/'2A' → 정규화 문자열. 결측은 None."""
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
    # '2A (CLP Eye Irrit. 2)' 처럼 뒤에 설명이 붙는 값이 있다. 선두 구분토큰만 취한다.
    m = re.match(r"\s*(?:cat(?:egory)?\.?\s*)?(1A|1B|1C|2A|2B|1|2|3|4)\b", s, re.I)
    return m.group(1).upper() if m else s.upper()[:24]


def nrm_name(x):
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())


def log(*a):
    print(*a, flush=True)


# ================================================================ 1. 원본 적재

log("[1] 원본 적재")
F1 = pd.read_excel(SRC_V1, sheet_name="formulation")
I1 = pd.read_excel(SRC_V1, sheet_name="ingredient")
S_ING = pd.read_excel(SRC_2ND, sheet_name="성분")
S_FRM = pd.read_excel(SRC_2ND, sheet_name="제형코드")
S_TOX = pd.read_excel(SRC_2ND, sheet_name="독성코드")
S_PHC = pd.read_excel(SRC_2ND, sheet_name="특성")
CIPAC = json.load(open(CIPAC_J, encoding="utf-8"))
CIPAC_CODES = {e["code"] for k in ("current", "discontinued") for e in CIPAC[k]}
log(f"    formulation {F1.shape} · ingredient {I1.shape} · "
    f"2차 성분 {S_ING.shape}/제형 {S_FRM.shape}/독성 {S_TOX.shape}/특성 {S_PHC.shape}")

# ================================================================ 2. 2차 추출정보 파싱

log("[2] 2차 추출정보 파싱")

# --- 성분 (제형×성분 단위) : ing_idx 로 원본 ingredient 시트와 1:1 대응
ing2 = []
for r in S_ING.itertuples(index=False):
    d = parse_ingredient_extract(getattr(r, "추출정보", None))
    d["Formulation_ID"] = r.Formulation_ID
    d["ing_idx"] = r.ing_idx
    d["ing2_verdict"] = getattr(r, "검증여부", None)
    d["ing2_evidence"] = getattr(r, "검증근거", None)
    d["ing2_newlink"] = getattr(r, "신규소스링크", None)
    d["ing2_memo"] = getattr(r, "메모", None)
    ing2.append(d)
ING2 = pd.DataFrame(ing2)
log(f"    성분 파싱 {ING2['ing2_parse_ok'].sum()} / {len(ING2)}  "
    f"(CAS {ING2['ing2_cas'].notna().sum()} · 함량 {ING2['ing2_pct_value'].notna().sum()})")

# --- 제형코드
frm2 = []
for r in S_FRM.itertuples(index=False):
    d = parse_form_extract(getattr(r, "추출정보", None), CIPAC_CODES)
    d["Formulation_ID"] = r.Formulation_ID
    d["form2_verdict"] = getattr(r, "검증여부", None)
    d["form2_not_formulation"] = getattr(r, "not_formulation_reason", None)
    frm2.append(d)
FRM2 = pd.DataFrame(frm2)
log(f"    제형코드 CIPAC유효 {int(FRM2['form2_cipac_ok'].sum())} / "
    f"{FRM2['form2_code'].notna().sum()}")

# --- 독성코드
tox2 = []
for r in S_TOX.itertuples(index=False):
    d = parse_tox_extract(getattr(r, "추출정보", None))
    d["Formulation_ID"] = r.Formulation_ID
    d["tox2_verdict"] = getattr(r, "검증여부", None)
    d["tox2_src_url"] = getattr(r, "신규소스링크", None) or getattr(r, "tox_source_url", None)
    d["tox2_raw"] = getattr(r, "추출정보", None)
    tox2.append(d)
TOX2 = pd.DataFrame(tox2)
log(f"    독성 눈 {TOX2['tox2_eye_ghs'].notna().sum()} · 피부 "
    f"{TOX2['tox2_skin_ghs'].notna().sum()} · 감작 {TOX2['tox2_sens_ghs'].notna().sum()} "
    f"(명시적 NC {int(TOX2['tox2_not_classified'].sum())})")

# --- 특성
phc2 = []
for r in S_PHC.itertuples(index=False):
    d = parse_physchem_extract(getattr(r, "추출정보", None))
    d["Formulation_ID"] = r.Formulation_ID
    d["pc2_verdict"] = getattr(r, "검증여부", None)
    phc2.append(d)
PHC2 = pd.DataFrame(phc2)
log(f"    특성 파라미터행 {int(PHC2['pc2_parse_ok'].sum())} · pH {PHC2['pc2_ph_lo'].notna().sum()} "
    f"· 강산 {int(PHC2['pc2_strong_acid'].fillna(False).sum())} "
    f"· 강염기 {int(PHC2['pc2_strong_base'].fillna(False).sum())}")

# ================================================================ 3. Phase 1 회수분

log("[3] Phase 1 에이전트 회수분 병합")
row2fid = dict(zip(S_TOX.index + 2, S_TOX["Formulation_ID"]))   # 엑셀 행번호 → FID
p1_rows, p1_ing = [], []
files = sorted(f for f in os.listdir(RESULTS) if re.fullmatch(r"p1_\d+\.json", f)) \
    if os.path.isdir(RESULTS) else []
for fn in files:
    try:
        items = json.load(open(f"{RESULTS}/{fn}", encoding="utf-8"))
    except Exception as e:
        log(f"    !! {fn} 적재 실패: {e}")
        continue
    for it in items if isinstance(items, list) else items.get("results", []):
        fid = it.get("fid") or row2fid.get(it.get("row"))
        if not fid:
            continue
        pg = it.get("product_ghs") or {}
        p1_rows.append({
            "Formulation_ID": fid, "p1_tier": it.get("tier"),
            "p1_resolution": it.get("resolution"), "p1_confidence": it.get("confidence"),
            "p1_source_url": it.get("source_url"), "p1_source_type": it.get("source_type"),
            "p1_ingredients_match": it.get("ingredients_match"),
            "p1_eye": nrm_cat(pg.get("eye_cat")), "p1_skin": nrm_cat(pg.get("skin_cat")),
            "p1_sens": nrm_cat(pg.get("sens_cat")),
            "p1_signal": pg.get("signal_word"), "p1_h": pg.get("h_statements"),
            "p1_ph": pg.get("ph"), "p1_note": it.get("note_ko"),
            "p1_batch": fn,
        })
        for pi in (it.get("per_ingredient") or []):
            p1_ing.append({
                "Formulation_ID": fid, "p1i_name": pi.get("name"),
                "p1i_cas": (pi.get("cas") or "").strip() or None,
                "p1i_eye": nrm_cat(pi.get("eye_cat")), "p1i_skin": nrm_cat(pi.get("skin_cat")),
                "p1i_sens": nrm_cat(pi.get("sens_cat")),
                "p1i_h": pi.get("h_statements"), "p1i_src": pi.get("source_url"),
            })
P1 = pd.DataFrame(p1_rows) if p1_rows else pd.DataFrame(columns=["Formulation_ID"])
P1I = pd.DataFrame(p1_ing) if p1_ing else pd.DataFrame(
    columns=["Formulation_ID", "p1i_name", "p1i_cas", "p1i_eye", "p1i_skin", "p1i_sens",
             "p1i_h", "p1i_src"])
if len(P1):
    P1 = P1.drop_duplicates("Formulation_ID", keep="first")
log(f"    배치파일 {len(files)}개 · 제품행 {len(P1)} · 성분GHS레코드 {len(P1I)}")

# CAS 는 물질 고유값이므로, 한 배치가 찾아낸 성분 분류는 그 CAS 를 쓰는 *모든* 제형에
# 재사용할 수 있다. 이것이 Track B(성분별 CT 가산)의 커버리지를 늘리는 핵심 레버.
CAS_GHS = {}
for r in P1I.itertuples(index=False):
    if not r.p1i_cas:
        continue
    cur = CAS_GHS.setdefault(r.p1i_cas, {"eye": None, "skin": None, "sens": None,
                                         "h": None, "src": None, "name": r.p1i_name})
    for k, v in (("eye", r.p1i_eye), ("skin", r.p1i_skin), ("sens", r.p1i_sens)):
        if cur[k] is None and v is not None:
            cur[k] = v
    cur["h"] = cur["h"] or r.p1i_h
    cur["src"] = cur["src"] or r.p1i_src
NAME_GHS = {nrm_name(v["name"]): v for v in CAS_GHS.values() if v.get("name")}
for r in P1I.itertuples(index=False):
    k = nrm_name(r.p1i_name)
    if k and k not in NAME_GHS:
        NAME_GHS[k] = {"eye": r.p1i_eye, "skin": r.p1i_skin, "sens": r.p1i_sens,
                       "h": r.p1i_h, "src": r.p1i_src, "name": r.p1i_name}
log(f"    성분 GHS 룩업: CAS {len(CAS_GHS)}종 · 이름 {len(NAME_GHS)}종")

# ============================================ 3.5 SDS Section 11 재파싱 (F0.5)
# 하네스가 수집해 놓고 시트에 쓰지 않은 13개 파라미터를 여기서 회수한다.
# build_extract_text() 는 신호어·H코드 2종만 기입했다 — 나머지는 디스크에만 있었다.
log("[3.5] SDS Section 11 재파싱 (final_merged_results.json — 미활용 13파라미터)")
T11 = pd.DataFrame(columns=["Formulation_ID"])
_t11_stat = {}
try:
    _fm = json.load(open(f"{TOXRUN}/final_merged_results.json", encoding="utf-8"))
    _rm = json.load(open(f"{TOXRUN}/row_mapping.json", encoding="utf-8"))
    rows11 = []
    for it in _fm:
        fv = it.get("field_values") or {}
        tox = fv.get("toxicity") if isinstance(fv.get("toxicity"), dict) else \
            (fv if "ghs_signal_word" in fv else {})
        ss = it.get("sds_summary") or {}
        d = parse_tox11(tox, it.get("resolution"), ss.get("ingredients_match"))
        d["t11_resolution"] = it.get("resolution")
        d["t11_confidence"] = it.get("confidence")
        # 제품명 중복행 전체에 전파 (row_mapping 이 제품명 → 엑셀행 목록을 보관)
        for rw in (_rm.get(it.get("product_name")) or [it.get("row")]):
            fid = row2fid.get(rw)
            if fid:
                rows11.append({**d, "Formulation_ID": fid})
    T11 = pd.DataFrame(rows11).drop_duplicates("Formulation_ID", keep="first")
    for ep in ("eye", "skin", "sens"):
        _t11_stat[ep] = {"label": int(T11[f"t11_{ep}_label"].notna().sum()),
                         "comp_excluded": int(T11[f"t11_{ep}_comp"].fillna(0).sum()),
                         "ambig": int(T11[f"t11_{ep}_ambig"].fillna(0).sum()),
                         "weak_nc": int(T11[f"t11_{ep}_weak_nc"].fillna(0).sum())}
        log(f"    t11 {ep:5s} 라벨 {_t11_stat[ep]['label']:4d} "
            f"(성분나열 제외 {_t11_stat[ep]['comp_excluded']} · "
            f"충돌표시 {_t11_stat[ep]['ambig']} · 약한NC {_t11_stat[ep]['weak_nc']})")
    for k in ("t11_ld50_oral", "t11_ld50_dermal", "t11_lc50_inhal"):
        _t11_stat[k] = int(T11[k].notna().sum())
    log(f"    t11 수치 경구LD50 {_t11_stat['t11_ld50_oral']} · 경피LD50 "
        f"{_t11_stat['t11_ld50_dermal']} · 흡입LC50 {_t11_stat['t11_lc50_inhal']}")
except Exception as e:                                          # noqa: BLE001
    log(f"    !! Section 11 재파싱 건너뜀: {e}")

# ================================================================ 4. 성분 시트 조립

log("[4] ingredient_v2 조립")
ING = I1.merge(ING2, on=["Formulation_ID", "ing_idx"], how="left")
assert len(ING) == len(I1), "성분 조인에서 행수 변동 — ing_idx 중복 의심"

# 4a. 최선의 성분명/CAS/함량 (2차 수집분 우선, 없으면 원본)
ING["ing_name_best"] = ING["ing2_name"].fillna(ING["ingredient_name"])
ING["ing_cas_best"] = ING["ing2_cas"].fillna(ING["cas"])
ING["ing_pct_best"] = ING["ing2_pct_value"].fillna(ING["pct_value"])
ING["ing_pct_kind_best"] = ING["ing2_pct_kind"].fillna(ING["pct_kind"])
ING["ing_pct_src"] = np.where(ING["ing2_pct_value"].notna(), "2nd_sds",
                              np.where(ING["pct_value"].notna(), "v1", "missing"))

# ---- 4a-2. 6/30 성분 정밀감사(zip) 고신뢰 복구 병합 — v5 안전판 ----
# 대상: reconcile_june_audit.py 가 RECOVER_CAS_HIGH_CONFIDENCE로 분류한 제형만
# (SDS 원문 대조까지 끝난 MATCH 건). 기존 값이 있으면 건드리지 않고, 결측일 때만 채운다.
#
# v4 는 세 파이프리스트를 위치(index)로 짝지어 163/195 제형에서 정렬이 어긋났다.
# v5 는 위치조인을 **길이가 같은 리스트 쌍에만** 허용하고, 나머지는 스킵하거나
# 비위치 참조사전(이름→CAS 유일해)으로만 복구한다. 모든 병합건에 근거를 남긴다.
log("[4x] 6/30 성분감사(zip) 안전 재병합 (v5: 위치조인 금지)")
from rdkit import Chem as _Chem, RDLogger as _RDLogger   # noqa: E402
_RDLogger.DisableLog("rdApp.*")

_CAS_RE = re.compile(r"^\d{2,7}-\d{2}-\d$")

# SDS 표 헤더·단위·숫자 조각. 화학물질명이 아니므로 구조를 붙여선 안 된다.
_NONCHEM_RE = [re.compile(p, re.I) for p in (
    r"^\s*$", r"content", r"\bw\s*/\s*w\b", r"\bw\s*/\s*v\b", r"concentration",
    r"^\s*%\s*$", r"percent(age)?$", r"^\s*[\d.,\s%<>=~\-–]+$",
    r"^cas\b", r"^ec\b", r"^ec[\s_-]?index", r"^index\s*no", r"^name\s*[:：]",
    r"^ingredient(s)?\s*$", r"^component(s)?\s*$", r"^chemical\s+name",
    r"^classification", r"^hazard", r"^weight", r"^amount", r"^substance\s*$",
    r"^identifier", r"^reach", r"^ppm$", r"^mg/kg$", r"^g/l$", r"^wt\b",
    r"^n/?a$", r"^none$", r"^other\s+ingredient", r"^remainder", r"^balance$",
    r"^trade\s+secret", r"^proprietary\s*$", r"^total$", r"^sum$", r"^range$",
    r"^number$", r"^no\.$",
    # ---- v5 caveat C1 (2026-08-30 추가) ---------------------------------------
    # 독립 검증(v5_integrity_name_cas_mismatch_candidates.csv)에서 **회사/조직명**
    # 토큰에 화학물질 CAS 가 붙는 오배정이 확인되었다.
    #   PID985 idx0 "GSP Crop Science Limited" ↔ 129558-76-5 (PubChem 표제명
    #                = Tolfenpyrad. 회사명이지 물질명이 아니다)
    # 따라서 법인 접미어/조직명 패턴을 비화학 토큰으로 추가한다. "Chemical" 단독은
    # 실제 물질명("Chemical Abstracts" 류가 아닌 진짜 성분명)을 오탐할 수 있으므로
    # 쓰지 않고, 반드시 co/company/corp/industries 와 결합된 형태만 잡는다.
    r"\b(?:limited|ltd|inc|incorporated|corp(?:oration)?|gmbh|pvt|llc|company"
    r"|industries|holdings|s\.\s*a\.\s*r\.\s*l\.)\b",
    r"\bco\.?\s*,?\s*ltd\b",
    r"\bs\.\s*a\.|\bb\.\s*v\.",              # S.A. / B.V. — 반드시 점이 있는 형태만
    r"\bcrop\s*science\b|\bcrop\s*protection\b|\blife\s*sciences?\b",
    r"\bchemical(?:s)?\s+(?:co|company|corp(?:oration)?|industries)\b",
    r"株式会社|有限公司|技術|테크|주식회사",
    #   PID955 idx1 "THFA Proprietary" ↔ 872-50-4 (PubChem 표제명 = N-methyl-2-
    #   pyrrolidone). THFA(테트라하이드로퍼퓨릴 알코올)의 실제 CAS 는 97-99-4 이므로
    #   전혀 다른 물질이다. 이 토큰은 회사명이 아니라 '영업비밀' 표기이므로
    #   기존 `^proprietary\s*$`(단독형)로는 잡히지 않았다 → 단어단위로 확장한다.
    #   'proprietary' 는 SDS 에서 조성 비공개를 뜻하는 부기어이지 물질명이 아니다.
    #   다만 인용부호 안(예: Ethylene glycol (listed as 'Proprietary solvent' ...))은
    #   실제 물질명을 SDS 가 부연설명한 경우이므로 제외한다 — 휴리스틱임을 명시한다.
    r"(?<!['\"‘’“”])\bproprietary\b")]


def _is_nonchem(tok):
    t = str(tok or "").strip()
    if not t:
        return True
    if any(rx.search(t) for rx in _NONCHEM_RE):
        return True
    return len(re.sub(r"[^A-Za-z]", "", t)) < 3      # 알파벳 3자 미만 → 물질명 불인정


def _cas_ok(c):
    """정규 CAS 형식 + 체크디지트 검증."""
    if not c or not _CAS_RE.match(str(c).strip()):
        return False
    d = str(c).strip().replace("-", "")
    return sum(int(x) * (i + 1) for i, x in enumerate(reversed(d[:-1]))) % 10 == int(d[-1])


def _smi_ok(s):
    if not s:
        return False
    try:
        return _Chem.MolFromSmiles(str(s)) is not None
    except Exception:                                            # noqa: BLE001
        return False


def _canon(s):
    try:
        m = _Chem.MolFromSmiles(str(s))
        return _Chem.MolToSmiles(m) if m is not None else None
    except Exception:                                            # noqa: BLE001
        return None


def _split_pipe(s):
    if pd.isna(s):
        return []
    return [x.strip() for x in str(s).split("|")]


# nrm_name() 은 비영숫자를 모두 지우므로 '(-)-Limonene' 와 '(+)-Limonene' 가 같은 키가
# 된다. 이름 기반 참조복구에서 거울상이성질체 CAS 가 뒤바뀌는 실제 사례가 있었으므로
# (v4: 'Limonene, (-)-' ← 5989-27-5 = (+)-체), 입체·이성질 표기를 보존한 보조키를 쓴다.
_STEREO_TOK = (
    (r"\(\s*[±+]\s*[/-]\s*[-]?\s*\)|\(\s*±\s*\)|\bracem", "pm"),
    (r"\(\s*\+\s*\)|(?<![a-z])d\s*-", "p"),
    (r"\(\s*-\s*\)|(?<![a-z])l\s*-", "m"),
    (r"\(\s*r\s*\)|\br\s*-(?=[a-z(])", "R"),
    (r"\(\s*s\s*\)|\bs\s*-(?=[a-z(])", "S"),
    (r"\balpha\b|α", "a"), (r"\bbeta\b|β", "b"), (r"\bgamma\b|γ", "g"),
    (r"\bcis\b", "c"), (r"\btrans\b", "t"),
    (r"\bortho\b", "o"), (r"\bmeta\b", "me"), (r"\bpara\b", "pa"),
)


def _stereo_key(x):
    """정규화 이름 + 검출된 입체/이성질 표기 집합. 표기 유실로 인한 이성질체 혼동 차단."""
    s = str(x or "").lower()
    marks = sorted({tag for pat, tag in _STEREO_TOK if re.search(pat, s)})
    return nrm_name(x) + "#" + ",".join(marks)


_recon = pd.read_csv(JUNE_RECON)
_target_fids = set(_recon.loc[_recon["recommendation"] == "RECOVER_CAS_HIGH_CONFIDENCE", "Formulation_ID"])
_june = pd.read_excel(JUNE_MASTER, sheet_name="Formulations")

# ---- 비위치 참조사전: 이름→CAS, CAS→SMILES.
# 출처는 (a) 워크북 전체 중 이름/CAS(또는 이름/SMILES) 길이가 정확히 일치하는 행 =
# 워크북 자신의 정렬 계약이 지켜진 행, (b) v1 ingredient 시트의 기존 (이름,CAS,SMILES).
# 어느 쪽도 '길이 불일치 행의 위치조인'에 의존하지 않는다.
_ref_n2c, _ref_c2s = defaultdict(set), defaultdict(set)
for _r in _june.itertuples(index=False):
    _nm = _split_pipe(_r.Formulation_Ingredients)
    _cs = _split_pipe(_r.Formulation_Ingredients_CAS)
    _sm = _split_pipe(_r.Formulation_Ingredients_SMILES)
    if _nm and len(_nm) == len(_cs):
        for _a, _b in zip(_nm, _cs):
            if nrm_name(_a) and _cas_ok(_b) and not _is_nonchem(_a):
                _ref_n2c[_stereo_key(_a)].add(_b.strip())
    if _nm and len(_nm) == len(_cs) == len(_sm):
        for _b, _s in zip(_cs, _sm):
            if _cas_ok(_b) and _smi_ok(_s):
                _ref_c2s[_b.strip()].add(_canon(_s))
for _r in I1.itertuples(index=False):
    _c = str(_r.cas).strip() if pd.notna(_r.cas) else ""
    _s = str(_r.smiles).strip() if pd.notna(_r.smiles) else ""
    if nrm_name(_r.ingredient_name) and _cas_ok(_c):
        _ref_n2c[_stereo_key(_r.ingredient_name)].add(_c)
    if _cas_ok(_c) and _smi_ok(_s):
        _ref_c2s[_c].add(_canon(_s))
log(f"    비위치 참조사전: 이름(입체보존키)→CAS {len(_ref_n2c)}종 · "
    f"CAS→SMILES {len(_ref_c2s)}종")

# ---- 제형별 병합 후보 구성
_june_lookup = {}                    # (fid, nrm_name) -> (cas, smiles, basis)
_skip_reason = {}                    # fid -> 스킵 사유 (제형 단위)
_basis_form = {}                     # fid -> 제형 단위 basis
_n_tok_dropped = Counter()
_tokdec = []                         # 워크북 토큰 단위 판정 로그 (근거 산출물)
for _r in _june.itertuples(index=False):
    fid = _r.Formulation_ID
    if fid not in _target_fids:
        continue
    names = _split_pipe(_r.Formulation_Ingredients)
    cass = _split_pipe(_r.Formulation_Ingredients_CAS)
    smis = _split_pipe(_r.Formulation_Ingredients_SMILES)
    eq_c = bool(names) and len(names) == len(cass)
    eq_s = bool(names) and len(names) == len(smis)
    if eq_c and eq_s:
        basis = "length_matched_all"
    elif eq_c:
        basis = "name_cas_length_matched"
    elif eq_s:
        basis = "name_smiles_length_matched"
    else:
        basis = "name_ref_unique"
        _skip_reason[fid] = (f"positional join unsafe: len(names)={len(names)} "
                            f"len(cas)={len(cass)} len(smiles)={len(smis)}")
    _basis_form[fid] = basis

    # 제형 내 동일 정규화 이름이 서로 다른 값을 가리키면 그 이름은 통째로 배제한다.
    _seen = defaultdict(set)
    _cand = {}
    def _rec(i, raw, decision, cas=None, smi=None):
        _tokdec.append({"Formulation_ID": fid, "pipe_index": i, "name_token": raw,
                        "len_names": len(names), "len_cas": len(cass),
                        "len_smiles": len(smis), "form_basis": basis,
                        "decision": decision, "cas": cas, "smiles": smi})

    for i, raw in enumerate(names):
        nm = nrm_name(raw)
        if not nm:
            _rec(i, raw, "dropped:empty_name")
            continue
        if _is_nonchem(raw):
            _n_tok_dropped["nonchemical_name_token"] += 1
            _rec(i, raw, "dropped:nonchemical_name_token")
            continue
        if basis in ("length_matched_all", "name_cas_length_matched",
                     "name_smiles_length_matched"):
            c_raw = cass[i] if eq_c else None
            s_raw = smis[i] if eq_s else None
            c_v = c_raw.strip() if c_raw and _cas_ok(c_raw) else None
            s_v = s_raw.strip() if s_raw and _smi_ok(s_raw) else None
            if c_raw and not c_v:
                _n_tok_dropped["cas_format_or_checkdigit_fail"] += 1
            if s_raw and not s_v:
                _n_tok_dropped["smiles_unparseable"] += 1
            b = basis
        else:
            # 길이 불일치 → 위치조인 사용 금지. 참조사전이 유일해를 줄 때만 복구.
            # 조회키는 입체표기를 보존하므로 (+)/(-)/(R)/(S) 혼동이 발생하지 않는다.
            refc = _ref_n2c.get(_stereo_key(raw), set())
            if len(refc) != 1:
                _why = ("no_unique_reference_cas" if not refc
                        else "ambiguous_reference_cas")
                _n_tok_dropped[_why] += 1
                _rec(i, raw, f"skipped_length_mismatch:{_why}")
                continue
            c_v = next(iter(refc))
            refs = {x for x in _ref_c2s.get(c_v, set()) if x}
            s_v = next(iter(refs)) if len(refs) == 1 else None
            b = "name_ref_unique"
        if c_v is None and s_v is None:
            _rec(i, raw, "dropped:no_usable_cas_or_smiles")
            continue
        _rec(i, raw, f"candidate:{b}", c_v, s_v)
        _seen[nm].add((c_v, s_v))
        # 입체키까지 저장해, ING 행 이름과 입체표기가 다르면 병합하지 않는다.
        _cand[nm] = (c_v, s_v, b, _stereo_key(raw))
    for nm, vals in _seen.items():
        if len(vals) > 1:                        # 같은 이름 · 상충값 → 안전하게 포기
            _cand.pop(nm, None)
            _n_tok_dropped["duplicate_name_conflict"] += 1
    for nm, v in _cand.items():
        _june_lookup[(fid, nm)] = v

_TOKDEC = pd.DataFrame(_tokdec)
_TOKDEC.to_csv(f"{OUTDIR}/june_merge_v5_token_decisions.csv", index=False)
log(f"    제형 basis 분포: {dict(Counter(_basis_form.values()))}")
log(f"    토큰 게이트 탈락: {dict(_n_tok_dropped)}")
log(f"    토큰 판정 로그 {len(_TOKDEC)}건 → {OUTDIR}/june_merge_v5_token_decisions.csv")
log(f"    토큰 판정 분포: {dict(_TOKDEC['decision'].value_counts())}")

_n_smiles_filled = _n_cas_filled = 0
_new_smiles, _new_cas, _src_flag, _basis_col = [], [], [], []
for r in ING.itertuples(index=False):
    fid, nm = r.Formulation_ID, nrm_name(r.ing_name_best)
    smi, cas_v = r.smiles, r.ing_cas_best
    src, basis = False, None
    if fid in _target_fids:
        hit = _june_lookup.get((fid, nm))
        if hit and hit[3] != _stereo_key(r.ing_name_best):
            # 워크북 토큰과 ING 행 이름의 입체표기가 다르다 → 다른 이성질체일 수 있다.
            hit, basis = None, "skipped_stereo_mismatch"
        if hit:
            j_cas, j_smi, j_basis, _ = hit
            if pd.isna(smi) and j_smi:
                smi = j_smi
                _n_smiles_filled += 1
                src = True
            if pd.isna(cas_v) and j_cas:
                cas_v = j_cas
                _n_cas_filled += 1
                src = True
            if src:
                basis = j_basis
        elif basis is None:
            # 병합되지 않은 이유를 남긴다(제형 단위 사유 우선).
            basis = ("skipped_length_mismatch" if fid in _skip_reason
                     else "skipped_no_safe_pair")
    _new_smiles.append(smi)
    _new_cas.append(cas_v)
    _src_flag.append(src)
    _basis_col.append(basis)
ING["smiles"] = _new_smiles
ING["ing_cas_best"] = _new_cas
ING["june_audit_recovered"] = _src_flag
ING["june_merge_basis"] = _basis_col
log(f"    6/30 감사 병합(v5): SMILES {_n_smiles_filled}건, CAS {_n_cas_filled}건 신규 채움 "
    f"(대상 제형 {len(_target_fids)}개, 매칭성분 {sum(_src_flag)}행)")
log(f"    june_merge_basis 분포: "
    f"{dict(Counter(b for b in _basis_col if b))}")

# ---- v5 caveat C1: 확정 철회 2건이 실제로 철회되었는지 검증 -----------------
# 근거: 04_모델산출물/v4_fixed/v5_integrity_name_cas_mismatch_candidates.csv
#       (PubChem 표제명 대조 결과 성분명과 CAS 가 서로 다른 물질을 가리킴)
#   (PID985, 0) "GSP Crop Science Limited" ← 129558-76-5 (Tolfenpyrad)
#   (PID955, 1) "THFA Proprietary"         ← 872-50-4   (N-methyl-2-pyrrolidone;
#                                            THFA 의 실제 CAS 는 97-99-4)
# 위 두 건은 _NONCHEM_RE 확장(회사명 / proprietary)으로 게이트되어야 한다.
# 게이트가 어떤 이유로든 놓치면 아래 명시목록으로 강제 철회한다(임시방편 — 근본
# 해결은 게이트이며, 개별 하드코딩은 감사추적용 안전판일 뿐이다).
_C1_RETRACT = [("EyeIrritation6pack_PID985", 0, "129558-76-5"),
               ("EyeIrritation6pack_PID955", 1, "872-50-4")]
_c1_status = []
for _fid, _idx, _bad_cas in _C1_RETRACT:
    _m = (ING["Formulation_ID"] == _fid) & (ING["ing_idx"] == _idx)
    if not _m.any():
        _c1_status.append({"key": f"{_fid}#{_idx}", "state": "row_not_found"})
        continue
    _cur = ING.loc[_m, "ing_cas_best"].iloc[0]
    _cur = str(_cur).strip() if pd.notna(_cur) else None
    if _cur == _bad_cas:
        # 게이트 실패 → 명시 철회 (v1 원본 CAS 는 애초에 결측이었으므로 결측 복귀)
        ING.loc[_m, "ing_cas_best"] = np.nan
        ING.loc[_m, "june_merge_basis"] = "retracted_name_cas_mismatch_explicit"
        ING.loc[_m, "june_audit_recovered"] = False
        _c1_status.append({"key": f"{_fid}#{_idx}", "state": "explicit_retraction"})
    else:
        _c1_status.append({"key": f"{_fid}#{_idx}", "state": "gated_by_is_nonchem",
                           "cas_now": _cur})
log(f"    C1 성분명↔CAS 오배정 철회: {_c1_status}")

# ---- 병합 결과 검증 및 감사로그 -------------------------------------------
_v5log = []
for r in ING[ING["june_audit_recovered"]].itertuples(index=False):
    _c = str(r.ing_cas_best).strip() if pd.notna(r.ing_cas_best) else None
    _s = str(r.smiles).strip() if pd.notna(r.smiles) else None
    _refc = sorted(_ref_n2c.get(_stereo_key(r.ing_name_best), set()))
    _refs = sorted({x for c in _refc for x in _ref_c2s.get(c, set()) if x})
    _sk = (lambda z: re.sub(r"[@/\\]", "", z) if z else None)
    _smi_consistent = None
    if _s and _refs:
        _smi_consistent = _sk(_canon(_s)) in {_sk(x) for x in _refs}
    _v5log.append({
        "Formulation_ID": r.Formulation_ID, "ing_idx": r.ing_idx,
        "ing_name_best": r.ing_name_best, "merged_cas": _c, "merged_smiles": _s,
        "june_merge_basis": r.june_merge_basis,
        "cas_format_checkdigit_ok": _cas_ok(_c) if _c else None,
        "smiles_rdkit_parses": _smi_ok(_s) if _s else None,
        "reference_cas_for_name": "|".join(_refc),
        "cas_consistent_with_reference": (_c in _refc) if (_c and _refc) else None,
        "smiles_consistent_with_reference": _smi_consistent,
    })
_V5LOG = pd.DataFrame(_v5log)
if len(_V5LOG):
    _V5LOG.to_csv(f"{OUTDIR}/june_merge_v5_audit.csv", index=False)
    _bad = _V5LOG[(_V5LOG["cas_consistent_with_reference"] == False)              # noqa: E712
                  | (_V5LOG["smiles_consistent_with_reference"] == False)         # noqa: E712
                  | (_V5LOG["smiles_rdkit_parses"] == False)                      # noqa: E712
                  | (_V5LOG["cas_format_checkdigit_ok"] == False)]                # noqa: E712
    log(f"    v5 병합 검증: {len(_V5LOG)}행 중 참조사전 불일치/파싱실패 {len(_bad)}행")
    if len(_bad):
        log(f"    !! 잔여 의심행: "
            f"{_bad[['Formulation_ID', 'ing_name_best', 'merged_cas']].to_dict('records')[:10]}")
    log(f"    → {OUTDIR}/june_merge_v5_audit.csv")
_JUNE_STATS = {
    "target_formulations": len(_target_fids),
    "form_basis": dict(Counter(_basis_form.values())),
    "token_gate_dropped": dict(_n_tok_dropped),
    "rows_merged": int(sum(_src_flag)),
    "cas_filled": int(_n_cas_filled), "smiles_filled": int(_n_smiles_filled),
    "row_basis": dict(Counter(b for b in _basis_col if b)),
    "audit_rows_inconsistent": int(len(_bad)) if len(_V5LOG) else 0,
    "c1_name_cas_retraction": _c1_status,
}

ING["ing_name_corrected"] = (
    ING["ing2_name"].notna()
    & (ING["ing2_name"].map(nrm_name) != ING["ingredient_name"].map(nrm_name)))
ING["ing_cas_corrected"] = (
    ING["ing2_cas"].notna() & ING["cas"].notna()
    & (ING["ing2_cas"].astype(str).str.strip() != ING["cas"].astype(str).str.strip()))
log(f"    함량 v1 {int((ING['ing_pct_src']=='v1').sum())} / 2차 "
    f"{int((ING['ing_pct_src']=='2nd_sds').sum())} / 결측 "
    f"{int((ING['ing_pct_src']=='missing').sum())}")
log(f"    2차에서 성분명 정정 {int(ING['ing_name_corrected'].sum())} · "
    f"CAS 정정 {int(ING['ing_cas_corrected'].sum())}")

# 4b. 성분별 진짜 GHS (원본 ghs_*/cat_* 는 제형값 브로드캐스트라 사용 금지)
rows = []
p1i_by_fid = defaultdict(list)
for r in P1I.itertuples(index=False):
    p1i_by_fid[r.Formulation_ID].append(r)
for r in ING.itertuples(index=False):
    cas = (str(r.ing_cas_best).strip() if pd.notna(r.ing_cas_best) else None)
    nm = nrm_name(r.ing_name_best)
    hit, src = None, None
    for cand in p1i_by_fid.get(r.Formulation_ID, []):        # 같은 제형 안에서 먼저
        if (cas and cand.p1i_cas == cas) or (nm and nrm_name(cand.p1i_name) == nm):
            hit = {"eye": cand.p1i_eye, "skin": cand.p1i_skin, "sens": cand.p1i_sens,
                   "h": cand.p1i_h, "src": cand.p1i_src}
            src = "phase1_row"
            break
    if hit is None and cas and cas in CAS_GHS:               # 전역 CAS 룩업
        hit, src = CAS_GHS[cas], "phase1_cas_global"
    if hit is None and nm and nm in NAME_GHS:
        hit, src = NAME_GHS[nm], "phase1_name_global"
    rows.append({"ing_ghs_eye": (hit or {}).get("eye"), "ing_ghs_skin": (hit or {}).get("skin"),
                 "ing_ghs_sens": (hit or {}).get("sens"), "ing_ghs_h": (hit or {}).get("h"),
                 "ing_ghs_src_url": (hit or {}).get("src"), "ing_ghs_src": src})
ING = pd.concat([ING.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
log(f"    성분별 실측 GHS 확보 {ING['ing_ghs_src'].notna().sum()} 행 "
    f"({dict(ING['ing_ghs_src'].value_counts())})")

# ============================================ 4b-2. 함량(%) 정합성 복구 (P-PCT)
# ---------------------------------------------------------------------------
# 문제: ing_pct_best 합이 물리적으로 불가능한 제형이 있다(최대 445.9%). 이 값은
# 농도가중 pooled 지문(f_*_wmean, fp_form_pooled)과 GHS CT 가산의 **가중치**이므로
# 표시상의 문제가 아니라 피처 오염이다.
#
# 원인 확정 (코드로 재현. 추정 아님)
#   [A] v1 원본 시트(03_입력데이터/input_dataset.xlsx: ingredient)에 이미 오염됨.
#       pct_sum_known(formulation 시트) 은 ingredient 시트 pct_value 합과 982행 중
#       980행이 정확히 일치한다 → v5 파싱이 만든 값이 아니다.
#       세부 원인은 ing_src 로 완전히 갈린다:
#         ing_src='master'  = SDS 3장 조성표 파싱분 (pct_text 에 '%' 있음, 736/738행)
#         ing_src='ntp_ice' = NTP ICE 성분목록 (pct_text 가 맨숫자, 1315/1317행)
#       v1 은 같은 Formulation_ID 에 두 출처 블록을 **이어 붙였고** pct_sum_known 은
#       두 블록을 합산했다 → 100% 초과. (문제기술서의 '패턴 3 다중 블록 누적')
#       같은 v1 파싱에서 PDF 표 추출 잔해가 성분행으로 들어온 것도 확인:
#         PID1013 'Food 30.8 25.4 22.9'/'otal 33.6 28.3 26.3' (무역통계표),
#         PID813 'Total 100.0%', MIX287 'Concentration 98–99%',
#         MIX210 'cid:131) Satisfaction Guaranteed ...' → 이름이 화학물질이 아니다.
#         (문제기술서의 '패턴 1 총계행', '패턴 2 중복'은 이 잔해행의 부분집합이다)
#   [B] 2차 시트 자유서술 3번째 칸을 num_range() 에 통째로 넣은 경로.
#       lib_parse.py:20 의 _NUM 이 선행부호 [-+]? 를 허용하므로
#       'not the formulated VC-90 product' → -90.0 (MIX151),
#       'catalog item 46417' → 46417.0 (MIX588), 'HTTP 403' → 403.0,
#       'MW 510.38' → 510.38, '418 g/L' → 418.0 등 11행이 생성됐다.
#       → lib_parse.parse_pct_field() 게이트로 파서 단계에서 차단했다(21행 변동).
#
# 복구 원칙 — **값을 추측해 채우지 않는다.** 잘못된 값은 결측으로 만들고, 규칙으로
# 판정 가능한 경우에만 행을 골라 결측화한다. 합이 100 이하인 제형은 건드리지 않는다
# (일부 성분만 공개된 SDS 는 정상이다).
log("[4b-2] 함량(%) 정합성 복구")
_PCT_TOL = 100.5          # 반올림 여유. 이 값 이하는 '정상'으로 본다.
_PCT_HARD = 100.0         # w/w % 의 물리적 상한


def _pct_lower_bound(v, kind, text):
    """함량의 하한값. 'upper/max'(<X%) 는 하한 0, 'range' 는 lo.

    상한표기('<18%')를 액면가로 더하면 합이 100 을 넘는 것이 정상이므로,
    하한합이 100 이하인 제형은 파싱 결함이 아니라 **표기 불확실성**이다.
    """
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return np.nan
    k = str(kind)
    if k in ("upper", "max"):
        return 0.0
    if k == "range":
        lo = num_range(str(text))[0] if pd.notna(text) else None
        return float(lo) if lo is not None else float(v)
    return float(v)


ING["ing_pct_raw"] = ING["ing_pct_best"].astype(float)
_pct_kind_eff = ING["ing_pct_kind_best"]
_pct_text_eff = ING["ing2_pct_text"].fillna(ING["pct_text"])
ING["_pct_lb"] = [_pct_lower_bound(v, k, t) for v, k, t
                  in zip(ING["ing_pct_raw"], _pct_kind_eff, _pct_text_eff)]
ING["_pct_nonchem"] = [_is_nonchem(n) for n in ING["ing_name_best"]]

_pct_row_status = pd.Series("ok", index=ING.index, dtype=object)
_pct_keep = ING["ing_pct_raw"].notna()

# (0) 물리적으로 불가능한 값은 제형 합과 무관하게 무조건 결측화한다.
_neg = _pct_keep & (ING["ing_pct_raw"] < 0)
_pct_row_status[_neg] = "negative_blanked"
_gt = _pct_keep & (ING["ing_pct_raw"] > _PCT_HARD)
_pct_row_status[_gt] = "impossible_gt100_blanked"
_pct_keep &= ~(_neg | _gt)

_form_status, _pct_detail = {}, []
for _fid, _g in ING.groupby("Formulation_ID", sort=False):
    _idx = _g.index
    _keep = _pct_keep.loc[_idx].copy()          # (0) 적용 후 상태
    _val = ING.loc[_idx, "ing_pct_raw"]
    _raw_sum_all = float(_val.sum()) if _val.notna().any() else np.nan
    _raw_sum = float(_val[_keep].sum()) if _keep.any() else np.nan
    _reasons = [r for r in _pct_row_status.loc[_idx].unique() if r != "ok"]
    if not (_raw_sum > _PCT_TOL):
        _form_status[_fid] = "+".join(_reasons) if _reasons else "ok"
    elif float(ING.loc[_idx, "_pct_lb"][_keep].sum()) <= _PCT_TOL:
        # 상한/범위 표기의 액면합일 뿐 — 하한합은 정합적이다. 건드리지 않는다.
        _form_status[_fid] = "+".join(_reasons + ["ok_bound_consistent"]) \
            if _reasons else "ok_bound_consistent"
    else:
        _form_status[_fid] = None               # 아래 규칙 단계로 진행
    if _form_status[_fid] is not None:
        if _reasons:                            # (0) 에서 결측화된 행이 있으면 기록
            _pct_keep.loc[_idx] = _keep
            _pct_detail.append({
                "Formulation_ID": _fid,
                "product_name": ING.loc[_idx, "product_name"].iloc[0],
                "n_ing": int(len(_idx)),
                "pct_sum_known_raw": round(_raw_sum_all, 4),
                "pct_sum_known_fixed": (round(float(_val[_keep].sum()), 4)
                                        if _keep.any() else None),
                "n_pct_known_raw": int(_val.notna().sum()),
                "n_pct_known_fixed": int(_keep.sum()),
                "pct_status": _form_status[_fid],
                "rows_blanked": "; ".join(
                    f"idx{int(ING.at[i, 'ing_idx'])}[{ING.at[i, 'ing_src']}]"
                    f"{str(ING.at[i, 'ing_name_best'])[:40]!r}="
                    f"{ING.at[i, 'ing_pct_raw']}({_pct_row_status[i]})"
                    for i in _idx if _pct_row_status[i] != "ok"),
            })
        continue

    def _still_bad():
        return float(_val[_keep].sum()) > _PCT_TOL

    # (1) 비화학 토큰 행(SDS 표 헤더·총계·PDF 표 잔해)의 함량을 결측화.
    #     성분행 자체는 지우지 않는다 — 이름/CAS 는 유효할 수 있다.
    _jk = _keep & ING.loc[_idx, "_pct_nonchem"]
    if _jk.any():
        _keep &= ~_jk
        _pct_row_status[_idx[_jk.to_numpy()]] = "nonchem_name_blanked"
        _reasons.append("nonchem_name_blanked")
    # (2) 총계행: 값이 정확히 100 이고, 그 행을 빼면 나머지 합이 100 이하.
    #     단일성분 100% 제형(성분 1개)은 정상이므로 제외한다.
    if _still_bad() and int(_keep.sum()) > 1:
        _t100 = _keep & (_val == 100.0)
        _rest = _keep & ~_t100
        if _t100.any() and _rest.any() and float(_val[_rest].sum()) <= _PCT_TOL:
            _keep &= ~_t100
            _pct_row_status[_idx[_t100.to_numpy()]] = "total_row_dropped"
            _reasons.append("total_row_dropped")
    # (3) 중복 파싱: (정규화 이름, 함량) 쌍이 같은 행. 첫 행만 남긴다.
    #     이름이 다르면 함량이 같아도 중복으로 보지 않는다(정상 케이스 보호).
    if _still_bad():
        _seen, _dups = set(), []
        for _i in _idx[_keep]:
            _k = (nrm_name(ING.at[_i, "ing_name_best"]), float(ING.at[_i, "ing_pct_raw"]))
            if _k in _seen:
                _dups.append(_i)
            else:
                _seen.add(_k)
        if _dups:
            _keep[_dups] = False
            _pct_row_status[_dups] = "duplicate_dropped"
            _reasons.append("duplicate_dropped")
    # (4) 출처 블록 분리: v1 이 이어붙인 master(SDS 조성표) / ntp_ice 블록 중
    #     하나만 남긴다. 어느 블록을 남길지는 추측하지 않고 관측치로 결정한다 —
    #     블록 자체가 정합적(하한합<=100.5)이고 화학적 신원(CAS)이 더 많은 쪽.
    #     둘 다 정합적이지 않으면 아무것도 고르지 않는다.
    if _still_bad():
        _src = ING.loc[_idx, "ing_src"]
        _cands = []
        for _s in ("master", "ntp_ice"):
            _b = _keep & (_src == _s)
            if not _b.any() or float(ING.loc[_idx, "_pct_lb"][_b].sum()) > _PCT_TOL:
                continue
            _cands.append((int(ING.loc[_idx, "ing_cas_best"][_b].notna().sum()),
                           int(_b.sum()), _s))
        if _cands:
            _cands.sort(reverse=True)
            _pick = _cands[0][2]
            _drop = _keep & (_src != _pick)
            _keep &= ~_drop
            _pct_row_status[_idx[_drop.to_numpy()]] = \
                f"cross_source_block_dropped:{_pick}_kept"
            _reasons.append(f"cross_source_block_split:{_pick}")
    # (5) 그래도 초과하면 규칙으로 블록을 가를 수 없다 → 제형 전체 함량 결측화.
    if _still_bad() or not _keep.any():
        _pct_row_status[_idx[_keep.to_numpy()]] = "unrecoverable_blanked"
        _keep[:] = False
        _reasons.append("unrecoverable_blanked")
    _pct_keep.loc[_idx] = _keep
    _form_status[_fid] = "+".join(_reasons) if _reasons else "ok"
    _pct_detail.append({
        "Formulation_ID": _fid,
        "product_name": ING.loc[_idx, "product_name"].iloc[0],
        "n_ing": int(len(_idx)),
        "pct_sum_known_raw": round(_raw_sum_all, 4),
        "pct_sum_known_fixed": (round(float(_val[_keep].sum()), 4)
                                if _keep.any() else None),
        "n_pct_known_raw": int(_val.notna().sum()),
        "n_pct_known_fixed": int(_keep.sum()),
        "pct_status": _form_status[_fid],
        "rows_blanked": "; ".join(
            f"idx{int(ING.at[i, 'ing_idx'])}[{ING.at[i, 'ing_src']}]"
            f"{str(ING.at[i, 'ing_name_best'])[:40]!r}="
            f"{ING.at[i, 'ing_pct_raw']}({_pct_row_status[i]})"
            for i in _idx if _pct_row_status[i] != "ok"),
    })

# 실제 적용: 결측화
ING["ing_pct_best"] = ING["ing_pct_raw"].where(_pct_keep)
ING["ing_pct_kind_best"] = ING["ing_pct_kind_best"].where(_pct_keep)
ING["ing_pct_src"] = np.where(_pct_keep, ING["ing_pct_src"], "blanked_by_pct_repair")
ING["pct_status"] = _pct_row_status
ING["pct_repaired"] = (_pct_row_status != "ok")
ING.drop(columns=["_pct_lb", "_pct_nonchem"], inplace=True)

_PCT_FORM_STATUS = pd.Series(_form_status, name="pct_status")
_PCT_DETAIL = pd.DataFrame(_pct_detail)
_PCT_STATS = {
    "tolerance_pct_sum": _PCT_TOL,
    "n_formulation_overshoot_raw": int(sum(
        1 for v in _form_status.values() if v != "ok")),
    "form_status_counts": dict(Counter(_form_status.values())),
    "form_terminal_status_counts": dict(Counter(
        v.split("+")[-1] for v in _form_status.values())),
    "row_status_counts": dict(Counter(_pct_row_status)),
    "n_rows_blanked": int((_pct_row_status != "ok").sum()),
    "ing2_pct_reject_counts": dict(ING["ing2_pct_reject"].value_counts(dropna=True)),
}
log(f"    행 판정: {_PCT_STATS['row_status_counts']}")
log(f"    제형 판정(최종단계): {_PCT_STATS['form_terminal_status_counts']}")
log(f"    2차 산문칸 거부: {_PCT_STATS['ing2_pct_reject_counts']}")
log(f"    함량 결측화 {_PCT_STATS['n_rows_blanked']} 행 · "
    f"함량 보유 {int(ING['ing_pct_best'].notna().sum())} / {len(ING)}")
assert not ((ING["ing_pct_best"] < 0) | (ING["ing_pct_best"] > 100)).any(), \
    "복구 후에도 0~100 범위를 벗어난 함량이 있다"
_chk = ING.groupby("Formulation_ID")["ing_pct_best"].sum(min_count=1)
_chk_lb = ING.assign(_lb=[_pct_lower_bound(v, k, t) for v, k, t in zip(
    ING["ing_pct_best"], ING["ing_pct_kind_best"],
    ING["ing2_pct_text"].fillna(ING["pct_text"]))]).groupby("Formulation_ID")["_lb"].sum()
assert (_chk_lb > _PCT_TOL).sum() == 0, \
    f"복구 후에도 하한합 100.5 초과 제형 {int((_chk_lb > _PCT_TOL).sum())}건"
log(f"    [assert] 복구 후 하한합 100.5 초과 0건 · 액면합 초과 "
    f"{int((_chk > _PCT_TOL).sum())}건(상한/범위 표기분만 잔존)")

# 4c. 역할 추정
# 원본에 성분별 active 플래그가 없다(n_ing_active 는 제형 단위 개수뿐). 따라서
# '이름 규칙 + 함량순위' 라는 대리지표를 쓰고, 그 사실을 한계로 명시한다.
ING["ing_pct_rank"] = (ING.groupby("Formulation_ID")["ing_pct_best"]
                       .rank(ascending=False, method="first"))
ING["ing_is_max_pct"] = (ING["ing_pct_rank"] == 1).astype("int8")
ING["ing_role"] = [guess_role(n) for n in ING["ing_name_best"]]
# 역할 규칙에 안 걸리고 함량 1위인 성분은 유효성분(또는 원제)일 가능성이 높다.
ING["ing_role_final"] = np.where(
    (ING["ing_role"] == "unknown") & (ING["ing_pct_rank"] == 1), "active_presumed",
    ING["ing_role"])
log(f"    역할 추정: {dict(Counter(ING['ing_role_final']).most_common())}")

# ================================================================ 5. 디스크립터

log("[5] RDKit 디스크립터 계산")
smis = sorted({str(s).strip() for s in ING["smiles"].dropna() if str(s).strip()})
DESC, FPM, FPK, FAIL = {}, {}, {}, []
for s in smis:
    d, m, k = desc_for_smiles(s)
    if d is None:
        FAIL.append(s)
        continue
    DESC[s], FPM[s], FPK[s] = d, m, k
log(f"    unique SMILES {len(smis)} · 성공 {len(DESC)} · 실패 {len(FAIL)}")
if FAIL:
    log(f"    실패 예: {FAIL[:3]}")

EXTRA_NUM = ["n_structural_alerts", "formal_charge", "n_pos_atoms", "n_neg_atoms",
             "is_ionic", "n_fragments", "has_metal", "logKp_pottsguy", "max_alkyl_chain"]
FLAG_COLS = sorted(k for k in next(iter(DESC.values())) if k.startswith("has_")) if DESC else []
NUM_DESC = DESC_NAMES + EXTRA_NUM + FLAG_COLS

dsc = []
for s in ING["smiles"]:
    key = str(s).strip() if pd.notna(s) else ""
    d = DESC.get(key)
    dsc.append({c: (d.get(c) if d else None) for c in NUM_DESC}
               | {"surfactant_class": (d.get("surfactant_class") if d else None),
                  "desc_ok": d is not None})
ING = pd.concat([ING.reset_index(drop=True), pd.DataFrame(dsc)], axis=1)
log(f"    디스크립터 부착 {int(ING['desc_ok'].sum())} / {len(ING)} 행 "
    f"(구조 결측 {int((~ING['desc_ok']).sum())})")

# 계면활성제 클래스는 구조가 없으면 이름 규칙으로 보완한다(구조 커버리지가 낮으므로).
_SURF_NAME = {"anionic": r"sulfate|sulphate|sulfonate|sulphonate|laureth sulfate|"
                         r"dodecylbenzene|alkylbenzene sulfon|soap|oleate|stearate salt",
              "cationic": r"quaternium|quaternary ammonium|benzalkonium|"
                          r"trimethylammonium|cetrimonium|didecyldimethyl",
              "nonionic": r"ethoxylat|polysorbate|tween|nonoxynol|octoxynol|"
                          r"alcohols, ethoxylated|POE|polyoxyethylene|alkyl polyglucoside",
              "amphoteric": r"betaine|amphoacetate|sultaine|amine oxide"}
_surf_re = {k: re.compile(v, re.I) for k, v in _SURF_NAME.items()}


def _surf_fill(row):
    if row["surfactant_class"] and row["surfactant_class"] != "none":
        return row["surfactant_class"], "structure"
    n = str(row["ing_name_best"] or "")
    for k, rx in _surf_re.items():
        if rx.search(n):
            return k, "name_rule"
    return (row["surfactant_class"] or None), ("structure" if row["desc_ok"] else None)


sf = ING.apply(_surf_fill, axis=1, result_type="expand")
ING["surf_class_best"], ING["surf_class_src"] = sf[0], sf[1]
log(f"    계면활성제 클래스: {dict(Counter(ING['surf_class_best'].dropna()).most_common())}")

# ================================================================ 6. 제형 단위 집계

log("[6] 제형 블록 집계 (농도가중 모멘트 + 상호작용)")
AGG_BASE = [c for c in ["MolWt", "MolLogP", "TPSA", "NumHDonors", "NumHAcceptors",
                        "NumRotatableBonds", "NumAromaticRings", "FractionCSP3",
                        "MolMR", "LabuteASA", "BertzCT", "Kappa2", "HallKierAlpha",
                        "MaxAbsPartialCharge", "logKp_pottsguy", "max_alkyl_chain",
                        "n_structural_alerts", "NumHeteroatoms", "RingCount"]
            if c in ING.columns]

fp_dim_m, fp_dim_k = 2048, 167
form_rows, pool_m, pool_k, pool_ids = [], [], [], []

for fid, g in ING.groupby("Formulation_ID", sort=False):
    n = len(g)
    pct = g["ing_pct_best"].astype(float)
    known = pct.notna()
    w = pct.where(known, 0.0).to_numpy(dtype=float)
    wsum = w.sum()
    wn = w / wsum if wsum > 0 else np.zeros_like(w)
    row = {"Formulation_ID": fid, "f_n_ing": n,
           "f_n_pct_known": int(known.sum()),
           "f_pct_sum_known": float(wsum) if wsum > 0 else None,
           "f_pct_coverage": float(known.mean()),
           "f_n_smiles": int(g["desc_ok"].sum()),
           "f_smiles_coverage": float(g["desc_ok"].mean()),
           # 구조를 아는 성분이 전체 질량의 몇 %인가 — 집계 신뢰도의 핵심 지표
           "f_pct_with_structure": float(w[g["desc_ok"].to_numpy()].sum()) if wsum > 0 else None}

    # 조성 다양성 (Shannon). 단일물질 vs 복합제형을 구분한다.
    p = wn[wn > 0]
    row["f_shannon"] = float(-(p * np.log(p)).sum()) if len(p) else None
    row["f_simpson"] = float(1 - (p ** 2).sum()) if len(p) else None
    row["f_max_pct"] = float(pct.max()) if known.any() else None

    # 디스크립터 모멘트
    for c in AGG_BASE:
        v = g[c].astype(float).to_numpy()
        ok = np.isfinite(v)
        if not ok.any():
            for suf in ("wmean", "mean", "max", "min", "range", "std"):
                row[f"f_{c}_{suf}"] = None
            continue
        vv, ww = v[ok], w[ok]
        row[f"f_{c}_mean"] = float(vv.mean())
        row[f"f_{c}_max"] = float(vv.max())
        row[f"f_{c}_min"] = float(vv.min())
        row[f"f_{c}_range"] = float(vv.max() - vv.min())
        row[f"f_{c}_std"] = float(vv.std(ddof=0))
        row[f"f_{c}_wmean"] = float((vv * ww).sum() / ww.sum()) if ww.sum() > 0 else None

    # 역할별 농도합
    for role in ("active_presumed", "water", "surfactant", "solvent", "synergist",
                 "carrier_inert", "propellant", "ph_adjuster", "preservative",
                 "thickener", "fragrance_dye", "unknown"):
        m = (g["ing_role_final"] == role).to_numpy()
        row[f"f_pct_{role}"] = float(w[m].sum()) if wsum > 0 else None
        row[f"f_n_{role}"] = int(m.sum())

    # 계면활성제 전하별 농도합 + 전하쌍 상호작용
    sc = g["surf_class_best"].fillna("none").to_numpy()
    for cls in ("anionic", "cationic", "nonionic", "amphoteric"):
        row[f"f_pct_surf_{cls}"] = float(w[sc == cls].sum()) if wsum > 0 else None
        row[f"f_n_surf_{cls}"] = int((sc == cls).sum())
    a = row["f_pct_surf_anionic"] or 0.0
    c_ = row["f_pct_surf_cationic"] or 0.0
    nio = row["f_pct_surf_nonionic"] or 0.0
    amp = row["f_pct_surf_amphoteric"] or 0.0
    row["f_pct_surf_total"] = a + c_ + nio + amp
    # 음이온×양이온 곱: 두 계면활성제가 이온쌍을 이뤄 단량체 활성이 떨어지는 길항 기전
    # (docx §상호작용, SDS+C12TAB 사례)의 최소 표현.
    row["f_surf_charge_pair"] = a * c_
    # 음이온×비이온: 혼합미셀 형성 → CMC 하강 → 저농도 시너지
    row["f_surf_anionic_nonionic"] = a * nio
    row["f_surf_charge_imbalance"] = (a - c_) / (a + c_) if (a + c_) > 0 else None

    # 용매 × 활성성분 소수성: POEA 형 침투촉진 상호작용
    slv = row["f_pct_solvent"] or 0.0
    row["f_pct_active"] = row["f_pct_active_presumed"]
    lp = row.get("f_MolLogP_wmean")
    row["f_solvent_x_logp"] = slv * lp if lp is not None else None
    row["f_surf_x_logp"] = row["f_pct_surf_total"] * lp if lp is not None else None
    # 계면활성제 × 침투계수: 각질층 교란 후 투과 증가의 곱셈항
    kp = row.get("f_logKp_pottsguy_wmean")
    row["f_surf_x_logkp"] = row["f_pct_surf_total"] * kp if kp is not None else None
    row["f_has_synergist"] = int((row["f_n_synergist"] or 0) > 0)

    # 성분간 구조 유사도(Morgan Tanimoto). 유사성분 조합 vs 이질조합의 구분.
    fps = [FPM[str(s).strip()] for s in g["smiles"]
           if pd.notna(s) and str(s).strip() in FPM]
    if len(fps) >= 2:
        M = np.vstack(fps).astype(np.float32)
        inter = M @ M.T
        cnt = M.sum(1)
        union = cnt[:, None] + cnt[None, :] - inter
        with np.errstate(divide="ignore", invalid="ignore"):
            T = np.where(union > 0, inter / union, 0.0)
        iu = np.triu_indices(len(fps), 1)
        t = T[iu]
        row["f_tanimoto_mean"] = float(t.mean())
        row["f_tanimoto_max"] = float(t.max())
        row["f_tanimoto_min"] = float(t.min())
    else:
        row["f_tanimoto_mean"] = row["f_tanimoto_max"] = row["f_tanimoto_min"] = None

    # 지문 pooling — 제형 단위 지문. max = '이 구조가 조성 안에 존재하는가',
    # 농도가중 mean = '얼마나 많이 존재하는가'. 둘은 다른 정보다.
    if fps:
        M = np.vstack(fps).astype(np.float32)
        wf = np.array([w[i] for i, s in enumerate(g["smiles"])
                       if pd.notna(s) and str(s).strip() in FPM], dtype=np.float32)
        mx = M.max(0)
        wm = (M * wf[:, None]).sum(0) / wf.sum() if wf.sum() > 0 else M.mean(0)
        ks = [FPK[str(s).strip()] for s in g["smiles"]
              if pd.notna(s) and str(s).strip() in FPK]
        K = np.vstack(ks).astype(np.float32)
        kmx, kwm = K.max(0), ((K * wf[:, None]).sum(0) / wf.sum() if wf.sum() > 0 else K.mean(0))
    else:
        mx = wm = np.zeros(fp_dim_m, dtype=np.float32)
        kmx = kwm = np.zeros(fp_dim_k, dtype=np.float32)
    pool_m.append(np.concatenate([mx, wm]))
    pool_k.append(np.concatenate([kmx, kwm]))
    pool_ids.append(fid)

    # ---- GHS CT 가산 baseline (Track B) : 성분별 실측 GHS × 성분별 농도
    for ep, col in (("eye", "ing_ghs_eye"), ("skin", "ing_ghs_skin"), ("sens", "ing_ghs_sens")):
        pairs = list(zip(g[col], g["ing_pct_best"]))
        ct = ct_predict(pairs, ep)
        row[f"f_ct_{ep}_cat"] = ct["cat"]
        row[f"f_ct_{ep}_s1"] = ct["s1"]
        row[f"f_ct_{ep}_s2"] = ct["s2"]
        row[f"f_ct_{ep}_add"] = ct["add"]
        row[f"f_ct_{ep}_n_known"] = ct["n_known"]
        row[f"f_ct_{ep}_coverage"] = (ct["n_known"] / n) if n else None
    form_rows.append(row)

FAGG = pd.DataFrame(form_rows)
log(f"    제형 집계 {FAGG.shape}")

# ================================================================ 7. 제형 시트 조립

log("[7] formulation_v2 조립")
FORM = (F1.merge(FRM2, on="Formulation_ID", how="left")
          .merge(TOX2, on="Formulation_ID", how="left")
          .merge(PHC2, on="Formulation_ID", how="left")
          .merge(P1, on="Formulation_ID", how="left")
          .merge(T11, on="Formulation_ID", how="left")
          .merge(FAGG, on="Formulation_ID", how="left"))
assert len(FORM) == len(F1), "제형 조인 행수 변동"

# ---- P-PCT: 제형 단위 함량 합계를 복구 후 값으로 재계산 -----------------------
# v1 의 pct_sum_known / n_pct_known 은 두 출처 블록을 합산한 값이라 100% 를 넘는다.
# 원값은 pct_sum_known_raw 로 보존하고(감사 추적), 피처로 쓰이는 pct_sum_known 은
# 복구 후 값으로 바꾼다. **추측 보정은 하지 않는다** — 결측화된 행은 합에서 빠진다.
FORM["pct_sum_known_raw"] = FORM["pct_sum_known"].astype(float)
FORM["n_pct_known_raw"] = pd.to_numeric(FORM["n_pct_known"], errors="coerce")
# 컬럼의 정의(=v1 pct_value 의 합)는 바꾸지 않고, 복구에서 결측화된 행만 뺀다.
# (ing_pct_best 합은 f_pct_sum_known 이 이미 담당한다 — 중복 정의를 만들지 않는다.)
_v1_kept = ING["pct_value"].where(ING["pct_status"].eq("ok"))
_fix_sum = _v1_kept.groupby(ING["Formulation_ID"]).sum(min_count=1)
_fix_n = _v1_kept.groupby(ING["Formulation_ID"]).count()
FORM["pct_sum_known"] = FORM["Formulation_ID"].map(_fix_sum)
FORM["n_pct_known"] = FORM["Formulation_ID"].map(_fix_n).fillna(0).astype("int64")
FORM["pct_status"] = FORM["Formulation_ID"].map(_PCT_FORM_STATUS).fillna("ok")
FORM["pct_repaired"] = FORM["pct_status"].ne("ok") & FORM["pct_status"].ne(
    "ok_bound_consistent")
log(f"    함량합 재계산: >100.5 제형 {int((FORM['pct_sum_known_raw'] > 100.5).sum())} "
    f"→ {int((FORM['pct_sum_known'] > 100.5).sum())} "
    f"(복구표시 {int(FORM['pct_repaired'].sum())})")

# 최종 제형코드 (2차 우선)
FORM["form_code_best"] = FORM["form2_code_base"].fillna(FORM["formulation_code"])
FORM["form_code_src"] = np.where(FORM["form2_code_base"].notna(), "2nd",
                                 np.where(FORM["formulation_code"].notna(), "v1", None))
log(f"    제형코드 확보 {FORM['form_code_best'].notna().sum()} / {len(FORM)} "
    f"(2차 신규 {int((FORM['form_code_src']=='2nd').sum())})")

# pH: 2차 특성 우선, 없으면 Phase1 제품 SDS
FORM["ph_best"] = FORM["pc2_ph_lo"]
_p1ph = pd.to_numeric(FORM.get("p1_ph"), errors="coerce") if "p1_ph" in FORM else None
if _p1ph is not None:
    FORM["ph_best"] = FORM["ph_best"].fillna(_p1ph)
FORM["ph_src"] = np.where(FORM["pc2_ph_lo"].notna(), "2nd_physchem",
                          np.where(FORM["ph_best"].notna(), "phase1_sds", None))
FORM["ph_strong_acid"] = (FORM["ph_best"] <= 2).where(FORM["ph_best"].notna())
FORM["ph_strong_base"] = (FORM["pc2_ph_hi"].fillna(FORM["ph_best"]) >= 11.5) \
    .where(FORM["ph_best"].notna())
# GHS 가산성 접근을 쓸 수 없는 경우 = docx 표1 예외. CT baseline 신뢰도 게이트로 사용.
FORM["ct_not_applicable"] = (
    FORM["ph_strong_acid"].fillna(False) | FORM["ph_strong_base"].fillna(False)
    | (FORM["f_pct_surf_total"].fillna(0) >= 20)     # 계면활성제 고농도 → 비가산
    | (FORM["f_has_aldehyde_max"].fillna(0) > 0 if "f_has_aldehyde_max" in FORM else False))
log(f"    pH 확보 {FORM['ph_best'].notna().sum()} · 강산 "
    f"{int(FORM['ph_strong_acid'].fillna(False).sum())} · 강염기 "
    f"{int(FORM['ph_strong_base'].fillna(False).sum())} · CT 예외 "
    f"{int(FORM['ct_not_applicable'].sum())}")

# ================================================================ 8. 타깃 병합

log("[8] 타깃 병합 (출처 우선순위 + 충돌 플래그)")

# ---------------------------------------------------------------- 8-0. P0-1
# v1 음성 라벨 유실 복구.
#
# 진단(04_모델산출물/v4/label_source_bias_report.json): v1 formulation 시트는
#   sds_ghs_{ep}            = 파싱된 GHS 카테고리 (문자열)
#   sds_grade_status_{ep}   = 그 파싱의 '상태'
# 를 쌍으로 가진다. SDS 가 "Not classified" 를 명시한 행은 status=='negative' 로
# 기록되었으나, 카테고리 컬럼에는 아무 값도 쓰이지 않았다(예외 0건, "NC" 문자열
# 0건). v4 빌더는 sds_ghs_* 만 읽었으므로 이 명시적 음성이 전부 소멸했고,
# 그 결과 y_*_src=='sds_v1' 행의 양성률이 3개 엔드포인트 전부 100.0% 였다.
#
# 여기서 status=='negative' 인 행에만 "NC" 를 **보충**한다.
STATUS_DOMAIN_EXPECTED = {"negative", "not_stated", "verdict_only", "no_sds", "ok"}
# 'negative' 만 음성으로 단정할 근거가 있다:
#   negative      → SDS 본문에 "Not classified" 등 명시적 비분류 진술이 있었음  ⇒ NC
#   ok            → 카테고리가 정상 파싱됨 (sds_ghs_* 에 값 존재) ⇒ 손댈 필요 없음
#   not_stated    → SDS 에 해당 엔드포인트 언급 자체가 없음 ⇒ 결측 유지 (미측정 ≠ 음성)
#   verdict_only  → 신호어/H문구 등 간접 판정만 있고 등급 진술 없음 ⇒ 결측 유지
#   no_sds        → SDS 문서를 확보하지 못함 ⇒ 결측 유지
STATUS_NEGATIVE = {"negative"}

_neg_recover_log = {}
for ep in ("eye", "skin", "sens"):
    cat_col, st_col = f"sds_ghs_{ep}", f"sds_grade_status_{ep}"
    eff_col = f"sds_ghs_{ep}_eff"          # 라벨 후보 수집이 실제로 읽는 컬럼
    if cat_col not in FORM.columns:
        FORM[eff_col] = None
        FORM[f"sds_v1_neg_recovered_{ep}"] = False
        _neg_recover_log[ep] = {"error": f"{cat_col} 없음"}
        continue
    st = FORM[st_col].astype("string").str.strip().str.lower() \
        if st_col in FORM.columns else pd.Series(pd.NA, index=FORM.index, dtype="string")
    dom = {s for s in st.dropna().unique()}
    unexpected = sorted(dom - STATUS_DOMAIN_EXPECTED)
    if unexpected:
        # 예상 밖 상태값은 **보수적으로 무시**(결측 유지)하고 로그만 남긴다.
        log(f"    !! {st_col} 예상 밖 상태값 무시: {unexpected}")
    has_cat = FORM[cat_col].map(lambda v: nrm_cat(v) is not None)
    is_neg = st.isin(STATUS_NEGATIVE).fillna(False)
    # 기존 값 절대 보존: 카테고리가 이미 파싱된 행은 건드리지 않는다.
    recov = (~has_cat) & is_neg
    FORM[eff_col] = FORM[cat_col].where(has_cat, other=None)
    FORM.loc[recov, eff_col] = "NC"
    FORM[f"sds_v1_neg_recovered_{ep}"] = recov.to_numpy()
    _neg_recover_log[ep] = {
        "status_domain": {k: int((st == k).sum()) for k in sorted(dom)},
        "unexpected_status_values": unexpected,
        "n_status_negative": int(is_neg.sum()),
        "n_status_negative_with_existing_cat": int((is_neg & has_cat).sum()),
        "n_recovered_to_NC": int(recov.sum()),
        "n_cat_before": int(has_cat.sum()),
        "n_cat_after": int(FORM[eff_col].map(lambda v: nrm_cat(v) is not None).sum()),
    }
    log(f"    sds_v1 음성복구 {ep:5s}: status=negative {int(is_neg.sum())}행 중 "
        f"카테고리 결측 {int(recov.sum())}행 → NC 보충 "
        f"(기존 카테고리 {int(has_cat.sum())} → {_neg_recover_log[ep]['n_cat_after']})")

# 우선순위: NTP 실측(in vivo/ICE) > 원본 SDS > 2차 SDS > Phase1 제품 SDS
PRIO = [("ntp_measured", {"eye": "ntp_eye_ghs_cat", "skin": "ntp_skin_ghs_cat", "sens": None}),
        # sds_ghs_*_eff = sds_ghs_* + (status=='negative' & 카테고리 결측 → "NC").
        # 우선순위 자체는 v4 와 동일하다(ntp_measured > sds_v1 > ...).
        ("sds_v1", {"eye": "sds_ghs_eye_eff", "skin": "sds_ghs_skin_eff",
                    "sens": "sds_ghs_sens_eff"}),
        ("sds_2nd", {"eye": "tox2_eye_ghs", "skin": "tox2_skin_ghs", "sens": "tox2_sens_ghs"}),
        ("phase1_sds", {"eye": "p1_eye", "skin": "p1_skin", "sens": "p1_sens"}),
        # F0.5 — 같은 2차 수집 패스의 SDS Section 11 서술문. 기존 라벨을 덮지 않도록
        # **가장 낮은 우선순위**에 둔다. 즉 신규 라벨만 추가되고 기존 값은 불변이다.
        ("sds_2nd_sec11", {"eye": "t11_eye_label", "skin": "t11_skin_label",
                           "sens": "t11_sens_label"})]

for ep in ("eye", "skin", "sens"):
    vals, srcs, nsrc, conf, detail = [], [], [], [], []
    _recov_ep = FORM[f"sds_v1_neg_recovered_{ep}"].to_numpy()
    for i in range(len(FORM)):
        cands = []
        for name, m in PRIO:
            col = m[ep]
            if col and col in FORM.columns:
                v = nrm_cat(FORM[col].iloc[i])
                if v is not None:
                    cands.append((name, v))
        if not cands:
            vals.append(None); srcs.append(None); nsrc.append(0); conf.append(None)
            detail.append(None)
            continue
        vals.append(cands[0][1])
        srcs.append(cands[0][0])
        nsrc.append(len(cands))
        uniq = {SEV[ep].get(v, -1) for _, v in cands}
        conf.append(len(uniq) > 1)
        # 출처 추적(P0-1): y_{ep}_src 의 도메인은 v4 와 동일하게 유지하고,
        # 복구 경로는 y_{ep}_src_detail / y_{ep}_from_neg_recovery 로 분리한다.
        # 이유 — 하류 코드(audit_label_source_bias.py 등)는 y_*_src == "sds_v1"
        # 을 문자열 동등비교로 쓴다. src 문자열을 쪼개면 그 분석에서 복구된
        # 음성이 'sds_v1' 집단에서 빠져나가 양성률이 여전히 1.000 으로 보이는
        # (=결함을 은폐하는) 오독을 낳는다. 따라서 src 는 sds_v1 로 두고
        # 세분 정보는 별도 컬럼에 남긴다.
        detail.append("sds_v1_negative_recovered"
                      if (cands[0][0] == "sds_v1" and bool(_recov_ep[i])) else cands[0][0])
    FORM[f"y_{ep}"] = vals
    FORM[f"y_{ep}_src"] = srcs
    FORM[f"y_{ep}_src_detail"] = detail
    FORM[f"y_{ep}_from_neg_recovery"] = [d == "sds_v1_negative_recovered" for d in detail]
    FORM[f"y_{ep}_n_src"] = nsrc
    FORM[f"y_{ep}_conflict"] = conf
    FORM[f"y_{ep}_ord"] = [SEV[ep].get(v) if v else None for v in vals]
    FORM[f"y_{ep}_bin"] = [None if v is None else int(SEV[ep].get(v, 0) > 0) for v in vals]
    log(f"    y_{ep:5s} n={FORM[f'y_{ep}'].notna().sum():5d} "
        f"충돌={int(pd.Series(conf).fillna(False).sum()):4d} "
        f"분포={dict(pd.Series(vals).value_counts().head(6))}")

# Track B 잔차 타깃: 관측 서열 − CT 예측 서열. CT 커버리지가 있는 행에서만 정의된다.
for ep in ("eye", "skin", "sens"):
    ctc = FORM[f"f_ct_{ep}_cat"].map(lambda v: SEV[ep].get(nrm_cat(v)) if pd.notna(v) else None)
    FORM[f"ct_{ep}_ord"] = ctc
    FORM[f"resid_{ep}"] = pd.to_numeric(FORM[f"y_{ep}_ord"], errors="coerce") - \
        pd.to_numeric(ctc, errors="coerce")
    n = FORM[f"resid_{ep}"].notna().sum()
    log(f"    resid_{ep:5s} 정의행={n}" +
        (f" (0={int((FORM[f'resid_{ep}']==0).sum())} "
         f"+={int((FORM[f'resid_{ep}']>0).sum())} "
         f"-={int((FORM[f'resid_{ep}']<0).sum())})" if n else ""))

# 그룹키: 같은 주성분을 공유하는 제형이 train/test 로 쪼개지면 누출된다.
#
# ---- v5 caveat C2 (2026-08-30) --------------------------------------------
# 독립 검증 결과: (a) group_key 값이 7개 제형에서 v4 와 달라졌고(주성분 CAS 가
# 결측이 되면서 agg('first') 가 하위 성분의 CAS 를 집는다), (b) 142개 제형은
# group_primary_cas 와 group_primary_name 이 **서로 다른 성분행**에서 왔다
# (groupby.agg('first') 는 컬럼별로 독립적으로 결측을 건너뛴다).
# 정의는 **바꾸지 않는다** — 누수방지 기능은 유효하고 임의 변경은 금지사항이다.
# 대신 무엇으로 그룹키가 결정됐는지 추적 가능하도록 진단 컬럼만 추가한다.
_ING_SORT = ING.sort_values(["Formulation_ID", "ing_pct_best"], ascending=[True, False])
prim = (_ING_SORT.groupby("Formulation_ID")
        .agg(group_primary_cas=("ing_cas_best", "first"),
             group_primary_name=("ing_name_best", "first")))


def _first_valid_ing_idx(col):
    """정렬 후 해당 컬럼의 첫 유효값을 제공한 ing_idx (agg('first') 와 동일 규칙)."""
    s = _ING_SORT[["Formulation_ID", "ing_idx", col]].dropna(subset=[col])
    return s.groupby("Formulation_ID")["ing_idx"].first()


prim["group_primary_cas_ing_idx"] = _first_valid_ing_idx("ing_cas_best")
prim["group_primary_name_ing_idx"] = _first_valid_ing_idx("ing_name_best")
FORM = FORM.merge(prim, on="Formulation_ID", how="left")
FORM["group_key"] = (FORM["group_primary_cas"].fillna("")
                     .where(lambda s: s != "", FORM["group_primary_name"].fillna(""))
                     .replace("", np.nan).fillna(FORM["Formulation_ID"]))
# group_key_src: cas / name / fid — 그룹키가 무엇으로 결정됐는지
_has_cas = FORM["group_primary_cas"].fillna("").astype(str).str.strip().ne("")
_has_nm = FORM["group_primary_name"].fillna("").astype(str).str.strip().ne("")
FORM["group_key_src"] = np.where(_has_cas, "cas", np.where(_has_nm, "name", "fid"))
# 그룹키를 실제로 결정한 성분행의 ing_idx (cas 우선, 없으면 name, 둘 다 없으면 결측)
FORM["group_primary_ing_idx"] = np.where(
    _has_cas, FORM["group_primary_cas_ing_idx"],
    np.where(_has_nm, FORM["group_primary_name_ing_idx"], np.nan))
# cas 와 name 이 같은 성분행에서 왔는가 (둘 중 하나라도 없으면 False = 동일행 근거 없음)
FORM["group_primary_from_same_row"] = (
    FORM["group_primary_cas_ing_idx"].notna()
    & FORM["group_primary_name_ing_idx"].notna()
    & (FORM["group_primary_cas_ing_idx"] == FORM["group_primary_name_ing_idx"]))
log(f"    그룹키 {FORM['group_key'].nunique()}개 / 제형 {len(FORM)}  "
    f"(최대 그룹 {FORM['group_key'].value_counts().max()}행)")
log(f"    group_key_src 분포 {dict(FORM['group_key_src'].value_counts())} · "
    f"cas/name 동일행 {int(FORM['group_primary_from_same_row'].sum())} / "
    f"상이행 {int((~FORM['group_primary_from_same_row']).sum())}")

# ================================================================ 8z. 라벨 신뢰도
# ---- v5 caveat: 라벨 값은 **변경하지 않고** 신뢰도만 기록한다 ----------------
# 독립검증이 지적한 3부류(사람 재검 대상)를 y 를 건드리지 않고 표시한다.
#   Tier1  복구 근거 인용문에 명시적 음성 진술이 없고 반대신호만 있는 행
#          (검증 리포트 quote_evidence_hard_problem_rows = 4건)
#   Tier2  문서 전반은 음성이나 해당 엔드포인트가 '자료없음/미측정' 인 행
#          (quote_evidence_no_data_rows = 16건)
#   FLIP   복구된 NC 가 최종 y 로 채택되며 다른 출처의 양성 주장을 이긴 행
#          (recovered_nc_overrides_positive_claim = eye13/skin7/sens5 = 25건)
# 정규식은 verify_v5_integrity.py 와 동일 문자열을 사용해 판정을 재현한다.
log("[8z] 라벨 신뢰도 태깅 (y 값 불변)")
_QCOL = {"eye": "tox_eye_irritation_quote", "skin": "tox_skin_irritation_quote",
         "sens": "tox_skin_sensitization_quote"}
_NEG_RX = (r"not classified|shall not be classified|criteria are not met|"
           r"not meet the criteria|non-?irritant|non-?irritating|no irritat|not irritating|"
           r"not expected to|does not cause|no sensitis|no sensitiz|non-?sensitis|"
           r"non-?sensitiz|not a (skin |dermal )?sensitizer|not sensitizing|no effects known|"
           r"not a hazardous|not hazardous|no skin irritation|no eye irritation|"
           r"will not occur|did not cause|no significant effects|not a sensitizer|"
           r"minimal effects|not corrosive|no irritant effect")
_SUS_RX = (r"not determined|no data available|not conclusive|inconclusive|"
           r"not available|n/av|no information|not tested|may cause|likely irritat|"
           r"causes serious|irritating to|risk of serious|allergic")
_NODATA_RX = (r"not determined|no data available|not conclusive|inconclusive|"
              r"\bn/av\b|no information|not tested|no effects known|not applicable")
_lc_rows = []
for ep in ("eye", "skin", "sens"):
    qser = (FORM[_QCOL[ep]].astype("string") if _QCOL[ep] in FORM.columns
            else pd.Series(pd.NA, index=FORM.index, dtype="string"))
    conf_col, tier_col = [], []
    for i in range(len(FORM)):
        yv = FORM[f"y_{ep}"].iloc[i]
        if yv is None or (isinstance(yv, float) and math.isnan(yv)) or pd.isna(yv):
            conf_col.append(None); tier_col.append(None)
            continue
        q = "" if pd.isna(qser.iloc[i]) else str(qser.iloc[i])
        recov = bool(FORM[f"y_{ep}_src_detail"].iloc[i] == "sds_v1_negative_recovered")
        neg = bool(re.search(_NEG_RX, q, re.I))
        sus = bool(re.search(_SUS_RX, q, re.I))
        nod = bool(re.search(_NODATA_RX, q, re.I))
        ybin = FORM[f"y_{ep}_bin"].iloc[i]
        confl = bool(FORM[f"y_{ep}_conflict"].iloc[i]) \
            if pd.notna(FORM[f"y_{ep}_conflict"].iloc[i]) else False
        # ct_{ep}_ord 는 §8 잔차타깃 계산부에서 이미 만들어져 있다(f_ct_*_ord 는 §10
        # 에서 생성되므로 이 시점엔 없다).
        ctord = FORM[f"ct_{ep}_ord"].iloc[i] if f"ct_{ep}_ord" in FORM.columns else None
        ct_pos = bool(pd.notna(ctord) and ctord > 0)
        why = []
        if recov:
            why.append("y=sds_v1 status='negative' 복구분(카테고리 미기재 → NC 보충)")
        t1 = recov and (not neg) and sus
        t2 = recov and neg and nod
        flip = recov and (ybin == 0) and confl
        if t1:
            why.append("T1: 인용문에 명시적 음성진술 없음 + 반대신호(자료없음/자극가능/"
                       "알레르기) 존재 → 근거 모순")
        if t2:
            why.append("T2: 문서에 음성진술은 있으나 해당 엔드포인트가 자료없음/미측정 "
                       "→ 시험근거 없는 음성")
        if flip:
            why.append("FLIP: 복구된 NC 가 하위 출처의 양성 주장을 이겨 v4 대비 양성→음성 전환")
        if ct_pos and ybin == 0:
            why.append("CT baseline(GHS 가산법)은 양성인데 최종 라벨은 음성 — 상충")
        if confl and not flip:
            why.append("출처 간 서열 상충(y_conflict=True)")
        if t1 or flip:
            c = "low"
        elif t2 or recov or (ct_pos and ybin == 0) or confl:
            c = "medium"
        else:
            c = "high"
            why.append("복구분 아님 · 출처 상충 없음 · CT 와 모순 없음")
        tier = ("T1_evidence_contradicted" if t1 else
                "FLIP_pos_to_neg" if flip else
                "T2_endpoint_no_data" if t2 else
                "RECOVERED_negative" if recov else
                "CT_CONTRADICTION" if (ct_pos and ybin == 0) else
                "SOURCE_CONFLICT" if confl else "NONE")
        conf_col.append(c); tier_col.append(tier)
        _lc_rows.append({
            "Formulation_ID": FORM["Formulation_ID"].iloc[i],
            "endpoint": ep, "confidence": c, "tier": tier,
            "근거인용문": q[:400].replace("\n", " / "),
            "사유": " | ".join(why),
            "y": yv, "y_bin": ybin,
            "y_src_detail": FORM[f"y_{ep}_src_detail"].iloc[i],
            "ct_cat": FORM[f"f_ct_{ep}_cat"].iloc[i] if f"f_ct_{ep}_cat" in FORM.columns else None,
            "from_neg_recovery": recov, "y_conflict": confl,
            "quote_has_explicit_negative": neg, "quote_has_suspicious_marker": sus,
            "quote_has_no_data_marker": nod})
    FORM[f"label_confidence_{ep}"] = conf_col
    FORM[f"label_tier_{ep}"] = tier_col
_LC = pd.DataFrame(_lc_rows)
_LC.to_csv(f"{OUTDIR}/label_confidence_v5.csv", index=False)
_LC_STATS = {
    "rows": int(len(_LC)),
    "confidence": {k: int(v) for k, v in _LC["confidence"].value_counts().items()},
    "tier": {k: int(v) for k, v in _LC["tier"].value_counts().items()},
    "by_endpoint": {ep: {k: int(v) for k, v in
                         _LC[_LC.endpoint == ep]["confidence"].value_counts().items()}
                    for ep in ("eye", "skin", "sens")},
    "note": "y 값은 불변. 신뢰도는 학습에서 가중/제외 판단용 메타데이터일 뿐이다.",
}
log(f"    라벨 신뢰도 {len(_LC)}행 · {_LC_STATS['confidence']} · tier {_LC_STATS['tier']}")
log(f"    → {OUTDIR}/label_confidence_v5.csv")

# ================================================================ 9. 검증여부 요약

log("[9] 검증결과 요약 부착")
vsum = {}
for nm, df, col in (("성분", S_ING, "검증여부"), ("제형코드", S_FRM, "검증여부"),
                    ("독성코드", S_TOX, "검증여부"), ("특성", S_PHC, "검증여부")):
    vsum[nm] = dict(df[col].value_counts(dropna=False))
vfid = (S_ING.groupby("Formulation_ID")["검증여부"]
        .apply(lambda s: (s == "검증완료").mean()).rename("v_ing_ok_ratio"))
FORM = FORM.merge(vfid, on="Formulation_ID", how="left")
for nm, df, out in (("제형코드", S_FRM, "v_form_verdict"), ("독성코드", S_TOX, "v_tox_verdict"),
                    ("특성", S_PHC, "v_phc_verdict")):
    FORM = FORM.merge(df[["Formulation_ID", "검증여부"]].rename(columns={"검증여부": out}),
                      on="Formulation_ID", how="left")

# ================================================================ 10. 피처행렬

log("[10] 피처행렬 산출")
# 라벨 유도에 쓰인 컬럼 = 누출원. feature 로 절대 내보내지 않는다.
LABEL_SOURCE = {
    "tox_signal_word", "tox_h_statements", "tox2_signal_word", "tox2_h_codes",
    "tox2_signal_scheme", "tox2_raw", "ghs_h_statements", "ghs_signal_word",
    "p1_signal", "p1_h", "ing_ghs_h", "p1_note", "tox_notes",
    "cat_eye_ghs", "cat_skin_ghs", "cat_sens_ghs", "cat_eye_src", "cat_skin_src",
    "cat_sens_src", "cat_eye_epa", "cat_skin_epa", "cat_eye_epa_roman",
    "cat_skin_epa_roman", "sds_ghs_eye", "sds_ghs_skin", "sds_ghs_sens",
    # P0-1: 음성복구 경로에서 새로 읽는/만드는 컬럼. 전부 라벨 유도원이므로 X 금지.
    "sds_grade_status_eye", "sds_grade_status_skin", "sds_grade_status_sens",
    "sds_ghs_eye_eff", "sds_ghs_skin_eff", "sds_ghs_sens_eff",
    "sds_v1_neg_recovered_eye", "sds_v1_neg_recovered_skin", "sds_v1_neg_recovered_sens",
    "sds_epa_eye", "sds_epa_skin", "sds_epa_eye_roman", "sds_epa_skin_roman",
    "ntp_eye_ghs_cat", "ntp_skin_ghs_cat", "ntp_epa_eye_cat", "ntp_epa_skin_cat",
    "y_epa_eye", "y_epa_skin", "y_epa_eye_roman", "y_epa_skin_roman",
    "p1_eye", "p1_skin", "p1_sens",
    "tox2_eye_ghs", "tox2_skin_ghs", "tox2_sens_ghs", "tox2_sens_resp_ghs",
    "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens",
} | T11_LABEL_SOURCE
LABEL_PREFIX = ("y_", "resid_", "tox_eye_", "tox_skin_", "tox_ld50", "ghs_cat_",
                # v5: 라벨 신뢰도/티어는 y 로부터 파생된 메타데이터 → 피처 금지
                "label_confidence_", "label_tier_",
                "ghs_eye_", "ghs_skin_", "ntp_eye_", "ntp_skin_", "ntp_ld50",
                "ntp_lc50", "ntp_epa_", "ntp_conflict", "tox2_", "ghs_n_",
                "y_conflict", "y_needs_review", "y_pref")
ID_COLS = {"Formulation_ID", "product_name", "ing_idx", "group_key",
           "group_primary_cas", "group_primary_name",
           # v5 caveat C2: 그룹키 진단 컬럼. 분할용 메타데이터이므로 절대 피처 아님
           # (group_primary_ing_idx / *_from_same_row 는 수치·불리언이라 ID_COLS 에
           #  넣지 않으면 build_X 가 피처로 집어간다).
           "group_key_src", "group_primary_ing_idx", "group_primary_from_same_row",
           "group_primary_cas_ing_idx", "group_primary_name_ing_idx"}
PROV_PREFIX = ("v_", "p1_source", "p1_resolution", "p1_tier", "p1_confidence",
               "p1_batch", "p1_ingredients_match", "form2_verdict", "pc2_verdict",
               "tox2_verdict", "ing2_verdict", "ing2_evidence", "ing2_memo",
               "ing2_newlink", "pc2_unparsed", "pc2_suspect", "ing_ghs_src",
               "t11_resolution", "t11_confidence", "june_merge_basis")
SRC_SUFFIX = ("_src", "_source", "_url", "_rel", "_quote", "_by", "_evidence")

# ---------------------------------------------------------------- P0-2
# 부기(bookkeeping) 메타 피처 배제 목록.
#
# 판단 기준 (단 하나의 질문):
#   "이 피처는 제형의 화학/조성 정보를 담고 있는가, 아니면 **우리가 이 행을
#    어떻게 수집했는지**를 인코딩하는가?"
# 후자면 bookkeeping 이다. 라벨 출처와 수집 경로가 상관되어 있으므로
# (감사 결과: X 전체로 is_sds_v1 을 AUC 0.95 로 복원 가능, 화학피처만 쓰면
#  0.68~0.79) 이 피처들은 라벨출처 대리변수로 작동해 성능을 부풀린다.
#
# 정책: **X 행렬에서 물리적으로 제거하지 않는다.** (기존 산출물 호환 + 감사추적)
#       feature_role_manifest.csv 에 태깅만 하고, 모델링 스크립트가
#       chemistry_columns() 로 필터링한다. 배제가 기본값이다.
BOOKKEEPING_FEATS = {
    # (a) 라벨/타깃의 존재 자체 — y 를 알기 전엔 알 수 없는 정보
    "has_y", "has_toxicity", "has_ghs_label", "has_ntp_label", "has_epa_target",
    "n_tox_endpoints", "n_ntp_endpoints", "n_y_conflict",
    "trainable", "trainable_ntp", "trainable_ghs", "trainable_epa",
    "trainable_epa_any", "trainable_trackA", "trainable_trackB",
    "has_any_y", "n_y", "review_matched",
    # (b) 우리 스프레드시트의 행/파싱 부기
    "tox_n_composition_rows", "align_ok", "n_ing_align_bad", "n_structural_na",
    "n_smiles", "n_smiles_ice", "n_pct_known", "n_ing_missing", "n_ing_total_known",
    "ice_n_ingredients", "ice_n_smiles",
    "pc2_n_params", "pc2_n_items", "pc2_parse_ok", "ing2_parse_ok",
    "t11_present", "t11_gated", "f_n_pct_known", "f_n_smiles",
    "june_audit_recovered",
    # (c) 원본 문서 텍스트의 모호성 플래그 = 파서 상태
    "sds_epa_eye_ambiguous", "sds_epa_skin_ambiguous",
    # (d) Section 11 파싱 상태 (값의 개수 / 다중기재 / 파싱실패)
    "t11_ld50_oral_n", "t11_ld50_oral_multi", "t11_ld50_oral_unparsed",
    "t11_ld50_dermal_n", "t11_ld50_dermal_multi", "t11_ld50_dermal_unparsed",
    "t11_lc50_inhal_n", "t11_lc50_inhal_multi", "t11_lc50_inhal_unparsed",
    # (e) P-PCT 함량 복구 부기 — 복구 전 값과 복구 여부는 파싱 이력이지 조성이 아니다.
    "pct_sum_known_raw", "n_pct_known_raw", "pct_repaired", "ing_pct_raw",
    "n_pct_known",
}
# 경계가 애매한 피처 — 임의 판정하지 않고 ambiguous 로 태깅해 사람 판단에 넘긴다.
# 공통 성질: 실제 조성/측정 정보와 '우리 수집 커버리지'가 분리 불가능하게 섞임.
AMBIGUOUS_FEATS = {
    # 제형 복잡도(실재) vs 성분 추출 완결성(수집)
    "n_ingredients", "n_ing_active", "f_n_ing", "is_mixture",
    # 함량 합계/커버리지: 진짜 미기재 조성 vs 우리가 못 채운 부분
    "pct_sum_known", "active_pct_sum", "ice_active_pct_sum",
    "f_pct_sum_known", "f_pct_coverage", "f_smiles_coverage",
    "f_pct_with_structure", "ph_basis_pct",
    # CT baseline 커버리지: 성분 GHS 확보율(수집) 이면서 CT 신뢰도(실질)
    "f_ct_eye_n_known", "f_ct_eye_coverage", "f_ct_skin_n_known",
    "f_ct_skin_coverage", "f_ct_sens_n_known", "f_ct_sens_coverage",
    # 검열(censored) 여부: 보고된 측정치의 실제 성질이나 문서 서술 방식에도 의존
    "t11_ld50_oral_censored", "t11_ld50_dermal_censored", "t11_lc50_inhal_censored",
}
# 성분 역할별 '개수'(f_n_water 등)는 조성 정보이면서 추출 커버리지에 비례한다 → ambiguous
AMBIGUOUS_PREFIX = ("f_n_",)
# 단, f_n_structural_alerts_* 는 RDKit SMARTS 로 구조에서 직접 계산한 값이므로
# 수집 부기와 무관한 순수 화학 피처다 (AMBIGUOUS_PREFIX 예외).
CHEMISTRY_OVERRIDE_PREFIX = ("f_n_structural_alerts",)


def feature_role(col, base_role):
    """chemistry / bookkeeping / provenance / ambiguous / (label_source, id, target).

    base_role 은 classify() 결과(누출 차단용 1차 분류)를 그대로 승계한다.
    *_isna 결측플래그는 기저 컬럼의 판정을 물려받되, 기저가 chemistry 인 경우엔
    '값이 왜 없는가' 자체가 수집 이력이므로 ambiguous 로 강등한다.
    """
    if base_role in ("id", "label_source", "provenance"):
        return base_role if base_role != "provenance" else "provenance"
    base = re.sub(r"_isna$", "", col)
    is_na_flag = base != col
    if base in BOOKKEEPING_FEATS:
        return "bookkeeping"
    if base.startswith(CHEMISTRY_OVERRIDE_PREFIX):
        return "ambiguous" if is_na_flag else "chemistry"
    if base in AMBIGUOUS_FEATS or base.startswith(AMBIGUOUS_PREFIX):
        return "ambiguous"
    # one-hot 파생: 접두 컬럼명으로 되돌려 판정
    for c in CAT_FEATS:
        if col.startswith(c + "_"):
            return "chemistry"
    return "ambiguous" if is_na_flag else "chemistry"


# CT baseline 은 Track B 에서 '피처'로 쓰지만, 그 자체가 라벨은 아니다.
# 다만 f_ct_*_cat 은 문자열이므로 순서형으로 바꿔 넣는다.
for ep in ("eye", "skin", "sens"):
    FORM[f"f_ct_{ep}_ord"] = FORM[f"f_ct_{ep}_cat"].map(
        lambda v: SEV[ep].get(nrm_cat(v)) if pd.notna(v) else None)


def classify(col):
    if col in ID_COLS:
        return "id"
    if col in LABEL_SOURCE or col.startswith(LABEL_PREFIX):
        return "label_source"
    if col.startswith(PROV_PREFIX) or col.endswith(SRC_SUFFIX):
        return "provenance"
    return "feature"


CAT_FEATS = ["form_code_best", "pc2_physical_state", "pc2_solubility_qual",
             "formulation_type", "f_ct_eye_cat", "f_ct_skin_cat", "f_ct_sens_cat"]


def build_X(df, drop_extra=()):
    roles = {c: classify(c) for c in df.columns}
    feats = [c for c, r in roles.items() if r == "feature" and c not in drop_extra]
    num = [c for c in feats if pd.api.types.is_numeric_dtype(df[c])
           or pd.api.types.is_bool_dtype(df[c])]
    X = df[num].apply(pd.to_numeric, errors="coerce").astype("float64")
    # 결측 플래그: 결측 패턴 자체가 '출처 품질'을 담고 있어 신호다.
    hi_na = [c for c in num if 0.02 < df[c].isna().mean() < 0.98]
    blocks = [X, pd.DataFrame({f"{c}_isna": df[c].isna().astype("int8") for c in hi_na},
                              index=X.index)]
    # 범주형 one-hot (희소 레벨은 묶어 과적합 방지)
    for c in CAT_FEATS:
        if c not in df.columns:
            continue
        s = df[c].astype("string").fillna("__NA__")
        vc = s.value_counts()
        s = s.where(s.map(vc).fillna(0) >= 10, "__RARE__")
        blocks.append(pd.get_dummies(s, prefix=c, dtype="int8"))
    ids = {"Formulation_ID": df["Formulation_ID"].to_numpy()}
    if "ing_idx" in df.columns:
        ids["ing_idx"] = df["ing_idx"].to_numpy()
    X = pd.concat([pd.DataFrame(ids, index=X.index)] + blocks, axis=1)
    # 전상수/전결측 컬럼 제거
    drop = [c for c in X.columns if c not in ("Formulation_ID", "ing_idx")
            and (X[c].notna().sum() == 0 or X[c].nunique(dropna=True) <= 1)]
    X = X.drop(columns=drop)
    return X, roles, drop


XF, ROLE_F, DROP_F = build_X(FORM)
XI, ROLE_I, DROP_I = build_X(ING)
log(f"    X_formulation {XF.shape} (상수/전결측 제거 {len(DROP_F)})")

# ---- P0-2 누출 assert: label_source / y_*_src 및 그 원-핫 파생이 X 에 없어야 한다.
_ID_OK = {"Formulation_ID", "ing_idx"}
for _nm, _X, _df in (("X_formulation", XF, FORM), ("X_ingredient", XI, ING)):
    bad = []
    for c in _X.columns:
        if c in _ID_OK:
            continue
        base = re.sub(r"_isna$", "", c)
        # 원-핫은 '<원본컬럼>_<레벨>' 형태이므로 접두 일치도 함께 본다.
        cands = {c, base} | {p for p in (c, base)}
        if any(x == "label_source" or x.startswith("label_source") for x in cands):
            bad.append((c, "label_source"))
            continue
        if re.match(r"^y_(eye|skin|sens)(_|$)", base) or base.endswith("_src") \
                or "_src_" in base or base.endswith("_src_detail") \
                or re.search(r"_from_neg_recovery", base) \
                or re.match(r"^sds_grade_status_", base) \
                or re.match(r"^sds_v1_neg_recovered_", base) \
                or re.match(r"^sds_ghs_.*_eff", base) \
                or re.match(r"^label_(confidence|tier)_", base):
            bad.append((c, "y_*_src / 라벨출처 파생"))
            continue
        if classify(base) == "label_source" and base in _df.columns:
            bad.append((c, "classify=label_source"))
    assert not bad, f"{_nm} 에 라벨출처 누출 컬럼: {bad[:20]}"
log("    [assert] label_source / y_*_src 및 파생 원-핫이 X 에 없음 — OK")
log(f"    X_ingredient  {XI.shape} (상수/전결측 제거 {len(DROP_I)})")

YCOLS = ["Formulation_ID", "product_name", "group_key"]
for ep in ("eye", "skin", "sens"):
    YCOLS += [f"y_{ep}", f"y_{ep}_ord", f"y_{ep}_bin", f"y_{ep}_src",
              f"y_{ep}_src_detail", f"y_{ep}_from_neg_recovery",
              f"sds_grade_status_{ep}", f"sds_v1_neg_recovered_{ep}",
              f"y_{ep}_n_src", f"y_{ep}_conflict", f"ct_{ep}_ord", f"resid_{ep}",
              f"label_confidence_{ep}", f"label_tier_{ep}"]
YF = FORM[[c for c in YCOLS if c in FORM.columns]].copy()
for c in ("y_epa_eye", "y_epa_skin", "ntp_epa_acute_oral_cat",
          "ntp_epa_acute_dermal_cat"):
    if c in FORM.columns:
        YF[c] = pd.to_numeric(FORM[c], errors="coerce")   # EPA 40 CFR 156.62 cat I~IV
YF["has_any_y"] = YF[[f"y_{e}" for e in ("eye", "skin", "sens")]].notna().any(axis=1)
YF["n_y"] = YF[[f"y_{e}" for e in ("eye", "skin", "sens")]].notna().sum(axis=1)
YF["trainable_trackA"] = YF["has_any_y"] & FORM["f_smiles_coverage"].fillna(0).gt(0)
YF["trainable_trackB"] = (YF["has_any_y"]
                          & FORM[[f"f_ct_{e}_n_known" for e in ("eye", "skin", "sens")]]
                          .fillna(0).gt(0).any(axis=1))
log(f"    타깃 보유 {int(YF['has_any_y'].sum())} · Track A 학습가능 "
    f"{int(YF['trainable_trackA'].sum())} · Track B 학습가능 "
    f"{int(YF['trainable_trackB'].sum())}")

# ================================================================ 11. 저장

log("[11] 저장")
XF.to_parquet(f"{OUTDIR}/X_formulation.parquet", index=False)
XI.to_parquet(f"{OUTDIR}/X_ingredient.parquet", index=False)
YF.to_parquet(f"{OUTDIR}/y_formulation.parquet", index=False)
# v5 caveat C2: 진단 컬럼 동봉. group_key 정의는 v4 와 동일하다.
_GCOLS = ["Formulation_ID", "group_key", "group_primary_cas", "group_primary_name",
          "group_key_src", "group_primary_from_same_row", "group_primary_ing_idx",
          "group_primary_cas_ing_idx", "group_primary_name_ing_idx"]
FORM[_GCOLS].to_parquet(f"{OUTDIR}/groups.parquet", index=False)
FORM[_GCOLS].to_csv(f"{OUTDIR}/group_key_snapshot_v5.csv", index=False)

# v4 대비 group_key 변동 제형 목록. v4 산출물은 읽기전용으로만 접근한다.
_G4P = f"{MODEL_DIR}/v4/groups.parquet"
_gdiff_n = None
if os.path.exists(_G4P):
    _g4 = pd.read_parquet(_G4P)[["Formulation_ID", "group_key", "group_primary_cas",
                                 "group_primary_name"]]
    _g4.columns = ["Formulation_ID", "group_key_v4", "group_primary_cas_v4",
                   "group_primary_name_v4"]
    _gd = FORM[_GCOLS].merge(_g4, on="Formulation_ID", how="outer", indicator=True)
    _gd = _gd[(_gd["group_key"].astype(str) != _gd["group_key_v4"].astype(str))
              | (_gd["_merge"] != "both")]
    _gd.to_csv(f"{OUTDIR}/group_key_v4_v5_diff.csv", index=False)
    _gdiff_n = int(len(_gd))
    log(f"    group_key v4→v5 변동 제형 {_gdiff_n}건 → "
        f"{OUTDIR}/group_key_v4_v5_diff.csv")
else:
    log(f"    !! {_G4P} 없음 → group_key_v4_v5_diff.csv 생성 불가")

# 성분 지문 (행순서 = ING 행순서)
mkeys = [str(s).strip() if pd.notna(s) else "" for s in ING["smiles"]]
FM = np.vstack([FPM.get(k, np.zeros(fp_dim_m, np.uint8)) for k in mkeys])
FK = np.vstack([FPK.get(k, np.zeros(fp_dim_k, np.uint8)) for k in mkeys])
np.savez_compressed(f"{OUTDIR}/fp_ing_morgan.npz", fp=FM,
                    fid=ING["Formulation_ID"].to_numpy().astype(str),
                    ing_idx=ING["ing_idx"].to_numpy(),
                    has_structure=ING["desc_ok"].to_numpy())
np.savez_compressed(f"{OUTDIR}/fp_ing_maccs.npz", fp=FK,
                    fid=ING["Formulation_ID"].to_numpy().astype(str),
                    ing_idx=ING["ing_idx"].to_numpy(),
                    has_structure=ING["desc_ok"].to_numpy())
np.savez_compressed(f"{OUTDIR}/fp_form_pooled.npz",
                    morgan=np.vstack(pool_m).astype(np.float32),
                    maccs=np.vstack(pool_k).astype(np.float32),
                    fid=np.array(pool_ids, dtype=str),
                    layout="morgan=[max(2048)|wmean(2048)], maccs=[max(167)|wmean(167)]")

man = []
for src, roles, df in (("formulation", ROLE_F, FORM), ("ingredient", ROLE_I, ING)):
    for c, r in roles.items():
        man.append({"sheet": src, "column": c, "role": r,
                    "dtype": str(df[c].dtype),
                    "n_notna": int(df[c].notna().sum()),
                    "fill_pct": round(100 * df[c].notna().mean(), 2),
                    "in_X": bool(c in (XF.columns if src == "formulation" else XI.columns)),
                    # 라벨과 **같은 SDS 문서**에서 나온 피처 = 동일문서 상관 위험.
                    # 누출은 아니지만 ablation 대상이므로 별도 표시한다(§F0.5).
                    "sds_coderived": bool(c in T11_CODERIVED
                                          or c.replace("_isna", "") in T11_CODERIVED)})
MAN = pd.DataFrame(man)
# X 에만 존재하는 파생열(*_isna, one-hot)도 사전에 올린다 — ablation 시 필요하다.
_known = set(zip(MAN["sheet"], MAN["column"]))
for src, Xm in (("formulation", XF), ("ingredient", XI)):
    for c in Xm.columns:
        if (src, c) in _known:
            continue
        base = re.sub(r"_isna$", "", c)
        man.append({"sheet": src, "column": c, "role": "feature_derived",
                    "dtype": str(Xm[c].dtype), "n_notna": int(Xm[c].notna().sum()),
                    "fill_pct": round(100 * Xm[c].notna().mean(), 2), "in_X": True,
                    "sds_coderived": bool(base in T11_CODERIVED)})
MAN = pd.DataFrame(man)

# ---- P0-2: feature_role 태깅 (chemistry / bookkeeping / provenance / ambiguous)
MAN["feature_role"] = [
    feature_role(c, "feature" if r in ("feature", "feature_derived") else r)
    for c, r in zip(MAN["column"], MAN["role"])]

# ---- v5 caveat C3 (2026-08-30): sds_coderived 열의 역할 교정 -----------------
# 문제: t11_* 실측값 15열(t11_ld50_*/t11_acute_*_ord/t11_carc_ord 등)이
# feature_role='chemistry', exclude_by_default=False 로 태깅되어 있었다. 그런데
# 이 열들은 lib_tox11.parse_tox11() 이 **라벨(t11_eye_label/t11_skin_label/
# t11_sens_label = y 출처 sds_2nd_sec11)과 동일한 SDS Section 11 텍스트**를
# 동일 파서로 훑어 얻은 값이다. 즉 문서·파싱 경로가 라벨과 완전히 동일하다.
# 판단: T11_CODERIVED 54열(원본 27 + *_isna 27) 전부 label_coderived 로 강등하고
#       exclude_by_default=True 로 둔다. 근거 —
#   · LD50/LC50·발암/변이/생식/STOT/흡인 서열은 물리화학 측정값이 아니라
#     **독성 엔드포인트 측정값**이며, 같은 문서의 자극/감작성 진술과 상관된다.
#   · 문서 확보 여부 자체가 라벨 확보 여부와 동일하므로 결측 패턴이 라벨 존재를
#     인코딩한다(t11_*_isna 27열).
#   · 라벨과 독립인 물리화학 측정값(pc2_* 물성, ph_* 등)은 T11_CODERIVED 에
#     포함되지 않으므로 이 강등의 영향을 받지 않고 chemistry 로 남는다.
#   · 애매한 열은 배제 쪽으로(지시사항). 누출이라 단정하는 것이 아니라
#     "기본 배제 + ablation 으로 기여도 측정" 이 안전한 기본값이라는 판단이다.
# 정의만 바꾸며 X 행렬에서 물리적으로 제거하지는 않는다(기존 산출물 호환).
_prior_role = MAN["feature_role"].copy()
_cod_mask = MAN["sds_coderived"] & _prior_role.isin(
    ["chemistry", "ambiguous", "bookkeeping"])
MAN.loc[_cod_mask, "feature_role"] = "label_coderived"
log(f"    C3 sds_coderived 역할 교정: {int(_cod_mask.sum())}열 → label_coderived "
    f"(직전 역할 분포 {dict(_prior_role[_cod_mask].value_counts())})")
# X 에 실제로 들어간 컬럼만 모델링 관점에서 의미가 있다.
MAN["exclude_by_default"] = MAN["in_X"] & MAN["feature_role"].isin(
    ["bookkeeping", "ambiguous", "label_coderived"])
MAN.to_csv(f"{OUTDIR}/feature_manifest.csv", index=False)
_ROLE_MAN = MAN[MAN["in_X"]][["sheet", "column", "role", "feature_role",
                              "exclude_by_default", "dtype", "n_notna", "fill_pct",
                              "sds_coderived"]].copy()
_prior_in_X = _prior_role[MAN["in_X"]]
_base_col = _ROLE_MAN["column"].str.replace(r"_isna$", "", regex=True)
_reason = np.where(
    _base_col.isin(BOOKKEEPING_FEATS), "명시 부기목록(BOOKKEEPING_FEATS)",
    np.where(_base_col.isin(AMBIGUOUS_FEATS), "명시 애매목록(AMBIGUOUS_FEATS)",
             np.where(_ROLE_MAN["column"].str.endswith("_isna"),
                      "화학컬럼의 결측플래그 → 수집이력 가능성",
                      np.where(_ROLE_MAN["column"].str.startswith("f_n_"),
                               "성분 역할별 개수 → 조성/커버리지 혼재", "규칙 기본값"))))
# C3: 라벨과 동일문서/동일파서에서 나온 열은 사유를 명시적으로 덮어쓴다.
_reason = np.where(
    _ROLE_MAN["feature_role"].to_numpy() == "label_coderived",
    ("라벨공생성(C3): lib_tox11.parse_tox11() 이 y 출처 sds_2nd_sec11 라벨"
     "(t11_*_label)과 동일한 SDS Section 11 텍스트를 동일 파서로 추출한 열 → "
     "물리화학 독립측정값이 아니므로 기본 배제 + ablation 대상. 직전 역할=")
    + _prior_in_X.to_numpy().astype(str),
    _reason)
_ROLE_MAN["role_reason"] = _reason
# 하류 호환: 기존 컬럼명 role_basis 도 동일 내용으로 남긴다.
_ROLE_MAN["role_basis"] = _ROLE_MAN["role_reason"]
_ROLE_MAN.to_csv(f"{OUTDIR}/feature_role_manifest.csv", index=False)
log(f"    manifest {MAN.shape} · 역할분포 {dict(MAN['role'].value_counts())}")
log(f"    feature_role (X 내부) {dict(_ROLE_MAN['feature_role'].value_counts())}")
log("    사용법: 모델링 시 exclude_by_default==True 컬럼을 기본 배제한다 — "
    "X = X[[c for c in X.columns if c in chem_cols]]  (chem_cols = feature_role"
    "=='chemistry' 인 column 집합 + Formulation_ID/ing_idx)")

# ---- provenance (long)
prov = []
for r in FORM.itertuples(index=False):
    for field, val, src, url, verd in (
            ("formulation_code", r.form_code_best, r.form_code_src, None,
             getattr(r, "v_form_verdict", None)),
            ("pH", r.ph_best, r.ph_src, None, getattr(r, "v_phc_verdict", None)),
            ("y_eye", r.y_eye, r.y_eye_src, getattr(r, "tox2_src_url", None),
             getattr(r, "v_tox_verdict", None)),
            ("y_skin", r.y_skin, r.y_skin_src, getattr(r, "tox2_src_url", None),
             getattr(r, "v_tox_verdict", None)),
            ("y_sens", r.y_sens, r.y_sens_src, getattr(r, "tox2_src_url", None),
             getattr(r, "v_tox_verdict", None))):
        if val is None or (isinstance(val, float) and math.isnan(val)):
            continue
        prov.append({"Formulation_ID": r.Formulation_ID, "field": field,
                     "value": val, "source_tier": src, "source_url": url,
                     "verdict": verd})
PROV = pd.DataFrame(prov)

# ---- MetaData
def fillrow(k, v, note=""):
    return {"구분": k, "항목": v, "설명": note}


meta = [
    fillrow("버전", "input_dataset_v5", "2026-08-30 생성. v4와 동일 파이프라인 + 6/30 감사 워크북 병합부 안전화(위치조인 제거)"),
    fillrow("v5 병합규칙", str(_JUNE_STATS["form_basis"]),
            "위치조인은 파이프리스트 길이가 같을 때만 허용. 길이 불일치 제형은 "
            "비위치 참조사전(이름→CAS 유일해)으로만 복구하거나 스킵. "
            "근거: 04_모델산출물/v4_fixed/june_merge_defect_evidence.csv"),
    fillrow("v5 병합결과",
            f"{_JUNE_STATS['rows_merged']}행 (CAS {_JUNE_STATS['cas_filled']} · "
            f"SMILES {_JUNE_STATS['smiles_filled']})",
            f"basis={_JUNE_STATS['row_basis']} · 토큰게이트 탈락="
            f"{_JUNE_STATS['token_gate_dropped']}"),
    fillrow("행수", f"formulation {len(FORM)} / ingredient {len(ING)}", ""),
    # v5 caveat C4: 기존 표기는 키 컬럼을 뺀 수(-1/-2)라 실제 파케이 열수와 어긋났다
    # (독립검증 지적: 520 vs 521, 173 vs 175). 파케이 실측 열수로 정정한다.
    fillrow("피처", f"X_formulation {XF.shape[1]}열 / X_ingredient {XI.shape[1]}열",
            f"v4_fixed/X_*.parquet 실측 열수(키 컬럼 포함). "
            f"키 제외 순피처는 X_formulation {XF.shape[1]-1}열"
            f"(-Formulation_ID) / X_ingredient {XI.shape[1]-2}열"
            f"(-Formulation_ID,-ing_idx)"),
    fillrow("하네스", "독성코드 시트만 사용", "제형코드·성분·특성 시트는 하네스 미사용(ad-hoc 수집)"),
]
for ep in ("eye", "skin", "sens"):
    meta.append(fillrow(f"타깃 y_{ep}", f"{int(FORM[f'y_{ep}'].notna().sum())}행",
                        f"출처={dict(FORM[f'y_{ep}_src'].value_counts())} / "
                        f"충돌={int(FORM[f'y_{ep}_conflict'].fillna(False).sum())}"))
    meta.append(fillrow(f"CT baseline {ep}",
                        f"{int(FORM[f'f_ct_{ep}_n_known'].fillna(0).gt(0).sum())}행",
                        f"잔차타깃 정의 {int(FORM[f'resid_{ep}'].notna().sum())}행"))
for nm, d in vsum.items():
    meta.append(fillrow(f"검증({nm})", " · ".join(f"{k}={v}" for k, v in d.items()), ""))
meta += [
    fillrow("구조 커버리지", f"성분 {int(ING['desc_ok'].sum())}/{len(ING)} "
            f"({100*ING['desc_ok'].mean():.1f}%)", "SMILES 파싱 성공 기준"),
    fillrow("성분별 GHS", f"{int(ING['ing_ghs_src'].notna().sum())}행",
            "Phase1 PubChem/ECHA 회수분. 원본 ghs_*/cat_* 는 제형값 브로드캐스트라 성분피처로 사용 금지"),
    fillrow("pH", f"{int(FORM['ph_best'].notna().sum())}행",
            f"강산(≤2) {int(FORM['ph_strong_acid'].fillna(False).sum())} · "
            f"강염기(≥11.5) {int(FORM['ph_strong_base'].fillna(False).sum())}"),
    fillrow("그룹분할", f"group_key {FORM['group_key'].nunique()}개",
            "주성분 공유 제형이 train/test 로 갈리는 누출 방지 — GroupKFold 필수"),
    fillrow("누출차단", f"label_source {int((MAN['role']=='label_source').sum())}열 X에서 제외",
            "일부 라벨이 SDS H코드에서 유도되었으므로 H코드·신호어 계열은 피처 금지"),
    fillrow("Section11 재파싱(F0.5)",
            " · ".join(f"{ep} 라벨 {_t11_stat.get(ep, {}).get('label', 0)}"
                       for ep in ("eye", "skin", "sens")),
            "하네스가 수집했으나 시트에 기입되지 않았던 13파라미터 회수분. "
            "우선순위 최하위이므로 기존 라벨은 불변, 신규만 추가"),
    fillrow("동일문서 파생피처",
            f"sds_coderived {int(MAN['sds_coderived'].sum())}열",
            "라벨과 같은 SDS 문서에서 나온 LD50/LC50·기타 엔드포인트. 누출은 아니지만 "
            "ablation 필요 — MAN[MAN.sds_coderived] 로 한 줄에 제거 가능"),
    # ---- v5 caveat C3 ----
    fillrow("라벨공생성 열(C3)",
            f"label_coderived {int((MAN['feature_role']=='label_coderived').sum())}열 "
            f"(exclude_by_default=True)",
            "t11_* 실측값/서열/결측플래그. lib_tox11.parse_tox11() 이 y 출처 "
            "sds_2nd_sec11 라벨과 동일한 SDS Section 11 을 동일 파서로 훑어 만든 열 → "
            "기본 배제. 사유는 feature_role_manifest.csv 의 role_reason 참조"),
    # ---- v5 caveat C4: 레거시 컬럼 오독 방지 ----
    fillrow("⚠ 사용금지 레거시열(C4)",
            "has_y · n_y_conflict · trainable · trainable_ghs · trainable_ntp · "
            "trainable_epa · trainable_epa_any",
            f"v1 시점 값 그대로 이월된 레거시 컬럼이며 v5 라벨복구를 반영하지 않는다. "
            f"has_y={int(pd.to_numeric(FORM['has_y'], errors='coerce').fillna(0).sum())} "
            f"인데 실제 타깃보유는 y 파케이 has_any_y="
            f"{int(YF['has_any_y'].sum())} 이다(불일치). '학습가능 행수'로 읽으면 오독. "
            f"학습대상 판정은 반드시 y_formulation.parquet 의 has_any_y / n_y / "
            f"trainable_trackA / trainable_trackB 를 쓸 것. "
            f"리네임하지 않은 이유: audit_y_conflict.py · triage_y_conflict_313.py · "
            f"audit_label_source_bias.py · verify_label_recovery.py · "
            f"verify_v5_integrity.py 가 이 이름을 문자열 동등비교로 참조하고, "
            f"BOOKKEEPING_FEATS 역할태깅 키도 동일 이름이므로 리네임이 더 위험하다."),
    fillrow("⚠ v4 성능 직접비교 금지(C2)",
            f"group_key {FORM['group_key'].nunique()}개 · v4 대비 변동 제형 "
            f"{_gdiff_n if _gdiff_n is not None else '확인불가'}건",
            "group_key 정의는 v4 와 동일하나 6/30 병합 철회로 주성분 CAS 가 결측이 된 "
            "제형에서 그룹키가 하위 성분으로 이동했다 → GroupKFold 폴드 구성이 v4 와 "
            "다르다. v4 성능 수치와 직접 비교하면 안 되며, 비교가 필요하면 동일 "
            "group_key 스냅샷으로 v4·v5 를 재학습해야 한다. "
            "스냅샷: v4_fixed/group_key_snapshot_v5.csv · 변동목록: "
            "v4_fixed/group_key_v4_v5_diff.csv"),
    fillrow("⚠ 라벨 신뢰도(불변)",
            "formulation 시트 label_confidence_eye/skin/sens · "
            "v4_fixed/label_confidence_v5.csv",
            "y 값은 어느 것도 변경하지 않았다. Tier1(인용문 모순) 4건, Tier2(엔드포인트 "
            "자료없음) 16건, 양성→음성 전환 25건은 사람 재검 대상이며 신뢰도 컬럼으로만 "
            "표시한다."),
]
META = pd.DataFrame(meta)

log("    xlsx 쓰기")
with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as xw:
    META.to_excel(xw, sheet_name="MetaData", index=False)
    FORM.to_excel(xw, sheet_name="formulation", index=False)
    ING.to_excel(xw, sheet_name="ingredient", index=False)
    FAGG.to_excel(xw, sheet_name="desc_formulation", index=False)
    ING[["Formulation_ID", "ing_idx", "ing_name_best", "ing_cas_best", "smiles",
         "ing_role", "surf_class_best", "ing_pct_best", "ing_ghs_eye", "ing_ghs_skin",
         "ing_ghs_sens", "ing_ghs_src"] + [c for c in NUM_DESC if c in ING.columns]] \
        .to_excel(xw, sheet_name="desc_ingredient", index=False)
    YF.to_excel(xw, sheet_name="targets", index=False)
    PROV.to_excel(xw, sheet_name="provenance", index=False)
    MAN.to_excel(xw, sheet_name="feature_manifest", index=False)

log(f"\n→ {OUT_XLSX}")
log(f"→ {OUTDIR}/  (X_*.parquet, y_formulation.parquet, groups.parquet, fp_*.npz, "
    f"feature_manifest.csv)")

json.dump({"formulation": list(FORM.shape), "ingredient": list(ING.shape),
           "X_formulation": list(XF.shape), "X_ingredient": list(XI.shape),
           "y_counts": {ep: int(FORM[f"y_{ep}"].notna().sum()) for ep in ("eye", "skin", "sens")},
           "y_src": {ep: {k: int(v) for k, v in FORM[f"y_{ep}_src"].value_counts().items()}
                     for ep in ("eye", "skin", "sens")},
           "resid_defined": {ep: int(FORM[f"resid_{ep}"].notna().sum())
                             for ep in ("eye", "skin", "sens")},
           "ing_ghs_rows": int(ING["ing_ghs_src"].notna().sum()),
           "sec11_reparse": _t11_stat,
           "ph_rows": int(FORM["ph_best"].notna().sum()),
           "structure_coverage": float(ING["desc_ok"].mean()),
           "june_merge_v5": _JUNE_STATS,
           "pct_repair": _PCT_STATS,
           # ---- v5 caveat 반영 요약 (C1~C4) ----
           "caveat_fixes": {
               "C1_name_cas_retraction": _JUNE_STATS["c1_name_cas_retraction"],
               "C2_group_key": {
                   "nunique": int(FORM["group_key"].nunique()),
                   "src_counts": {k: int(v) for k, v
                                  in FORM["group_key_src"].value_counts().items()},
                   "primary_from_same_row": int(FORM["group_primary_from_same_row"].sum()),
                   "primary_from_different_row":
                       int((~FORM["group_primary_from_same_row"]).sum()),
                   "changed_vs_v4": _gdiff_n,
               },
               "C3_label_coderived_columns":
                   int((MAN["feature_role"] == "label_coderived").sum()),
               "C4_metadata_feature_counts": {
                   "X_formulation_parquet_cols": int(XF.shape[1]),
                   "X_ingredient_parquet_cols": int(XI.shape[1])},
               "label_confidence": _LC_STATS,
           },
           "WARNING_do_not_compare_with_v4_performance": (
               "group_key 정의는 v4 와 동일하지만 6/30 병합 철회로 주성분 CAS 가 "
               "결측이 된 제형에서 그룹키가 하위 성분으로 이동했다. 그 결과 "
               f"group_key 값이 v4 대비 {_gdiff_n}개 제형에서 변했고 GroupKFold "
               "폴드 구성이 달라진다. v5 성능수치를 v4 성능수치와 직접 비교하면 "
               "그룹분할 차이와 데이터 변경 효과가 뒤섞여 해석 불가다. 비교가 "
               "필요하면 동일 group_key 스냅샷(v4_fixed/group_key_snapshot_v5.csv)"
               "으로 양쪽을 재학습할 것."),
           "WARNING_legacy_v1_columns": (
               "formulation 시트의 has_y · n_y_conflict · trainable* 는 v1 시점 값을 "
               "그대로 이월한 레거시 컬럼이며 v5 라벨복구를 반영하지 않는다"
               f"(has_y={int(pd.to_numeric(FORM['has_y'], errors='coerce').fillna(0).sum())}"
               f" vs y 파케이 has_any_y={int(YF['has_any_y'].sum())}). "
               "리네임하지 않고 MetaData 경고로 처리했다 — 이유: 5개 하류 스크립트가 "
               "이 이름을 문자열 동등비교로 참조하고 BOOKKEEPING_FEATS 역할태깅 키도 "
               "동일 이름이라 리네임이 더 위험하다. 학습대상 판정은 "
               "y_formulation.parquet 의 has_any_y/n_y/trainable_track* 를 쓸 것."),
           "WARNING_labels_unchanged": (
               "이번 재생성에서 y 값은 단 한 건도 변경하지 않았다. Tier1 4건 · Tier2 "
               "16건 · 양성→음성 전환 25건은 사람 재검 대상이며 "
               "v4_fixed/label_confidence_v5.csv 와 formulation 시트 "
               "label_confidence_{eye,skin,sens} 로만 표시된다."),
           "verify": vsum, "p1_batches": files},
          open(f"{OUTDIR}/build_summary.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
log(f"→ {OUTDIR}/build_summary.json")

# ---- P-PCT 리포트 -----------------------------------------------------------
_PCT_REPORT = dict(_PCT_STATS)
_PCT_REPORT["formulation_level"] = {
    "n_formulations": int(len(FORM)),
    "n_with_pct_raw": int(FORM["pct_sum_known_raw"].notna().sum()),
    "n_with_pct_fixed": int(FORM["pct_sum_known"].notna().sum()),
    "overshoot_raw": {str(t): int((FORM["pct_sum_known_raw"] > t).sum())
                      for t in (100.0, 100.5, 102, 105, 110, 150)},
    "overshoot_fixed": {str(t): int((FORM["pct_sum_known"] > t).sum())
                        for t in (100.0, 100.5, 102, 105, 110, 150)},
    "pct_sum_known_raw_describe": {
        k: (None if pd.isna(v) else float(v))
        for k, v in FORM["pct_sum_known_raw"].describe().items()},
    "pct_sum_known_fixed_describe": {
        k: (None if pd.isna(v) else float(v))
        for k, v in FORM["pct_sum_known"].describe().items()},
    "f_max_pct_raw_range": [float(ING["ing_pct_raw"].min()),
                            float(ING["ing_pct_raw"].max())],
    "f_max_pct_fixed_range": [float(FORM["f_max_pct"].min()),
                              float(FORM["f_max_pct"].max())],
    "n_pct_with_structure_gt100_fixed": int((FORM["f_pct_with_structure"] > 100).sum()),
    # 가중치로 실제 쓰이는 합 (ing_pct_best 기준). 복구 전 상태는 백업 산출물 참조.
    "f_pct_sum_known_overshoot_fixed": {
        str(t): int((FORM["f_pct_sum_known"] > t).sum())
        for t in (100.0, 100.5, 102, 105, 110, 150)},
    "f_pct_sum_known_fixed_describe": {
        k: (None if pd.isna(v) else float(v))
        for k, v in FORM["f_pct_sum_known"].describe().items()},
    "labeled_overshoot_raw": {
        ep: int(((FORM["pct_sum_known_raw"] > 105) & FORM[f"y_{ep}_bin"].notna()).sum())
        for ep in ("eye", "skin", "sens")},
    "pct_status_counts": {k: int(v) for k, v
                          in FORM["pct_status"].value_counts().items()},
}
json.dump(_PCT_REPORT, open(f"{OUTDIR}/pct_repair_report.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
if len(_PCT_DETAIL):
    _PD = _PCT_DETAIL.copy()
    for _ep in ("eye", "skin", "sens"):
        _PD[f"y_{_ep}_bin"] = _PD["Formulation_ID"].map(
            FORM.set_index("Formulation_ID")[f"y_{_ep}_bin"])
    _PD.sort_values("pct_sum_known_raw", ascending=False).to_csv(
        f"{OUTDIR}/pct_repair_detail.csv", index=False)
log(f"→ {OUTDIR}/pct_repair_report.json · pct_repair_detail.csv ({len(_PCT_DETAIL)}행)")

# ================================================================ 12. P0 리포트

log("[12] P0 리포트 (음성복구 / feature_role / 충돌 재검목록)")
_rep = {"p0_1_negative_recovery": {}, "p0_2_feature_role": {}}
for ep in ("eye", "skin", "sens"):
    d = dict(_neg_recover_log[ep])
    m_v1 = FORM[f"y_{ep}_src"] == "sds_v1"
    b = pd.to_numeric(FORM.loc[m_v1, f"y_{ep}_bin"], errors="coerce")
    d["y_n_total"] = int(FORM[f"y_{ep}"].notna().sum())
    d["y_src_counts"] = {k: int(v) for k, v in FORM[f"y_{ep}_src"].value_counts().items()}
    d["y_src_detail_counts"] = {k: int(v) for k, v
                                in FORM[f"y_{ep}_src_detail"].value_counts().items()}
    d["sds_v1_n_rows"] = int(m_v1.sum())
    d["sds_v1_n_pos"] = int((b == 1).sum())
    d["sds_v1_pos_rate"] = round(float(b.mean()), 4) if len(b) else None
    d["n_labels_from_neg_recovery"] = int(FORM[f"y_{ep}_from_neg_recovery"].sum())
    _rep["p0_1_negative_recovery"][ep] = d

# status=='negative' 인데 **다른 출처**가 양성을 주장 → 실질 충돌. 자동수정 금지.
# (우선순위상 sds_v1 이 이기므로 최종 y 는 NC 가 되지만, 근거 문서가 서로
#  모순한다는 사실 자체는 남으므로 사람 재검 목록으로 분리한다.)
_cf = []
for ep in ("eye", "skin", "sens"):
    st = FORM[f"sds_grade_status_{ep}"].astype("string").str.strip().str.lower()
    neg = st.eq("negative").fillna(False).to_numpy()
    others = [(nm, m[ep]) for nm, m in PRIO
              if nm != "sds_v1" and m[ep] and m[ep] in FORM.columns]
    for i in np.flatnonzero(neg):
        pos = []
        for nm, col in others:
            v = nrm_cat(FORM[col].iloc[i])
            if v is not None and SEV[ep].get(v, 0) > 0:
                pos.append(f"{nm}={v}")
        if not pos:
            continue
        r = FORM.iloc[i]
        _cf.append({"endpoint": ep, "Formulation_ID": r["Formulation_ID"],
                    "product_name": r.get("product_name"),
                    "sds_grade_status": st.iloc[i],
                    "sds_ghs_raw": r.get(f"sds_ghs_{ep}"),
                    "positive_assertions_other_sources": " | ".join(pos),
                    "y_final": r[f"y_{ep}"], "y_bin": r[f"y_{ep}_bin"],
                    "y_src": r[f"y_{ep}_src"], "y_src_detail": r[f"y_{ep}_src_detail"],
                    "y_n_src": r[f"y_{ep}_n_src"], "y_conflict": r[f"y_{ep}_conflict"],
                    "resolved_by_priority": r[f"y_{ep}_src"] == "sds_v1",
                    "note": "SDS v1 은 Not classified 를 명시했으나 다른 출처가 양성 "
                            "→ 사람 재검 대상(자동 수정하지 않음)"})
CF = pd.DataFrame(_cf)
CF.to_csv(f"{OUTDIR}/label_conflict_manual_review.csv", index=False)
_rep["p0_3_manual_review_conflicts"] = (
    {ep: int((CF["endpoint"] == ep).sum()) for ep in ("eye", "skin", "sens")}
    if len(CF) else {ep: 0 for ep in ("eye", "skin", "sens")})
if len(CF):
    _rep["p0_3_manual_review_conflicts"]["final_y_still_positive"] = {
        ep: int(((CF["endpoint"] == ep) & (CF["y_bin"] == 1)).sum())
        for ep in ("eye", "skin", "sens")}

_xr = _ROLE_MAN[_ROLE_MAN["sheet"] == "formulation"]
_rep["p0_2_feature_role"] = {
    "counts_all_in_X": {k: int(v) for k, v in _ROLE_MAN["feature_role"].value_counts().items()},
    "counts_formulation_X": {k: int(v) for k, v in _xr["feature_role"].value_counts().items()},
    "ambiguous_columns_formulation": sorted(
        _xr.loc[_xr["feature_role"] == "ambiguous", "column"]),
    "bookkeeping_columns_formulation": sorted(
        _xr.loc[_xr["feature_role"] == "bookkeeping", "column"]),
    "n_exclude_by_default": int(_ROLE_MAN["exclude_by_default"].sum()),
    "usage": "feature_role=='chemistry' 만 X 에 남기는 것이 기본. "
             "bookkeeping/ambiguous 는 exclude_by_default==True.",
}
json.dump(_rep, open(f"{OUTDIR}/label_recovery_report.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
log(f"→ {OUTDIR}/label_recovery_report.json")
log(f"→ {OUTDIR}/label_conflict_manual_review.csv ({len(CF)}행)")
log(f"→ {OUTDIR}/feature_role_manifest.csv")
for ep in ("eye", "skin", "sens"):
    d = _rep["p0_1_negative_recovery"][ep]
    log(f"    {ep:5s} 복구 {d['n_recovered_to_NC']}행 · 최종 y {d['y_n_total']} · "
        f"sds_v1 양성률 {d['sds_v1_pos_rate']} ({d['sds_v1_n_pos']}/{d['sds_v1_n_rows']})")
