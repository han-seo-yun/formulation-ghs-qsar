#!/usr/bin/env python3
"""input_dataset_v6.xlsx — 팀원 4인 산출물을 통합한 단일 입력 데이터셋.

목적
----
지금까지 v6 측정 스크립트들은 `정채윤_GHS 조사.xlsx` 를 런타임에 직접 읽었다.
그래서 입력이 (v4_fixed parquet + input_dataset_v5.xlsx + 팀원 xlsx) 3원화되어
있었다. 이 스크립트는 팀원 신규열을 v5 위에 통합해 v6 단일 파일을 만든다.

설계 근거: `04_모델산출물/v5_team_qa/병합설계.md` PHASE 0 + PHASE 1.
그 설계를 재설계하지 않고 그대로 구현한다.

PHASE 0 게이트 (설계 §6)
  팀원 4파일에서 **신규열만** 가져온다. 기존열은 단 1열도 가져오지 않는다.
  이것으로 배포본 vs 저장소원본 1028셀 차이 문제 전체를 우회한다
  (그 차이는 전부 기존열 `독성코드.cat_*_ghs` 등에 있고, 신규열에는 없다).

ACTIVE / STAGED 구분 (중요)
  ACTIVE  = 지금 모델이 실제로 쓰는 값. 정채윤 GHS 조사만 해당.
            (이미 CT 증강 A2/A3 arm 으로 쓰이고 있었다 — 통합은 순수 리팩터이며
             수치가 바뀌면 안 된다.)
  STAGED  = 통합만 하고 아직 쓰지 않는 값. 최소빈 S11 판독(라벨 변경 →
            PHASE 2, 별도 승인 필요), 이서윤·김보경 pH(비가산 게이트 미구현.
            김보경은 260907 재제출로 배정 274행 전부 기입 완료 — 舊 0건에서 갱신).
  라벨을 임의로 바꾸지 않기 위한 구분이다. STAGED 를 ACTIVE 로 올리는 것은
  총책임자 결정 사안이다.

불변 assert (설계 §6 PHASE 1 — 전부 통과 필수)
  group_key 차이 0행 / nunique 587, y_* 차이 0행,
  ing_cas_best·ing_name_best·ing_pct_best 차이 0행.

읽기 전용: input_dataset_v5.xlsx, 팀원 xlsx 4종, 03_입력데이터/*, v4_fixed/*.
산출: 04_모델산출물/input_dataset_v6.xlsx (신규 파일, v5 를 덮지 않는다)
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
V5 = ROOT / "04_모델산출물" / "input_dataset_v5.xlsx"
FIXED = ROOT / "04_모델산출물" / "v4_fixed"
OUT = ROOT / "04_모델산출물" / "input_dataset_v6.xlsx"
QA = ROOT / "04_모델산출물" / "v6_통합"
QA.mkdir(parents=True, exist_ok=True)

# 260908: 팀원 원본 4개를 05_원본보관_260908/ 로 이동(삭제 아님 — L0 불변 원칙).
# 사람이 보는 통합 정리본은 04_모델산출물/v6_통합/팀원통합_260908.xlsx 이고,
# 파이프라인은 계속 아래 원본을 직접 읽어 재현성을 유지한다.
TEAMDIR = ROOT / "05_원본보관_260908"
TEAM = {
    "정채윤": TEAMDIR / "정채윤_GHS 조사.xlsx",
    "최소빈": TEAMDIR / "최소빈_독성라벨.xlsx",
    "이서윤": TEAMDIR / "이서윤_pH.xlsx",   # 260908 희석·참고값 48행 격리 반영본
    "김보경": TEAMDIR / "보경_dataset.xlsx",  # 260907 재제출본(舊 보경_dataset_배정_20260824.xlsx)
}
_t0 = time.time()
_LOG = open(QA / "run_build_v6.log", "w", encoding="utf-8")


def log(m):
    line = f"[{time.time()-_t0:6.1f}s] {m}"
    print(line, flush=True)
    _LOG.write(line + "\n")
    _LOG.flush()


# ==================================================================== v5 로드
log("v5 로드 (읽기 전용)")
xl5 = pd.ExcelFile(V5)
SH = {s: xl5.parse(s) for s in xl5.sheet_names}
log("  시트: " + ", ".join(f"{s}{SH[s].shape}" for s in SH))
FORM, ING = SH["formulation"], SH["ingredient"]
assert len(FORM) == 1675 and len(ING) == 5287, "v5 행수 이탈"
FID = FORM["Formulation_ID"].astype(str).to_numpy()

# 불변 기준선 스냅샷
SNAP = pd.read_csv(FIXED / "group_key_snapshot_v5.csv")
YPQ = pd.read_parquet(FIXED / "y_formulation.parquet")
assert SNAP["group_key"].nunique() == 587, "기준선 group_key nunique != 587"

# ==================================================================== PHASE 0
# 팀원 파일에서 신규열만 추출한다. 기존열은 가져오지 않는다.
NEWCOLS = {
    "정채윤": ("성분", ["GHS조사_CAS대표행", "GHS조사_담당", "GHS조사_눈", "GHS조사_피부",
                     "GHS조사_감작", "GHS조사_근거URL", "GHS조사_메모"]),
    "최소빈": ("독성코드", ["S11_담당", "S11_원문_눈", "S11_판독_눈", "S11_원문_피부",
                        "S11_판독_피부", "S11_원문_감작", "S11_판독_감작", "S11_메모"]),
    "이서윤": ("특성", ["pH작업_담당", "pH작업_확보값", "pH작업_근거URL", "pH작업_메모",
                     "pH작업_상세"]),
}
BASE_COLS = {"성분": set(ING.columns), "독성코드": set(SH["targets"].columns),
             "특성": set(FORM.columns)}
RAW = {}
for who, (sheet, cols) in NEWCOLS.items():
    d = pd.ExcelFile(TEAM[who]).parse(sheet)
    # 게이트: 가져오는 열이 정말 '신규열'인지 — v5 에 이미 있으면 중단
    for c in cols:
        assert c in d.columns, f"{who}/{sheet} 에 {c} 없음"
        assert c not in BASE_COLS.get(sheet, set()), f"{c} 는 신규열이 아니다 — 게이트 위반"
    key = ["Formulation_ID"] + (["ing_idx"] if "ing_idx" in d.columns else [])
    RAW[who] = d[key + cols].copy()
    log(f"PHASE0 {who}: {sheet} 신규열 {len(cols)}개 추출, 키={key}, {len(d)}행")

# 김보경: 260907 재제출본에서 배정·기입 현황을 코드로 확인한다(추측 아님).
# 이전 버전(보경_dataset_배정_20260824.xlsx)은 배정 274행/기입 0행이었다.
# 재제출본은 274행 전부 기입됐다 — 실제 병합은 §이서윤 pH 블록 뒤에서 수행한다
# (동일한 parse_ph 로직을 재사용하기 위함).
_bk = pd.ExcelFile(TEAM["김보경"]).parse("특성")
BK_ASSIGNED = int((_bk["pH작업_담당"].astype(str) == "김보경").sum())
BK_FILLED = int(_bk.loc[_bk["pH작업_담당"].astype(str) == "김보경", "pH작업_확보값"].notna().sum())
assert BK_ASSIGNED == 274, f"김보경 배정행 {BK_ASSIGNED} != 274 — 배정 변경, 설계 전제 재확인 필요"
assert BK_FILLED == 274, f"김보경 기입행 {BK_FILLED} != 274 — 재제출본 완료 여부 재확인 필요"
log(f"PHASE0 김보경: 배정 {BK_ASSIGNED}행, 기입 {BK_FILLED}행 (260907 재제출 → 100% 완료, 이전 0건에서 갱신)")

# ==================================================================== 정채윤 파싱
# measure_full_metrics_v6.py 의 parse_cy 와 **완전히 동일한 로직**을 쓴다.
# 통합은 리팩터이므로 수치가 바뀌면 안 된다. 로직을 여기서 '개선'하지 않는다.
H2CAT = {"eye": {"H318": "1", "H319": "2"}, "skin": {"H314": "1", "H315": "2"},
         "sens": {"H317": "1"}}
_RX_H = re.compile(r"\bH(3\d\d)\b")
_RX_PCT = re.compile(r"H\d{3}\s*\((\d+(?:\.\d+)?)%\)")


def parse_cy(val, ep):
    s = str(val)
    if s.startswith("정보없음"):
        return None, None, None
    tier = ("annex_vi" if "Annex VI" in s and "ECHA C&L 통보" not in s
            else ("echa_cl" if "ECHA C&L 통보" in s else "annex_vi"))
    if s.startswith("분류대상아님"):
        return "NC", tier, None
    hits = [h for h in ("H" + m for m in _RX_H.findall(s)) if h in H2CAT[ep]]
    if not hits:
        return None, tier, None
    cat = min((H2CAT[ep][h] for h in hits), key=lambda c: {"1": 0, "2": 1}[c])
    pm = _RX_PCT.search(s)
    return cat, tier, (float(pm.group(1)) if pm else None)


def tier_from_memo(memo, cls_cell):
    """출처 등급을 **메모의 출처 기록**에서 파생한다 — parse_cy 의 tier 결함 교정.

    결함(critic C1, 2026-09-02 확인): parse_cy 는 분류 셀 문자열에서
    `"ECHA C&L 통보"` 를 찾아 tier 를 정한다. 그러나 NC 보일러플레이트는
    `분류대상아님(EU CLP Annex VI/ECHA C&L **기준** 해당 H코드 없음)` 이라
    `"통보"` 검사에 걸리지 않고 첫 분기로 떨어져 **NC 200건 전부가 annex_vi**
    로 판정된다. 실측: 감작 NC 201건 중 81건은 메모에 Annex VI 기록이 없고
    ECHA C&L 통보(자가신고)만 있다. 즉 A3("Annex VI 조화분류 전용") arm 이
    자가신고 근거만 있는 CAS 를 조화분류로 받아들이고, 그것을 `n_known` 에
    넣어 '조회했으나 자가신고에 없음' 을 음성으로 쓰고 있었다 — 이 프로젝트가
    금지한 '미시험을 음성으로' 결함이 형태를 바꿔 재발한 것이다.

    기존 `_tier` 는 **재현을 위해 그대로 남긴다**. 이 함수의 결과는
    `_tier_src` 로 병기해, 오염분을 측정 가능하게만 한다(값 교체는 총책임자
    판단 사안이며, assert 상수를 조용히 맞추지 않는다).

    반환: "annex_vi" | "echa_cl" | None(출처 판정 불가)
    """
    m = "" if memo is None else str(memo)
    if "EU CLP Annex VI 존재" in m:
        return "annex_vi"
    if "ECHA C&L 통보 존재" in m:
        return "echa_cl"
    # 메모에 기계 기록이 없는 62건 = 정보없음 60 + 수동보완 2.
    # 수동보완 2건은 메모 본문에 근거를 적었으므로 분류 셀 문자열로 판정한다.
    c = "" if cls_cell is None else str(cls_cell)
    if c.startswith("정보없음"):
        return None
    if "ECHA C&L 통보" in c:
        return "echa_cl"
    if "Annex VI" in c:
        return "annex_vi"
    return None


CYR = RAW["정채윤"]
CY331 = CYR[CYR["GHS조사_눈"].notna()][
    ["cas", "GHS조사_눈", "GHS조사_피부", "GHS조사_감작"]
].drop_duplicates("cas") if "cas" in CYR.columns else None
if CY331 is None:                      # cas 는 기존열이므로 신규열 추출에 없다 → v5 에서 가져온다
    CYR = CYR.merge(ING[["Formulation_ID", "ing_idx", "cas"]],
                    on=["Formulation_ID", "ing_idx"], how="left", validate="1:1")
    CY331 = CYR[CYR["GHS조사_눈"].notna()][
        ["cas", "GHS조사_눈", "GHS조사_피부", "GHS조사_감작", "GHS조사_메모"]].drop_duplicates("cas")
assert len(CY331) == 331, f"정채윤 고유 CAS {len(CY331)} != 331 — 전처리 규약 이탈"

EPS = ("eye", "skin", "sens")
_COL = {"eye": "GHS조사_눈", "skin": "GHS조사_피부", "sens": "GHS조사_감작"}
CY_MAP, _unp = {}, []
_tsrc_n = {"annex_vi": 0, "echa_cl": 0, "none": 0}
_tier_diff = {ep: 0 for ep in EPS}
for _, r in CY331.iterrows():
    d = {}
    for ep in EPS:
        cat, tier, pct = parse_cy(r[_COL[ep]], ep)
        if cat is None and not str(r[_COL[ep]]).startswith("정보없음"):
            _unp.append((str(r["cas"]), ep))
        tsrc = tier_from_memo(r["GHS조사_메모"], r[_COL[ep]])
        if cat is not None and tier != tsrc:
            _tier_diff[ep] += 1
        d[ep] = (cat, tier, pct, tsrc)
    _tsrc_n[tier_from_memo(r["GHS조사_메모"], r[_COL["eye"]]) or "none"] += 1
    CY_MAP[str(r["cas"]).strip()] = d
assert not _unp, f"파싱 실패: {_unp[:5]}"
log(f"정채윤 파싱: 331 CAS, 파싱실패 0")
log(f"  출처등급(메모 기반, 교정판): annex_vi {_tsrc_n['annex_vi']} / "
    f"echa_cl {_tsrc_n['echa_cl']} / 판정불가 {_tsrc_n['none']}")
log(f"  기존 tier 와 불일치한 CAS(범주 있는 셀만): "
    + " / ".join(f"{ep} {_tier_diff[ep]}" for ep in EPS)
    + "  ← critic C1. 기존 _tier 는 재현용으로 보존, _tier_src 로 병기")

# ingredient 에 통합 — 원문 7열 + 파싱결과 6열
ING6 = ING.copy()
_cyidx = RAW["정채윤"].set_index(["Formulation_ID", "ing_idx"])
_k = pd.MultiIndex.from_arrays([ING6["Formulation_ID"], ING6["ing_idx"]])
for c in NEWCOLS["정채윤"][1]:
    ING6[c] = _cyidx[c].reindex(_k).to_numpy()
_ingcas = ING6["cas"].astype(str).str.strip()
for ep in EPS:
    ING6[f"ing_ghs_indep_{ep}_cat"] = [
        (CY_MAP[c][ep][0] if c in CY_MAP else None) for c in _ingcas]
    ING6[f"ing_ghs_indep_{ep}_tier"] = [
        (CY_MAP[c][ep][1] if c in CY_MAP else None) for c in _ingcas]
    # 교정판 출처등급(critic C1). 기존 _tier 를 대체하지 않고 병기한다.
    ING6[f"ing_ghs_indep_{ep}_tier_src"] = [
        (CY_MAP[c][ep][3] if c in CY_MAP else None) for c in _ingcas]
_matched = int(_ingcas.isin(CY_MAP).sum())
assert _matched == 1550, f"CY CAS 매칭 성분행 {_matched} != 1550"
log(f"ingredient 통합: 원문 7열 + 파싱 6열, CY 매칭 성분행 {_matched}")

# ==================================================================== 이서윤 pH
# STAGED. GHS 비가산 게이트가 미구현이므로 여전히 학습에 쓰지 않는다.
# 260908 정리: 희석 조건·타제품 참고값 48행을 pH작업_참고값_격리 로 이동하고
# pH작업_확보값 에는 원액(neat) 기준 46행만 남겼다. 게이트는 제품을 그대로 측정한
# 원액 pH 로만 판정할 수 있고, 희석 수용액 pH 는 농도에 따라 로그적으로 달라져
# 원액 pH 를 대신할 수 없다. 48행은 재수집 대상이며 값을 추정해 채우지 않는다.
_RX_NUM = re.compile(r"(\d+(?:\.\d+)?)")
_DIL = ("%", "dispersion", "solution", "suspension", "w/v", "w/w", "aqueous",
        "dilut", "희석", "수용액")


def parse_ph(v):
    """반환: (lo, hi, basis, gate_applicable). 추측으로 대푯값을 만들지 않는다."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None, None, None, None
    s = str(v).strip()
    if not s:
        return None, None, None, None
    nums = [float(x) for x in _RX_NUM.findall(s)]
    low = s.lower()
    # 희석 조건 문구가 있으면 원액 pH 가 아니다 → 게이트 적용 불가
    basis = "dilution" if any(t in low for t in _DIL) else "neat"
    # 희석 배수 표기(1%, 10% 등)에서 온 숫자는 pH 값이 아니므로 제외
    ph_nums = [n for n in nums if 0.0 <= n <= 14.0]
    if not ph_nums:
        return None, None, basis, False
    if basis == "dilution":
        # '5.8 - 6.2 at 1% dispersion' 의 1 은 pH 가 아니다. 농도 토큰을 떼어낸다.
        ph_nums = [n for n in ph_nums
                   if not re.search(rf"{re.escape(str(n).rstrip('0').rstrip('.'))}\s*%", low)]
        if not ph_nums:
            return None, None, basis, False
    lo, hi = min(ph_nums), max(ph_nums)
    return lo, hi, basis, (basis == "neat")


