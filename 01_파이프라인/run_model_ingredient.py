#!/usr/bin/env python3
"""성분(물질) 단위 모델 — eye · skin. 산출: 04_모델산출물/v7_성분모델/

1행 = 1물질. v6 까지 `X_ingredient.parquet` 는 빌드만 되고 어떤 학습 스크립트도
읽지 않았다. 이 스크립트가 그 층을 처음으로 독립 모델로 세운다.

제형 모델과의 차이 (설계 의도)
  단위    제형 1675행 → **물질 단위로 접은 뒤** 학습. 같은 물질이 여러 제형에
          중복 등장하던 것을 InChIKey 골격 블록으로 1행으로 만든다.
  라벨    제형 라벨(`y_*`)이 아니라 **성분별 GHS 구분**을 쓴다.
            ing_ghs_{ep}           Phase 1 수집(PubChem LCSS / ECHA C&L)
            ing_ghs_indep_{ep}_cat 정채윤 GHS 조사(ACTIVE)
          두 출처는 행 수준에서 겹치지 않는다(코드로 assert). 같은 물질에 상이
          구분이 붙은 경우만 규제 보수성 원칙으로 심한 쪽을 채택하고 목록을
          `라벨충돌_물질.csv` 로 남긴다.
  피처    **구조에서 나온 것만.** RDKit 디스크립터 31종 + 구조경보 + 이온성/
          Potts-Guy logKp + (arm2) MACCS·Morgan 지문. 농도·제형맥락·라벨유도원은
          명시 목록으로 배제한다(lib_model.ING_DROP_*).
  CV      물질이 CV 단위라 제형 group_key 누출이 원리적으로 없다. 그 위에 Murcko
          골격을 group 으로 준 StratifiedGroupKFold 로 동족체 분산도 막는다.

감작은 학습하지 않는다(총책임자 지시). v6 까지의 기록은 v7_감작/ 에 보존한다.

읽기 전용 입력: v4_fixed/X_ingredient.parquet, fp_ing_*.npz, input_dataset_v6.xlsx
"""
from __future__ import annotations

import json

import numpy as np
import openpyxl
import pandas as pd

import lib_model as L

EPS = ("eye", "skin")
FEAT_CANON = "구조+지문"      # 대표 피처셋. 구조 단독 arm 도 부가 행으로 남긴다.
# 성분 행렬에는 임퓨트된 0 이 없다(audit_zero_vs_missing.py 로 확인). 그래서
# 복원할 것이 없고 대표 규약은 `native` 다 — 제형과 달리 `native_복원` 을 만들지 않는다.
IMP = ("nan0", "native")
IMP_CANON = "native"
EXCL_NOTES = [
    "`eye` × EU_CLP: 정채윤 조사의 눈 `2` 는 2A/2B 세분이 없다. EU 는 2A=분류 / "
    "2B=비분류로 갈리므로 판정 불가여서 해당 물질을 EU 레이어에서 제외한다(60물질, "
    "n 398 → 338). 대표 기준(K_REACH)은 2B 도 분류로 보므로 이 제외가 없다.",
    "같은 물질에 두 출처가 상이 구분을 준 경우 규제 보수성 원칙으로 심한 쪽을 채택하고 "
    "전 건을 `라벨충돌_물질.csv` 에 남긴다 — 없는 값을 만드는 것이 아니라 이미 수집된 "
    "두 값 중 하나를 고르는 것이다.",
    "소수 클래스가 폴드수(5) 미만인 셀은 측정을 생략한다. 억지로 폴드를 줄이지 않는다.",
]
OUT = L.ROOT / "04_모델산출물" / "v7_성분모델"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "run.log")

log("=== 성분(물질) 단위 모델 (eye · skin) ===")
log(f"대표 기준: 관할 {L.CANON_JUR} · 결측규약 {IMP_CANON} · 피처 {FEAT_CANON}")
sub = L.SubstanceData(log, EPS, OUT)
XM = sub.matrices()
SCAF = sub.SCAF
log(f"Murcko 골격 그룹 {len(set(SCAF))}종 / 물질 {sub.n_sub}종")

