#!/usr/bin/env python3
"""성분 GHS 조사값 반영 — 산출: v8_성분조사/ING_조사반영_오버레이.csv

`collect_ing_ghs.py` 가 조사한 성분 구분을 하류가 실제로 쓸 수 있게 **행 단위
오버레이**로 굳힌다. 이 파일이 곧 '반영' 이고, 되돌리려면 지우면 된다.

## 왜 오버레이인가 — 원본을 고치지 않는다

`input_dataset_v6.xlsx` 의 `ingredient` 시트는 빌드 산출물이고 `lib_model` 이
`len(ING) == 5287` 로 행수를 못 박아 읽는다(`lib_model.py:261`). 그 파일을 고치면
v6·v7 재현이 깨지고, 무엇을 언제 왜 채웠는지 남지 않는다. 그래서 원본은 그대로 두고
**채울 자리와 값만 따로 적는다**. 하류는 `apply(ING)` 한 줄로 얹는다.

## 어느 칸에 넣는가

`ing_ghs_indep_{ep}_cat`·`_tier`·**`_tier_src`** 에 넣는다. `ing_ghs_{ep}`(성분 MSDS
선언값)에 넣지 않는다 — 이 조사는 외부 규제 DB(CLP Annex VI·ECHA C&L) 유래라서,
MSDS 칸에 섞으면 L2 노선의 정보원 귀속이 무너지고 검수 C1 의 '독립 정답' 층도
오염된다.

`_tier_src` 를 빼먹으면 안 된다. v6 에는 `_tier` 와 `_tier_src` 가 **둘 다** 있고
값이 다르다(실측 eye `_tier` annex_vi 1144 vs `_tier_src` annex_vi 1050).
`A3_strict` arm 은 `_tier_src` 만 본다(`v6_integrated.py` · `measure_ct_tier_
correction.py` · `measure_sens_arm_grid.py`). 여기를 비워 두면 신규값 963 행이 그
arm 에서 **통째로 무효**가 되고, 특히 tier1(법적 구속력 있는 조화분류) 유래 유해
구분만 골라 사라진다 — 검수가 지적한 NC 편향과 같은 방향이다. 조사의 tier 는
`SourceName` 으로 확정한 것이라 교정 대상이 아니므로 두 열에 같은 값을 넣는다.

`sens`(감작)는 채우지 않는다. 조사 자체가 눈·피부만 다뤘고, 감작은 학습 라벨로
쓰지 않는다는 규약이 있다.

## 이 오버레이를 얹은 뒤 `matrices()` 를 부르면 안 된다

`lib_model.FormulationData.matrices()` 는 대표 arm 커버리지를 assert 로 박아 놨다
(`lib_model.py:402-408`, eye 0.330 / skin 0.296 ±0.002). 반영 후 실측은 eye 0.3508 /
skin 0.3156 이므로 **반드시 실패한다**. 기준선이 필요하면 `matrices()` 를 **먼저**
부르고, 그 뒤에는 `build_ct` → `ct_to_X` 를 직접 부른다
(`measure_ing_survey_gain.py`·`measure_a0_fill.py` 가 그렇게 한다). assert 가 터졌다고
`lib_model.py` 의 검증값을 고치는 것은 버전 고정 파일 수정 금지 위반이다.

## 되돌리기

오버레이 파일을 지우면 `apply()` 가 assert 로 멈춘다. 다만 이미 `apply()` 결과로
만든 **파생 산출물은 지워지지 않는다** — 파생물은 각자 다시 돌려야 한다.

## 채우지 않는 자리

1. **이미 값이 있는 칸** — 조사는 결측만 채운다. 덮어쓰지 않는다.
2. **농도가 없는 성분행** — `ct_predict` 는 `pct is None` 만 걸러내고 `NaN` 은
   통과시킨다(`lib_model.py:439`). 농도 `NaN` 행에 구분을 넣으면 `s2` 가 `NaN` 이
   되어 `add` 가 통째로 `NaN` 이 되고 `NaN >= 10` 이 거짓이라 제형 판정이 조용히
   `NC` 로 **내려간다**. 실측: `ct_predict([('2',17.6),('2A',nan)],'eye')` →
   `add=nan, cat='NC'`. 구분을 더했더니 2A 가 NC 가 되는 것이다.
3. **동일성 보류 성분** — CID 동일성이 확인되지 않은 값은 조사 결과에서 이미
   `*_보류값` 으로만 남아 `{ep}_구분` 이 비어 있다. 여기서 따로 막을 것이 없다.

## 검수 결과를 근거로 남긴다

`audit_ing_ghs_survey.py` 가 남긴 중대 결함 4 건은 전부 **NC 편향**이라는 한 방향
신호다(C5 이름경로 · C6 NC편향 눈·피부 · C1 편향). 값을 빼지 않고 오버레이 열에
`동일성등급`·`근거` 를 함께 적어, 하류에서 `동일성등급 != "이름_동의어일치"` 나
`근거 != "NC_tier2_ECHA_CL_무코드"` 로 **골라 뺄 수 있게** 둔다. 성능 측정
(`measure_ing_survey_gain.py`)에서 이름경로 제외가 잡음 범위였으므로 기본은 전체
반영이다.

읽기 전용 입력: input_dataset_v6.xlsx, v8_성분조사/성분GHS_조사결과.csv
"""
from __future__ import annotations

