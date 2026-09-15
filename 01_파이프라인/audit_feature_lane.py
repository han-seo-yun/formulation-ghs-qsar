#!/usr/bin/env python3
"""피처 노선(정보원) 귀속 감사. 산출: 04_모델산출물/v8_공통/

3인 3노선 비교 설계(05_제안/노선분리_비교규약.md)의 전제는 **피처가 어느 문서
계층에서 나왔는지** 열 단위로 확정되어 있다는 것이다. 이 스크립트가 그 판정을
근거와 함께 파일로 못 박는다.

  L1       완제품 SDS 선언값    pc2_*(9절 물성) · form_code_* · formulation_type_*
  L2       성분 위험정보 × 농도  f_ct_*/ct_*(GHS 가산) · f_pct_*(역할별 농도) · 조성
  L3       구조(SMILES)        RDKit 디스크립터 집계 · 구조경보 · 지문유사도
  L2L3결합  두 정보원이 다 필요   농도가중 디스크립터 · 농도×구조량 상호작용 · 전하별 농도
  금지      라벨 공파생          t11_*(라벨과 같은 문서) · has_* · trainable*

**`L2L3결합` 을 왜 따로 두는가.** 판정 기준은 "이 열을 계산하는 데 어느 정보원이
필요한가" 이고, 필요 정보원이 둘이면 어느 한쪽 단독 arm 에 넣는 순간 그 노선의
가설이 반증 불가가 된다. 세 부류가 여기 해당한다.

  `f_pct_surf_anionic` 계열 6열 — 계면활성제 전하 클래스는 조성표가 아니라 RDKit
      SMARTS 매칭으로 정한다(`lib_desc.surfactant_class`, 성분 5287행 중 5237행
      = 99.1% 가 `surf_class_src=="structure"`). 농도합은 조성표에서 오지만 분류
      기준이 구조다. L2 단독에 두면 "성분 위험정보 × 농도만으로" 라는 L2 가설이
      성립하지 않는다.
  `f_*_wmean` 19열 — 디스크립터를 성분 농도로 가중평균한 값이다. 구조값이 없으면
      계산 불가이고 농도가 없어도 계산 불가다. L3 단독에 두면 "구조식만으로" 라는
      L3 가설이 성립하지 않는다.
  `f_surf_x_logp` 계열 3열 — 농도 × 구조유래량의 곱. 위와 같은 논리다.

이름만 보고 갈라지지 않는 경계가 있어서 명시 규칙을 먼저 적용하고, 남은 열은
디스크립터 어휘 대조로 판정한 뒤 미판정분을 따로 보고한다. 미판정이 남으면
비교 규약을 배포할 수 없으므로 assert 로 세운다(현재 허용: `ph_best` 1열, §5-2
결정 대기).

읽기 전용 입력: v4_fixed/feature_role_manifest.csv, X_formulation.parquet
"""
from __future__ import annotations

import json

import pandas as pd

import lib_model as L

OUT = L.ROOT / "04_모델산출물" / "v8_공통"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "audit_feature_lane.log")

# ------------------------------------------------------------ 어휘 (규칙보다 먼저)
# 조성표에서 성분마다 배정한 역할 어휘. f_n_{role} · f_has_{role} 형태로 집계된다.
# f_n_* 를 접두 일치로 뭉뚱그리면 f_n_smiles(구조 해석 성공 개수 = L3 정보 가용성)
# 같은 열이 L2 로 오분류된다. 그래서 역할명을 명시 열거한다.
ROLES = ("ing", "pct_known", "active", "active_presumed", "water", "surfactant",
         "solvent", "synergist", "carrier_inert", "propellant", "ph_adjuster",
         "preservative", "thickener", "fragrance_dye", "unknown",
         "surf_anionic", "surf_cationic", "surf_nonionic")
ROLE_COLS = frozenset(f"f_{k}_{r}" for k in ("n", "has") for r in ROLES)