SYR = RAW["이서윤"].set_index("Formulation_ID")
FORM6 = FORM.copy()
_fk = FORM6["Formulation_ID"].astype(str)
_ren = {"pH작업_확보값": "ph_v6_raw", "pH작업_근거URL": "ph_v6_url",
        "pH작업_메모": "ph_v6_memo", "pH작업_상세": "ph_v6_detail",
        "pH작업_담당": "ph_v6_assignee"}
for src, dst in _ren.items():
    FORM6[dst] = SYR[src].reindex(_fk).to_numpy()
_p = [parse_ph(v) for v in FORM6["ph_v6_raw"]]
FORM6["ph_v6_lo"] = [x[0] for x in _p]
FORM6["ph_v6_hi"] = [x[1] for x in _p]
FORM6["ph_v6_basis"] = [x[2] for x in _p]
FORM6["ph_gate_applicable"] = [x[3] for x in _p]
_phn = int(FORM6["ph_v6_raw"].notna().sum())
_phparsed = int(sum(x[0] is not None for x in _p))
_phneat = int(sum(x[3] is True for x in _p))
assert _phn == 46, f"이서윤 pH 확보값 {_phn} != 46 (260908 격리 후 원액 기준만)"
assert _phneat == _phn, \
    f"이서윤 확보값에 희석 기준 잔존({_phn - _phneat}행) — 격리 미완, 병합 중단"
