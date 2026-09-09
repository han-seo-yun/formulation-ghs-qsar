#!/usr/bin/env python3
"""회수 C — EPA Toxicity Category → GHS 매핑표로 최소빈 epa_only 56셀 회수.

배경
----
최소빈에게 'SDS Section 11 원문 재확인'을 지시했지만 EPA↔GHS 변환표를 함께 주지
않았다. 그 결과 EPA 범주만 적힌 56셀은 판독 없이 전부 '폐기' 처리됐다. 판단을
보류한 것은 옳았다 — 변환표 없이 임의 변환하면 그게 결함이다. 변환표를 만들어
주는 것은 지시자인 내 몫이었으므로 여기서 회수한다.

매핑 근거
--------
* EPA 급성 안점막/피부 자극 범주 정의 (40 CFR 156.62 표1, 156.64 라벨 문구)
  - 눈  I 비가역 손상 / II 8–21일 내 회복 / III 7일 내 회복 / IV 24시간 내 회복
  - 피부 I 부식(조직 파괴) / II 72시간 심한 자극 / III 72시간 중등도 자극 /
         IV 72시간 경미한 자극
* UN GHS 정의
  - 눈  1 비가역 / 2A 21일 내 가역 / 2B 7일 내 가역
  - 피부 1 부식 / 2 자극(평균 2.3–4.0) / 3 경미한 자극(1.5–<2.3, EU CLP 미채택)
* EPA 라벨 표준 문구(40 CFR 156.64)는 범주와 1:1 대응이므로 문구만으로도 범주 확정
  - 눈:  "irreversible eye damage"=I / "substantial but temporary eye injury"=II /
         "moderate eye irritation"=III / "slight eye irritation" 또는 무문구=IV
  - 피부: "skin burns"/"corrosive"=I / "severe skin irritation"=II /
         "moderate skin irritation"=III / "slight skin irritation"=IV

신뢰 등급
--------
definitional : 회복기간 정의가 양쪽에서 그대로 대응 (눈 I/II/III/IV, 피부 I)
judgment     : 자극 강도 서술의 대응 (피부 II/III/IV) — GHS 2 와 3 의 경계는
               EPA 범주 경계와 정확히 일치하지 않는다. 관할별로 갈린다.

절대 하지 않은 것
----------------
* 급성 경피독성 문구("harmful if absorbed through skin")를 자극성으로 변환하지 않음
* 한 문장에 범주가 둘 있으면 자극성 절(clause)에 붙은 범주만 사용
* 매핑 불가는 '확인 불가'로 남김 — 추측 보간 없음
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "04_모델산출물" / "v5_team_qa" / "소빈_S11_검수.csv"
SRC = ROOT / "04_모델산출물" / "v4_fixed"
OUT = ROOT / "04_모델산출물" / "v6_recovery"
OUT.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------------ 매핑표
# (endpoint, EPA cat) -> (UN GHS, EU CLP, 신뢰등급)
EPA2GHS = {
    ("eye", "I"):   ("1",  "1",  "definitional"),
    ("eye", "II"):  ("2A", "2",  "definitional"),
    ("eye", "III"): ("2B", "NC", "definitional"),   # EU CLP 는 2B(H320) 미채택
    ("eye", "IV"):  ("NC", "NC", "definitional"),
    ("skin", "I"):   ("1",  "1",  "definitional"),
    ("skin", "II"):  ("2",  "2",  "judgment"),
    ("skin", "III"): ("2",  "2",  "judgment"),
    ("skin", "IV"):  ("3",  "NC", "judgment"),      # EU CLP 는 Cat 3 미채택
}

ROMAN = {"I": "I", "II": "II", "III": "III", "IV": "IV",
         "1": "I", "2": "II", "3": "III", "4": "IV"}

# 자극성 절을 식별하는 키워드 (급성 경피/경구 독성 절과 구별)
IRRIT_KW = re.compile(
    r"irritat|irritan|자극|eye (damage|injury)|dermal irritation|skin (burn|corros)|"
    r"부식|primary (eye|skin|dermal)", re.I)
ACUTE_KW = re.compile(
    r"absorbed through|if swallowed|inhal|acute (oral|dermal)|경피독성|삼키면", re.I)

# EPA 라벨 표준 문구 → 범주
LABEL_PHRASE = [
    ("eye",  re.compile(r"irreversible eye damage|corrosive to eyes", re.I), "I"),
    ("eye",  re.compile(r"substantial but temporary eye injury", re.I), "II"),
    ("eye",  re.compile(r"moderate eye irritation", re.I), "III"),
    ("eye",  re.compile(r"(slight|mild|minimal)[a-z ]*eye irritation", re.I), "IV"),
    ("skin", re.compile(r"skin burns|corrosive to skin", re.I), "I"),
    ("skin", re.compile(r"severe skin irritation", re.I), "II"),
    ("skin", re.compile(r"moderate skin irritation", re.I), "III"),
    ("skin", re.compile(r"(slight|mild)[a-z ]*skin irritation", re.I), "IV"),
]


def epa_cat_from_text(text: str, ep: str) -> tuple[str | None, str]:
    """(EPA 범주, 근거설명). 확정 불가면 (None, 이유)."""
    s = str(text)
    # 1) 문장을 절로 쪼개 자극성 절만 남긴다 — 급성독성 범주 오채택 방지
    clauses = [c for c in re.split(r"[;·]|(?<=\))\s*,", s) if c.strip()]
    irr = [c for c in clauses if IRRIT_KW.search(c)]
    pool = irr if irr else ([c for c in clauses if not ACUTE_KW.search(c)] or clauses)
    if not irr and ACUTE_KW.search(s) and not IRRIT_KW.search(s):
        return None, "급성 경피/경구 독성 문구만 존재 — 자극성 범주 아님(변환 거부)"

    # 2) 자극성 절에서 로마숫자 범주 추출
    for c in pool:
        m = re.search(r"(?:Tox(?:icity)?\s*)?Categ(?:ory|ory)?\s*"
                      r"(IV|III|II|I|[1-4])\b", c, re.I)
        if m:
            return ROMAN[m.group(1).upper()], f"자극성 절의 명시 범주: '{c.strip()[:60]}'"

    # 3) 범주 표기가 없으면 EPA 라벨 표준 문구로 확정
    for e, rx, cat in LABEL_PHRASE:
        if e == ep and rx.search(s):
            return cat, f"EPA 라벨 표준문구(40 CFR 156.64) 대응: '{rx.pattern[:32]}'"

    # 4) 절 밖이라도 문서 전체에 범주가 하나뿐이면 그것을 쓴다
    cats = set(ROMAN[x.upper()] for x in re.findall(
        r"Categ(?:ory)?\s*(IV|III|II|I|[1-4])\b", s, re.I))
    if len(cats) == 1:
        return cats.pop(), "문서 내 범주 표기가 단일 — 그 값을 사용"
    return None, f"범주 확정 불가(후보 {sorted(cats) or '없음'})"


# ------------------------------------------------------------------ 적용
d = pd.read_csv(QA)
e = d[d["태그"] == "epa_only"].copy()
assert len(e) == 56, f"epa_only {len(e)} != 56"
assert (e["채택가능"] == "폐기").all(), "이미 채택된 셀이 섞여 있다"

rows = []
for _, r in e.iterrows():
    ep = r["ep"]
    cat, why = epa_cat_from_text(r["원문"], ep)
    un, eu, tier = EPA2GHS.get((ep, cat), (None, None, None)) if cat else (None, None, None)
    rows.append({
        "Formulation_ID": r["Formulation_ID"], "ep": ep, "원문": r["원문"],
        "EPA범주": cat or "확인 불가", "판정근거": why,
        "GHS_UN": un or "확인 불가", "GHS_EU_CLP": eu or "확인 불가",
        "신뢰등급": tier or "변환불가",
        "기존y": r["기존y"], "기존src_detail": r["기존src_detail"],
    })
C = pd.DataFrame(rows)

# 기존 라벨과의 정합성
POS = {"eye": {"1", "2", "2A", "2B"},
       "skin": {"1", "1A", "1B", "1C", "2", "3", "2A", "2B"}}


def agree(row, col):
    """기존y 는 이진 라벨(0/1)이다. 범주 문자열과 직접 비교하지 말고 이진화해서 본다."""
    new, old = str(row[col]), str(row["기존y"])
    if new == "확인 불가" or old in ("nan", "None", ""):
        return "비교불가"
    try:
        po = int(float(old)) == 1
    except ValueError:
        return "비교불가"
    pn = new in POS[row["ep"]]
    return "이진일치" if pn == po else "이진충돌"


C["기존대비_UN"] = C.apply(lambda r: agree(r, "GHS_UN"), axis=1)
C["기존대비_EU"] = C.apply(lambda r: agree(r, "GHS_EU_CLP"), axis=1)

print("=" * 74)
print("회수 C — EPA → GHS 변환 결과 (56셀)")
print(f"EPA 범주 확정: {int((C['EPA범주']!='확인 불가').sum())}/56  "
      f"변환 거부: {int((C['EPA범주']=='확인 불가').sum())}")
print("EPA 범주 분포:", C["EPA범주"].value_counts().to_dict())
print("신뢰등급:", C["신뢰등급"].value_counts().to_dict())
print()
print("UN GHS 결과:", C["GHS_UN"].value_counts().to_dict())
print("EU CLP 결과:", C["GHS_EU_CLP"].value_counts().to_dict())
print()
print("기존 라벨 대비(UN):", C["기존대비_UN"].value_counts().to_dict())
print("기존 라벨 대비(EU):", C["기존대비_EU"].value_counts().to_dict())
print()
print("변환 거부 사례:")
for _, r in C[C["EPA범주"] == "확인 불가"].iterrows():
    print(f"  {r['Formulation_ID']:26} {r['ep']:5} | {str(r['원문'])[:70]}\n"
          f"    └ {r['판정근거']}")
# 충돌의 성격 분류 — 관할 경계 때문에 생긴 것인지, 출처 간 진짜 모순인지 구분한다.
# 두 관할에서 모두 충돌하면 관할 선택으로 설명할 수 없는 '진짜 모순'이다.
def conflict_kind(r):
    u, e_ = r["기존대비_UN"], r["기존대비_EU"]
    if u == "비교불가":
        return "비교불가"
    if u == "이진충돌" and e_ == "이진충돌":
        return "출처간_진짜모순(재검 필수)"
    if u == "이진충돌" or e_ == "이진충돌":
        return "관할경계_인공물(2B/Cat3 채택 여부로 갈림)"
    return "일치"


C["충돌성격"] = C.apply(conflict_kind, axis=1)
print()
print("충돌 성격 분류:", C["충돌성격"].value_counts().to_dict())
print()
print("출처간 진짜 모순 (두 관할 모두에서 충돌 — 원문 재검 필수):")
for _, r in C[C["충돌성격"].str.startswith("출처간")].iterrows():
    print(f"  {r['Formulation_ID']:26} {r['ep']:5} EPA {r['EPA범주']:4}"
          f" → UN {r['GHS_UN']:3} / EU {r['GHS_EU_CLP']:3}  vs 기존 {r['기존y']}"
          f" ({r['기존src_detail']})")

print()
print("전체 이진충돌 사례:")
cf = C[(C["기존대비_UN"] == "이진충돌") | (C["기존대비_EU"] == "이진충돌")]
if len(cf) == 0:
    print("  없음")
for _, r in cf.iterrows():
    print(f"  {r['Formulation_ID']:26} {r['ep']:5} EPA {r['EPA범주']:4}"
          f" → UN {r['GHS_UN']:3} / EU {r['GHS_EU_CLP']:3}  vs 기존 {r['기존y']}"
          f" ({r['기존src_detail']})")

C.to_csv(OUT / "회수C_EPA_GHS_변환_56셀.csv", index=False, encoding="utf-8-sig")

# 매핑표 자체를 산출물로 남긴다 — 재사용·감사 가능하게
MT = pd.DataFrame([{"endpoint": ep, "EPA_category": c,
                    "UN_GHS": v[0], "EU_CLP": v[1], "신뢰등급": v[2]}
                   for (ep, c), v in EPA2GHS.items()])
MT.to_csv(OUT / "EPA_GHS_매핑표.csv", index=False, encoding="utf-8-sig")

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "EPA_GHS_매핑표"
ws.append(list(MT.columns))
for _, r in MT.iterrows():
    ws.append(r.tolist())
ws.freeze_panes = "A2"
w2 = wb.create_sheet("회수56셀")
w2.append(list(C.columns))
for _, r in C.iterrows():
    w2.append([str(v) for v in r.tolist()])
w2.freeze_panes = "A2"
w2.auto_filter.ref = w2.dimensions
wb.save(OUT / "회수C_EPA_GHS.xlsx")

summary = {
    "대상": "최소빈 epa_only 56셀 (전부 '폐기' 처리됨 — 변환표 미제공이 원인)",
    "책임": "EPA↔GHS 변환표를 지시와 함께 주지 않은 것은 배정 설계 오류. 판단을 "
          "보류한 최소빈의 처리는 옳았다.",
    "EPA범주_확정": int((C["EPA범주"] != "확인 불가").sum()),
    "변환거부": int((C["EPA범주"] == "확인 불가").sum()),
    "EPA범주_분포": C["EPA범주"].value_counts().to_dict(),
    "신뢰등급_분포": C["신뢰등급"].value_counts().to_dict(),
    "UN_GHS_결과": C["GHS_UN"].value_counts().to_dict(),
    "EU_CLP_결과": C["GHS_EU_CLP"].value_counts().to_dict(),
    "기존대비_UN": C["기존대비_UN"].value_counts().to_dict(),
    "기존대비_EU": C["기존대비_EU"].value_counts().to_dict(),
    "이진충돌_건수": int(len(cf)),
    "충돌성격_분류": C["충돌성격"].value_counts().to_dict(),
    "한계": [
        "피부 II/III/IV 는 신뢰등급 judgment — EPA 자극강도 경계와 GHS 2/3 경계가 "
        "정확히 일치하지 않는다. 눈은 회복기간 정의가 양쪽에 그대로 있어 definitional.",
        "EU CLP 열은 2B(H320)와 Skin Irrit. 3 을 미채택 관할로 처리한 결과다. "
        "대상 관할이 확정되기 전에는 UN 열과 EU 열 중 어느 것을 쓸지 결정할 수 없다.",
        "원문은 최소빈이 전사한 텍스트이며 SDS 원문 PDF 재대조는 하지 않았다(로컬에 "
        "해당 문서 없음 — 448셀 중 원문 확보 5건).",
        "이 변환은 라벨 후보를 만든 것이고 아직 어떤 라벨도 덮어쓰지 않았다.",
    ],
}
with open(OUT / "회수C_요약.json", "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
print(f"\n→ {OUT}/회수C_EPA_GHS_변환_56셀.csv, EPA_GHS_매핑표.csv, 회수C_EPA_GHS.xlsx")