# 구조 해석이 성공했는지를 재는 열. 값은 농도·개수 단위지만 재는 대상이 L3 정보의
# 가용성이므로 L3 이다. 현재는 학습 대상이 아니지만 승격되면 f_pct_ 접두 규칙에
# 조용히 걸려 L2 로 새므로 규칙 최상단에 명시 배제한다.
STRUCT_AVAIL = frozenset({"f_pct_with_structure", "f_smiles_coverage", "f_n_smiles"})

# 계면활성제 전하 클래스는 RDKit SMARTS 매칭으로 정한다(lib_desc.surfactant_class).
# 농도합은 조성표에서 오지만 분류 기준이 구조여서 두 정보원이 다 필요하다.
SURF_CHARGE = frozenset({
    "f_pct_surf_anionic", "f_pct_surf_cationic", "f_pct_surf_nonionic",
    "f_pct_surf_total", "f_surf_anionic_nonionic", "f_surf_charge_imbalance"})

# §5-2 결정 대기. 원액 pH 는 정보원상 완제품 SDS 9절(L1)이지만 강산·강염기
# 비가산 예외가 GHS 혼합물 규칙의 일부여서 L2 논리에도 필요하다. 결정 전까지
# 미판정으로 남기고, 어느 노선 arm 에도 넣지 않는다.
# 접두가 아니라 완전일치로 둔다 — startswith("ph") 는 phosphate_frac·phys_form_*
# 같은 미래 열까지 삼켜 assert 를 우회한 채 모든 arm 에서 빼 버린다.
PENDING_EXACT = frozenset({"ph_best"})

# ---------------------------------------------------------------- 판정 규칙
# 순서대로 적용한다. 앞선 규칙이 이기므로 경계 함정을 위에 둔다.
#   (lane, 근거, 판정자)  — 판정자는 열 이름을 받아 bool 을 돌려준다.
#
# 짧은 부분문자열 토큰은 쓰지 않는다. `"_sim" in c` 는 지문 유사도를 잡으려던
# 토큰인데 `f_simpson`(농도 비율로 계산하는 조성 다양성 지수)까지 삼켜서 순수
# 조성 지표를 L3 단독 arm 에 넣었다. 형제 열 f_shannon 은 같은 p 벡터에서 나오는데
# L2 였다. 토큰은 `tanimoto` 처럼 뜻이 유일한 것만 쓴다.
RULES = [
    ("L3", "구조 해석 성공 여부를 재는 열 — 값의 단위와 무관하게 L3 정보 가용성 지표",
     lambda c: c in STRUCT_AVAIL),
    # 농도가중 규칙이 구조경보 규칙보다 위에 있어야 한다. 아래에 두면
    # f_n_structural_alerts_wmean 이 구조경보 규칙에 먼저 걸려 L3 단독에 남는다.
    ("L2L3결합", "디스크립터(L3)를 성분 농도(L2)로 가중평균한 값. 두 정보원이 다 필요",
     lambda c: c.endswith("_wmean")),
    ("L3", "구조경보(SMILES 유래) 집계. 이름의 f_n_ 접두는 개수 집계를 뜻할 뿐이다",
     lambda c: c.startswith("f_n_structural_alerts")),
    ("L2L3결합", "농도(L2) × 구조유래량(logP/logKp) 상호작용항. 두 정보원이 다 필요",
     lambda c: c in ("f_surf_x_logp", "f_surf_x_logkp", "f_solvent_x_logp")),
    ("L2L3결합", "전하 클래스는 RDKit SMARTS 매칭(구조), 농도합은 조성표. 두 정보원이 "
                "다 필요 — 성분 5287행 중 99.1%가 surf_class_src=='structure'",
     lambda c: c in SURF_CHARGE),
    ("L2", "GHS 혼합물 가산식 출력(성분 구분 × 농도)",
     lambda c: c.startswith(("f_ct_", "ct_"))),
    ("L1", "완제품 SDS 9절 선언물성",
     lambda c: c.startswith("pc2_")),
    ("L1", "완제품 SDS 제형 코드·유형(CIPAC)",
     lambda c: c.startswith(("form_code", "formulation_type"))),
    ("L2", "조성표 유래 — 역할별 농도합·최대농도·조성 다양성(f_shannon·f_simpson)",
     lambda c: c.startswith(("f_pct_", "f_surf_"))
     or c in ("f_max_pct", "f_shannon", "f_simpson", "f_n_ing", "f_pct_sum_known")),
    ("L3", "RDKit 디스크립터 6모멘트 집계 · 이온성 · logKp · 지문 유사도",
     lambda c: any(t in c for t in (
         "MolWt", "ExactMolWt", "MolLogP", "MolMR", "TPSA", "NumH", "NumRotatable",
         "NumAromatic", "NumAliphatic", "RingCount", "FractionCSP3", "HeavyAtom",
         "NumHetero", "NOCount", "NHOH", "NumSaturated", "LabuteASA", "BalabanJ",
         "BertzCT", "Chi0v", "Chi1v", "Kappa", "HallKier", "PartialCharge",
         "NumValence", "qed", "logKp", "logkp", "tanimoto", "is_ionic",
         "formal_charge", "n_pos_atoms", "n_neg_atoms", "n_fragments", "has_metal",
         "max_alkyl", "_alert"))),
    ("L2", "조성표의 역할 배정 유래 — 해당 역할 성분의 개수/존재 여부",
     lambda c: c in ROLE_COLS),
]
LANES = ("L1", "L2", "L3", "L2L3결합")