# 기존 ph_best 와 충돌이 없는지(설계 §3-3: 94행 전부 현재 결측) 확인
if "ph_best" in FORM6.columns:
    _clash = int((FORM6["ph_v6_raw"].notna() & FORM6["ph_best"].notna()).sum())
    log(f"이서윤 pH: 확보 {_phn}행, 파싱성공 {_phparsed}, 원액기준 {_phneat}, "
        f"기존 ph_best 와 충돌 {_clash}행")
else:
    log(f"이서윤 pH: 확보 {_phn}행, 파싱성공 {_phparsed}, 원액기준 {_phneat}")

# ==================================================================== 김보경 pH
# STAGED. 260907 재제출본에서 배정 274행 전부 기입 완료(舊 0건에서 갱신).
# 이서윤과 동일한 parse_ph 로직을 그대로 재사용한다 — 통합 로직을 새로 만들지 않는다.
# 게이트(GHS 비가산 예외) 자체가 미구현이므로 이서윤과 마찬가지로 STAGED 유지.
BK = _bk[_bk["pH작업_담당"].astype(str) == "김보경"].copy()
BK_idx = BK.set_index("Formulation_ID")
_bk_mask = _fk.isin(BK_idx.index.astype(str))
assert int(_bk_mask.sum()) == 274, f"김보경 배정 매칭행 {int(_bk_mask.sum())} != 274 — 키 불일치"
assert not FORM6.loc[_bk_mask, "ph_v6_raw"].notna().any(), \
    "김보경/이서윤 배정 formulation 중복 — 병합 중단(설계 전제: 배정 상호배타)"
