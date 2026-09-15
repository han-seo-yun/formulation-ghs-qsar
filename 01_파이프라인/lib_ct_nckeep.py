#!/usr/bin/env python3
"""`A2` 의 `NC` 폐기만 뺀 가산식 입력 — `measure_*` 스크립트가 공유한다.

## 왜 이 파일이 따로 있는가

`lib_model.build_ct` 의 `A2_aug_nc_unknown` 은 증강분이 `NC` 면 `c = None` 로
되돌린다(`lib_model.py:346-347`). 그래서 유해 구분만 더해지고 성분을 **무해로
확정하는 정보는 가산식에 들어가지 않는다** — 가산합을 올리는 쪽으로만 작동한다.
그런데 이 조사가 새로 만든 값의 약 70%(눈 164/234 종)가 `NC` 다. `A2` 로만 재면
조사 결과의 대부분이 가산식에 닿지도 못한 채 '효과 없음' 으로 기록된다. 그래서
`NC` 만 살린 대조 arm 이 필요하다.

`lib_model.py` 는 버전 고정이라 수정하지 않는다. 대신 같은 계산을 복제하고
**eye·skin 의 NC 폐기 한 줄만** 뺀다.

## 왜 복제본이 하나여야 하는가

앞판에서는 이 함수가 `measure_ing_survey_gain.py` 와 `measure_a0_fill.py` 에
각각 복사돼 있었고, 한쪽에서 `sens` 의 A3 tier 게이트가 빠졌다. 그 결과 **두
스크립트가 같은 이름의 arm 을 다른 숫자로 발표했다**(eye `NC유지` 보정 AUC
0.6591 vs 0.6629). 어느 쪽이 맞는지는 코드를 나란히 놓고 읽어야만 알 수 있었다.
복제본이 둘이면 다음 수정에서 같은 일이 다시 난다 — 그래서 하나로 모은다.

## `sens` 의 tier 게이트를 함께 빼지 않는다

`sens` 의 권고 arm 은 `A3_aug_annexvi` 이고 그쪽은 애초에 `NC` 를 버리지 않는다 —
대신 `tier != "annex_vi"` 를 버린다(`lib_model.py:348-349`). 이 게이트까지 빼면
이 arm 이 'NC 만 살린 기준선' 이 아니라 **sens tier 필터 해제까지 얹은 것**이
되어 대조가 무의미해진다. 실측 상이행 `f_ct_sens_cat` 43/1675 · coverage
0.4964 → 0.5118, 그리고 `L2단독` 피처에 든 sens 열 7 개가 함께 흔들렸다.

읽기 전용 — `lib_model` 과 넘겨받은 `data` 의 어떤 상태도 바꾸지 않는다.
"""
from __future__ import annotations

import pandas as pd

import lib_model as L


def build_ct_nc유지(data, I: pd.DataFrame) -> pd.DataFrame:
    """`data.build_ct` 와 같은 형태의 CT 표. eye·skin 에서만 `NC` 를 살린다.

    `data` 는 `lib_model.FormulationData`(행 순서 `data.FID`, `_cy_of` 제공),
    `I` 는 성분 표(`data.ING` 와 같은 스키마)다.
    """
    G = I[["Formulation_ID", "cas", "ing_pct_best"]
          + [f"ing_ghs_{ep}" for ep in L.EPS_ALL]
          + [f"ing_ghs_indep_{ep}_{k}" for ep in L.EPS_ALL
             for k in ("cat", "tier")]].copy()
    rows = []
    for fid, g in G.groupby("Formulation_ID", sort=False):
        n = len(g)
        row = {"Formulation_ID": fid}
        for ep in L.EPS_ALL:
            cats, pcts = [], []
            for _, r in g.iterrows():
                base = r[f"ing_ghs_{ep}"]
                base = None if (base is None
                                or (isinstance(base, float) and pd.isna(base))
                                or str(base).lower() in ("nan", "none", "")) \
                    else str(base)
                cat = base
                if cat is None:
                    c, _t = data._cy_of(r, ep)
                    # sens 의 A3 tier 게이트는 그대로 둔다(위 docstring 참조).
                    # eye·skin 에서만 NC 를 살린다 — 그것이 이 복제본의 단 하나의 차이
                    if ep == "sens" and _t != "annex_vi":
                        c = None
                    cat = c
                cats.append(cat)
                pcts.append(r["ing_pct_best"])
            ct = L.ct_predict(list(zip(cats, pcts)), ep)
            for k in ("cat", "s1", "s2", "add", "n_known"):
                row[f"f_ct_{ep}_{k}"] = ct[k]
            row[f"f_ct_{ep}_coverage"] = (ct["n_known"] / n) if n else None
            o = L.SEV_CT[ep].get(ct["cat"]) if ct["cat"] is not None else None
            row[f"f_ct_{ep}_ord"] = o
            row[f"ct_{ep}_ord"] = o
        rows.append(row)
    return pd.DataFrame(rows).set_index("Formulation_ID").reindex(data.FID)
