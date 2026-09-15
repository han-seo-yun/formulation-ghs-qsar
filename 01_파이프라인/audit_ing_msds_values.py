#!/usr/bin/env python3
"""기존 성분 MSDS 선언값 검수 — 산출: v8_성분조사/검수_MSDS값_*.csv

`ing_ghs_{ep}`(성분 MSDS 선언값)은 이 연구 L2 노선의 **1차 정보원**이고, 조사 검수
(`audit_ing_ghs_survey.py` C1)에서 조사 정확도를 재는 **정답**으로도 써 왔다. 그런데
그 칸 자체는 한 번도 검증된 적이 없다. 정답이 틀렸으면 조사 정확도 91.6% 라는 숫자도
뜻이 없다. 이 스크립트가 그 칸을 검사한다.

## 값을 고치지 않는다

MSDS 선언값은 팀원이 원본 문서에서 추출한 값이다. 규제 DB 와 어긋난다는 것이 곧
'MSDS 가 틀렸다' 는 뜻은 아니다 — 공급자 자기분류가 조화분류와 다른 것은 흔하고,
관할과 시점도 다르다. 그래서 이 검수는 **불일치 목록과 방향만 낸다**. 어떤 값도
덮지 않고, 어떤 값도 만들지 않는다.

## 검수 항목

**M1. 대조 커버리지** MSDS 값을 가진 고유 물질 중 규제 DB 로 대조된 비율. 낮으면
아래 수치 전부가 일부만 본 것이다.

**M2. 규제 DB 와의 불일치와 방향** 심각도 순서로 견준다. 방향이 한쪽으로 쏠리면
가산합이 계통 편향된다. 성분행 수로 가중해 하류 영향 크기도 낸다.

**M3. 조화분류 위반 — 가장 강한 결함 신호** tier1(CLP Annex VI)은 법적 구속력이 있는
**조화분류**다. 여기에 등재된 물질의 구분은 EU 안에서 공급자가 임의로 낮출 수 없다.
MSDS 선언값이 Annex VI 보다 **낮으면** 그것은 관할 차이로 설명되지 않는다. 반대로
높은 것은 설명된다(공급자가 더 엄격하게 분류할 수 있다). 그래서 **낮은 쪽만** 결함
후보로 센다.

**M4. 같은 물질 안의 자기모순** 같은 물질(CAS·이름 키)이 여러 제형에 걸쳐 **서로 다른
구분**으로 선언돼 있는가. 이것은 외부 대조가 필요 없는 내부 증거다 — 같은 물질의
고유 위험이 문서에 따라 달리 적혔다는 뜻이고, 라벨이 아니라 **피처 잡음**의 크기다.

**M5. 어휘 위반** 가산식이 실제로 받는 어휘 밖 값. `lib_model.CAT1`·`CAT2` 멤버십으로
확인한다. 눈의 맨 `2`(2A/2B 구분 없음)는 규약 §5 미결 항목이라 따로 센다.

**M6. 결측 구조** 어느 칸이 얼마나 비었는가. 농도까지 함께 봐야 가산식 천장이 나온다.

**M7. `indep` 칸의 `annex_vi` + `NC` 조합** MSDS 칸이 아니라 **독립조사 칸**을 본다.
v6 이 채운 부분은 한 번도 검사되지 않았다. `tier` 가 `annex_vi` 인데 구분이 `NC` 인 행은 **Annex VI 부재를
무해로 읽은 것**의 서명이다 — `collect_ing_ghs.decide()` 가 반증까지 붙여 금지하는
추론이다(Annex VI 는 부분 조화 목록이라 부재가 무해를 뜻하지 않는다). `echa_cl`
tier 에는 이 조합이 없어야 정상이 아니라, **있어야** 정상이다(신고자가 전 엔드포인트를
올리므로 `NC` 를 유도할 수 있다). 두 tier 의 `NC` 분포 대조가 곧 판정이다.

## 심각도 순서와 하위구분 미지정 규칙

심각도는 `lib_model.SEV_PICK` 정본을 그대로 쓴다. 감사 스크립트가 자기 순서를 몰래
도입하면 그 산출물은 파이프라인에 대한 증거가 못 된다.

정본에서 피부 `1B`·`1C` 는 3, `1A`·`1` 은 4 다. 그래서 MSDS `1B` vs 규제 `1` 이 그냥
견주면 `MSDS가_낮음` 으로 잡힌다. 그런데 맨 `1` 은 하위구분을 **말하지 않는 표기**이고
(Annex VI 의 H314 가 1A/1B/1C 를 구별하지 못한다) MSDS 가 `1B` 로 더 구체적인 것이
과소선언의 증거는 아니다. 이 쌍은 `구분granularity차이` 로 따로 세고 방향 판정에서
뺀다(눈은 맨 `2` vs `2A`/`2B` 가 같은 관계다). 둘 다 하위구분이 명시된 쌍
(`1A` vs `1B`)은 이 규칙에 걸리지 않으므로 진짜 과소선언은 그대로 잡힌다.

읽기 전용. 어떤 파일도 수정하지 않는다.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import lib_model as L

EPS = ("eye", "skin")
OUT = L.ROOT / "04_모델산출물" / "v8_성분조사"
BENCH = OUT / "성분GHS_대조조사.csv"
log = L.make_logger(OUT / "검수_MSDS값.log")

# **파이프라인 정본을 그대로 쓴다.** 앞판은 skin 의 `1B`·`1C` 를 `1A`·`1` 과 같은 4 로
# 평탄화했고(정본은 `1C`=3, `1B`=3, `1A`=4, `1`=4), 그 비공개 변경이 skin 일치율
# 100.0% 와 M4 자기모순 0 종을 만들었다. 감사 스크립트가 자기 심각도 순서를 몰래
# 도입하면 그 산출물은 파이프라인에 대한 증거가 아니다.
SEV = {ep: dict(L.SEV_PICK[ep]) for ep in ("eye", "skin")}

# --- 하위구분 미지정 규칙 -----------------------------------------------------
# 맨 `1` 은 `1A/1B/1C` 중 어느 것인지 **말하지 않는 표기**다. CLP Annex VI 의 H314 는
# 하위구분을 구별하지 못하므로 규제 DB 쪽 값이 맨 `1` 로 내려오는 일이 흔하고,
# MSDS 가 `1B` 로 더 구체적인 것은 과소선언의 증거가 아니다. 정본 순서로는 `1B`(3)
# < `1`(4) 이라 그냥 견주면 이 쌍이 `MSDS가_낮음` 으로 잡힌다.
#
# 그래서 **방향 판정에서 빼고 `구분granularity차이` 로 따로 센다.** 평탄화와 결과가
# 겹치는 경우도 있지만 뜻이 다르다 — 평탄화는 진짜 `1A` vs `1B/1C` 과소선언까지
# 영원히 못 보고, 이 규칙은 그것을 잡는다(둘 다 하위구분이 명시된 쌍이므로).
GRAN = {"eye": ({"2"}, {"2A", "2B"}), "skin": ({"1"}, {"1A", "1B", "1C"})}


def 방향(ep, msds, 규제):
    """`동일` / `MSDS가_낮음` / `MSDS가_높음` / `구분granularity차이` / `None`."""
    a, b = SEV[ep].get(msds), SEV[ep].get(규제)
    if a is None or b is None:
        return None
    막, 세 = GRAN[ep]
    if (msds in 막 and 규제 in 세) or (규제 in 막 and msds in 세):
        return "구분granularity차이"
    return "동일" if a == b else "MSDS가_낮음" if a < b else "MSDS가_높음"


# M5 의 허용 어휘. **`SEV` 와 범위가 일부러 다르다.** `SEV_PICK["skin"]` 에는 `2A`·`2B`
# 가 있지만(라벨 판독 쪽에서 오는 표기를 받으려고) 피부 GHS 에 `2A`/`2B` 하위구분은
# 없으므로 성분 선언값에 그것이 나오면 어휘 위반으로 잡아야 한다. 즉 `SEV` 쪽 두
# 항목은 이 감사에서는 닿지 않는 사문(dead letter)이고, 그것이 의도다 — 순서표를
# 정본으로 쓰는 것과 허용 어휘를 좁게 두는 것은 별개 판단이다
OK = {"eye": {"1", "2", "2A", "2B", "NC"},
      "skin": {"1", "1A", "1B", "1C", "2", "3", "NC"}}
FIND = []


def note(항목, 심각도, 내용):
    FIND.append({"항목": 항목, "심각도": 심각도, "내용": 내용})
    log(f"  [{심각도}] {항목} — {내용}")


def has(s):
    return s.notna() & (~s.astype(str).str.strip().str.lower()
                        .isin(["nan", "none", ""]))


def norm(s):
    """CSV 왕복에서 `1`·`3` 이 int 로 읽히는 것을 문자열로 되돌린다."""
    t = s.astype(str).str.strip()
    return t.str.replace(r"\.0$", "", regex=True).where(has(s))


log("=== 기존 성분 MSDS 선언값 검수 ===")
I = pd.ExcelFile(L.V6).parse("ingredient")
assert len(I) == 5287, f"v6 ingredient 행수 이탈: {len(I)}"
I["키"] = I["ing_cas_best"].where(has(I["ing_cas_best"]),
                                  I["ing_name_best"]).astype(str)
pct_ok = has(I["ing_pct_best"])

# ------------------------------------------------------------- M6 결측 구조
log("--- M6 결측 구조 ---")
구조 = []
for ep in EPS:
    a = has(I[f"ing_ghs_{ep}"])
    b = has(I[f"ing_ghs_indep_{ep}_cat"])
    구조.append({"endpoint": ep, "총_성분행": len(I),
                 "MSDS선언값": int(a.sum()), "독립조사값": int(b.sum()),
                 "둘다없음": int((~a & ~b).sum()),
                 "농도있음": int(pct_ok.sum()),
                 "MSDS값_그리고_농도": int((a & pct_ok).sum()),
                 "가산식_가용": int(((a | b) & pct_ok).sum())})
    log(f"  {ep:4} MSDS 선언값 {int(a.sum())} · 독립조사값 {int(b.sum())} · "
        f"둘 다 없음 {int((~a & ~b).sum())} / {len(I)} 성분행 · "
        f"가산식 가용 {구조[-1]['가산식_가용']}")
pd.DataFrame(구조).to_csv(OUT / "검수_MSDS값_결측구조.csv", index=False,
                         encoding="utf-8-sig")

# ------------------------------------------ M7 indep 칸의 annex_vi + NC 조합
log("--- M7 독립조사 칸(`indep`)의 `annex_vi` + `NC` ---")
indep = []
# 이번 조사가 얹은 행은 빼고 **v6 이 원래 채운 것만** 본다. 조사분은 이미
# `audit_ing_ghs_survey.py` 가 검사했고, 섞으면 어느 층의 결함인지 흐려진다.
# **현 산출에서는 무연산이다** — `I` 는 오버레이 적용 전 원본 v6 이고 오버레이는
# `indep` 결측 행만 채우므로 그 행들은 아래 `has(c)` 가 이미 거짓이다(`v6기존_보유행`
# 이 두 tier 열 모두 1355 로 같은 것이 그 증거). 방어로 남긴다
try:
    OVL = pd.read_csv(L.ROOT / "04_모델산출물" / "v8_성분조사"
                      / "ING_조사반영_오버레이.csv")
except FileNotFoundError:
    OVL = pd.DataFrame(columns=["행번호", "endpoint"])
for ep in EPS:
    c = norm(I[f"ing_ghs_indep_{ep}_cat"])
    신규 = I.index.isin(OVL.loc[OVL.get("endpoint", pd.Series(dtype=str)) == ep,
                              "행번호"])
    for 열 in (f"ing_ghs_indep_{ep}_tier", f"ing_ghs_indep_{ep}_tier_src"):
        t = norm(I[열])
        보유 = has(c) & has(t) & ~신규
        av_nc = int((보유 & (t == "annex_vi") & (c == "NC")).sum())
        av = int((보유 & (t == "annex_vi")).sum())
        cl_nc = int((보유 & (t == "echa_cl") & (c == "NC")).sum())
        cl = int((보유 & (t == "echa_cl")).sum())
        indep.append({"endpoint": ep, "tier열": 열.split("_")[-1],
                      "v6기존_보유행": int(보유.sum()),
                      "annex_vi": av, "annex_vi_그리고_NC": av_nc,
                      "echa_cl": cl, "echa_cl_그리고_NC": cl_nc})
        log(f"  {ep:4} {열.split('_')[-1]:8} annex_vi {av} 중 NC {av_nc} · "
            f"echa_cl {cl} 중 NC {cl_nc}")
    # 두 tier 열을 한 번에 판정한다 — 같은 결함의 두 관측면이므로 발견을 두 번 세지
    # 않는다. `_tier` 는 `a0_fill` 이 읽고 `_tier_src` 는 `A3_strict` arm 이 읽는다
    r1, r2 = indep[-2], indep[-1]
    if r1["annex_vi"] and r1["annex_vi_그리고_NC"] / r1["annex_vi"] > 0.3:
        note("M7 indep_부재를NC", "중대",
             f"{ep} 독립조사 칸의 v6 기존 행 중 tier 가 `annex_vi` 인데 구분이 "
             f"`NC` 인 것: `_tier` {r1['annex_vi_그리고_NC']}/{r1['annex_vi']}"
             f"({r1['annex_vi_그리고_NC']/r1['annex_vi']:.0%}) · `_tier_src` "
             f"{r2['annex_vi_그리고_NC']}/{r2['annex_vi']}. Annex VI 는 부분 조화 "
             f"목록이라 부재가 무해를 뜻하지 않으므로 이 조합은 **부재를 NC 로 읽은 "
             f"것**의 서명이다(`collect_ing_ghs.decide()` 가 반증까지 붙여 금지하는 "
             f"추론). 두 열이 다른 것도 함께 남는다 — `_tier` 의 `echa_cl` 에는 "
             f"`NC` 가 {r1['echa_cl_그리고_NC']}건뿐인데 `_tier_src` 에는 "
             f"{r2['echa_cl_그리고_NC']}건이다. 영향: 조사 검수 C1 은 이 층을 "
             f"`순환 대조` 로 표시해 정확도 산정에서 이미 배제하므로"
             f"(`audit_ing_ghs_survey.py:330, 407-409`) 그쪽 영향은 없다. 실질 "
             f"영향은 ① `A0채움_tier표기_annexvi` arm — 채움의 대부분이 이 행들이라 "
             f"그 arm 을 '근거가 가장 강한 것' 으로 읽으면 안 된다 ② 이 칸의 `NC` 를 "
             f"'조화분류가 확정한 무해' 로 인용하는 모든 자리다")
pd.DataFrame(indep).to_csv(OUT / "검수_MSDS값_indep_tierNC.csv", index=False,
                           encoding="utf-8-sig")

# ------------------------------------------------------------- M5 어휘 위반
log("--- M5 어휘 위반 (가산식이 실제로 받는가) ---")
어휘 = []
for ep in EPS:
    v = norm(I[f"ing_ghs_{ep}"]).dropna()
    밖 = sorted(set(v) - OK[ep])
    미인식 = sorted(x for x in set(v) if x != "NC" and x not in L.CAT1
                 and x not in L.CAT2 and not (ep == "skin" and x == "3"))
    맨2 = int((v == "2").sum()) if ep == "eye" else None
    어휘.append({"endpoint": ep, "고유값": sorted(set(v)), "어휘밖": 밖,
                 "가산식_미인식": 미인식, "eye_맨2_성분행": 맨2})
    log(f"  {ep:4} 고유값 {sorted(set(v))}")
    if 밖:
        note("M5 어휘밖", "치명",
             f"{ep} MSDS 선언값에 허용 어휘 밖 값 {밖} — 하류가 조용히 무시한다")
    if 미인식:
        note("M5 CAT미인식", "치명",
             f"{ep} 가산식이 못 알아보는 값 {미인식} (CAT1={sorted(L.CAT1)} "
             f"CAT2={sorted(L.CAT2)})")
    if 맨2:
        note("M5 eye맨2", "경미",
             f"eye 맨 `2` {맨2} 성분행 — 2A/2B 구분이 없다. `CAT2` 에는 들어가므로 "
             f"가산식은 받지만, 관할 스위치(2B 를 분류로 보는가)가 적용되지 않는다. "
             f"규약 §5 미결 항목이다")

# ------------------------------------------------- M4 같은 물질 안의 자기모순
log("--- M4 같은 물질 안의 자기모순 (외부 대조 없이 나오는 증거) ---")
모순행 = []
모순 = []
for ep in EPS:
    d = I[has(I[f"ing_ghs_{ep}"])].copy()
    d["값"] = norm(d[f"ing_ghs_{ep}"])
    g = d.groupby("키")["값"].agg(["nunique", "size", lambda s: sorted(set(s))])
    g.columns = ["고유값수", "성분행수", "값들"]
    나쁜 = g[g["고유값수"] > 1].sort_values("성분행수", ascending=False)
    # 심각도까지 달라지는 것만 진짜 모순으로 본다. `2` vs `2A`(눈)·`1` vs `1B`(피부)
    # 는 하위구분 미지정 차이라 `GRAN` 규칙으로 빼고 따로 센다
    sev나쁜, gran나쁜 = [], []
    for 키, r in 나쁜.iterrows():
        s = {SEV[ep].get(x) for x in r["값들"]}
        if len(s - {None}) <= 1:
            continue
        # 값 쌍 전부가 하위구분 미지정 차이면 모순이 아니다
        쌍판정 = {방향(ep, x, y) for x in r["값들"] for y in r["값들"] if x != y}
        if 쌍판정 - {"동일", "구분granularity차이", None} == set():
            gran나쁜.append(키)
            continue
        sev나쁜.append(키)
        모순행.append({"endpoint": ep, "키": 키, "값들": "|".join(r["값들"]),
                      "성분행수": int(r["성분행수"]),
                      "심각도들": "|".join(str(SEV[ep].get(x))
                                       for x in r["값들"])})
    행수 = int(나쁜["성분행수"].sum())
    sev행수 = int(나쁜.loc[sev나쁜, "성분행수"].sum()) if sev나쁜 else 0
    gran행수 = int(나쁜.loc[gran나쁜, "성분행수"].sum()) if gran나쁜 else 0
    모순.append({"endpoint": ep, "값보유_고유물질": int(len(g)),
                 "표기까지_불일치_물질": int(len(나쁜)), "표기불일치_성분행": 행수,
                 "심각도_불일치_물질": len(sev나쁜), "심각도불일치_성분행": sev행수,
                 "구분granularity차이_물질": len(gran나쁜),
                 "구분granularity차이_성분행": gran행수,
                 "심각도_불일치율": round(len(sev나쁜) / len(g), 4) if len(g) else None})
    log(f"  {ep:4} 값 보유 고유물질 {len(g)}종 중 같은 물질이 서로 다르게 선언된 것 "
        f"{len(나쁜)}종(성분행 {행수}) · 그중 **심각도까지 다른** 것 "
        f"{len(sev나쁜)}종(성분행 {sev행수}) · 하위구분 미지정 차이 "
        f"{len(gran나쁜)}종(성분행 {gran행수})")
    if len(g) and len(sev나쁜) / len(g) > 0.05:
        note("M4 자기모순", "중대",
             f"{ep} MSDS 선언값 보유 물질의 {len(sev나쁜)/len(g):.1%}"
             f"({len(sev나쁜)}/{len(g)}종, 성분행 {sev행수})가 같은 물질인데 제형에 "
             f"따라 **심각도가 다르게** 선언돼 있다. 물질 고유 위험이 문서마다 다를 "
             f"수 없으므로 이것은 문서 추출 잡음이다 — 가산식 입력의 잡음 하한이다")
def 저장(rows, 열, 파일, 정렬):
    """빈 결과도 **열 이름이 있는 빈 파일**로 남긴다. 헤더 없는 0 바이트 파일은
    '검사를 안 했다' 와 '걸린 게 없다' 를 구별하지 못한다"""
    D = pd.DataFrame(rows) if rows else pd.DataFrame(columns=열)
    if rows:
        # 마지막 키만 내림차순. 키가 하나면 그 하나가 내림차순이다 — 앞판은
        # `[True]*0 + [False]` 가 아니라 길이가 어긋나 단일 키에서 터졌다
        D = D.sort_values(정렬, ascending=[True] * max(len(정렬) - 1, 0)
                          + ([False] if 정렬 else []))
    D.to_csv(OUT / 파일, index=False, encoding="utf-8-sig")


저장(모순행, ["endpoint", "키", "값들", "성분행수", "심각도들"],
    "검수_MSDS값_자기모순.csv", ["endpoint", "성분행수"])
pd.DataFrame(모순).to_csv(OUT / "검수_MSDS값_자기모순요약.csv", index=False,
                         encoding="utf-8-sig")

# ------------------------------------ M1·M2·M3 규제 DB 대조
log("--- M1 대조 커버리지 · M2 불일치 방향 · M3 조화분류 위반 ---")
대조표, 위반행 = [], []
if not BENCH.exists():
    note("M1 미측정", "치명",
         f"{BENCH.name} 없음 — `collect_ing_ghs.py --대조군 400` 을 먼저 돌려야 "
         f"MSDS 선언값을 규제 DB 와 견줄 수 있다")
else:
    B = pd.read_csv(BENCH)
    for ep in EPS:
        # MSDS 유래 정답만 본다. `독립조사유래` 는 규제 DB 유래라 순환 대조다
        d = B[(B[f"기존_{ep}_출처"] == "MSDS유래") & has(B[f"기존_{ep}"])].copy()
        모집단키 = set(I.loc[has(I[f"ing_ghs_{ep}"]), "키"].astype(str))
        모집단 = len(모집단키)
        d["MSDS"] = norm(d[f"기존_{ep}"])
        d["규제"] = norm(d[f"{ep}_구분"])
        조회됨 = d[has(d["규제"])]
        # **두 가지 미대조를 가른다.** 앞판은 하나로 합쳐 커버리지 39% 라 적었는데,
        # 그 대부분은 DB 에 없는 것이 아니라 **애초에 대조 표본에 없는 것**이다
        # (`collect_ing_ghs.py --대조군 400` 이 성분행 수 상위 400 종으로 자른다).
        # 두 값은 뜻이 전혀 다르다 — 표본 미포함은 표본을 늘리면 사라지고,
        # DB 미등재는 늘려도 안 사라진다(UVCB·석유유분·혼합 계면활성제)
        표본키 = set(d["키"].astype(str))
        표본미포함 = len(모집단키 - 표본키)
        DB미등재 = len(표본키 & 모집단키) - len(set(조회됨["키"].astype(str))
                                          & 모집단키)
        cov = len(조회됨) / 모집단 if 모집단 else None
        cov표본 = (len(조회됨) / len(표본키 & 모집단키)
                 if len(표본키 & 모집단키) else None)
        log(f"  {ep:4} [M1] MSDS 값 보유 {모집단}종 중 규제 DB 대조 성공 "
            f"{len(조회됨)}종"
            + (f" (전체 {cov:.1%})" if cov is not None else " (모집단 0)")
            + f" · 미대조 = 표본미포함 {표본미포함}종 + DB미등재 {DB미등재}종"
            + (f" · 표본 안 커버리지 {cov표본:.1%}" if cov표본 else ""))
        if cov표본 is not None and cov표본 < 0.5:
            note("M1 대조커버리지", "중대",
                 f"{ep} 대조 표본에 든 물질 중에서도 {cov표본:.0%} 만 규제 DB 로 "
                 f"대조됐다 — DB 미등재 {DB미등재}종(UVCB·석유유분 등). 아래 불일치 "
                 f"수치는 이 범위 안의 것이다")
        elif cov is not None and cov < 0.5:
            note("M1 표본범위", "경미",
                 f"{ep} MSDS 값 보유 {모집단}종 중 대조된 것은 {len(조회됨)}종"
                 f"({cov:.0%})이다. 다만 미대조 {모집단-len(조회됨)}종의 "
                 f"{표본미포함}종은 DB 미등재가 아니라 **대조 표본 자체에 없다** "
                 f"(`--대조군` 상한). 표본 안 커버리지는 {cov표본:.0%} 다 — "
                 f"불일치율은 표본 안에서 읽고, 전수로 일반화하지 않는다")
        if not len(조회됨):
            continue
        # 방향은 `방향()` 하나로만 낸다. 하위구분 미지정 쌍(맨 `1` vs `1A/1B/1C`)은
        # `구분granularity차이` 로 빠져 낮음·높음 어디에도 안 들어간다
        dir_ = 조회됨.apply(lambda r: 방향(ep, r["MSDS"], r["규제"]), axis=1)
        일치 = int((dir_ == "동일").sum())
        낮음 = int((dir_ == "MSDS가_낮음").sum())    # MSDS 가 규제보다 낮다
        높음 = int((dir_ == "MSDS가_높음").sum())
        gran = int((dir_ == "구분granularity차이").sum())
        # M3 — tier1 조화분류보다 낮은 것만. 이것은 관할 차이로 설명되지 않는다
        t1m = 조회됨[f"{ep}_출처tier"] == "tier1_AnnexVI"
        t1 = 조회됨[t1m]
        t1낮음 = 조회됨[t1m & (dir_ == "MSDS가_낮음")]
        행수 = lambda x: int(x["성분행수"].sum()) if len(x) else 0   # noqa: E731
        대조표.append({"endpoint": ep, "MSDS값_보유물질": int(모집단),
                     "대조성공": int(len(조회됨)),
                     "대조커버리지": round(cov, 4) if cov is not None else None,
                     "표본미포함": 표본미포함, "DB미등재": DB미등재,
                     "표본안_커버리지": round(cov표본, 4) if cov표본 else None,
                     "심각도일치": 일치,
                     "일치율": round(일치 / len(조회됨), 4),
                     "MSDS가_낮음": 낮음, "MSDS가_높음": 높음,
                     "구분granularity차이": gran,
                     "구분granularity차이_성분행":
                         행수(조회됨[dir_ == "구분granularity차이"]),
                     "MSDS낮음_성분행": 행수(조회됨[dir_ == "MSDS가_낮음"]),
                     "tier1_대조": int(len(t1)),
                     "tier1보다_낮음": int(len(t1낮음)),
                     "tier1보다낮음_성분행": 행수(t1낮음)})
        log(f"  {ep:4} [M2] 일치 {일치}/{len(조회됨)} ({일치/len(조회됨):.1%}) · "
            f"MSDS 가 낮음 {낮음}(성분행 {대조표[-1]['MSDS낮음_성분행']}) · "
            f"높음 {높음} · 하위구분 미지정 차이 {gran}"
            f"(성분행 {대조표[-1]['구분granularity차이_성분행']}) — 방향 판정 제외")
        log(f"  {ep:4} [M3] tier1 조화분류 대조 {len(t1)}종 중 MSDS 가 낮은 것 "
            f"{len(t1낮음)}종(성분행 {대조표[-1]['tier1보다낮음_성분행']})")
        for (_, r), dv in zip(조회됨.iterrows(), dir_):
            if dv in ("동일", None):
                continue
            위반행.append({
                "endpoint": ep, "키": r["키"], "이름": r.get("이름"),
                "cid": r.get("cid"), "MSDS선언": r["MSDS"], "규제DB": r["규제"],
                "규제tier": r[f"{ep}_출처tier"], "규제근거": r[f"{ep}_근거"],
                "방향": dv,
                "조화분류_위반": bool(r[f"{ep}_출처tier"] == "tier1_AnnexVI"
                                and dv == "MSDS가_낮음"),
                "성분행수": r["성분행수"], "동일성등급": r.get("동일성등급"),
                "출처URL": r.get("출처URL")})
        # **동일성 보류분도 목록에 싣는다.** 조사가 CID 동일성을 확정하지 못해
        # `{ep}_구분` 을 비우고 `{ep}_보류값` 에만 남긴 물질은 위 대조에서 통째로
        # 빠져 있었다. 판정 근거로 쓸 수는 없지만 **어긋난다는 신호는 보인다** —
        # 예: 타르타르산(133-37-9) MSDS 눈 `1` vs 보류값 `2A`. 방향을
        # `대조보류` 로 따로 표시해 하류가 집계에서 배제할 수 있게 둔다
        보류열 = f"{ep}_보류값"
        if 보류열 in d.columns:
            hold = d[~has(d["규제"]) & has(d[보류열])]
            for _, r in hold.iterrows():
                v = norm(pd.Series([r[보류열]])).iloc[0]
                if 방향(ep, r["MSDS"], v) in ("동일", "구분granularity차이", None):
                    continue
                위반행.append({
                    "endpoint": ep, "키": r["키"], "이름": r.get("이름"),
                    "cid": r.get("cid"), "MSDS선언": r["MSDS"], "규제DB": v,
                    "규제tier": r.get(f"{ep}_출처tier"),
                    "규제근거": "동일성보류값 — 판정 근거 아님",
                    "방향": "대조보류", "조화분류_위반": False,
                    "성분행수": r["성분행수"],
                    "동일성등급": r.get("동일성등급"),
                    "출처URL": r.get("출처URL")})
            if len(hold):
                log(f"  {ep:4} [M2b] 동일성 보류로 대조에서 빠진 물질 {len(hold)}종 중 "
                    f"MSDS 와 심각도가 다른 것 "
                    f"{sum(1 for x in 위반행 if x['endpoint'] == ep and x['방향'] == '대조보류')}종"
                    f" — `방향=대조보류` 로 목록에만 싣는다")
        if 낮음 + 높음 and max(낮음, 높음) > 3 * max(min(낮음, 높음), 1):
            note("M2 방향편향", "중대",
                 f"{ep} MSDS 와 규제 DB 의 불일치가 한쪽으로 쏠렸다(낮음 {낮음} vs "
                 f"높음 {높음}). 공급자 자기분류가 조화분류와 다를 수 있다는 것으로는 "
                 f"한쪽 쏠림이 설명되지 않는다 — 추출 단계의 계통 오류를 의심한다")
        if len(t1낮음):
            note("M3 조화분류위반", "중대",
                 f"{ep} CLP Annex VI 조화분류가 있는 {len(t1)}종 중 {len(t1낮음)}종"
                 f"(성분행 {대조표[-1]['tier1보다낮음_성분행']})의 MSDS 선언값이 "
                 f"조화분류보다 **낮다**. 조화분류는 EU 안에서 공급자가 낮출 수 "
                 f"없으므로 관할 차이로 설명되지 않는다 — `검수_MSDS값_불일치.csv` "
                 f"의 `조화분류_위반` 열 참조")
저장(위반행, ["endpoint", "키", "이름", "cid", "MSDS선언", "규제DB", "규제tier",
            "규제근거", "방향", "조화분류_위반", "성분행수", "동일성등급", "출처URL"],
    "검수_MSDS값_불일치.csv", ["endpoint", "성분행수"])
pd.DataFrame(대조표).to_csv(OUT / "검수_MSDS값_대조.csv", index=False,
                           encoding="utf-8-sig")

# ------------------------------------------------------------------ 종합
log("--- 종합 ---")
F = pd.DataFrame(FIND) if FIND else pd.DataFrame(columns=["항목", "심각도", "내용"])
F.to_csv(OUT / "검수_MSDS값_판정.csv", index=False, encoding="utf-8-sig")
치명 = int((F["심각도"] == "치명").sum()) if len(F) else 0
중대 = int((F["심각도"] == "중대").sum()) if len(F) else 0
판정 = ("통과" if 치명 == 0 and 중대 == 0 else
       "차단 — 치명 결함" if 치명 else "조건부 — 중대 결함 해소 필요")
log(f"치명 {치명} · 중대 {중대} · 경미 "
    f"{int((F['심각도'] == '경미').sum()) if len(F) else 0} → **{판정}**")
with open(OUT / "검수_MSDS값요약.json", "w", encoding="utf-8") as f:
    json.dump({"판정": 판정, "치명": 치명, "중대": 중대, "발견": FIND,
               "결측구조": 구조, "어휘": 어휘, "자기모순": 모순,
               "indep_tierNC": indep,
               "규제DB대조": 대조표 or "미측정",
               "해석주의": [
                   "규제 DB 와 어긋난다는 것이 곧 MSDS 가 틀렸다는 뜻은 아니다. "
                   "공급자 자기분류는 조화분류와 다를 수 있고 관할·시점도 다르다. "
                   "관할 차이로 설명되지 않는 것은 M3(조화분류보다 낮음)뿐이다.",
                   "M4 자기모순은 외부 대조가 필요 없는 내부 증거다. 같은 물질의 "
                   "고유 위험이 제형에 따라 다를 수 없으므로 이것은 문서 추출 잡음이고 "
                   "가산식 입력 품질의 상한을 정한다.",
                   "이 검수는 어떤 값도 고치지 않는다. 목록만 낸다.",
               ]}, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT.relative_to(L.ROOT)}/검수_MSDS값_판정.csv")