import json

import pandas as pd

import lib_model as L

EPS = ("eye", "skin")
OUT = L.ROOT / "04_모델산출물" / "v8_성분조사"
SURVEY = OUT / "성분GHS_조사결과.csv"
OVERLAY = OUT / "ING_조사반영_오버레이.csv"
제외목록 = OUT / "ING_조사반영_제외_농도결측.csv"
# 조사 tier → v6 의 `_tier`·`_tier_src` 어휘. v6 에 이미 있는 두 값과 맞춘다
TIER_MAP = {"tier1_AnnexVI": "annex_vi", "tier2_ECHA_CL": "echa_cl"}
TIER_OK = set(TIER_MAP.values())
N_ING = 5287                       # v6 ingredient 행수. lib_model 과 같은 못
OK_CAT = {"eye": {"1", "2", "2A", "2B", "NC"},
          "skin": {"1", "1A", "1B", "1C", "2", "3", "NC"}}


def has(s):
    return s.notna() & (~s.astype(str).str.strip().str.lower()
                        .isin(["nan", "none", ""]))


def 키(I):
    return I["ing_cas_best"].where(has(I["ing_cas_best"]),
                                   I["ing_name_best"]).astype(str)


def build(log) -> pd.DataFrame:
    """오버레이를 만든다. ING 을 수정하지 않는다."""
    I = pd.ExcelFile(L.V6).parse("ingredient")
    assert len(I) == N_ING, f"v6 ingredient 행수 이탈: {len(I)}"
    R = pd.read_csv(SURVEY, dtype=str)
    assert "동일성등급" in R.columns, "조사 결과가 구버전이다 — 재조사 필요"
    # `dict(zip(...))` 은 마지막 값이 이긴다. 중복 키가 있으면 어느 판정이 남는지가
    # 파일 순서에 달리므로, 조용히 이기게 두지 않고 막는다
    dup = R.loc[R["키"].duplicated(keep=False), "키"].unique()
    assert not len(dup), f"조사 결과에 중복 키 {len(dup)}개 — 병합 경로 확인: {dup[:5]}"
    k = 키(I)
    pct_ok = has(I["ing_pct_best"])
    rows, 버림행 = [], []
    for ep in EPS:
        s = R[R[f"{ep}_구분"].notna()]
        m_cat = dict(zip(s["키"].astype(str), s[f"{ep}_구분"].astype(str)))
        m_tier = dict(zip(s["키"].astype(str), s[f"{ep}_출처tier"].map(TIER_MAP)))
        m_근거 = dict(zip(s["키"].astype(str), s[f"{ep}_근거"].astype(str)))
        m_등급 = dict(zip(s["키"].astype(str), s["동일성등급"].astype(str)))
        cat = k.map(m_cat)
        빈칸 = ~has(I[f"ing_ghs_indep_{ep}_cat"]) & has(cat)
        버린칸 = 빈칸 & ~pct_ok
        빈칸 &= pct_ok
        버림 = int(버린칸.sum())
        log(f"  {ep:4} 채움 {int(빈칸.sum())} 성분행"
            + (f" · 농도 결측 {버림} 성분행은 제외(가산식이 NaN 으로 오염된다)"
               if 버림 else ""))
        if 버림:
            # 숫자만 로그에 남기면 검수에서 어느 성분인지 되짚을 수 없다
            b = I.loc[버린칸, ["Formulation_ID", "ing_cas_best",
                              "ing_name_best"]].copy()
            b["endpoint"], b["버린_구분"] = ep, cat[버린칸]
            b["사유"] = "농도결측"
            버림행.append(b)
        d = I.loc[빈칸, ["Formulation_ID", "ing_cas_best", "ing_name_best",
                        "ing_pct_best"]].copy()
        d.insert(0, "행번호", d.index)          # v6 시트 위치 인덱스 = 적용 좌표
        d["endpoint"] = ep
        d["키"] = k[빈칸]
        d["구분"] = cat[빈칸]
        d["tier"] = k[빈칸].map(m_tier)
        d["근거"] = k[빈칸].map(m_근거)
        d["동일성등급"] = k[빈칸].map(m_등급)
        rows.append(d)
    O = pd.concat(rows, ignore_index=True)
    검증(O)
    (pd.concat(버림행, ignore_index=True) if 버림행
     else pd.DataFrame(columns=["Formulation_ID", "ing_cas_best",
                                "ing_name_best", "endpoint", "버린_구분", "사유"])
     ).to_csv(제외목록, index=False, encoding="utf-8-sig")
    return O