# 관할 프로파일 중복 계산 방지 — 라벨·마스크가 동일한 관할끼리 묶는다.
LAYER, GROUPS = {}, []
for ep in EPS:
    seen = {}
    for jn in L.JUR:
        b, k, ex = sub.layer(jn, ep)
        LAYER[(jn, ep)] = (b, k, ex)
        seen.setdefault((b[k].tobytes(), k.tobytes()), []).append(jn)
    for jns in seen.values():
        GROUPS.append((ep, jns))
log("계산 단위: " + "; ".join(f"{ep}:{'+'.join(j)}" for ep, j in GROUPS))

RES, EXCL = [], []
for ep, jns in GROUPS:
    b, k, ex = LAYER[(jns[0], ep)]
    y = b[k]
    g = SCAF[k]
    n_pos, n_neg = int(y.sum()), int((y == 0).sum())
    if ex:
        EXCL.append({"endpoint": ep, "관할": "+".join(jns), **ex})
        log(f"  {ep}/{'+'.join(jns)} 제외: {ex}")
    if min(n_pos, n_neg) < L.N_SPLITS:
        log(f"  {ep}/{'+'.join(jns)} n={len(y)} 양성={n_pos} — 소수 클래스가 "
            f"폴드수 미만이라 측정 생략(값을 만들지 않는다)")
        continue
    folds = L.make_folds(y, g)                   # arm·결측규약 간 고정
    for im in IMP:
        for fs in ("구조", "구조+지문"):
            row = L.cv_eval(XM[(fs, im)][k], y, folds,
                            {"단위": "성분", "endpoint": ep, "관할": "+".join(jns),
                             "피처": fs, "결측처리": im, "CT": "해당없음"})
            row["골격그룹수"] = int(len(set(g)))
            RES.append(row)
            log(f"  {ep:4} {row['관할']:26} [{im:6}] {fs:8} n={row['n']:4} "
                f"p={row['유병률']:.3f} AUC={row['roc_auc']:.4f} "
                f"PR={row['pr_auc']:.4f} MCC={row['MCC']:.3f} BA={row['BA']:.3f} "
                f"재현율={row['recall']:.3f} 정밀도={row['precision']:.3f}")

R = L.order_cols(L.mark_canon(pd.DataFrame(RES), canon_impute=IMP_CANON,
                              canon_feat=FEAT_CANON))
for _, r in R[R["대표"] == 1].iterrows():
    log(f"대표 ▶ 성분/{r['endpoint']:4} {r['관할']:30} n={r['n']} p={r['유병률']} "
        f"AUC={r['roc_auc']} MCC={r['MCC']} BA={r['BA']}")
L.write_jur_doc(OUT, "성분", R, extra=EXCL_NOTES)
R.to_csv(OUT / "지표_성분모델.csv", index=False, encoding="utf-8-sig")

# 라벨 대장 — 어떤 물질이 무슨 근거로 어떤 구분을 받았는지 감사 가능하게 남긴다.
LED = pd.DataFrame({"물질키_InChIKey14": sub.AGG.index})
LED["Murcko골격"] = SCAF
for ep in EPS:
    LED[f"{ep}_구분"] = sub.LAB[ep].to_numpy()
    LED[f"{ep}_출처"] = sub.LABSRC[ep].to_numpy()
_rep = (sub.ING[sub.ING["_skel"].notna()]
        .groupby("_skel", sort=True)
        .agg(대표성분명=("ing_name_best", "first"), 대표CAS=("cas", "first"),
             대표SMILES=("smiles", "first"), 등장제형수=("Formulation_ID", "nunique")))
LED = LED.merge(_rep, left_on="물질키_InChIKey14", right_index=True, how="left")
LED.to_csv(OUT / "물질_라벨대장.csv", index=False, encoding="utf-8-sig")
log(f"물질 라벨대장 {len(LED)}행 → 물질_라벨대장.csv")