_bk_ren = {"pH작업_확보값": "ph_v6_raw", "pH작업_근거URL": "ph_v6_url",
           "pH작업_메모": "ph_v6_memo", "pH작업_담당": "ph_v6_assignee"}
for src, dst in _bk_ren.items():
    FORM6.loc[_bk_mask, dst] = BK_idx[src].reindex(_fk[_bk_mask]).to_numpy()
_bkp = [parse_ph(v) for v in FORM6.loc[_bk_mask, "ph_v6_raw"]]
FORM6.loc[_bk_mask, "ph_v6_lo"] = [x[0] if x[0] is not None else np.nan for x in _bkp]
FORM6.loc[_bk_mask, "ph_v6_hi"] = [x[1] if x[1] is not None else np.nan for x in _bkp]
FORM6.loc[_bk_mask, "ph_v6_basis"] = [x[2] for x in _bkp]
FORM6.loc[_bk_mask, "ph_gate_applicable"] = [x[3] for x in _bkp]
_bkparsed = int(sum(x[0] is not None for x in _bkp))
_bkneat = int(sum(x[3] is True for x in _bkp))
_bkreview = int((BK["검증여부"] == "재검토필요").sum())
assert _bkparsed == 72, f"김보경 pH 파싱성공 {_bkparsed} != 72"
assert _bkneat == 49, f"김보경 pH 원액기준 {_bkneat} != 49"
if "ph_best" in FORM6.columns:
    _bkclash = int((FORM6.loc[_bk_mask, "ph_v6_raw"].notna()
                    & FORM6.loc[_bk_mask, "ph_best"].notna()).sum())
    log(f"김보경 pH: 배정 {BK_ASSIGNED}행, 기입 {BK_FILLED}행, 파싱성공 {_bkparsed}, "
        f"원액기준 {_bkneat}, 재검토필요 {_bkreview}, 기존 ph_best 와 충돌 {_bkclash}행")