def 검증(O):
    """오버레이 자체의 정합. `build()` 와 `apply()` **양쪽에서** 부른다.

    앞판은 이 검사를 `build()` 에만 뒀는데, 하류가 실제로 부르는 것은 `apply()` 다.
    Critic 이 오버레이 CSV 를 손으로 고쳐 `apply()` 를 통과시키는 것을 실증했다
    (`구분` 을 `Eye Irrit. 2` 로 바꾸니 그대로 기록되고 `ct_predict` 가 조용히
    탈락시킨다). 생산 시점 검사는 소비 시점 검사가 아니다.
    """
    for ep in EPS:
        d = O[O.endpoint == ep]
        bad = sorted(set(d["구분"].astype(str)) - OK_CAT[ep])
        assert not bad, f"{ep} 허용 어휘 밖 구분 {bad} — 가산식이 조용히 무시한다"
    assert O["tier"].notna().all(), "tier 미매핑 행이 있다 — TIER_MAP 확인"
    bad_t = sorted(set(O["tier"].astype(str)) - TIER_OK)
    assert not bad_t, f"tier 어휘 밖 값 {bad_t} (허용 {sorted(TIER_OK)})"
    assert set(O["endpoint"]) <= set(EPS), \
        f"모르는 endpoint {sorted(set(O['endpoint']) - set(EPS))} — 조용히 무시하지 않는다"
    for ep in EPS:
        assert not O[O.endpoint == ep]["행번호"].duplicated().any(), \
            f"{ep} 행번호 중복 — 한 성분행에 두 값을 넣으려 한다"