wb = openpyxl.Workbook()
ws = wb.active
ws.title = "성분모델"
ws.append(list(R.columns))
for _, r in R.iterrows():
    ws.append(["" if v is None else v for v in r.tolist()])
ws.freeze_panes = "I2"
ws.auto_filter.ref = ws.dimensions
ws2 = wb.create_sheet("물질_라벨대장")
ws2.append(list(LED.columns))
for _, r in LED.iterrows():
    ws2.append(["" if (v is None or (isinstance(v, float) and np.isnan(v))) else v
                for v in r.tolist()])
ws2.freeze_panes = "A2"
ws2.auto_filter.ref = ws2.dimensions
wb.save(OUT / "지표_성분모델.xlsx")

with open(OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "단위": f"물질(substance). 1행 = 1물질, 고유 물질 {sub.n_sub}종",
        "대표모델": {
            "기준": {"관할": L.CANON_JUR, "결측처리": IMP_CANON, "피처": FEAT_CANON},
            "관할근거": L.JUR_DOC[L.CANON_JUR],
            "선정이유": "제형 모델과 같은 대표 관할을 쓴다(K_REACH·US_OSHA 투영표 동일). "
                    "눈 2B 를 분류로, 피부 구분3 을 비분류로 보는 조합이다.",
            "결측처리_이유": "성분 행렬에는 임퓨트된 0 이 없다(v7_결측감사 확인). 되돌릴 "
                       "것이 없으므로 native 가 대표이고 native_복원 arm 을 만들지 않는다.",
            "행": [r for r in R[R["대표"] == 1].to_dict("records")],
        },
        "규제처리_명시": "규제처리_명시.md 참조 (관할별 근거 법령 + 구분→라벨 투영표 전체)",
        "물질_동일성_키": "RDKit InChIKey 앞 14자(골격 블록). "
                    "canonicalize_ingredient_smiles.py 의 확립된 규칙과 동일",
        "엔드포인트": list(EPS),
        "감작": "학습 제외. v6 까지의 기록은 04_모델산출물/v7_감작/ 에 동결 보존",
        "라벨": {
            "출처": ["ing_ghs_{ep} = Phase 1 수집(PubChem LCSS / ECHA C&L)",
                   "ing_ghs_indep_{ep}_cat = 정채윤 GHS 조사(ACTIVE)"],
            "겹침": "행 수준 교집합 0 — assert 로 고정",
            "충돌해소": "같은 물질에 상이 구분이 붙은 경우 규제 보수성 원칙으로 "
                    "심각한 쪽 채택. 전체 목록은 라벨충돌_물질.csv",
        },
        "피처": "구조에서 나온 것만 — RDKit 디스크립터 31종 · 구조경보 · 이온성 · "
              "Potts-Guy logKp · (arm2) MACCS 167 + Morgan(빈도≥5). "
              "농도·제형맥락(formulation_type_*)·라벨유도원은 명시 배제",
        "CV": "Murcko 골격 group 기준 StratifiedGroupKFold 5-fold × 5 seed. "
              "물질이 CV 단위이므로 제형 group_key 누출은 원리적으로 없다",
        "임계값": f"{L.THRESHOLD} 고정",
        "임계값_주의": L.THRESHOLD_NOTE,
        "레이어_제외": EXCL,
        "제형모델과_비교_주의": [
            "성분 모델과 제형 모델은 예측 대상이 다르다(물질의 고유 유해성 vs "
            "혼합물의 분류). n·유병률·CV 단위가 모두 달라 지표를 직접 비교할 수 없다.",
            "성분 모델의 용도는 (a) 순수 QSAR 성능의 독립 측정, (b) CT 가산식에 "
            "넣을 성분 구분의 예측 보완이다. 후자는 별도 승인 사안이다.",
        ],
        "행": RES,
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT}/지표_성분모델.csv / .xlsx / 요약.json")