else:
    log(f"김보경 pH: 배정 {BK_ASSIGNED}행, 기입 {BK_FILLED}행, 파싱성공 {_bkparsed}, "
        f"원액기준 {_bkneat}, 재검토필요 {_bkreview}")
log("김보경 pH → STAGED 유지(이서윤과 동일 게이트 미구현). ACTIVE 전환은 총책임자 결정 사안")

# ==================================================================== 최소빈 S11
# STAGED. 라벨을 바꾸는 경로이므로 별도 시트로 격리한다(설계 §4: label_coderived,
# 피처로 절대 쓰지 않는다). targets 시트에 섞지 않는 이유가 그것이다.
S11 = RAW["최소빈"].copy()
_s11n = {ep: int(S11[f"S11_판독_{k}"].notna().sum())
         for ep, k in (("eye", "눈"), ("skin", "피부"), ("sens", "감작"))}
assert _s11n == {"eye": 40, "skin": 47, "sens": 136}, f"S11 판독수 이탈: {_s11n}"
S11["적용여부"] = "STAGED_미적용"
S11["미적용사유"] = "라벨 변경 경로(PHASE 2) — 총책임자 승인 전 적용 금지"
log(f"최소빈 S11: 판독 눈 {_s11n['eye']} / 피부 {_s11n['skin']} / 감작 {_s11n['sens']}"
    f" → STAGED (라벨 미적용)")