def apply(ING: pd.DataFrame, log=None, 필터=None) -> pd.DataFrame:
    """오버레이를 얹은 ING **사본**을 돌려준다. 원본을 수정하지 않는다.

    `필터` 에 콜러블을 주면 오버레이 DataFrame 을 받아 골라낼 수 있다. 예:
    `lambda O: O[O.동일성등급 != "이름_동의어일치"]`.
    """
    assert OVERLAY.exists(), f"오버레이 없음 — `apply_ing_ghs_survey.py` 먼저: {OVERLAY}"
    # `dtype=str` — `구분` 이 `1`·`3` 뿐인 상황이 오면 pandas 가 int 로 읽고
    # `CAT1`·`CAT2` 문자열 멤버십이 조용히 빗나간다. 지금은 `NC` 가 섞여 있어
    # 발생하지 않지만, 발생하지 않는 이유가 데이터 우연이면 막아 두는 게 싸다
    O전체 = pd.read_csv(OVERLAY, dtype={"구분": str, "tier": str})
    검증(O전체)
    O = 필터(O전체) if 필터 is not None else O전체
    J = ING.copy()
    assert len(J) == N_ING, f"ING 행수 이탈: {len(J)}"
    # 좌표를 신뢰하지 않고 키로 다시 맞춘다. 행수만 맞고 순서가 다른 표를 받으면
    # 좌표 적용은 조용히 **다른 성분에** 값을 넣는다 — 그것은 값을 만드는 것이다
    k = 키(J)
    붙임 = 0
    for ep in EPS:
        d = O[O.endpoint == ep]
        나쁜좌표 = d[k.reindex(d["행번호"]).to_numpy() != d["키"].to_numpy()]
        assert not len(나쁜좌표), \
            f"{ep} 오버레이 좌표가 ING 과 어긋난다 {len(나쁜좌표)}행 — 입력 표가 다르다"
        c, t = f"ing_ghs_indep_{ep}_cat", f"ing_ghs_indep_{ep}_tier"
        기존있음 = has(J[c]).reindex(d["행번호"]).to_numpy()
        assert not 기존있음.any(), \
            f"{ep} 이미 값이 있는 칸에 얹으려 한다 {int(기존있음.sum())}행 — 덮지 않는다"
        # **농도를 적용 시점에 다시 본다.** 오버레이 생산 때 걸렀어도, 다른 ING 을
        # 받으면 그 행의 농도가 결측일 수 있다. 그러면 `add` 가 NaN 이 되어 제형
        # 판정이 조용히 NC 로 내려간다 — Critic 실측으로 눈 60 제형이 흔들리고 그중
        # 59 개가 NC 로 떨어졌다. 이 모듈이 스스로 가장 위험하다고 적어 둔 경로다
        농도없음 = (~has(J["ing_pct_best"])).reindex(d["행번호"]).to_numpy()
        assert not 농도없음.any(), \
            f"{ep} 농도 결측 성분행에 얹으려 한다 {int(농도없음.sum())}행 — " \
            f"가산합이 NaN 으로 오염돼 제형 판정이 NC 로 내려간다"
        J.loc[d["행번호"], c] = d["구분"].to_numpy()
        J.loc[d["행번호"], t] = d["tier"].to_numpy()
        # `_tier_src` 도 같이 채운다. 이 열만 보는 arm(A3_strict)이 따로 있다
        J.loc[d["행번호"], f"ing_ghs_indep_{ep}_tier_src"] = d["tier"].to_numpy()
        # 반영 후 ING 안에서 **신규 조사값과 기존 값을 가를 수단**을 남긴다. 이것이
        # 없으면 하류가 검수 결함(이름경로·NC 추론분)을 골라 뺄 수 없다. 기존 열이
        # 아니라 새 열이므로 버전 고정 스크립트의 열 선택에는 영향이 없다
        기원 = f"ing_ghs_indep_{ep}_출처"
        if 기원 not in J.columns:
            J[기원] = None
        J.loc[d["행번호"], 기원] = (
            "v8조사:" + d["동일성등급"].astype(str) + ":" + d["근거"].astype(str)
        ).to_numpy()
        붙임 += len(d)
    if log:
        log(f"오버레이 적용 {붙임} 성분행 (오버레이 전체 {len(O전체)}행"
            + (f", 필터로 {len(O전체) - len(O)}행 제외)" if len(O) != len(O전체)
               else ")"))
    return J