def adjudicate(col: str):
    for lane, reason, pred in RULES:
        if pred(col):
            return lane, reason
    if col in PENDING_EXACT:
        return "미판정", "원액 pH — 규약 §5-2 총책임자 결정 대기(L1 배제 vs L2 예외 허용)"
    return "미판정", "규칙 미적용 — 판정 규칙 추가 필요"


# ---------------------------------------------------------------- 감사 실행
MAN = pd.read_csv(L.SRC / "feature_role_manifest.csv")
MANF = MAN[MAN["sheet"] == "formulation"].copy()

# 학습에 실제로 들어가는 열 집합. lib_model.FormulationData 와 같은 식이어야 한다.
CHEM = sorted(c for c in MANF[(MANF["feature_role"] == "chemistry")
                              & (~MANF["exclude_by_default"].astype(bool))]["column"]
              if c != "Formulation_ID")
X = pd.read_parquet(L.SRC / "X_formulation.parquet")
missing = [c for c in CHEM if c not in X.columns]
assert not missing, f"manifest 에만 있고 행렬에 없는 열: {missing[:5]}"
log(f"학습 대상 피처 {len(CHEM)}열 (manifest chemistry & not exclude_by_default)")

rows = []
for c in MANF["column"]:
    if c == "Formulation_ID":
        continue
    trainable = c in CHEM
    if not trainable:
        role = MANF.loc[MANF["column"] == c, "feature_role"].iloc[0]
        coder = bool(MANF.loc[MANF["column"] == c, "sds_coderived"].iloc[0])
        lane = "금지_라벨공파생" if coder or role == "label_coderived" else "미사용"
        reason = ("라벨과 같은 문서에서 판독 — 피처로 쓰면 답이 샌다"
                  if lane == "금지_라벨공파생" else f"feature_role={role}, 학습 미사용")
    else:
        lane, reason = adjudicate(c)
    rows.append({"열": c, "노선": lane, "학습대상": trainable, "판정근거": reason,
                 "feature_role": MANF.loc[MANF["column"] == c, "feature_role"].iloc[0],
                 "sds_coderived": bool(MANF.loc[MANF["column"] == c,
                                                "sds_coderived"].iloc[0])})

LANE = pd.DataFrame(rows)
TR = LANE[LANE["학습대상"]]

log("--- 학습 대상 피처의 노선 분해 ---")
for lane, n in TR["노선"].value_counts().items():
    log(f"  {lane:16} {n:4}열")
log("--- 학습 미사용/금지 ---")
for lane, n in LANE[~LANE["학습대상"]]["노선"].value_counts().items():
    log(f"  {lane:16} {n:4}열")