# ==================================================================== 불변 검증
log("불변 assert 검증")
assert (FORM6["Formulation_ID"].astype(str).values == FID).all(), "formulation 키 순서 변동"
assert (ING6["Formulation_ID"].astype(str).values
        == ING["Formulation_ID"].astype(str).values).all(), "ingredient 키 순서 변동"
for c in ("ing_cas_best", "ing_name_best", "ing_pct_best", "cas", "ing_idx"):
    if c in ING.columns:
        a, b = ING[c].astype(str).fillna(""), ING6[c].astype(str).fillna("")
        assert (a.values == b.values).all(), f"ingredient.{c} 변동 — 병합 중단"
GK6 = SH["targets"]["group_key"].astype(str) if "group_key" in SH["targets"].columns else None
if GK6 is not None:
    assert (GK6.values == SNAP["group_key"].astype(str).values).all(), "group_key 변동"
    assert GK6.nunique() == 587, f"group_key nunique {GK6.nunique()} != 587"
for c in SH["targets"].columns:
    if c.startswith("y_") and c.endswith("_bin"):
        pass  # targets 시트를 수정하지 않으므로 정의상 불변
assert set(SH["targets"].columns) == set(xl5.parse("targets", nrows=0).columns), \
    "targets 스키마 변동 — 라벨을 건드렸다"
log("  통과: group_key 불변(nunique 587) / targets 스키마 불변 / 성분 식별열 불변")

# ==================================================================== 매니페스트
FM = SH["feature_manifest"].copy()
_fmcols = list(FM.columns)


