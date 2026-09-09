#!/usr/bin/env python3
"""0 과 결측을 구분하는 감사 — 산출: 04_모델산출물/v7_결측감사/

문제
----
"값이 없음"이 0 으로 들어간 곳이 있는지 전 피처를 훑는다. 0 이 나쁜 값인 것은
아니다. 세 가지를 반드시 구분해야 한다.

  (a) 구조적 0   실측값이다. "계면활성제가 안 들어간 제형이라 계면활성제 농도합 0",
                "성분이 1종이라 디스크립터 분산 0", "구조경보가 없어서 지시열 0".
                이건 결측이 아니고 정보다. 결측으로 바꾸면 오히려 정보를 버린다.
  (b) 임퓨트된 0  원래 결측인데 0 이 적힌 것. 모델이 "농도 0%"라는 사실로 읽는다.
                이건 값을 만든 것이다 — 규칙 위반이며 되돌려야 한다.
  (c) 학습시 0   행렬은 결측인데 학습 직전 `np.nan_to_num(nan=0)` 로 0 이 되는 것
                (`nan0` 규약). 데이터는 안 건드리지만 모델이 보는 값은 (b) 와 같다.

판정 방법
--------
(b) 를 (a) 와 가르는 기준은 **그 열의 정보원이 그 행에 아예 없는가**다. 두 마스크를
쓴다.
  무조성  `f_pct_sum_known` 결측 = 성분 농도가 하나도 파싱되지 않은 제형(281행).
          역할별 농도합·다양성지수가 정의되지 않는다.
  무구조  `f_MolWt_mean` 결측 = SMILES 기반 디스크립터가 하나도 없는 제형(225행) /
          성분행에서는 `MolWt` 결측(1707행).
정보원이 없는 행에 0 이 적혀 있으면 (b) 다. 열마다 정보원을 명시적 분류표로 정하고
**그 열의 정보원 마스크와의 교집합만** 본다. 값 분포로 "이진열이니 지시열"이라고
추정하는 방식은 쓰지 않는다 — `f_surf_anionic_nonionic` 은 연속 곱항인데 우연히 값이
{0,1} 두 개뿐이어서 그 방식으로는 오판된다. 지시열·플래그열·실측 물성은 분류표에서
판정제외로 명시한다.

읽기 전용. 쓰기는 v7_결측감사/ 안에서만.
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

import lib_model as L

OUT = L.ROOT / "04_모델산출물" / "v7_결측감사"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "run.log")


def profile(X, cols, masks, unit, source_of):
    """열별 결측·0 프로파일. masks 는 {이름: bool 마스크}.

    `정보원` 은 그 열의 값이 어디서 오는지다. 판정은 **그 열의 정보원 마스크와의
    교집합만** 본다 — 예컨대 역할별 농도합이 무구조 행에서 0 인 것은 아무 의미가
    없다(농도의 정보원은 조성이지 구조가 아니다). 값 분포로 이진열을 추정하는
    방식은 쓰지 않는다. `f_surf_anionic_nonionic` 처럼 연속 곱항인데 우연히 값이
    {0,1} 두 개뿐인 열이 지시열로 오판되기 때문이다.
    """
    rows = []
    for c in cols:
        s = pd.to_numeric(X[c], errors="coerce")
        if s.notna().sum() == 0:
            continue
        z = (s == 0).to_numpy()
        src = source_of(c)
        r = {"단위": unit, "열": c, "정보원": src or "판정제외",
             "n행": len(s), "결측": int(s.isna().sum()), "0": int(z.sum()),
             "고유값수": int(s.nunique()),
             "최소": round(float(s.min()), 6), "최대": round(float(s.max()), 6)}
        for mn, mk in masks.items():
            r[f"0_{mn}"] = int((z & mk).sum())
        r["임퓨트된0"] = int((z & masks[src]).sum()) if src in masks else 0
        r["결측플래그열_있음"] = int(f"{c}_isna" in X.columns)
        rows.append(r)
    return pd.DataFrame(rows)


# --------------------------------------------------------------- 정보원 분류표
# 판정제외(None) 로 두는 것과 그 이유
#   *_isna                    결측 플래그 자체. 0 은 "결측 아님"이라는 실측값
#   one-hot (form_code_best_· formulation_type_· pc2_physical_state_ 등)
#                             범주 지시열. 0 은 "그 범주가 아님"
#   pc2_* · t11_* · ph_*      SDS 물성·급성독성 실측치. 0 이 물리적으로 유효하고
#                             (융점 0 °C 등) 결측은 이미 NaN 이다
#   ct_* · f_ct_*             ct_predict 가 n_known==0 이면 None 을 낸다. 0 은
#                             "아는 성분이 있고 그중 해당 구분이 없다"는 실측값
#   f_has_* · f_n_<역할>      역할 카운트는 성분명에서 나온다. 농도를 몰라도 유효
_NOJUDGE_PREFIX = ("pc2_", "t11_", "ph_", "form_code_best_", "formulation_type_",
                   "ct_", "f_ct_", "f_has_", "f_alert_", "alert_")
_MOMENT = re.compile(r"^f_(.+)_(mean|max|min|range|std|wmean)$")
# 조성(성분 농도)에서 나오는 열
_FROM_COMP = ("f_pct_", "f_surf_", "f_solvent_")
_FROM_COMP_EXACT = {"f_shannon", "f_simpson", "f_max_pct", "f_n_pct_known"}
# 구조(SMILES)에서 나오는 열
_FROM_STRUCT_EXACT = {"f_n_smiles", "f_smiles_coverage"}


def source_form(c):
    if c.endswith("_isna") or c.startswith(_NOJUDGE_PREFIX):
        return None
    if _MOMENT.match(c):
        return "무구조"                       # 디스크립터·구조경보·tanimoto 모멘트
    if c in _FROM_STRUCT_EXACT:
        return "무구조"
    if c in _FROM_COMP_EXACT or c.startswith(_FROM_COMP):
        return "무조성"
    return None                              # f_n_ing 등 항상 관측되는 부기열


def source_ing(c):
    """성분행은 정보원이 구조 하나뿐이다(농도열은 ING_DROP_EXACT 로 이미 배제).

    `desc_ok` 는 판정제외다 — 이 열의 0 집합이 무구조 마스크와 정확히 같다. 즉
    이 열 자체가 결측 플래그이고, 0 은 "디스크립터를 못 만들었다"는 실측값이다.
    """
    if c.endswith(("_isna", "_ok")) or c.startswith(_NOJUDGE_PREFIX):
        return None
    return "무구조"


# ============================================================= 제형(혼합물) 행렬
log("=== 제형 행렬 감사 ===")
XF = pd.read_parquet(L.SRC / "X_formulation.parquet")
MAN = pd.read_csv(L.SRC / "feature_role_manifest.csv")
MF = MAN[(MAN["sheet"] == "formulation") & (MAN["feature_role"] == "chemistry")
         & (~MAN["exclude_by_default"].astype(bool))]
CHEM = sorted(c for c in MF["column"] if c != "Formulation_ID")
mF = {"무조성": pd.to_numeric(XF["f_pct_sum_known"], errors="coerce").isna().to_numpy(),
      "무구조": pd.to_numeric(XF["f_MolWt_mean"], errors="coerce").isna().to_numpy()}
log(f"제형 {len(XF)}행 × 학습피처 {len(CHEM)}열 · "
    + " · ".join(f"{k} {int(v.sum())}행" for k, v in mF.items()))
PF = profile(XF, CHEM, mF, "제형", source_form)
log("정보원 분류: " + " · ".join(f"{k} {v}열" for k, v
                             in PF["정보원"].value_counts().items()))
BAD_F = PF[PF["임퓨트된0"] > 0].sort_values("임퓨트된0", ascending=False)
log(f"임퓨트된 0 으로 판정된 제형 열 {len(BAD_F)}개")
for _, r in BAD_F.iterrows():
    log(f"  {r['열']:26} 정보원 {r['정보원']} · 결측 {r['결측']:5}행 · 0 {r['0']:5}행 중 "
        f"{r['임퓨트된0']}행이 정보원 없음 · "
        f"결측플래그열 {'있음' if r['결측플래그열_있음'] else '없음'}")
assert set(BAD_F["열"]) == set(L.ZERO_IS_MISSING), \
    f"감사 결과와 lib_model.ZERO_IS_MISSING 불일치: {sorted(set(BAD_F['열']))} vs " \
    f"{sorted(L.ZERO_IS_MISSING)} — 복원 목록을 갱신하고 재측정할 것"

# ================================================================ 성분(물질) 행렬
log("=== 성분 행렬 감사 ===")
XI = pd.read_parquet(L.SRC / "X_ingredient.parquet")
FEATS = [c for c in XI.columns
         if c not in L.ING_DROP_EXACT and not c.startswith(L.ING_DROP_PREFIX)]
mI = {"무구조": pd.to_numeric(XI["MolWt"], errors="coerce").isna().to_numpy()}
log(f"성분 {len(XI)}행 × 학습피처 {len(FEATS)}열 · 무구조 {int(mI['무구조'].sum())}행")
PI = profile(XI, FEATS, mI, "성분", source_ing)
BAD_I = PI[PI["임퓨트된0"] > 0].sort_values("임퓨트된0", ascending=False)
log(f"임퓨트된 0 으로 판정된 성분 열 {len(BAD_I)}개")
for _, r in BAD_I.iterrows():
    log(f"  {r['열']:26} 결측 {r['결측']}행 · 0 {r['0']}행 중 {r['임퓨트된0']}행이 무구조")

# ==================================================== nan0 규약이 만드는 0 (c)
def cellcount(X, cols):
    A = X[cols].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    return {"셀": int(A.size), "결측": int(np.isnan(A).sum()),
            "0": int((A == 0).sum()),
            "결측률": round(float(np.isnan(A).mean()), 4),
            "0률": round(float((A == 0).mean()), 4),
            "nan0_적용후_0률": round(float(((A == 0) | np.isnan(A)).mean()), 4)}


CELL = {"제형": cellcount(XF, CHEM), "성분": cellcount(XI, FEATS)}
for k, v in CELL.items():
    log(f"{k} 행렬 {v['셀']:,}셀 — 결측 {v['결측']:,}({v['결측률']*100:.1f}%) · "
        f"0 {v['0']:,}({v['0률']*100:.1f}%) → nan0 적용시 0 이 "
        f"{v['nan0_적용후_0률']*100:.1f}%")

# ==================================================================== 산출
PROF = pd.concat([PF, PI], ignore_index=True)
PROF.to_csv(OUT / "열별_0결측_프로파일.csv", index=False, encoding="utf-8-sig")
BAD = pd.concat([BAD_F, BAD_I], ignore_index=True)
BAD.to_csv(OUT / "임퓨트된0_열목록.csv", index=False, encoding="utf-8-sig")

report = {
    "감사일": "2026-09-09",
    "질문": "결측을 0 으로 처리한 곳이 있는가. 있다면 결측으로 되돌릴 수 있는가.",
    "판정기준": {
        "구조적_0": "정보원이 그 행에 존재하는데 값이 0 — 실측값이다. 되돌리면 정보 손실",
        "임퓨트된_0": "정보원이 그 행에 아예 없는데 0 — 값을 만든 것이다. 되돌린다",
        "판정제외": "지시열·one-hot·결측플래그(*_isna·*_ok)·SDS 실측 물성(pc2_*·t11_*·"
                "ph_*)·CT 파생(ct_*). 0 이 정의상 실측값이거나 결측이 이미 NaN 인 열",
    },
    "마스크": {"제형_무조성행": int(mF["무조성"].sum()),
             "제형_무구조행": int(mF["무구조"].sum()),
             "성분_무구조행": int(mI["무구조"].sum())},
    "행렬_셀_통계": CELL,
    "임퓨트된0_열": [{"단위": r["단위"], "열": r["열"], "정보원": r["정보원"],
                 "임퓨트된0_행수": int(r["임퓨트된0"]),
                 "결측플래그열": bool(r["결측플래그열_있음"])} for _, r in BAD.iterrows()],
    "원인": "build_input_v5.py 의 `a = row[\"f_pct_surf_anionic\"] or 0.0` 관용구. "
          "None 이 0.0 으로 바뀐 뒤 합계·곱항에 들어가, 구성 4열은 결측인데 "
          "합계만 0 이 되고 결측 플래그열(`*_isna`)조차 생성되지 않았다.",
    "조치": [
        "lib_model.ZERO_IS_MISSING 에 해당 열을 등록하고, `native_복원` 결측규약에서 "
        "조성 미상 행의 값을 결측으로 되돌린다.",
        "build_input_v5.py 는 버전 고정 스크립트라 수정하지 않는다(과거 측정치 재현 "
        "근거). 복원은 모델 쪽에서 규약으로 처리한다.",
        "nan0 규약은 v6 재현 대조용으로만 남기고 대표값을 native_복원 으로 바꾼다. "
        "sklearn ≥1.4 의 트리는 결측을 분기 방향으로 학습하므로 임퓨트가 필요 없다.",
    ],
    "구조적_0_사례": [
        "f_pct_surfactant = 0 — 조성을 알고, 그 안에 계면활성제가 없다는 실측값",
        "f_MolWt_std = 0 — 구조를 아는 성분이 1종이라 분산이 0 (단일물질 제형)",
        "f_ct_eye_s1 = 0 — 구분을 아는 성분이 있고 그중 구분1 이 없다 "
        "(하나도 모르면 ct_predict 가 None 을 낸다)",
        "구조경보 지시열 = 0 — 해당 부분구조가 없다",
    ],
}
with open(OUT / "감사보고.json", "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=2, default=str)

md = ["# 결측 vs 0 감사 (2026-09-09)", "",
      "## 결론", "",
      f"- 학습 행렬에 **임퓨트된 0 은 {len(BAD)}열**이다 — 제형 {len(BAD_F)}열, "
      f"성분 {len(BAD_I)}열. 되돌릴 셀은 총 "
      f"{int(BAD['임퓨트된0'].sum()) if len(BAD) else 0}개다.",
      "- 나머지 0 은 전부 구조적 0(실측값)이다. 되돌리면 정보를 버리는 것이 된다.",
      f"- 그와 별개로 `nan0` 결측규약이 학습 직전 결측 셀 전부를 0 으로 바꾼다. "
      f"제형 행렬은 이 때문에 0 비율이 {CELL['제형']['0률']*100:.1f}% → "
      f"{CELL['제형']['nan0_적용후_0률']*100:.1f}%, 성분 행렬은 "
      f"{CELL['성분']['0률']*100:.1f}% → {CELL['성분']['nan0_적용후_0률']*100:.1f}% 로 뛴다. "
      f"**여기가 규모로는 훨씬 크다.**", "",
      "## 임퓨트된 0", "",
      "| 단위 | 열 | 정보원 | 임퓨트된 0 행수 | 결측 플래그열 |", "|---|---|---|---|---|"]
md += [f"| {r['단위']} | `{r['열']}` | {r['정보원']} | {int(r['임퓨트된0'])} | "
       f"{'있음' if r['결측플래그열_있음'] else '**없음**'} |" for _, r in BAD.iterrows()]
md += ["", "## 행렬 셀 통계", "",
       "| 단위 | 셀 | 결측 | 0 | nan0 적용 후 0 |", "|---|---|---|---|---|"]
md += [f"| {k} | {v['셀']:,} | {v['결측']:,} ({v['결측률']*100:.1f}%) | "
       f"{v['0']:,} ({v['0률']*100:.1f}%) | {v['nan0_적용후_0률']*100:.1f}% |"
       for k, v in CELL.items()]
md += ["", "## 조치"] + [f"{i+1}. {t}" for i, t in enumerate(report["조치"])] + [""]
(OUT / "README.md").write_text("\n".join(md), encoding="utf-8")
log(f"완료 → {OUT}/ (README.md · 감사보고.json · 열별_0결측_프로파일.csv · "
    f"임퓨트된0_열목록.csv)")
