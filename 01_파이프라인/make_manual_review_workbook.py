# -*- coding: utf-8 -*-
"""
수동 재검 작업시트 생성 (읽기 전용 입력 → 신규 xlsx 1개 출력)

목적
----
출처 간 라벨이 실제로 상충하는 행(예: 원본 SDS 는 "Not classified" 명시,
NTP 실측은 2B 양성)은 **오류가 아니라 증거 상충**이다. 자동 삭제·자동 수정
대상이 아니고 원문(SDS 원본 PDF / NTP 레코드)을 직접 확인해 사람이 판정해야
한다. 이 스크립트는 그 판정을 기록할 수 있는 작업시트를 만든다.

설계 원칙
--------
1) 라벨 값을 **바꾸지 않는다**. 판정 컬럼은 전부 공란으로 두고 사람이 채운다.
2) 판정에 필요한 근거를 한 행에 모아 둔다 — 출처별 원 주장값, pH, CT 가산
   예측, 성분 구성, 원문 링크. 시트를 떠나지 않고 판정할 수 있어야 한다.
3) 이진 분류가 목표이므로 **이진 라벨이 갈리는 충돌**과 **서열만 갈리는 충돌**
   (2A vs 2B 등, bin 은 동일)을 분리해 우선순위를 매긴다. 후자는 현재 목표에
   영향이 없다.
4) 입력 파일은 어느 것도 수정하지 않는다.

출력
----
04_모델산출물/v4_fixed/수동재검_작업시트.xlsx
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "04_모델산출물" / "input_dataset_v5.xlsx"
OUT = ROOT / "04_모델산출물" / "v4_fixed" / "수동재검_작업시트.xlsx"
REPORT = ROOT / "04_모델산출물" / "v4_fixed" / "manual_review_workbook_summary.json"

EPS = ("eye", "skin", "sens")
EP_KO = {"eye": "눈자극", "skin": "피부자극", "sens": "피부감작성"}

# build_input_v5.py 의 PRIO 와 동일한 정의. 여기서 재선언하는 이유는 이 시트가
# "어느 출처가 무엇을 주장했는가"를 그대로 보여줘야 하기 때문이다. 우선순위가
# 바뀌면 이 표도 함께 고쳐야 한다.
PRIO = [
    ("ntp_measured", {"eye": "ntp_eye_ghs_cat", "skin": "ntp_skin_ghs_cat", "sens": None}),
    ("sds_v1", {"eye": "sds_ghs_eye_eff", "skin": "sds_ghs_skin_eff", "sens": "sds_ghs_sens_eff"}),
    ("sds_2nd", {"eye": "tox2_eye_ghs", "skin": "tox2_skin_ghs", "sens": "tox2_sens_ghs"}),
    ("phase1_sds", {"eye": "p1_eye", "skin": "p1_skin", "sens": "p1_sens"}),
    ("sds_2nd_sec11", {"eye": "t11_eye_label", "skin": "t11_skin_label", "sens": "t11_sens_label"}),
]
# --- build_input_v5.py:81-102 에서 그대로 가져온 정의 ---------------------------
# 이 두 값은 절대 재해석하지 말 것. 처음에 근사해서 재선언했다가 skin 의
# "2A"/"2B" 키가 빠져 NTP 실측 양성 3건(MIX342/MIX378/MIX54)이 "이진 라벨 동일"
# 로 오분류되었다. 정의가 갈리면 시트의 우선순위가 곧바로 틀린다.
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
    m = re.match(r"\s*(?:cat(?:egory)?\.?\s*)?(1A|1B|1C|2A|2B|1|2|3|4)\b", s, re.I)
    return m.group(1).upper() if m else s.upper()[:24]


def log(m):
    print(m, flush=True)


# ---------------------------------------------------------------- 입력 적재
log(f"[1/5] 입력 적재 (읽기 전용): {SRC.name}")
xl = pd.ExcelFile(SRC)
FORM = xl.parse("formulation")
TGT = xl.parse("targets")
ING = xl.parse("desc_ingredient")

assert FORM["Formulation_ID"].is_unique, "formulation Formulation_ID 중복"
F = FORM.set_index("Formulation_ID")
T = TGT.set_index("Formulation_ID")
log(f"      formulation {FORM.shape} / targets {TGT.shape} / desc_ingredient {ING.shape}")


def get(fid, col):
    if col not in F.columns:
        return None
    v = F.at[fid, col]
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def tget(fid, col):
    if col not in T.columns:
        return None
    v = T.at[fid, col]
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


# 성분 요약 (판정 근거용). 함량 내림차순 상위 6개.
log("[2/5] 성분 요약 구성")
_ing_txt: dict[str, str] = {}
_ing_flag: dict[str, str] = {}
for fid, g in ING.groupby("Formulation_ID"):
    g = g.sort_values("ing_pct_best", ascending=False, na_position="last")
    parts = []
    for _, r in g.head(6).iterrows():
        nm = str(r.get("ing_name_best") or "?")[:40]
        pct = r.get("ing_pct_best")
        cas = r.get("ing_cas_best")
        seg = nm
        if isinstance(cas, str) and cas.strip():
            seg += f" [{cas.strip()}]"
        if pd.notna(pct):
            seg += f" {float(pct):.4g}%"
        parts.append(seg)
    if len(g) > 6:
        parts.append(f"... (총 {len(g)}성분)")
    _ing_txt[fid] = " | ".join(parts)
    fl = []
    if pd.to_numeric(g.get("is_ionic"), errors="coerce").fillna(0).sum() > 0:
        fl.append("이온성")
    if pd.to_numeric(g.get("has_metal"), errors="coerce").fillna(0).sum() > 0:
        fl.append("금속함유")
    sc = g.get("surf_class_best")
    if sc is not None and sc.notna().any():
        fl.append("계면활성제:" + ",".join(sorted(set(sc.dropna().astype(str)))[:3]))
    _ing_flag[fid] = "/".join(fl)


# --------------------------------------------------- 충돌행 수집 (전 엔드포인트)
log("[3/5] 출처 간 충돌 수집")
rows = []
for ep in EPS:
    sev = SEV[ep]
    conf_mask = T[f"y_{ep}_conflict"].fillna(False).astype(bool)
    for fid in T.index[conf_mask]:
        claims = []
        for name, m in PRIO:
            col = m[ep]
            if not col:
                continue
            v = nrm_cat(get(fid, col))
            if v is not None:
                claims.append((name, v, sev.get(v, -1)))
        if len(claims) < 2:
            continue  # y_*_conflict 정의상 발생하지 않아야 함
        bins = {int(s > 0) for _, _, s in claims}
        bin_disagree = len(bins) > 1
        ords = {s for _, _, s in claims}

        y_fin = tget(fid, f"y_{ep}")
        y_bin = tget(fid, f"y_{ep}_bin")
        winner = tget(fid, f"y_{ep}_src")
        sdsv1_neg = nrm_cat(get(fid, PRIO[1][1][ep])) == "NC"
        recovered = bool(tget(fid, f"sds_v1_neg_recovered_{ep}") or False)

        # 우선순위 배정 — 이진 분류 목표 기준
        if bin_disagree and sdsv1_neg and int(y_bin or 0) == 1:
            tier, tier_note = "T1", "SDS 미분류 vs 타출처 양성 → 최종 라벨 양성 채택"
        elif bin_disagree and sdsv1_neg:
            tier, tier_note = "T2", "SDS 미분류 vs 타출처 양성 → 최종 라벨 음성 채택"
        elif bin_disagree:
            tier, tier_note = "T3", "이진 라벨이 출처 간 상충 (SDS 미분류 아님)"
        else:
            tier, tier_note = "T4", "서열만 상충 (이진 라벨은 동일) → 이진분류 목표에 영향 없음"

        ph = tget(fid, "ph_best") if "ph_best" in T.columns else get(fid, "ph_best")
        ph = pd.to_numeric(ph, errors="coerce")
        ph_gate = ""
        if pd.notna(ph):
            if ph <= 2:
                ph_gate = "강산 pH<=2 (GHS 비가산성 게이트)"
            elif ph >= 11.5:
                ph_gate = "강염기 pH>=11.5 (GHS 비가산성 게이트)"

        ct_cat = nrm_cat(get(fid, f"f_ct_{ep}_cat"))
        ct_ord = tget(fid, f"ct_{ep}_ord")
        ct_contra = ""
        if ct_cat is not None and y_bin is not None:
            ct_bin = int(sev.get(ct_cat, -1) > 0)
            if ct_bin != int(y_bin):
                ct_contra = f"CT가산={ct_cat}(bin{ct_bin}) vs 라벨 bin{int(y_bin)}"

        rec = {
            "우선순위": tier,
            "우선순위_설명": tier_note,
            "엔드포인트": EP_KO[ep],
            "ep": ep,
            "Formulation_ID": fid,
            "제품명": get(fid, "product_name"),
            "제형코드": get(fid, "formulation_code"),
            "이진라벨_상충": bin_disagree,
            "출처수": len(claims),
            "최종라벨": y_fin,
            "최종라벨_bin": y_bin,
            "채택출처": winner,
            "음성복구행": recovered,
        }
        for name, m in PRIO:
            rec[f"주장_{name}"] = nrm_cat(get(fid, m[ep])) if m[ep] else None
        rec["주장요약"] = " / ".join(f"{n}={v}" for n, v, _ in claims)
        rec["CT가산예측"] = ct_cat
        rec["CT모순"] = ct_contra
        rec["pH"] = float(ph) if pd.notna(ph) else None
        rec["pH_출처"] = get(fid, "ph_src")
        rec["pH_게이트경고"] = ph_gate
        rec["성분_상위6"] = _ing_txt.get(fid, "")
        rec["성분_특이사항"] = _ing_flag.get(fid, "")
        rec["원문_SDS문서"] = get(fid, "doc_rel")
        rec["원문_URL"] = get(fid, "tox_source_url") or get(fid, "tox2_src_url") or get(fid, "p1_source_url")
        # 사람이 채우는 판정 컬럼 (전부 공란)
        rec["★판정"] = None
        rec["★확정라벨"] = None
        rec["★근거출처"] = None
        rec["★확인한원문쪽수"] = None
        rec["★검토자"] = None
        rec["★검토일"] = None
        rec["★메모"] = None
        rows.append(rec)

REV = pd.DataFrame(rows)
tier_order = {"T1": 0, "T2": 1, "T3": 2, "T4": 3}
ep_order = {"eye": 0, "skin": 1, "sens": 2}
REV = REV.sort_values(
    by=["우선순위", "ep", "Formulation_ID"],
    key=lambda s: s.map(tier_order) if s.name == "우선순위" else (s.map(ep_order) if s.name == "ep" else s),
).reset_index(drop=True)
REV.insert(0, "no", np.arange(1, len(REV) + 1))
log(f"      충돌 총 {len(REV)}행 / 티어분포 {dict(REV['우선순위'].value_counts())}")

# 자기검증 — SEV/nrm_cat 을 재해석해 우선순위가 틀리는 사고를 막는다.
#  (1) 재계산한 최종 카테고리가 저장된 y_{ep} 와 일치해야 한다(우선순위 재현).
#  (2) 기존 label_conflict_manual_review.csv(48행)의 모든 행이 이 시트에 있어야 하고,
#      그 중 최종 y 가 양성인 행은 반드시 T1 이어야 한다.
_mis = []
for _, r in REV.iterrows():
    claims = [(n, r[f"주장_{n}"]) for n, _ in PRIO if pd.notna(r.get(f"주장_{n}"))]
    if claims and claims[0][1] != r["최종라벨"]:
        _mis.append((r["Formulation_ID"], r["ep"], claims[0], r["최종라벨"]))
assert not _mis, f"우선순위 재현 실패 {len(_mis)}행: {_mis[:5]}"

_old = ROOT / "04_모델산출물" / "v4_fixed" / "label_conflict_manual_review.csv"
_xchk = {"checked": False}
if _old.exists():
    OLD = pd.read_csv(_old)
    key = set(zip(REV["Formulation_ID"], REV["ep"]))
    missing = [(f, e) for f, e in zip(OLD["Formulation_ID"], OLD["endpoint"]) if (f, e) not in key]
    assert not missing, f"기존 48행 중 시트에 누락 {len(missing)}행: {missing[:5]}"
    pos = OLD[OLD["y_bin"] == 1.0]
    t1 = set(zip(REV.loc[REV["우선순위"] == "T1", "Formulation_ID"],
                 REV.loc[REV["우선순위"] == "T1", "ep"]))
    notT1 = [(f, e) for f, e in zip(pos["Formulation_ID"], pos["endpoint"]) if (f, e) not in t1]
    assert not notT1, f"최종 y 양성 충돌인데 T1 이 아닌 행 {len(notT1)}: {notT1[:5]}"
    _xchk = {"checked": True, "old_rows": int(len(OLD)), "old_positive": int(len(pos))}
    log(f"      자기검증 통과 — 우선순위 재현 OK, 기존 {len(OLD)}행 전부 포함, "
        f"최종 y 양성 {len(pos)}행 전부 T1")

# ------------------------------------------- 부수 시트: pH 게이트 위반 음성복구
log("[4/5] 부수 점검 시트 구성")
gate_rows = []
for ep in EPS:
    col = f"sds_v1_neg_recovered_{ep}"
    if col not in T.columns:
        continue
    for fid in T.index[T[col].fillna(False).astype(bool)]:
        ph = pd.to_numeric(tget(fid, "ph_best") if "ph_best" in T.columns else get(fid, "ph_best"),
                           errors="coerce")
        if pd.isna(ph) or (2 < ph < 11.5):
            continue
        gate_rows.append({
            "엔드포인트": EP_KO[ep], "Formulation_ID": fid,
            "제품명": get(fid, "product_name"), "pH": float(ph),
            "pH_출처": get(fid, "ph_src"),
            "게이트": "강산 pH<=2" if ph <= 2 else "강염기 pH>=11.5",
            "복구된라벨": tget(fid, f"y_{ep}"),
            "최종채택출처": tget(fid, f"y_{ep}_src"),
            "성분_상위6": _ing_txt.get(fid, ""),
            "원문_SDS문서": get(fid, "doc_rel"),
            "★판정": None, "★확정라벨": None, "★검토자": None, "★메모": None,
        })
GATE = pd.DataFrame(gate_rows)
log(f"      pH 게이트 충돌 음성복구 {len(GATE)}행")

# ------------------------------------------------- 부수 시트: 비화학 토큰 + 구조
# 주의 — 이 패턴은 build_input_v5.py 의 6/30 병합 게이트(37개 정규식)와 **다르다**.
# 여기서는 v5 최종 성분표에 남아 있는 명백한 비화학 토큰만 보수적으로 잡는다.
# 따라서 건수는 6/30 병합 감사 때의 수치와 직접 비교할 수 없다.
NONCHEM_PAT = (
    r"^(?:content|composition|ingredient|name|cas|no\.?|remarks?|note|other|others|"
    r"balance|total|etc\.?|기타|합계|성분명?|비고|함량|나머지|미상|해당없음|n/?a|"
    r"trade\s*secret|proprietary|confidential|w/?w|v/?v|wt%?|percent|%)\b"
)
tok = ING.copy()
nm = tok["ing_name_best"].astype(str).str.strip().str.lower()
is_nonchem = nm.str.contains(NONCHEM_PAT, regex=True, na=False) | nm.str.match(r"^[\W\d\s]+$", na=False)
has_struct = tok["smiles"].notna() | tok["ing_cas_best"].notna()
TOK = tok.loc[is_nonchem & has_struct,
              ["Formulation_ID", "ing_idx", "ing_name_best", "ing_cas_best", "smiles",
               "ing_pct_best"]].copy()
if not TOK.empty:
    src_map = ING.set_index(["Formulation_ID", "ing_idx"])
    TOK["제품명"] = TOK["Formulation_ID"].map(F["product_name"])
    TOK["★판정"] = None
    TOK["★메모"] = None
log(f"      비화학 토큰인데 구조/CAS 보유 {len(TOK)}행 "
    f"(SMILES {int(TOK['smiles'].notna().sum()) if not TOK.empty else 0} / "
    f"CAS {int(TOK['ing_cas_best'].notna().sum()) if not TOK.empty else 0})")

# ------------------------------------ 부수 시트: 음성 복구의 근거 품질 (D 시트)
# label_confidence_v5.csv 의 tier 중 사람 판정이 필요한 3종만 뽑는다.
# 등급 이름을 A 시트의 T1~T4 와 겹치지 않게 E1~E3 로 둔 이유 — A 의 T1 은
# "출처 상충의 우선순위"이고 여기 T1 은 "인용문 근거 품질"이라 의미가 전혀
# 다르다. 같은 문서에서 같은 이름을 쓰면 반드시 혼동이 생긴다.
_CONF = ROOT / "04_모델산출물" / "v4_fixed" / "label_confidence_v5.csv"
EVID = pd.DataFrame()
if _CONF.exists():
    CF = pd.read_csv(_CONF)
    # tier 문자열은 label_confidence_v5.csv 의 실제 값과 정확히 같아야 한다.
    # 처음에 "T1"/"T2" 로 적었다가 E1 4행·E2 16행이 조용히 0행으로 빠졌다.
    _grade = {
        "T1_evidence_contradicted": (
            "E1_반대신호", "인용문에 음성 진술이 없고 오히려 자극/알레르기 신호만 있다. 음성 복구 근거 없음 — 최우선"),
        "T2_endpoint_no_data": (
            "E2_근거없음", "문서 전반은 Not classified 이나 해당 엔드포인트가 No data available / Not determined. 시험근거 없는 음성"),
        "FLIP_pos_to_neg": (
            "E3_부호전환", "복구된 NC 가 하위 출처의 양성 주장을 이겨 v4 대비 양성→음성으로 뒤집힌 행"),
    }
    _known = set(CF["tier"].dropna().unique())
    _miss = [k for k in _grade if k not in _known]
    assert not _miss, f"label_confidence_v5.csv 에 없는 tier 키: {_miss} (실제 값: {sorted(_known)})"
    sub = CF[CF["tier"].isin(_grade)].copy()
    sub["등급"] = sub["tier"].map(lambda t: _grade[t][0])
    sub["등급_설명"] = sub["tier"].map(lambda t: _grade[t][1])
    sub["엔드포인트"] = sub["endpoint"].map(EP_KO)
    sub["제품명"] = sub["Formulation_ID"].map(F["product_name"])
    sub["pH"] = sub["Formulation_ID"].map(
        pd.to_numeric(F["ph_best"], errors="coerce") if "ph_best" in F.columns else pd.Series(dtype=float))
    sub["성분_상위6"] = sub["Formulation_ID"].map(_ing_txt)
    sub["원문_SDS문서"] = sub["Formulation_ID"].map(F["doc_rel"])
    # v4 시점의 라벨(무엇이 뒤집혔는지) — E3 판정에 필요하다.
    sub["v4대비_전환"] = np.where(sub["tier"] == "FLIP_pos_to_neg", "양성 → 음성", "")
    EVID = sub[["등급", "등급_설명", "엔드포인트", "Formulation_ID", "제품명",
                "y", "y_bin", "y_src_detail", "v4대비_전환",
                "근거인용문", "사유", "ct_cat", "pH", "성분_상위6", "원문_SDS문서",
                "quote_has_explicit_negative", "quote_has_suspicious_marker",
                "quote_has_no_data_marker"]].rename(
        columns={"y": "현재라벨", "y_bin": "현재라벨_bin", "ct_cat": "CT가산예측",
                 "quote_has_explicit_negative": "인용문_명시적음성",
                 "quote_has_suspicious_marker": "인용문_의심표현",
                 "quote_has_no_data_marker": "인용문_데이터없음"})
    EVID = EVID.sort_values(["등급", "엔드포인트", "Formulation_ID"]).reset_index(drop=True)
    for c in ("★판정", "★확정라벨", "★근거출처", "★확인한원문쪽수", "★검토자", "★검토일", "★메모"):
        EVID[c] = None
    EVID.insert(0, "no", np.arange(1, len(EVID) + 1))
    log(f"      음성복구 근거품질 {len(EVID)}행 / 등급분포 {dict(EVID['등급'].value_counts())}")
else:
    log("      label_confidence_v5.csv 없음 — D 시트 생략")

# -------------------------------------------------------------------- 안내 시트
GUIDE = pd.DataFrame([
    ("목적", "출처 간 라벨이 상충하는 행을 원문 확인 후 사람이 판정한다. 상충은 오류가 아니라 증거 상충이므로 자동 삭제/자동 수정하지 않았다."),
    ("작업 방법", "★ 표시 컬럼만 채운다. 나머지 컬럼은 판정 근거이므로 수정하지 않는다."),
    ("★판정 코드", "유지 = 현재 최종라벨이 맞다 / 수정 = ★확정라벨에 올바른 값을 적는다 / 제외 = 어느 쪽도 신뢰할 수 없어 학습에서 뺀다 / 보류 = 추가 자료 필요"),
    ("★확정라벨", "판정=수정 일 때만 기입. 눈: NC/2B/2A/1, 피부: NC/3/2/1A/1B/1C, 감작성: NC/1B/1A"),
    ("★근거출처", "판정의 근거가 된 문서. 예: 'SDS 원본 p.3 Section 11' / 'NTP ICE 레코드' / 'ECHA C&L'"),
    ("우선순위 T1", "SDS 원본은 Not classified 명시, 타출처는 양성 → 최종 라벨이 양성으로 채택된 행. 잘못된 양성이면 학습에 직접 해를 준다. 최우선."),
    ("우선순위 T2", "같은 상충인데 최종 라벨이 음성으로 채택된 행. 실제로 양성이면 위양성 누락(라벨 노이즈)."),
    ("우선순위 T3", "이진 라벨이 출처 간 갈리지만 SDS 미분류 케이스는 아닌 행."),
    ("우선순위 T4", "서열만 갈리는 행(예: 2A vs 2B). 현재 목표는 이진 분류이므로 학습에 영향 없음. 시간 남을 때만."),
    ("pH_게이트경고", "pH<=2 또는 pH>=11.5 는 GHS 상 농도 가산이 적용되지 않고 원칙적으로 부식성/자극성으로 간주된다. 이 조건에서 음성 라벨은 의심 대상이다."),
    ("CT모순", "GHS 농도한계 가산 예측과 관측 라벨의 이진값이 다른 행. 반드시 오류는 아니지만(가산 예측은 근사) 판정 시 참고."),
    ("D 시트 E1", "SDS 인용문에 음성 진술이 없고 오히려 자극/알레르기 신호만 있는데 음성으로 복구된 행. 근거가 반대이므로 A 시트 T1 보다 먼저 본다."),
    ("D 시트 E2", "해당 엔드포인트가 No data available / Not determined 인데 음성으로 복구된 행. 시험근거 없는 음성."),
    ("D 시트 E3", "복구된 음성이 하위 출처의 양성 주장을 이겨 v4 대비 양성→음성으로 뒤집힌 행. 부호가 바뀐 라벨이므로 확인 필요."),
    ("작업 순서 권고", "D 시트 E1 → E3 중 Cat 1 이었던 건 → A 시트 T1 → A 시트 T2 → D 시트 E2 → A 시트 T3. A 시트 T4 는 하지 않는다(이진분류 무관)."),
    ("원문 PDF 주의", "원문_SDS문서 경로의 PDF 는 이 저장소에 없다. 별도 보관 위치에서 찾거나 원문_URL 을 이용해야 한다."),
    ("주의", "이 시트는 어떤 데이터 파일도 수정하지 않는다. 판정 결과를 반영하는 것은 별도 단계이며, 반영 시 원본 라벨과 판정 이력을 모두 보존한다."),
], columns=["항목", "내용"])

STAT = pd.DataFrame([
    ("총 충돌행", len(REV)),
    ("T1 (최우선)", int((REV["우선순위"] == "T1").sum())),
    ("T2", int((REV["우선순위"] == "T2").sum())),
    ("T3", int((REV["우선순위"] == "T3").sum())),
    ("T4 (이진분류 영향 없음)", int((REV["우선순위"] == "T4").sum())),
    ("이진 라벨 상충 소계", int(REV["이진라벨_상충"].sum())),
    ("pH 게이트 위반 음성복구", len(GATE)),
    ("CT 가산 예측과 라벨 이진 불일치", int((REV["CT모순"] != "").sum())),
    ("비화학 토큰인데 구조/CAS 보유", len(TOK)),
    ("D: 음성복구 근거품질 소계", len(EVID)),
    ("D-E1 (인용문이 반대 신호) 최우선", int((EVID["등급"] == "E1_반대신호").sum()) if len(EVID) else 0),
    ("D-E2 (시험근거 없는 음성)", int((EVID["등급"] == "E2_근거없음").sum()) if len(EVID) else 0),
    ("D-E3 (양성→음성 부호전환)", int((EVID["등급"] == "E3_부호전환").sum()) if len(EVID) else 0),
    ("실질 작업량 (D전체 + A의 T1/T2)",
     len(EVID) + int((REV["우선순위"].isin(["T1", "T2"])).sum())),
], columns=["항목", "건수"])

# ------------------------------------------------------------------ 엑셀 기록
log(f"[5/5] 저장: {OUT}")
OUT.parent.mkdir(parents=True, exist_ok=True)
# pandas 3.0.2 + openpyxl 3.1.5 조합에서 pd.ExcelWriter(engine="openpyxl") 가
# 저장 시 IndexError("At least one sheet must be visible") 로 실패한다(최소
# 재현 케이스에서도 발생). 따라서 openpyxl 으로 직접 기록한다.
if True:
    wb = Workbook()
    wb.remove(wb.active)
    _sheets = [
        ("00_읽어주세요", GUIDE),
        ("01_요약", STAT),
        ("A_출처충돌_재검", REV),
        ("B_pH게이트_음성복구", GATE if not GATE.empty else pd.DataFrame([{"비고": "해당 행 없음"}])),
        ("C_비화학토큰_구조부착", TOK if not TOK.empty else pd.DataFrame([{"비고": "해당 행 없음"}])),
        ("D_음성복구_근거품질", EVID if not EVID.empty else pd.DataFrame([{"비고": "해당 행 없음"}])),
    ]
    for name, df in _sheets:
        ws = wb.create_sheet(name)
        ws.append([str(c) for c in df.columns])
        for tup in df.itertuples(index=False, name=None):
            ws.append([None if (isinstance(v, float) and math.isnan(v)) else
                       (v if v is None or isinstance(v, (int, float, str, bool)) else str(v))
                       for v in tup])

    hdr_fill = PatternFill("solid", fgColor="1F3864")
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    fill_input = PatternFill("solid", fgColor="FFF2CC")   # 사람이 채우는 칸
    fill_t1 = PatternFill("solid", fgColor="F8CBAD")
    fill_t2 = PatternFill("solid", fgColor="FFE699")
    fill_warn = PatternFill("solid", fgColor="FFC7CE")

    widths = {"제품명": 30, "성분_상위6": 60, "주장요약": 34, "우선순위_설명": 40,
              "원문_SDS문서": 34, "원문_URL": 34, "★메모": 30, "내용": 100, "항목": 22,
              "성분_특이사항": 20, "Formulation_ID": 28, "CT모순": 26, "pH_게이트경고": 26}
    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for c in ws[1]:
            c.fill, c.font = hdr_fill, hdr_font
            c.alignment = Alignment(vertical="center", wrap_text=True)
        ws.row_dimensions[1].height = 28
        heads = [c.value for c in ws[1]]
        for j, h in enumerate(heads, start=1):
            ws.column_dimensions[get_column_letter(j)].width = widths.get(h, 14)
            if isinstance(h, str) and h.startswith("★"):
                for i in range(2, ws.max_row + 1):
                    ws.cell(i, j).fill = fill_input

    # A 시트: 판정 드롭다운 + 티어 색상
    wsA = wb["A_출처충돌_재검"]
    headsA = [c.value for c in wsA[1]]
    n = wsA.max_row
    dv = DataValidation(type="list", formula1='"유지,수정,제외,보류"', allow_blank=True,
                        showDropDown=False)
    wsA.add_data_validation(dv)
    jj = headsA.index("★판정") + 1
    dv.add(f"{get_column_letter(jj)}2:{get_column_letter(jj)}{n}")
    jt = headsA.index("우선순위") + 1
    jw = headsA.index("pH_게이트경고") + 1
    for i in range(2, n + 1):
        tv = wsA.cell(i, jt).value
        if tv == "T1":
            wsA.cell(i, jt).fill = fill_t1
        elif tv == "T2":
            wsA.cell(i, jt).fill = fill_t2
        if wsA.cell(i, jw).value:
            wsA.cell(i, jw).fill = fill_warn
    wsA.auto_filter.ref = wsA.dimensions

    for nm_ in ("B_pH게이트_음성복구", "C_비화학토큰_구조부착", "D_음성복구_근거품질"):
        ws = wb[nm_]
        if ws.max_row > 1:
            ws.auto_filter.ref = ws.dimensions
        hh = [c.value for c in ws[1]]
        if "★판정" in hh:
            d2 = DataValidation(type="list", formula1='"유지,수정,제외,보류"', allow_blank=True,
                                showDropDown=False)
            ws.add_data_validation(d2)
            j2 = hh.index("★판정") + 1
            d2.add(f"{get_column_letter(j2)}2:{get_column_letter(j2)}{ws.max_row}")

    wb.active = 0
    wb.save(OUT)

summary = {
    "output": str(OUT.relative_to(ROOT)),
    "source_readonly": str(SRC.relative_to(ROOT)),
    "sheets": {
        "A_출처충돌_재검": {"rows": int(len(REV)),
                        "tiers": {k: int(v) for k, v in REV["우선순위"].value_counts().items()},
                        "by_endpoint": {k: int(v) for k, v in REV["엔드포인트"].value_counts().items()},
                        "bin_disagree": int(REV["이진라벨_상충"].sum())},
        "B_pH게이트_음성복구": {"rows": int(len(GATE))},
        "C_비화학토큰_구조부착": {"rows": int(len(TOK))},
        "D_음성복구_근거품질": {"rows": int(len(EVID)),
                        "grades": {k: int(v) for k, v in EVID["등급"].value_counts().items()}
                        if len(EVID) else {}},
    },
    "notes": [
        "입력 파일은 수정하지 않음(읽기 전용).",
        "라벨 값은 어느 것도 변경하지 않음. ★ 컬럼은 전부 공란으로 출력.",
        "T4 는 서열만 상충하여 이진분류 목표에 영향 없음.",
    ],
}
REPORT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
log("완료")
log(json.dumps(summary["sheets"], ensure_ascii=False, indent=2))
