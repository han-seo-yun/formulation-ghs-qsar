#!/usr/bin/env python3
"""통합 입력(input_dataset_v6.xlsx) 단일 로더 — 팀원 파일 직접 읽기를 대체한다.

배경
----
v6 측정 스크립트들이 각각 `input_dataset_v5.xlsx` + `정채윤_GHS 조사.xlsx` 를
따로 읽고, `parse_cy()` 를 **스크립트마다 복제**해 런타임에 파싱했다.
그 복제가 실제로 결함을 낳았다 — `보고_관할별_라벨레이어.md` §0 결함 1
(331 CAS 필터·중복제거 누락으로 591행이 들어가 마지막 행이 앞 행을 덮어씀)이
복제본 하나에서만 발생한 사고였다.

`build_input_v6.py` 가 동일한 `parse_cy` 로 파싱 결과를 컬럼화해 통합본에
넣어 두었으므로, 이제 스크립트들은 파싱하지 않고 컬럼을 읽으면 된다.
값은 정의상 동등하다 — **이 모듈로 바꿔서 수치가 바뀌면 결함이다.**
호출부는 기존 산출 CSV 와 대조하는 재현 lock 을 함께 두는 것을 권한다.

읽기 전용. 아무 파일도 쓰지 않는다.
"""
from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/hanseoyun/Desktop/260830")
V6 = ROOT / "04_모델산출물" / "input_dataset_v6.xlsx"
EPS = ("eye", "skin", "sens")
CY_SRC_COL = {"eye": "GHS조사_눈", "skin": "GHS조사_피부", "sens": "GHS조사_감작"}

# 통합본 불변 기준선. 드리프트를 조용히 넘기지 않기 위해 상수로 잠근다.
N_FORM, N_ING = 1675, 5287
N_CY_CAS = 331        # 조사 원문이 기입된 CAS 대표행 == 고유 조사 CAS 수
N_CY_MATCH = 1550     # 그 331 CAS 를 성분으로 갖는 성분행 (CAS 조회 매칭 범위)
N_CY_PARSED = 1355    # 그 중 파싱해 범주가 나온 성분행. 차 195행 = `정보없음` CAS 60종


def _clean(v):
    """엑셀 왕복에서 생기는 nan/"nan"/"None"/"" 을 전부 None 으로 정규화."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v)
    return None if s.strip().lower() in ("nan", "none", "") else s


def load_sheets(need=("formulation", "ingredient")):
    """통합본에서 시트를 읽고 행수 불변을 확인한다."""
    xl = pd.ExcelFile(V6)
    sh = {s: xl.parse(s) for s in need}
    if "formulation" in sh:
        assert len(sh["formulation"]) == N_FORM, \
            f"formulation {len(sh['formulation'])} != {N_FORM} — 통합본 손상"
    if "ingredient" in sh:
        assert len(sh["ingredient"]) == N_ING, \
            f"ingredient {len(sh['ingredient'])} != {N_ING} — 통합본 손상"
    return sh


def cy_map(ING, strict_tier=False):
    """정채윤 성분 GHS 조사 결과를 {cas: {ep: (cat, tier)}} 로 돌려준다.

    이전 판의 `CY_MAP[cas][ep] = (cat, tier, pct)` 와 앞 두 값이 동등하다.
    세 번째 `pct` 는 **통보자 신고 농도**이고 제형 함량이 아니어서 CT 가산에
    쓰인 적이 없다(호출부가 전부 `_` 로 버렸다). 오해를 막기 위해 여기서는
    반환하지 않는다.

    strict_tier=True 면 tier 를 교정판 `_tier_src`(메모의 'EU CLP Annex VI
    존재' 기록 기반)에서 읽는다 — critic C1. 기본값은 False 로, 기존 산출을
    재현하는 결함 있는 `_tier` 를 그대로 쓴다. 값 교체는 총책임자 결정 사안이다.
    """
    tcol = "tier_src" if strict_tier else "tier"
    for ep in EPS:
        for k in ("cat", tcol):
            assert f"ing_ghs_indep_{ep}_{k}" in ING.columns, \
                f"통합열 ing_ghs_indep_{ep}_{k} 없음 — build_input_v6.py 를 먼저 실행"

    cas = ING["cas"].astype(str).str.strip()
    src = ING[CY_SRC_COL["eye"]].notna()
    assert int(src.sum()) == N_CY_CAS, f"조사 대표행 {int(src.sum())} != {N_CY_CAS}"
    surveyed = set(cas[src])
    assert len(surveyed) == N_CY_CAS, f"고유 조사 CAS {len(surveyed)} != {N_CY_CAS}"
    assert int(cas.isin(surveyed).sum()) == N_CY_MATCH, "CAS 매칭 성분행 이탈"
    assert int(ING["ing_ghs_indep_eye_cat"].notna().sum()) == N_CY_PARSED, \
        "파싱성공 성분행 이탈"

    out = {}
    for c, (_, r) in zip(cas, ING.iterrows()):
        if c in out or c not in surveyed:
            continue
        out[c] = {ep: (_clean(r[f"ing_ghs_indep_{ep}_cat"]),
                       _clean(r[f"ing_ghs_indep_{ep}_{tcol}"])) for ep in EPS}
    assert len(out) == N_CY_CAS, f"CY_MAP {len(out)} != {N_CY_CAS}"
    return out


def cy_survey_rows(ING, cols=None):
    """조사 원문 대표행 331행을 그대로 돌려준다(원문 텍스트가 필요한 스크립트용)."""
    d = ING[ING[CY_SRC_COL["eye"]].notna()].copy()
    assert len(d) == N_CY_CAS, f"조사 대표행 {len(d)} != {N_CY_CAS}"
    d["cas"] = d["cas"].astype(str).str.strip()
    d = d.drop_duplicates("cas")
    assert len(d) == N_CY_CAS
    return d[cols] if cols else d