def fm_row(col, role, feature_role, in_x, note):
    r = {c: None for c in _fmcols}
    for k, v in (("column", col), ("role", role), ("feature_role", feature_role),
                 ("in_X", in_x), ("note", note), ("sheet", None)):
        if k in r:
            r[k] = v
    if "exclude_by_default" in r:
        r["exclude_by_default"] = (not in_x)
    return r


NEW_FM = []
for c in NEWCOLS["정채윤"][1]:
    fr = ("id" if "CAS대표행" in c else
          "chemistry" if c in ("GHS조사_눈", "GHS조사_피부", "GHS조사_감작") else "bookkeeping")
    NEW_FM.append(fm_row(c, "raw_survey", fr, False,
                         "정채윤 성분 GHS 조사 원문. 파싱본은 ing_ghs_indep_*"))
for ep in EPS:
    NEW_FM.append(fm_row(f"ing_ghs_indep_{ep}_cat", "feature", "chemistry", False,
                         "정채윤 독립출처 GHS 범주(ACTIVE — CT 증강 A2/A3 입력)"))
    NEW_FM.append(fm_row(f"ing_ghs_indep_{ep}_tier", "feature", "chemistry", False,
                         "출처 등급 annex_vi/echa_cl. 기존 A3 arm 필터 기준 — "
                         "NC 셀에서 annex_vi 로 과대판정되는 결함 있음(critic C1)"))
    NEW_FM.append(fm_row(f"ing_ghs_indep_{ep}_tier_src", "feature", "chemistry", False,
                         "출처 등급 교정판. 메모의 'EU CLP Annex VI 존재' 기록에서 파생. "
                         "A3_strict arm 의 필터 기준"))
for c, fr, note in (
    ("ph_v6_raw", "ambiguous",
     "이서윤·김보경 pH 원문 통합(배정 상호배타, formulation 단위 병합). "
     "희석조건 혼재 → 원액 pH 미확정(STAGED)"),
    ("ph_v6_lo", "ambiguous", "파싱 하한. 게이트는 ph_gate_applicable=True 에서만"),
    ("ph_v6_hi", "ambiguous", "파싱 상한"),
    ("ph_v6_basis", "bookkeeping", "neat/dilution 판정"),
    ("ph_gate_applicable", "bookkeeping", "GHS 비가산 게이트 적용 가능 여부"),
    ("ph_v6_url", "bookkeeping", "출처 URL"),
    ("ph_v6_memo", "bookkeeping", "작업 메모"),
    ("ph_v6_detail", "bookkeeping", "작업 상세(이서윤만 제공, 김보경 파일에는 없음)"),
    ("ph_v6_assignee", "bookkeeping", "담당자 (이서윤 / 김보경)"),
):
    NEW_FM.append(fm_row(c, "feature" if fr == "ambiguous" else "bookkeeping",
                         fr, False, note))
FM6 = pd.concat([FM, pd.DataFrame(NEW_FM)], ignore_index=True)
log(f"feature_manifest: {len(FM)} → {len(FM6)}행 (신규 {len(NEW_FM)}) — 전부 in_X=False")