def main():
    log = L.make_logger(OUT / "반영.log")
    log("=== 성분 GHS 조사값 반영 (오버레이 생성) ===")
    O = build(log)
    O.to_csv(OVERLAY, index=False, encoding="utf-8-sig")
    # 적용을 실제로 한 번 해 본다. 만들어만 놓고 안 되는 오버레이를 남기지 않는다
    I = pd.ExcelFile(L.V6).parse("ingredient")
    J = apply(I, log)
    S = {"오버레이행": int(len(O)), "총_성분행": N_ING}
    for ep in EPS:
        d = O[O.endpoint == ep]
        전 = int(has(I[f"ing_ghs_indep_{ep}_cat"]).sum())
        후 = int(has(J[f"ing_ghs_indep_{ep}_cat"]).sum())
        assert 후 - 전 == len(d), f"{ep} 반영 수 불일치 {후-전} != {len(d)}"
        # **대표 arm 에 실제로 닿는 행수를 함께 적는다.** `A2_aug_nc_unknown` 은
        # 증강분이 `NC` 면 버린다(`lib_model.py:346-347`). 채운 행의 대부분이 NC 라
        # `채운_성분행` 만 적으면 커버리지 이득을 몇 배 과대 제시하게 된다
        비NC = int((d["구분"].astype(str) != "NC").sum())
        S[ep] = {"채운_성분행": int(len(d)), "고유_성분": int(d["키"].nunique()),
                 "대표arm_A2에_닿는_행": 비NC,
                 "대표arm_에서_버려지는_NC행": int(len(d)) - 비NC,
                 "indep_보유_성분행": {"전": 전, "후": 후},
                 "구분분포": d["구분"].value_counts().to_dict(),
                 "tier분포": d["tier"].value_counts().to_dict(),
                 "NC_추론분": int((d["근거"] == "NC_tier2_ECHA_CL_무코드").sum()),
                 "이름경로분": int((d["동일성등급"] == "이름_동의어일치").sum())}
        log(f"  {ep:4} indep 보유 성분행 {전} → {후} (+{len(d)}) · "
            f"그중 대표 arm(A2)에 닿는 것 {비NC}행 "
            f"(나머지 {len(d)-비NC}행은 NC 라 버려진다) · "
            f"NC 추론분 {S[ep]['NC_추론분']} · 이름경로 {S[ep]['이름경로분']}")
    with open(OUT / "반영요약.json", "w", encoding="utf-8") as f:
        json.dump({"요약": S, "규약": [
            "`ing_ghs_indep_*` 에만 넣는다. `ing_ghs_*`(MSDS 선언값)은 건드리지 "
            "않는다 — 정보원 귀속과 검수 C1 독립 정답 층이 오염된다.",
            "결측만 채운다. 기존 값은 덮지 않는다(`apply` 가 assert 로 막는다).",
            "농도가 없는 성분행은 채우지 않는다 — 가산식의 `add` 가 NaN 이 되어 "
            "제형 판정이 NC 로 조용히 내려간다.",
            "커버리지 이득을 `채운_성분행 / 5287` 로 읽으면 안 된다. 대표 arm "
            "`A2_aug_nc_unknown` 은 증강분이 `NC` 면 버리므로(`lib_model.py:346-347`) "
            "실제 가산식에 닿는 것은 `대표arm_A2에_닿는_행` 뿐이다. 실측 성분커버리지는 "
            "eye 0.3301 → 0.3508 · skin 0.2960 → 0.3156 으로, 채운 행 비율의 약 1/4 이다 "
            "(`measure_ing_survey_gain.py` 산출).",
            "성능은 잡음 범위였다(`조사반영_판정.csv`). 반영의 근거는 커버리지와 "
            "근거 추적성이지 성능 개선이 아니다.",
            "하류 배제 경로: `동일성등급`(이름경로) · `근거`(NC 추론분).",
        ]}, f, ensure_ascii=False, indent=2, default=str)
    log(f"완료 → {OVERLAY.relative_to(L.ROOT)} · 반영요약.json")


if __name__ == "__main__":
    main()