pend = TR[TR["노선"] == "미판정"]["열"].tolist()
log(f"미판정 {len(pend)}열: {pend}")
# 규약 §5-2 의 ph_best 1열만 허용한다. 그 밖의 미판정이 생기면 규칙을 고쳐야 한다.
unexpected = [c for c in pend if c not in PENDING_EXACT]
assert not unexpected, f"판정 규칙 미적용 열: {unexpected}"

assert set(TR["노선"]) <= set(LANES) | {"미판정"}, "노선 값 이탈"
N = {k: int((TR["노선"] == k).sum()) for k in LANES}
assert sum(N.values()) + len(pend) == len(CHEM), "노선 합계가 피처 수와 다르다"
# 단독 arm 에 결합 열이 새지 않았는지 직접 확인한다 — 이 감사의 존재 이유다.
_L2 = set(TR[TR["노선"] == "L2"]["열"])
_L3 = set(TR[TR["노선"] == "L3"]["열"])
assert not (_L2 | _L3) & (SURF_CHARGE | STRUCT_AVAIL), "결합·가용성 열이 단독 노선에 남음"
assert not any(c.endswith("_wmean") for c in _L2 | _L3), "농도가중 열이 단독 노선에 남음"
assert "f_simpson" in _L2 and "f_shannon" in _L2, "조성 다양성 형제 열이 갈렸다"

# ---------------------------------------------------------------- arm 정의 배포
# 노선별 단독 arm 과 점증 ablation arm. 세 노선이 같은 파일을 읽어야 비교가 성립한다.
#
# **단독 arm 은 그 노선의 정보원만으로 계산 가능한 열만 담는다.** 결합 열을 넣으면
# 그 노선의 가설이 반증 불가가 되므로, 결합 열을 쓰는 측정은 `+결합` 부가 arm 으로만
# 낸다. 정보는 버리지 않고 대표 자격만 뺀다.
def cols(*lanes):
    return sorted(TR[TR["노선"].isin(lanes)]["열"])


ARMS = {
    "L1단독": cols("L1"),
    "L2단독": cols("L2"),
    "L3단독": cols("L3"),
    "L2+결합": cols("L2", "L2L3결합"),
    "L3+결합": cols("L3", "L2L3결합"),
    "L2+L1": cols("L2", "L1"),
    "L2+L3": cols("L2", "L3"),
    "전체_노선혼합": cols(*LANES),
}
for k, v in ARMS.items():
    log(f"arm {k:14} {len(v):4}열")

LANE.to_csv(OUT / "피처노선_판정.csv", index=False, encoding="utf-8-sig")
with open(OUT / "피처노선_arm.json", "w", encoding="utf-8") as f:
    json.dump({
        "생성": "audit_feature_lane.py",
        "규약": "05_제안/노선분리_비교규약.md §3",
        "학습대상_열수": len(CHEM),
        "노선_열수": {**N, "미판정": len(pend)},
        "미판정_열": pend,
        "미판정_처리": "어느 arm 에도 넣지 않는다. 규약 §5-2 결정 후 재실행",
        "L2L3결합_이유": "두 정보원이 다 있어야 계산되는 열. 단독 arm 에 넣으면 그 "
                    "노선의 가설이 반증 불가가 된다. 전하별 농도 6열(전하 클래스가 "
                    "RDKit SMARTS 유래) · 농도가중 디스크립터 19열 · 농도×구조량 "
                    "상호작용 3열.",
        "L2L3결합_열": cols("L2L3결합"),
        "금지_라벨공파생_열수": int((LANE["노선"] == "금지_라벨공파생").sum()),
        "arm": ARMS,
    }, f, ensure_ascii=False, indent=2)
# 절대경로를 로그에 남기지 않는다 — 산출물이 공유 대상이고 저장소는 사용자명을
# 익명화한 상태다. 새 로그가 그것을 되돌리면 안 된다.
log(f"완료 → {OUT.relative_to(L.ROOT)}/피처노선_판정.csv / 피처노선_arm.json")
