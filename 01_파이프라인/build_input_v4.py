#!/usr/bin/env python3
"""input_dataset_v2 빌더 — 2차 배정 수집분 + 검증결과 + 성분/제형 2층 디스크립터.

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
import re
import sys
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib_desc import DESC_NAMES, desc_for_smiles, guess_role   # noqa: E402
from lib_parse import (ct_predict, parse_form_extract, parse_ingredient_extract,   # noqa: E402
                       parse_physchem_extract, parse_tox_extract)
from lib_tox11 import (T11_CODERIVED, T11_LABEL_SOURCE, parse_tox11)   # noqa: E402

BASE = "/Users/hanseoyun/Desktop/260830"
INPUT_DIR = f"{BASE}/03_입력데이터"
DEPS_DIR = f"{BASE}/02_의존데이터"
MODEL_DIR = f"{BASE}/04_모델산출물"
SRC_V1 = f"{INPUT_DIR}/input_dataset.xlsx"
SRC_2ND = f"{INPUT_DIR}/dataset_배정_20260824.xlsx"
CIPAC_J = f"{DEPS_DIR}/formulation_harness/cipac_codes.json"
TOXRUN = f"{DEPS_DIR}/formulation_harness/_run_tox"
RESULTS = f"{DEPS_DIR}/results"
OUT_XLSX = f"{MODEL_DIR}/input_dataset_v4.xlsx"
OUTDIR = f"{MODEL_DIR}/v4"
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

# ---- 4a-2. 6/30 성분 정밀감사(zip) 고신뢰 복구 병합 ----
# 대상: reconcile_june_audit.py 가 RECOVER_CAS_HIGH_CONFIDENCE로 분류한 제형만
# (SDS 원문 대조까지 끝난 MATCH 건). 기존 값이 있으면 건드리지 않고, 결측일 때만 채운다.
log("[4x] 6/30 성분감사(zip) 고신뢰 복구 병합")
_recon = pd.read_csv(JUNE_RECON)
_target_fids = set(_recon.loc[_recon["recommendation"] == "RECOVER_CAS_HIGH_CONFIDENCE", "Formulation_ID"])
_june = pd.read_excel(JUNE_MASTER, sheet_name="Formulations")


def _split_pipe(s):
    if pd.isna(s):
        return []
    return [x.strip() for x in str(s).split("|")]


_june_lookup = {}
for _r in _june.itertuples(index=False):
    if _r.Formulation_ID not in _target_fids:
        continue
    names = _split_pipe(_r.Formulation_Ingredients)
    cass = _split_pipe(_r.Formulation_Ingredients_CAS)
    smis = _split_pipe(_r.Formulation_Ingredients_SMILES)
    for i in range(max(len(names), len(cass), len(smis))):
        nm = nrm_name(names[i]) if i < len(names) else None
        if not nm:
            continue
        cas_v = cass[i] if i < len(cass) and cass[i] else None
        smi_v = smis[i] if i < len(smis) and smis[i] else None
        _june_lookup[(_r.Formulation_ID, nm)] = (cas_v, smi_v)

_n_smiles_filled = _n_cas_filled = 0
_new_smiles, _new_cas, _src_flag = [], [], []
for r in ING.itertuples(index=False):
    fid, nm = r.Formulation_ID, nrm_name(r.ing_name_best)
    smi, cas_v = r.smiles, r.ing_cas_best
    src = False
    if fid in _target_fids:
        hit = _june_lookup.get((fid, nm))
        if hit:
            j_cas, j_smi = hit
            if pd.isna(smi) and j_smi:
                smi = j_smi
                _n_smiles_filled += 1
                src = True
            if pd.isna(cas_v) and j_cas:
                cas_v = j_cas
                _n_cas_filled += 1
                src = True
    _new_smiles.append(smi)
    _new_cas.append(cas_v)
    _src_flag.append(src)
ING["smiles"] = _new_smiles
ING["ing_cas_best"] = _new_cas
ING["june_audit_recovered"] = _src_flag
log(f"    6/30 감사 병합: SMILES {_n_smiles_filled}건, CAS {_n_cas_filled}건 신규 채움 "
    f"(대상 제형 {len(_target_fids)}개, 매칭성분 {sum(_src_flag)}행)")

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
# 우선순위: NTP 실측(in vivo/ICE) > 원본 SDS > 2차 SDS > Phase1 제품 SDS
PRIO = [("ntp_measured", {"eye": "ntp_eye_ghs_cat", "skin": "ntp_skin_ghs_cat", "sens": None}),
        ("sds_v1", {"eye": "sds_ghs_eye", "skin": "sds_ghs_skin", "sens": "sds_ghs_sens"}),
        ("sds_2nd", {"eye": "tox2_eye_ghs", "skin": "tox2_skin_ghs", "sens": "tox2_sens_ghs"}),
        ("phase1_sds", {"eye": "p1_eye", "skin": "p1_skin", "sens": "p1_sens"}),
        # F0.5 — 같은 2차 수집 패스의 SDS Section 11 서술문. 기존 라벨을 덮지 않도록
        # **가장 낮은 우선순위**에 둔다. 즉 신규 라벨만 추가되고 기존 값은 불변이다.
        ("sds_2nd_sec11", {"eye": "t11_eye_label", "skin": "t11_skin_label",
                           "sens": "t11_sens_label"})]

for ep in ("eye", "skin", "sens"):
    vals, srcs, nsrc, conf = [], [], [], []
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
            continue
        vals.append(cands[0][1])
        srcs.append(cands[0][0])
        nsrc.append(len(cands))
        uniq = {SEV[ep].get(v, -1) for _, v in cands}
        conf.append(len(uniq) > 1)
    FORM[f"y_{ep}"] = vals
    FORM[f"y_{ep}_src"] = srcs
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
prim = (ING.sort_values(["Formulation_ID", "ing_pct_best"], ascending=[True, False])
           .groupby("Formulation_ID")
           .agg(group_primary_cas=("ing_cas_best", "first"),
                group_primary_name=("ing_name_best", "first")))
FORM = FORM.merge(prim, on="Formulation_ID", how="left")
FORM["group_key"] = (FORM["group_primary_cas"].fillna("")
                     .where(lambda s: s != "", FORM["group_primary_name"].fillna(""))
                     .replace("", np.nan).fillna(FORM["Formulation_ID"]))
log(f"    그룹키 {FORM['group_key'].nunique()}개 / 제형 {len(FORM)}  "
    f"(최대 그룹 {FORM['group_key'].value_counts().max()}행)")

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
    "sds_epa_eye", "sds_epa_skin", "sds_epa_eye_roman", "sds_epa_skin_roman",
    "ntp_eye_ghs_cat", "ntp_skin_ghs_cat", "ntp_epa_eye_cat", "ntp_epa_skin_cat",
    "y_epa_eye", "y_epa_skin", "y_epa_eye_roman", "y_epa_skin_roman",
    "p1_eye", "p1_skin", "p1_sens",
    "tox2_eye_ghs", "tox2_skin_ghs", "tox2_sens_ghs", "tox2_sens_resp_ghs",
    "ing_ghs_eye", "ing_ghs_skin", "ing_ghs_sens",
} | T11_LABEL_SOURCE
LABEL_PREFIX = ("y_", "resid_", "tox_eye_", "tox_skin_", "tox_ld50", "ghs_cat_",
                "ghs_eye_", "ghs_skin_", "ntp_eye_", "ntp_skin_", "ntp_ld50",
                "ntp_lc50", "ntp_epa_", "ntp_conflict", "tox2_", "ghs_n_",
                "y_conflict", "y_needs_review", "y_pref")
ID_COLS = {"Formulation_ID", "product_name", "ing_idx", "group_key",
           "group_primary_cas", "group_primary_name"}
PROV_PREFIX = ("v_", "p1_source", "p1_resolution", "p1_tier", "p1_confidence",
               "p1_batch", "p1_ingredients_match", "form2_verdict", "pc2_verdict",
               "tox2_verdict", "ing2_verdict", "ing2_evidence", "ing2_memo",
               "ing2_newlink", "pc2_unparsed", "pc2_suspect", "ing_ghs_src",
               "t11_resolution", "t11_confidence")
SRC_SUFFIX = ("_src", "_source", "_url", "_rel", "_quote", "_by", "_evidence")

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
log(f"    X_ingredient  {XI.shape} (상수/전결측 제거 {len(DROP_I)})")

YCOLS = ["Formulation_ID", "product_name", "group_key"]
for ep in ("eye", "skin", "sens"):
    YCOLS += [f"y_{ep}", f"y_{ep}_ord", f"y_{ep}_bin", f"y_{ep}_src",
              f"y_{ep}_n_src", f"y_{ep}_conflict", f"ct_{ep}_ord", f"resid_{ep}"]
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
FORM[["Formulation_ID", "group_key", "group_primary_cas", "group_primary_name"]] \
    .to_parquet(f"{OUTDIR}/groups.parquet", index=False)

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
MAN.to_csv(f"{OUTDIR}/feature_manifest.csv", index=False)
log(f"    manifest {MAN.shape} · 역할분포 {dict(MAN['role'].value_counts())}")

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
    fillrow("버전", "input_dataset_v3", "2026-08-29 생성. input_dataset.xlsx + dataset_배정_20260824.xlsx(성분 2차검증·독성코드 재조사 2라운드 반영) + Phase1 회수분"),
    fillrow("행수", f"formulation {len(FORM)} / ingredient {len(ING)}", ""),
    fillrow("피처", f"X_formulation {XF.shape[1]-1}열 / X_ingredient {XI.shape[1]-2}열", "out/v2/*.parquet"),
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
           "verify": vsum, "p1_batches": files},
          open(f"{OUTDIR}/build_summary.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
log(f"→ {OUTDIR}/build_summary.json")
