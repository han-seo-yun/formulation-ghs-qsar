r"""성분 GHS 조사 결과 검수 — 자동 정합 대조. 산출: v8_성분조사/검수_*.csv

조사(`collect_ing_ghs.py`)가 만든 값을 학습에 반영하기 전에 통과해야 하는 관문이다.
수동 표본 확인을 대신해 **이미 값이 있는 성분과의 대조**로 조사 정확도를 측정한다.

## 검수 항목

**C1. 기존값 대조 — 조사 정확도의 유일한 객관 측정**
조사는 값이 없는 성분을 대상으로 했으므로 결과를 직접 채점할 대상이 없다. 대신 **이미
`ing_ghs_*` 나 `ing_ghs_indep_*_cat` 을 가진 성분을 같은 파이프라인으로 조사**해 기존값과
맞춰 본다. 조사 경로가 기존 데이터를 재현하지 못하면 신규 값도 믿을 수 없다. 일치율과
불일치 방향(과대·과소 분류)을 함께 낸다 — 한쪽으로 치우치면 가산합이 계통 편향된다.

**C2. 값 어휘 정합** 기존 열의 허용 어휘를 벗어난 값이 있으면 하류에서 조용히 무시된다.
`lib_model.CAT1`·`CAT2` 멤버십으로 가산식이 실제로 받아들일지 확인한다.

**C3. 결측 사유 완결성** 모든 결측에 사유가 붙어 있는가. 조회 실패에서 NC 가 유도된
행이 하나라도 있으면 치명 — '값을 만들지 않는다' 위반이다.

**C4. NC 근거 강도** NC 를 tier2 무코드 추론과 명시 미분류로 갈라 센다. 추론분은 ECHA
의 10% 표시 하한 때문에 '유해성이 10% 이하로만 신고됐다' 를 배제하지 못한다.

**C5. CID 매칭 오염** CID 후보가 여럿이거나 키 하나에 이름·CAS 가 여럿 붙은 행을
식별한다. 이름 경로로 찾은 값은 동명이의 위험이 높으므로 따로 센다.

**C6. 교차출처 불일치** 비EU 출처(NITE-CMC·HCIS·HSDB)와 어긋나는 값을 센다. 어긋남
자체는 결함이 아니다 — 이 연구의 규제 기준은 EU CLP 다. 다만 비율이 크면 EU 기준
선택이 결과를 좌우한다는 뜻이므로 공시한다.

**C7. 커버리지 개선 실측** 조사 결과를 가산식 입력에 얹었을 때 성분커버리지와 판정행이
실제로 얼마나 오르는지 잰다. 예상치가 아니라 실측이다.

읽기 전용. 조사 결과와 원본 데이터를 수정하지 않는다.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import lib_model as L
# 판독 경로 자체를 검사하려면(C8) 조사 코드를 그대로 불러 캐시를 다시 읽어야 한다.
# `collect_ing_ghs.log` 는 지연 생성이라 임포트만으로 로그가 잘리지 않는다
import collect_ing_ghs as C

OUT = L.ROOT / "04_모델산출물" / "v8_성분조사"
log = L.make_logger(OUT / "검수.log")
SURVEY = OUT / "성분GHS_조사결과.csv"

# 가산식이 실제로 받아들이는 어휘. 이 밖의 값은 조용히 무시된다.
OK_EYE = {"1", "2", "2A", "2B", "NC"}
OK_SKIN = {"1", "1A", "1B", "1C", "2", "3", "NC"}
OK_NOTE = f"{C.AGREE_MIN:g}%"
SEV = {"eye": {"NC": 0, "2B": 1, "2": 2, "2A": 2, "1": 3},
       "skin": {"NC": 0, "3": 2, "2": 3, "1": 4, "1A": 4, "1B": 4, "1C": 4}}

log("=== 성분 GHS 조사 검수 ===")
assert SURVEY.exists(), f"조사 결과 없음: {SURVEY}"
R = pd.read_csv(SURVEY)
data = L.FormulationData(log)
I = data.ING
FIND = []                      # (항목, 심각도, 내용)


def note(항목, 심각도, 내용):
    FIND.append({"항목": 항목, "심각도": 심각도, "내용": 내용})
    log(f"  [{심각도}] {항목} — {내용}")


def has(s):
    return s.notna() & (~s.astype(str).str.strip().str.lower()
                        .isin(["nan", "none", ""]))


log(f"조사 결과 {len(R)}종 · 조회상태 "
    f"{R['조회상태'].value_counts().to_dict()}")

# ---------------------------------------------------------------- C2 어휘 정합
log("--- C2 값 어휘 정합 ---")
for ep, ok in (("eye", OK_EYE), ("skin", OK_SKIN)):
    v = R.loc[R[f"{ep}_구분"].notna(), f"{ep}_구분"].astype(str)
    bad = sorted(set(v) - ok)
    if bad:
        note("C2 어휘", "치명", f"{ep} 허용 어휘 밖 값 {bad} — 가산식이 무시한다")
    else:
        log(f"  {ep:4} 값 {sorted(set(v))} — 전부 허용 어휘 안")
    # 가산식이 CAT1/CAT2 로 인식하는지 직접 확인
    미인식 = sorted(x for x in set(v)
                 if x != "NC" and x not in L.CAT1 and x not in L.CAT2
                 and not (ep == "skin" and x == "3"))
    if 미인식:
        note("C2 CAT멤버십", "치명",
             f"{ep} 가산식 미인식 값 {미인식} (CAT1={sorted(L.CAT1)} "
             f"CAT2={sorted(L.CAT2)})")

# ------------------------------------------------------- C3 결측 사유 완결성
log("--- C3 결측 사유 완결성 ---")
for ep in ("eye", "skin"):
    무사유 = R[R[f"{ep}_구분"].isna() & ~has(R[f"{ep}_근거"])]
    if len(무사유):
        note("C3 무사유결측", "중대", f"{ep} 결측 {len(무사유)}행에 사유가 없다")
    # 치명 검사 — 조회 실패에서 값이 유도됐는가
    위반 = R[R["조회상태"].astype(str).str.contains("실패")
             & R[f"{ep}_구분"].notna()]
    if len(위반):
        note("C3 실패행_값유도", "치명",
             f"{ep} 조회 실패 {len(위반)}행에 값이 적혔다 — '값을 만들지 않는다' 위반")
    else:
        log(f"  {ep:4} 조회 실패행에서 값 유도 없음")
if not FIND:
    log("  C2·C3 위반 없음")

# ---------------------------------------------------------- C4 NC 근거 강도
log("--- C4 NC 근거 강도 ---")
NC표 = []
for ep in ("eye", "skin"):
    nc = R[R[f"{ep}_구분"] == "NC"]
    g = nc[f"{ep}_근거"].value_counts().to_dict()
    추론 = int(nc[f"{ep}_근거"].eq("NC_tier2_ECHA_CL_무코드").sum())
    명시 = int(nc[f"{ep}_근거"].eq("NC_tier2_명시_미분류").sum())
    # 가장 약한 근거 — 소수 신고가 실제로 존재하는데 과반이 아니라서 NC 로 읽은 것
    미달 = int(nc[f"{ep}_근거"].eq("NC_tier2_과반미달").sum())
    NC표.append({"endpoint": ep, "NC_종": len(nc), "명시_미분류": 명시,
                 "추론_무코드": 추론, "추론_과반미달": 미달,
                 "NC_성분행": int(nc["성분행수"].sum()),
                 "추론_성분행": int(nc.loc[nc[f"{ep}_근거"]
                                    == "NC_tier2_ECHA_CL_무코드",
                                    "성분행수"].sum()),
                 "과반미달_성분행": int(nc.loc[nc[f"{ep}_근거"]
                                      == "NC_tier2_과반미달",
                                      "성분행수"].sum())})
    log(f"  {ep:4} NC {len(nc)}종 = 명시 {명시} + 무코드추론 {추론} + "
        f"과반미달 {미달} · 근거 {g}")
    if 미달:
        note("C4 과반미달NC", "경미",
             f"{ep} NC {미달}종(성분행 {NC표[-1]['과반미달_성분행']})은 유해 신고가 "
             f"실제로 있으나 과반({OK_NOTE})에 못 미쳐 NC 로 읽은 것이다 — 근거가 "
             f"가장 약하다. `NC_tier2_과반미달` 로 표시해 하류에서 뺄 수 있다")
    if len(nc) and 추론 / max(len(nc), 1) > 0.5:
        note("C4 NC추론비중", "경미",
             f"{ep} NC 의 {추론}/{len(nc)} 가 추론이다. ECHA 10% 표시 하한 때문에 "
             f"소수 신고 유해성을 배제하지 못한다 — 하류 배제 가능하게 근거를 남겼다")
pd.DataFrame(NC표).to_csv(OUT / "검수_NC근거.csv", index=False,
                          encoding="utf-8-sig")

# ------------------------------------------------------- C5 CID 매칭 오염
log("--- C5 CID 매칭 오염 ---")
성공 = R[R["조회상태"] == "성공"]
log(f"  성공 {len(성공)}종 · 동일성등급 "
    f"{성공['동일성등급'].value_counts().to_dict()}")
보류 = 성공[has(성공["동일성보류"])]
이름경로 = 성공[성공["동일성등급"] == "이름_동의어일치"]
키충돌 = 성공[(성공["cas_후보수"].fillna(1) > 1)
             | (성공["이름_후보수"].fillna(1) > 1)]
for ep in ("eye", "skin"):
    누출 = 보류[보류[f"{ep}_구분"].notna()]
    if len(누출):
        note("C5 보류_값유출", "치명",
             f"{ep} 동일성 보류 {len(누출)}종에 값이 적혔다 — 보류가 작동하지 않는다")
if len(보류):
    log(f"  보류 {len(보류)}종 / 성분행 {int(보류['성분행수'].sum())} — "
        f"값 대신 `*_보류값` 에만 남았다")
혼합보류 = 성공[성공["동일성보류"].astype(str) == "혼합물명"]
if len(혼합보류):
    log(f"  혼합물명 보류 {len(혼합보류)}종 / 성분행 "
        f"{int(혼합보류['성분행수'].sum())} — UVCB·정유·석유유분·중합체는 CID 하나로 "
        f"대표할 수 없다. CAS 동의어 대조는 이것을 통과시킨다(실측 3 건)")
if len(이름경로):
    # 값을 막지 않는다. 동의어 일치는 필요조건이고 충분조건이 아니므로(클래스명
    # `Glycol ether` 가 CID 8117 의 동의어로 실제 등재돼 통과한다) C1 에서
    # 등급별 정확도를 갈라 재고, 거기서 나쁘면 그때 뺀다.
    note("C5 이름경로", "중대",
         f"{len(이름경로)}종을 CAS 없이 이름으로 찾았다(성분행 "
         f"{int(이름경로['성분행수'].sum())}). 동의어 일치는 통과했지만 클래스명·"
         f"잘린 이름도 동의어로 등재돼 있어 충분조건이 아니다 — C1 등급별 정확도로 "
         f"판단한다")
의심 = pd.concat([보류, 이름경로, 키충돌]).drop_duplicates(subset=["키"])
# `*_보류값` 은 보류가 한 건도 없으면 아예 열이 생기지 않는다 — reindex 로 받는다
의심.reindex(columns=["키", "cas", "이름", "cid", "동일성등급", "동일성보류",
                     "조회질의", "CID후보수", "CID후보전체", "cas_후보수",
                     "이름_후보수", "성분행수", "eye_구분", "eye_보류값",
                     "skin_구분", "skin_보류값", "출처URL"]).to_csv(
    OUT / "검수_CID의심.csv", index=False, encoding="utf-8-sig")

# --------------------------------------------------- C6 교차출처 불일치
log("--- C6 교차출처 불일치 (비EU 출처와의 어긋남) ---")
교차 = []
for ep in ("eye", "skin"):
    mm = R[f"{ep}_교차불일치"]
    대상, 불일치 = int(mm.notna().sum()), int((mm == 1).sum())
    # **채택값의 방향으로 갈라 본다.** 총비율만 보면 어느 쪽으로 갈리는지 알 수
    # 없는데, 방향이 한쪽이면 그건 관할 차이가 아니라 계통 오류의 신호일 수 있다.
    # 특히 `NC` 를 채택했는데 비EU 출처가 유해를 준 건은 성분을 무해로 확정한
    # 것이므로 가산합을 낮추는 방향이다 — 임계값과 무관하게 항상 공시한다.
    d = R[mm == 1]
    nc불 = int((d[f"{ep}_구분"].astype(str) == "NC").sum())
    교차.append({"endpoint": ep, "대상": 대상, "불일치": 불일치,
                "비율": round(불일치 / 대상, 4) if 대상 else None,
                "불일치_채택NC": nc불, "불일치_채택유해": 불일치 - nc불,
                "불일치_채택NC_성분행": int(d.loc[d[f"{ep}_구분"].astype(str)
                                          == "NC", "성분행수"].sum())})
    log(f"  {ep:4} {불일치}/{대상}"
        + (f" ({불일치/대상:.1%})" if 대상 else "")
        + f" · 그중 채택값이 NC 인 것 {nc불} (성분행 "
          f"{교차[-1]['불일치_채택NC_성분행']}) · 채택값이 유해인 것 "
          f"{불일치 - nc불}")
    if 대상 and 불일치 / 대상 > 0.3:
        note("C6 교차불일치", "경미",
             f"{ep} 값의 {불일치/대상:.0%} 가 비EU 출처와 어긋난다. 결함은 아니지만"
             f"(기준은 EU CLP) EU 기준 선택이 결과를 크게 좌우한다는 뜻이다")
    if nc불 and 불일치 and nc불 / 불일치 > 0.5:
        # 임계값 없이 항상 판정한다. 앞판은 0.265 < 0.3 이라 아무 말도 하지
        # 않았는데, 그 안에서 눈 불일치 62 건 중 39 건이 '채택 NC' 였다
        note("C6 NC편향_불일치", "중대",
             f"{ep} 비EU 출처와의 불일치 {불일치}건 중 {nc불}건은 이 조사가 `NC`"
             f"(무해)를 채택한 건이다 — 비EU 출처는 유해라고 한다. EU 관할 선택의 "
             f"결과이긴 하지만 방향이 한쪽이므로 가산합이 계통적으로 낮아진다. "
             f"성분행 {교차[-1]['불일치_채택NC_성분행']}")
pd.DataFrame(교차).to_csv(OUT / "검수_교차출처.csv", index=False,
                          encoding="utf-8-sig")
R[(R["eye_교차불일치"] == 1) | (R["skin_교차불일치"] == 1)][
    ["키", "이름", "cid", "eye_구분", "eye_근거", "eye_참고출처값",
     "skin_구분", "skin_근거", "skin_참고출처값", "성분행수", "출처URL"]
].sort_values("성분행수", ascending=False).to_csv(
    OUT / "검수_교차불일치_목록.csv", index=False, encoding="utf-8-sig")

# --------------------------------------- C8 캐시 원문 재판독 대조
log("--- C8 캐시 원문 재판독 (기록된 값이 원문에서 다시 나오는가) ---")
# **이 검사가 없으면 판독기 결함이 관문을 그냥 통과한다.** 앞선 판은 같은
# `SourceName` 의 여러 정보블록을 `codes.update()` 로 병합해 JSON 순서상 마지막
# 블록이 이기게 만들었다(멘톨 CID 1254 눈 H319 = 82.9% · 13.3% · 43%). 결과 CSV 만
# 보면 어디에도 드러나지 않고, C1~C7 어느 항목도 이것을 잡지 못했다. 캐시된 원문을
# 다시 판독해 기록값과 맞춰야 판독 경로 자체를 검사할 수 있다.
재판독 = []
불일치행 = []
대상 = R[R["cid"].notna() & (R["조회상태"] == "성공")]
for _, r in 대상.iterrows():
    blocks = C.hazard_blocks(int(r["cid"]), False)   # use_net=False — 캐시만
    if not isinstance(blocks, dict):
        재판독.append({"키": r["키"], "결과": "캐시없음"})
        continue
    for ep in ("eye", "skin"):
        cat, 근거, tier, _ = C.decide(ep, blocks)
        기록 = r[f"{ep}_구분"]
        기록 = None if pd.isna(기록) else str(기록)
        # 동일성 보류행은 값이 의도적으로 비어 있다 — 불일치로 세지 않는다
        if has(pd.Series([r.get("동일성보류")]))[0] and cat is not None:
            continue
        if (cat or None) != 기록:
            불일치행.append({"키": r["키"], "cid": r["cid"], "endpoint": ep,
                           "기록값": 기록, "재판독값": cat, "재판독근거": 근거,
                           "캐시파일": r.get("캐시파일")})
n대상 = int(len(대상))
log(f"  대상 {n대상}종 · 재판독 불일치 {len(불일치행)}건")
pd.DataFrame(불일치행).to_csv(OUT / "검수_캐시재판독불일치.csv", index=False,
                            encoding="utf-8-sig")
if 불일치행:
    note("C8 재판독불일치", "치명",
         f"캐시 원문을 다시 판독하니 {len(불일치행)}건이 기록값과 다르다 — 판독이 "
         f"결정적(deterministic)이지 않다. `검수_캐시재판독불일치.csv` 참조")
else:
    log("  기록값 전부가 캐시 원문에서 같게 재현된다")
if not n대상:
    note("C8 대상없음", "중대", "캐시 재판독 대상이 없다 — 원문 검증이 비어 있다")

# ------------------------------------------- C7 커버리지 개선 실측
log("--- C7 커버리지 개선 실측 ---")
# 조사값을 성분 테이블에 매핑한다. 원본을 수정하지 않고 사본에서만 계산한다.
J = I.copy()
J["키"] = J["ing_cas_best"].where(has(J["ing_cas_best"]), J["ing_name_best"])
M = {ep: dict(zip(R["키"].astype(str),
                  R[f"{ep}_구분"].where(R[f"{ep}_구분"].notna())))
     for ep in ("eye", "skin")}
pct_ok = has(J["ing_pct_best"])
행 = []
for ep in ("eye", "skin"):
    기존 = has(J[f"ing_ghs_{ep}"]) | has(J[f"ing_ghs_indep_{ep}_cat"])
    신규 = (J["키"].astype(str).map(M[ep]).notna()) & ~기존
    for 이름, 구분있음 in (("조사 전", 기존), ("조사 후", 기존 | 신규)):
        쓸수있음 = 구분있음 & pct_ok
        cov = (쓸수있음.groupby(J["Formulation_ID"]).mean())
        행.append({"endpoint": ep, "시점": 이름,
                   "가산식_사용가능_성분행": int(쓸수있음.sum()),
                   "평균_성분커버리지": round(float(cov.mean()), 4),
                   "제형_커버리지0.75이상": int((cov >= 0.75).sum()),
                   "제형_커버리지0.5이상": int((cov >= 0.50).sum()),
                   "제형_커버리지0": int((cov == 0).sum())})
    추가 = int((신규 & pct_ok).sum())
    log(f"  {ep:4} 신규로 가산식에 들어가는 성분행 +{추가}")
C = pd.DataFrame(행)
C.to_csv(OUT / "검수_커버리지개선.csv", index=False, encoding="utf-8-sig")
for ep in ("eye", "skin"):
    a = C[(C.endpoint == ep) & (C.시점 == "조사 전")].iloc[0]
    b = C[(C.endpoint == ep) & (C.시점 == "조사 후")].iloc[0]
    log(f"  {ep:4} 평균 성분커버리지 {a['평균_성분커버리지']:.4f} → "
        f"{b['평균_성분커버리지']:.4f} · "
        f"커버리지≥0.75 제형 {a['제형_커버리지0.75이상']} → "
        f"{b['제형_커버리지0.75이상']} · "
        f"커버리지=0 제형 {a['제형_커버리지0']} → {b['제형_커버리지0']}")
    if b["평균_성분커버리지"] <= a["평균_성분커버리지"]:
        note("C7 개선없음", "중대",
             f"{ep} 성분커버리지가 오르지 않았다 — 조사값이 매핑되지 않는다")

# ------------------------------------------------ C1 기존값 대조 (정확도)
log("--- C1 기존값 대조 (조사 경로가 기존 데이터를 재현하는가) ---")
# 조사 결과에는 기존값 보유 성분이 없다. 대조는 별도 스크립트가 조사한 결과를
# 이 파일이 있을 때만 수행한다. 없으면 '미측정' 으로 명시한다.
BENCH = OUT / "성분GHS_대조조사.csv"
대조 = []
if not BENCH.exists():
    note("C1 미측정", "중대",
         f"기존값 대조 미실행 — `collect_ing_ghs_bench.py` 로 "
         f"{BENCH.name} 를 만들어야 조사 정확도를 수치로 말할 수 있다")
else:
    B = pd.read_csv(BENCH)

    def 채점(d, ep, 층, 검정):
        """심각도 일치·과대·과소를 센다. 층별로 갈라 부르면 층 비교가 된다."""
        s_new = d[f"{ep}_구분"].astype(str).map(SEV[ep])
        s_old = d[f"기존_{ep}"].astype(str).map(SEV[ep])
        일치 = int((s_new == s_old).sum())
        return {"endpoint": ep, "층": 층, "검정성격": 검정, "n": len(d),
                "심각도일치": 일치, "일치율": round(일치 / len(d), 4),
                "조사가_과대": int((s_new > s_old).sum()),
                "조사가_과소": int((s_new < s_old).sum()),
                "정확일치": int((d[f"{ep}_구분"].astype(str)
                             == d[f"기존_{ep}"].astype(str)).sum())}

    # **정답의 출처로 먼저 가른다.** `독립조사유래`(`ing_ghs_indep_*_cat`)는
    # annex_vi·echa_cl 에서 온 값이라 이 조사와 출처가 같다 — 그 대조는 정확도
    # 검정이 아니라 같은 코드가 같은 출처를 같게 읽는지 보는 **순환 재현성 확인**
    # 이다. 정확도 근거로 인용할 수 있는 것은 `MSDS유래` 층뿐이다.
    for ep in ("eye", "skin"):
        d0 = B[B[f"{ep}_구분"].notna() & B[f"기존_{ep}"].notna()].copy()
        if not len(d0):
            continue
        # **결함 정답을 먼저 뺀다.** `annex_vi` tier 의 `NC` 는 Annex VI 항목에
        # 해당 엔드포인트가 없는 것을 `NC` 로 읽던 앞판 결함의 산물이다(Annex VI 는
        # 부분 조화 목록이라 부재가 무해를 뜻하지 않는다). 이것과 일치하는 것은
        # 재현성이 아니라 같은 오류를 되풀이하는 것이고, 반대로 지금 코드가 옳게
        # 고친 값은 '과대 편향' 으로 잘못 채점된다.
        tcol = f"기존_{ep}_tier"
        if tcol in d0.columns:
            결함 = (d0[tcol].astype(str).str.contains("annex_vi", na=False)
                  & (d0[f"기존_{ep}"].astype(str) == "NC"))
            if 결함.any():
                대조.append(채점(d0[결함], ep, "결함정답_annexvi_NC",
                              "채점 제외 — 정답이 틀렸다"))
                log(f"  {ep:4} [결함정답 제외] annex_vi 유래 NC {int(결함.sum())}종 "
                    f"— 앞판 결함의 산물이라 정답으로 쓰지 않는다")
                d0 = d0[~결함].copy()
            if not len(d0):
                note("C1 정답소진", "치명",
                     f"{ep} 결함 정답을 빼니 대조 대상이 남지 않는다 — 정확도를 "
                     f"말할 근거가 없다")
                continue
        else:
            note("C1 정답tier없음", "중대",
                 f"{ep} 대조군에 `{tcol}` 이 없다 — annex_vi 유래 결함 NC 를 "
                 f"갈라낼 수 없어 일치율이 과대·과소 어느 쪽으로든 오염된다")
        대조.append(채점(d0, ep, "전체", "혼합 — 인용 금지"))
        for 출처, dd in d0.groupby(d0[f"기존_{ep}_출처"].astype(str)):
            검정 = "독립 정확도 검정" if 출처 == "MSDS유래" else "순환 — 재현성만"
            row = 채점(dd, ep, 출처, 검정)
            대조.append(row)
            log(f"  {ep:4} [{출처}] n={len(dd)} 일치율 {row['일치율']:.1%} "
                f"· 과대 {row['조사가_과대']} · 과소 {row['조사가_과소']} "
                f"({검정})")
            # 등급별 분리 — 이름 경로 값을 학습에 넣을지 정하는 근거
            for 등급, g in dd.groupby(dd["동일성등급"].astype(str)):
                # **n 이 작아도 건너뛰지 않고 기록한다.** 앞판은 `len(g) < 10` 이면
                # `continue` 했는데, 이름 경로는 기존값 보유 성분 자체가 12 종뿐이라
                # (층화 추출로도 늘 수 없다 — 모집단이 그만큼이다) 이름 경로 정확도를
                # 재려고 만든 대조군이 그것만 조용히 빼먹었다. 표본이 부족하면 부족
                # 하다고 적는 것이 침묵보다 정확하다.
                r2 = 채점(g, ep, f"{출처}·{등급}", 검정)
                r2["표본충분"] = int(len(g) >= 10)
                대조.append(r2)
                log(f"    └ {등급:14} n={len(g)} 일치율 {r2['일치율']:.1%}"
                    + ("" if len(g) >= 10 else "  ← 표본 부족, 인용 금지"))
                if len(g) < 10:
                    continue
                if (출처 == "MSDS유래" and 등급 == "이름_동의어일치"
                        and r2["일치율"] < row["일치율"] - 0.15):
                    note("C1 이름경로_열등", "중대",
                         f"{ep} 이름 경로 일치율 {r2['일치율']:.0%} 가 같은 층 전체 "
                         f"{row['일치율']:.0%} 보다 15%p 이상 낮다 — 이름 경로 값을 "
                         f"학습에서 빼야 한다(`동일성등급` 으로 걸러낸다)")
            if 출처 != "MSDS유래":
                if row["일치율"] < 0.9:
                    note("C1 순환재현성", "치명",
                         f"{ep} 같은 출처(annex_vi·echa_cl)를 읽었는데 기존 "
                         f"`indep` 값과 {row['일치율']:.0%} 만 일치한다 — 두 판독 "
                         f"가운데 하나가 틀렸다. 정확도가 아니라 판독 결함 신호다")
                continue
            일치, 과대, 과소 = (row["심각도일치"], row["조사가_과대"],
                            row["조사가_과소"])
            if row["일치율"] < 0.7:
                note("C1 재현율", "치명",
                     f"{ep} 조사 경로가 MSDS 유래 기존값을 {row['일치율']:.0%} 만 "
                     f"재현한다(n={len(dd)}) — 신규 값도 신뢰할 수 없다")
            if max(과대, 과소) > 3 * max(min(과대, 과소), 1):
                note("C1 편향", "중대",
                     f"{ep} 불일치가 한쪽으로 치우쳤다(과대 {과대} vs 과소 "
                     f"{과소}) — 가산합이 계통 편향된다")
        if not (d0[f"기존_{ep}_출처"] == "MSDS유래").any():
            note("C1 독립검정없음", "치명",
                 f"{ep} 대조군에 MSDS 유래 정답이 없다 — 순환 대조뿐이라 정확도를 "
                 f"말할 수 없다")
    pd.DataFrame(대조).to_csv(OUT / "검수_기존값대조.csv", index=False,
                             encoding="utf-8-sig")

# ---------------------------------------------------------------- 종합
log("--- 종합 ---")
F = pd.DataFrame(FIND) if FIND else pd.DataFrame(
    columns=["항목", "심각도", "내용"])
F.to_csv(OUT / "검수_판정.csv", index=False, encoding="utf-8-sig")
치명 = int((F["심각도"] == "치명").sum()) if len(F) else 0
중대 = int((F["심각도"] == "중대").sum()) if len(F) else 0
판정 = "통과" if 치명 == 0 and 중대 == 0 else (
    "차단 — 치명 결함" if 치명 else "조건부 — 중대 결함 해소 필요")
log(f"치명 {치명} · 중대 {중대} · 경미 "
    f"{int((F['심각도'] == '경미').sum()) if len(F) else 0} → **{판정}**")
with open(OUT / "검수요약.json", "w", encoding="utf-8") as f:
    json.dump({"판정": 판정, "치명": 치명, "중대": 중대,
               "발견": FIND, "NC근거": NC표, "교차출처": 교차,
               "커버리지개선": C.to_dict("records"),
               "기존값대조": 대조 or "미측정",
               "해석주의": [
                   "C1 이 미측정이면 조사 정확도에 대한 객관 근거가 없다. "
                   "커버리지가 올랐다는 것은 값이 많아졌다는 뜻일 뿐 값이 맞다는 "
                   "뜻이 아니다.",
                   "C6 교차불일치는 결함이 아니다 — 이 연구의 기준은 EU CLP 이고 "
                   "NITE-CMC·HCIS 는 다른 관할이다. 비율만 공시한다.",
                   "C7 은 가산식 입력 가능 여부만 잰다. 커버리지 상승이 성능 상승을 "
                   "보장하지 않는다 — 누출보정 AUC 는 층 내부 순위 지표이고 현재 어느 "
                   "층도 0.70 에 닿지 않는다.",
               ]}, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT.relative_to(L.ROOT)}/검수_판정.csv · 검수요약.json")