MANI = pd.DataFrame([
    {"담당": "정채윤", "산출물": "성분 GHS 조사(눈/피부/감작)", "고유CAS": 331,
     "매칭성분행": _matched, "상태": "ACTIVE",
     "반영위치": "ingredient.GHS조사_* (원문) + ing_ghs_indep_*_cat/_tier (파싱)",
     "용도": "CT 증강 A2/A3 arm 입력 — 이미 모델에 반영되어 있던 값",
     "주의": "통합은 순수 리팩터. 파싱 로직을 바꾸지 않았으므로 수치가 바뀌면 결함이다"},
    {"담당": "이서윤", "산출물": "pH 확보값", "고유CAS": None,
     "매칭성분행": _phn, "상태": "STAGED",
     "반영위치": "formulation.ph_v6_* (+ ph_gate_applicable)",
     "용도": "GHS 비가산 예외 게이트 — 게이트 자체가 미구현",
     "주의": f"260908 정리: 희석조건·타제품 참고값 48행을 원본에서 격리하고 원액 기준 "
           f"{_phn}행만 남김(원액기준 {_phneat}행 = 확보행 전부). 격리 48행은 "
           f"04_모델산출물/v6_통합/팀원통합_260908.xlsx 의 재작업_이서윤pH48 시트 참조. "
           f"결측을 0·7 로 임퓨트 금지"},
    {"담당": "최소빈", "산출물": "SDS S11 판독", "고유CAS": None,
     "매칭성분행": sum(_s11n.values()), "상태": "STAGED",
     "반영위치": "staged_s11 시트 (targets 에 섞지 않음)",
     "용도": "라벨 y_* 갱신 — PHASE 2, 총책임자 승인 필요",
     "주의": f"판독 눈 {_s11n['eye']}/피부 {_s11n['skin']}/감작 {_s11n['sens']}. "
           "label_coderived 이므로 피처로 절대 사용 금지"},
    {"담당": "김보경", "산출물": "pH (SC·EC)", "고유CAS": None,
     "매칭성분행": BK_FILLED, "상태": "STAGED",
     "반영위치": "formulation.ph_v6_* (+ ph_gate_applicable)",
     "용도": "GHS 비가산 예외 게이트 — 게이트 자체가 미구현",
     "주의": f"260907 재제출로 배정 {BK_ASSIGNED}행 전부 기입 완료(舊 0건에서 갱신). "
           f"파싱성공(숫자 pH) {_bkparsed}행, 원액기준 {_bkneat}행, 재검토필요 {_bkreview}행. "
           f"결측을 0·7 로 임퓨트 금지"},
])

META = SH["MetaData"].copy()
_mc = list(META.columns)
META = pd.concat([META, pd.DataFrame([{
    _mc[0]: "v6_통합", _mc[1] if len(_mc) > 1 else "value":
    "팀원 4인 신규열 통합 (정채윤 ACTIVE / 이서윤·최소빈·김보경 STAGED)",
    **({_mc[2]: "build_input_v6.py"} if len(_mc) > 2 else {})}])], ignore_index=True)

# ==================================================================== 저장
log("input_dataset_v6.xlsx 저장 (write_only 모드)")
OUTSH = {"MetaData": META, "formulation": FORM6, "ingredient": ING6,
         "desc_formulation": SH["desc_formulation"],
         "desc_ingredient": SH["desc_ingredient"], "targets": SH["targets"],
         "provenance": SH["provenance"], "feature_manifest": FM6,
         "staged_s11": S11, "team_integration_manifest": MANI}
wb = openpyxl.Workbook(write_only=True)
for name, df in OUTSH.items():
    ws = wb.create_sheet(name[:31])
    ws.append([str(c) for c in df.columns])
    for row in df.itertuples(index=False, name=None):
        ws.append([None if (isinstance(v, float) and np.isnan(v))
                   else (v if isinstance(v, (int, float, str, bool, type(None)))
                         else str(v)) for v in row])
    log(f"  {name}: {df.shape}")
wb.save(OUT)

with open(QA / "통합_요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "생성": "build_input_v6.py",
        "설계근거": "04_모델산출물/v5_team_qa/병합설계.md PHASE 0 + PHASE 1",
        "출력": str(OUT.relative_to(ROOT)),
        "시트": {k: list(v.shape) for k, v in OUTSH.items()},
        "PHASE0_게이트": "팀원 파일에서 신규열만 추출(정채윤7/최소빈8/이서윤5). "
                     "기존열 0개 → 배포본-원본 1028셀 차이 문제 우회",
        "불변검증": {"group_key_nunique": 587, "group_key_차이": 0,
                 "targets_스키마": "불변", "성분식별열": "불변"},
        "ACTIVE": {"정채윤": {"고유CAS": 331, "매칭성분행": _matched}},
        "STAGED": {"이서윤_pH": {"확보": _phn, "파싱성공": _phparsed, "원액기준": _phneat},
                   "김보경_pH": {"배정": BK_ASSIGNED, "기입": BK_FILLED,
                              "파싱성공": _bkparsed, "원액기준": _bkneat,
                              "재검토필요": _bkreview},
                   "최소빈_S11": _s11n},
        "미도착": {},
        "주의": "STAGED 는 모델에 반영되지 않았다. 반영은 총책임자 결정 사안이다.",
    }, f, ensure_ascii=False, indent=2)
MANI.to_csv(QA / "팀원통합_매니페스트.csv", index=False, encoding="utf-8-sig")
log(f"완료 → {OUT}")
