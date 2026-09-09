#!/usr/bin/env python3
"""과제 3 — 감작 라벨 충돌 32건 장부(ledger) 기록.

성격 규정
--------
cat_sens_ghs / cat_sens_src 는 build_input_v5.py:1479-1480 의 LABEL_SOURCE 에 있어
피처로 들어가지 않는다. CT 가산의 입력은 ing_ghs_sens(build_input_v5.py:684)로
경로가 다르다. 따라서 이 32건의 정정은 **모델 성능에 대한 무영향(no-op)** 이며,
성능 주장을 붙이지 않는다. 목적은 데이터 장부의 정합성 기록뿐이다.

판정 규칙(보수적)
----------------
정정권고: 출처가 EU CLP Annex VI(조화분류)이고 그 항목에 감작 분류가 없는 경우에만.
        조화분류는 법적 구속력이 있고 항목이 존재하는데 감작이 없으면 '음성'의
        근거로 쓸 수 있다.
보류    : ECHA C&L 통보 기반, 또는 Annex VI 항목 자체가 없어 침묵을 음성으로
        해석한 경우(= 미시험을 음성으로 쓰는 결함). 농약 활성성분 다수가 여기 속하며
        실제로 Skin Sens. 1 이 맞을 개연성이 높다. 임의 적용하면 결함을 재생산한다.
"""
from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pandas as pd

import v6_integrated as v6

ROOT = Path(__file__).resolve().parent.parent
QA = ROOT / "04_모델산출물" / "v5_team_qa" / "채윤_GHS_검수.csv"
V6 = v6.V6                     # 통합 단일 입력(팀원 파일 직접 읽기를 대체)
OUT = ROOT / "04_모델산출물" / "v6_skinmap_ct"
OUT.mkdir(parents=True, exist_ok=True)

d = pd.read_csv(QA)
mask = d["기존값충돌"].astype(str).str.contains("감작:")
conf = d[mask].copy()
assert len(conf) == 32, f"감작 충돌 건수 {len(conf)} != 32"

# 조사 원문은 통합본 ingredient 시트에서 읽는다(팀원 파일 직접 읽기 제거).
# 이전 판은 notna 필터 없이 drop_duplicates("cas") 만 했다. 확인 결과 조사 대표행이
# 항상 CAS 의 첫 등장 행이어서 결과는 같았으나(원문을 잃는 CAS 0건), 우연에 기대지
# 않도록 필터를 명시한다 — v6_integrated.cy_survey_rows 가 331행을 assert 한다.
_ING6 = v6.load_sheets(("ingredient",))["ingredient"]
cy = v6.cy_survey_rows(_ING6, ["cas", "GHS조사_감작", "GHS조사_근거URL", "GHS조사_메모"])
conf = conf.merge(cy, on="cas", how="left")

ing = _ING6[
    ["Formulation_ID", "cas", "ingredient_name", "cat_sens_ghs", "cat_sens_src",
     "ing_ghs_sens", "ing_pct_best"]]
ing["cas"] = ing["cas"].astype(str).str.strip()
conf["cas"] = conf["cas"].astype(str).str.strip()

hit = ing[ing["cas"].isin(set(conf["cas"]))]
print(f"영향 성분행={len(hit)}  cat_sens_ghs 비결측={int(hit['cat_sens_ghs'].notna().sum())}  "
      f"영향 제형={hit['Formulation_ID'].nunique()}")
print("cat_sens_src 분포:", hit["cat_sens_src"].value_counts(dropna=False).to_dict())
print("ing_ghs_sens 비결측:", int(hit["ing_ghs_sens"].notna().sum()),
      "  ← CT 입력 경로. 0 이면 CT 에도 무영향.")


def tier(s: str) -> str:
    s = str(s)
    if "Annex VI" in s and "ECHA C&L 통보" not in s:
        return "annex_vi"
    if "ECHA C&L 통보" in s:
        return "echa_cl"
    return "기타"


