#!/usr/bin/env python3
"""배정 설계 오류 회수 — 팀원에게 잘못 지시해 비게 된 구멍을 내가 직접 메운다.

회수 A. ' ;; ' 문자분리 손상 21셀 복구
  원본 워크북의 재조사 라운드에서만 존재하고 배포본에는 결측인 tox_h_statements 21셀.
  손상은 writer 의 ' ;; '.join(문자열) 버그이므로 구분자 제거로 무손실 복원된다.
  복원 후 엔드포인트 관련 H코드(H314/H315/H317/H318/H319)가 몇 건 회수되는지 계량한다.

회수 B. 극단 pH 게이트 진단
  v5 에 이미 존재하는 ph_strong_acid / ph_strong_base 행이 왜 수동재검 목록에
  들어가지 않았는지 규명한다. 새 pH 수집을 지시하기 전에 반드시 선행해야 하는 확인이다.
  (이서윤에게 SL 코드를 우선 지시한 것이 설계 오류였다 — 실제 채택률 3.9%.)

무수정 원칙: 읽기 전용. 산출은 v6_recovery/ 에만 기록한다.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DIFF = ROOT / "04_모델산출물" / "v5_team_qa" / "배포본_원본_차이.csv"
V5 = ROOT / "04_모델산출물" / "input_dataset_v5.xlsx"
SRC = ROOT / "04_모델산출물" / "v4_fixed"
OUT = ROOT / "04_모델산출물" / "v6_recovery"
OUT.mkdir(parents=True, exist_ok=True)

EP_HCODES = {"H314": ("skin", "1"), "H315": ("skin", "2"),
             "H318": ("eye", "1"), "H319": ("eye", "2"),
             "H317": ("sens", "1")}

report: dict = {}

# ==================================================================== 회수 A
print("=" * 70)
print("회수 A — ' ;; ' 문자분리 손상 21셀 복구")
d = pd.read_csv(DIFF)
dmg = d[d["손상측"].astype(str) == "원본"].copy()
assert len(dmg) == 21, f"손상 셀 {len(dmg)} != 21"
assert set(dmg["column"]) == {"tox_h_statements"}, f"예상 밖 컬럼: {set(dmg['column'])}"
assert dmg["배포본값"].isna().all(), "배포본에 값이 있는 셀이 섞여 있다 — 덮어쓰기 위험"


def unsplit(s: str) -> str:
    """' ;; ' 로 낱자 분해된 문자열을 원형으로 되돌린다. 구분자 제거만 — 추측 없음."""
    return "".join(str(s).split(" ;; "))


dmg["복원값"] = dmg["원본값"].map(unsplit)
dmg["복원_H코드"] = dmg["복원값"].map(lambda s: sorted(set(re.findall(r"H\d{3}[a-z]*", s))))
dmg["엔드포인트관련_H코드"] = dmg["복원_H코드"].map(
    lambda hs: sorted({h for h in hs if h[:4] in EP_HCODES}))

# 무손실 검증: 복원값의 낱자를 다시 분해하면 원본과 일치해야 한다
for _, r in dmg.iterrows():
    assert " ;; ".join(list(r["복원값"])) == r["원본값"], f"무손실 아님: {r['key']}"
print("무손실 검증 통과 — 복원값을 재분해하면 원본값과 완전 일치(21/21)")

n_ep = int((dmg["엔드포인트관련_H코드"].map(len) > 0).sum())
allep = [h for hs in dmg["엔드포인트관련_H코드"] for h in hs]
print(f"복원 21셀 중 엔드포인트 관련 H코드 보유: {n_ep}건")
print(f"회수된 엔드포인트 H코드 분포: {pd.Series(allep).value_counts().to_dict() or '없음'}")
print(f"전체 H코드 상위: {pd.Series([h for hs in dmg['복원_H코드'] for h in hs]).value_counts().head(8).to_dict()}")
print("\n복원 결과 (앞 8건):")
for _, r in dmg.head(8).iterrows():
    print(f"  {r['key']:32} → {r['복원값'][:60]}")

dmg[["key", "column", "원본값", "복원값", "복원_H코드", "엔드포인트관련_H코드"]].to_csv(
    OUT / "회수A_손상21셀_복구.csv", index=False, encoding="utf-8-sig")

report["회수A"] = {
    "손상셀수": 21,
    "손상원인": "writer 의 ' ;; '.join(문자열) 버그 — 문자열을 낱자로 분해",
    "복구방법": "구분자 ' ;; ' 제거만. 추측 보간 없음",
    "무손실검증": "복원값 재분해 == 원본값, 21/21 일치",
    "배포본_해당셀_전부결측": True,
    "엔드포인트관련_H코드_보유셀": n_ep,
    "회수된_엔드포인트_H코드": pd.Series(allep).value_counts().to_dict(),
    "파이프라인_영향": "tox_h_statements 는 파이프라인이 읽지 않는다(S_TOX 에서 읽는 것은 "
                 "추출정보/검증여부뿐). 따라서 이 복구는 라벨 증거의 회수이며 "
                 "자동 반영되지 않는다 — 반영하려면 parse_tox11 에 "
                 "ghs_hazard_statements 를 4번째 근거로 추가해야 한다(미완 과제).",
}

# ==================================================================== 회수 B
print()
print("=" * 70)
print("회수 B — 극단 pH 게이트 진단")
F = pd.ExcelFile(V5).parse("formulation")
Y = pd.read_parquet(SRC / "y_formulation.parquet")

phcols = [c for c in F.columns if c.startswith("ph")]
print(f"pH 관련 컬럼: {phcols}")

ext = F[(F.get("ph_strong_acid") == 1) | (F.get("ph_strong_base") == 1)].copy()
print(f"극단 pH 행: {len(ext)} "
      f"(강산 {int((F.get('ph_strong_acid')==1).sum())}, "
      f"강염기 {int((F.get('ph_strong_base')==1).sum())})")

# 수동재검 대상 목록들과 교집합
lists = {}
for name, path, key in [
    ("label_conflict_manual_review", SRC / "label_conflict_manual_review.csv", "Formulation_ID"),
    ("판정작업목록", SRC / "판정작업목록.csv", None),
    ("판정결과_A", SRC / "판정결과_A.csv", None),
]:
    if not path.exists():
        continue
    t = pd.read_csv(path)
    kc = key if (key and key in t.columns) else next(
        (c for c in t.columns if c.lower() in ("formulation_id", "fid")), None)
    if kc is None:
        print(f"  {name}: Formulation_ID 컬럼 없음 — 교집합 불가 (컬럼: {t.columns.tolist()[:8]})")
        continue
    ids = set(t[kc].astype(str))
    lists[name] = ids
    inter = set(ext["Formulation_ID"].astype(str)) & ids
    print(f"  {name}: n={len(t)} 고유제형={len(ids)}  극단pH와 교집합={len(inter)} {sorted(inter)[:6]}")

# 왜 안 걸렸나 — 코드에 pH 게이트가 존재하는지 확인
gate_hits = []
for py in sorted((ROOT / "01_파이프라인").glob("*.py")):
    txt = py.read_text(encoding="utf-8", errors="ignore")
    if re.search(r"ph_strong_(acid|base)", txt):
        gate_hits.append(py.name)
print(f"\n소스에서 ph_strong_acid/base 를 참조하는 파일: {gate_hits or '없음'}")

# 극단 pH 행의 라벨 상태 — CT 비가산 예외가 적용됐어야 하는 행들
ec = ext[["Formulation_ID"]].copy()
for ep in ("eye", "skin", "sens"):
    j = Y.set_index("Formulation_ID")
    ec[f"y_{ep}"] = j[f"y_{ep}"].reindex(ext["Formulation_ID"]).values
    ec[f"y_{ep}_src"] = j[f"y_{ep}_src_detail"].reindex(ext["Formulation_ID"]).values
    ec[f"ct_{ep}_ord"] = j[f"ct_{ep}_ord"].reindex(ext["Formulation_ID"]).values
for c in ("ph_best", "ph_basis", "ph_strong_acid", "ph_strong_base"):
    if c in F.columns:
        ec[c] = F.set_index("Formulation_ID")[c].reindex(ext["Formulation_ID"]).values
ec.to_csv(OUT / "회수B_극단pH_12행_진단.csv", index=False, encoding="utf-8-sig")

print("\n극단 pH 행의 라벨/CT 상태:")
print(ec[["Formulation_ID", "ph_best", "ph_basis", "y_eye", "y_skin",
          "ct_eye_ord", "ct_skin_ord"]].to_string(index=False))

# CT 가산 비적용 예외가 적용됐는지: 강산/강염기인데 CT 로 NC 가 나오면 그것이 결함
bad = ec[((ec["y_skin"].astype(str).isin(["1", "1A", "1B", "1C"]))
          & (ec["ct_skin_ord"].fillna(-1) < 4))]
print(f"\n라벨은 피부 Cat1(부식성)인데 CT 가산은 그에 못 미치는 행: {len(bad)} "
      f"→ CT 가산 비적용 예외(pH ≤ 2 / ≥ 11.5)가 코드에 없어서 생기는 계통 오차")

report["회수B"] = {
    "극단pH_행수": int(len(ext)),
    "강산": int((F.get("ph_strong_acid") == 1).sum()),
    "강염기": int((F.get("ph_strong_base") == 1).sum()),
    "수동재검목록과_교집합": {k: len(set(ext["Formulation_ID"].astype(str)) & v)
                     for k, v in lists.items()},
    "ph_strong_참조_소스파일": gate_hits,
    "진단": ("ph_strong_acid/base 는 build 단계에서 산출만 되고 어떤 라벨 판정·CT 예외·"
           "재검 선정 로직도 이 컬럼을 읽지 않는다. 따라서 극단 pH 행이 재검 목록에 "
           "들어가지 않은 것은 데이터 부족이 아니라 게이트 미구현이다."
           if not gate_hits or gate_hits == [] else
           f"참조 파일 존재: {gate_hits} — 해당 파일의 사용처를 추적해야 한다"),
    "피부Cat1인데_CT미달_행수": int(len(bad)),
    "결론": ("새 pH 수집을 지시하기 전에 게이트를 먼저 구현해야 한다. 게이트가 없으면 "
           "pH 를 몇 건 더 모아도 라벨·CT 어디에도 반영되지 않는다. "
           "이서윤에게 SL 코드 280건을 우선 지시한 것이 배정 설계 오류였다 — "
           "우선순위는 pH 수집이 아니라 게이트 구현이었다."),
}

with open(OUT / "회수_A_B_요약.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2, default=str)

# 엑셀 산출 (openpyxl 직접 — pd.ExcelWriter 는 이 환경에서 실패)
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "회수A_손상21셀"
ws.append(["key", "원본값(손상)", "복원값", "복원_H코드", "엔드포인트관련"])
for _, r in dmg.iterrows():
    ws.append([r["key"], r["원본값"], r["복원값"],
               ", ".join(r["복원_H코드"]), ", ".join(r["엔드포인트관련_H코드"])])
ws.freeze_panes = "A2"
ws.auto_filter.ref = ws.dimensions
ws2 = wb.create_sheet("회수B_극단pH")
ws2.append(list(ec.columns))
for _, r in ec.iterrows():
    ws2.append([str(v) for v in r.tolist()])
ws2.freeze_panes = "A2"
wb.save(OUT / "회수_A_B.xlsx")

print(f"\n→ {OUT}")