def annex_entry_exists(memo: str) -> bool | None:
    """메모에 Annex VI 항목의 존재/부재가 명시돼 있는가. 불명이면 None."""
    m = str(memo)
    if re.search(r"Annex\s*VI\s*(없음|미등재|부재|미존재|해당\s*없음)", m):
        return False
    if re.search(r"Annex\s*VI\s*(존재|등재|수록|있음)", m):
        return True
    return None


rows = []
for _, r in conf.iterrows():
    t = tier(r["GHS조사_감작"])
    ex = annex_entry_exists(r["GHS조사_메모"])
    sub = hit[hit["cas"] == r["cas"]]
    if t == "annex_vi" and ex is True:
        verdict, why = ("정정후보(원문확인 필요)",
                        "메모상 조화분류 항목이 존재하고 그 안에 감작 분류가 없음. "
                        "단 근거URL이 PubChem 미러이고 ECHA Annex VI 원문 미확인이므로 "
                        "적용 전 원문 대조 필수 — 미러가 항목의 일부만 반영할 수 있다")
    elif t == "annex_vi" and ex is None:
        verdict, why = "보류", "Annex VI 표기이나 항목 존재 여부가 메모에서 확인 불가 — 확인 불가"
    elif ex is False:
        verdict, why = "보류", "Annex VI 항목 부재를 음성으로 해석한 침묵 NC — 미시험을 음성으로 쓰는 결함"
    else:
        verdict, why = "보류", "ECHA C&L 자기통보 기반 — 비구속·통보율 미검증, 조화분류 대체 불가"
    rows.append({
        "cas": r["cas"], "ingredient_name": r["ingredient_name"],
        "충돌내용": re.search(r"(감작:[^|]*)", str(r["기존값충돌"])).group(1).strip(),
        "정채윤_감작": r["GHS조사_감작"], "출처티어": t,
        "annexVI_항목존재": {True: "존재", False: "부재", None: "확인 불가"}[ex],
        "근거URL": r["GHS조사_근거URL"], "메모": r["GHS조사_메모"],
        "영향_성분행": len(sub),
        "영향_제형수": int(sub["Formulation_ID"].nunique()),
        "기존_cat_sens_ghs": ";".join(sorted(set(sub["cat_sens_ghs"].dropna().astype(str)))) or "-",
        "기존_cat_sens_src": ";".join(sorted(set(sub["cat_sens_src"].dropna().astype(str)))) or "-",
        "CT입력_ing_ghs_sens_비결측": int(sub["ing_ghs_sens"].notna().sum()),
        "판정": verdict, "판정근거": why,
        "적용여부": "미적용 — 본 장부는 기록용이며 어떤 라벨도 덮어쓰지 않았다",
        "모델영향": "없음 — cat_sens_* 는 LABEL_SOURCE(build_input_v5.py:1479-1480), "
                "CT 입력 ing_ghs_sens 비결측 0",
    })

L = pd.DataFrame(rows).sort_values(["판정", "cas"])
L.to_csv(OUT / "감작충돌_32건_장부.csv", index=False, encoding="utf-8-sig")

wb = openpyxl.Workbook()          # pd.ExcelWriter(engine='openpyxl') 는 이 환경에서 실패
ws = wb.active
ws.title = "감작충돌32"
ws.append(list(L.columns))
for _, r in L.iterrows():
    ws.append([str(v) if v is not None else "" for v in r.tolist()])
ws.freeze_panes = "A2"
ws.auto_filter.ref = ws.dimensions
wb.save(OUT / "감작충돌_32건_장부.xlsx")

print()
print(L["판정"].value_counts().to_dict())
print(L["출처티어"].value_counts().to_dict())
print(L[L["판정"] == "정정권고"][["cas", "ingredient_name", "출처티어"]].to_string(index=False))
print(f"\n→ {OUT/'감작충돌_32건_장부.csv'}\n→ {OUT/'감작충돌_32건_장부.xlsx'}")
