#!/usr/bin/env python3
"""제형(혼합물) 단위 모델 — eye · skin. 산출: 04_모델산출물/v7_제형모델/

1행 = 1제형(1675). 피처는 성분 디스크립터 6모멘트 집계 + 역할별 농도합 +
계면활성제 전하 상호작용 + 제형 고유 물성(CIPAC 코드·pc2_*·t11_*) + CT 가산 블록.

v6 대비 달라지는 것은 **감작을 학습하지 않는다**는 것뿐이다. eye/skin 의 전처리·
폴드·추정기·임계값은 v6 과 동일하며, 스크립트 말미의 `v6_lock` 이 v6 산출물과
지표가 1e-9 이내로 같은지 검사한다. 다르면 분리 리팩터에 결함이 있다는 뜻이다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx
"""
from __future__ import annotations

import json

import openpyxl
import pandas as pd

import lib_model as L

EPS = ("eye", "skin")
OUT = L.ROOT / "04_모델산출물" / "v7_제형모델"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "run.log")

log("=== 제형 단위 모델 (eye · skin) ===")
data = L.FormulationData(log)
LAYER = data.layers(EPS)
log("L1/L2/마스크 재현 확인 — eye 1044(negrec 마스크 적용), skin 615/1059")
XM = data.matrices()
GROUPS = L.jur_groups(data, EPS, LAYER)
log("계산 단위: " + "; ".join(f"{ep}:{'+'.join(j)}" for ep, j in GROUPS))

RES = []
for ep, jns in GROUPS:
    b, k = LAYER[(jns[0], ep)]
    y = b[k]
    folds = L.make_folds(y, data.GK[k])          # CT arm·결측규약 간 고정
    for im in ("nan0", "native"):
        for xn in ("CT_A0", "CT_권고"):
            row = L.cv_eval(XM[(xn, im)][k], y, folds,
                            {"단위": "제형", "endpoint": ep, "관할": "+".join(jns),
                             "피처": "제형집계+물성+CT", "결측처리": im, "CT": xn})
            RES.append(row)
            log(f"  {ep:4} {row['관할']:26} [{im:6}] {xn:7} n={row['n']:5} "
                f"p={row['유병률']:.3f} AUC={row['roc_auc']:.4f} "
                f"PR={row['pr_auc']:.4f} MCC={row['MCC']:.3f} BA={row['BA']:.3f} "
                f"재현율={row['recall']:.3f} 정밀도={row['precision']:.3f}")

R = L.order_cols(pd.DataFrame(RES))
L.v6_lock(R, log, unit="제형")

R.to_csv(OUT / "지표_제형모델.csv", index=False, encoding="utf-8-sig")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "제형모델"
ws.append(list(R.columns))
for _, r in R.iterrows():
    ws.append(["" if v is None else v for v in r.tolist()])
ws.freeze_panes = "I2"
ws.auto_filter.ref = ws.dimensions
wb.save(OUT / "지표_제형모델.xlsx")

with open(OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "단위": "제형(혼합물). 1행 = 1제형, n=1675 중 관할별 라벨 보유분",
        "엔드포인트": list(EPS),
        "감작": "학습 제외. v6 까지의 기록은 04_모델산출물/v7_감작/ 에 동결 보존. "
              "단 f_ct_sens_* 는 라벨이 아닌 혼합물 가산 디스크립터라 피처에 남는다",
        "피처": "성분 디스크립터 6모멘트 집계 + 역할별 농도합 + 계면활성제 전하 "
              "상호작용 + 제형 물성(CIPAC·pc2_*·t11_*) + CT 가산 26열 + 결측플래그",
        "CV": "group_key(587) 기준 StratifiedGroupKFold 5-fold × 5 seed",
        "임계값": f"{L.THRESHOLD} 고정",
        "임계값_주의": L.THRESHOLD_NOTE,
        "v6_재현": "eye/skin 전 셀이 v6_jurisdiction/전체지표_ROC_F1.csv 와 1e-9 이내 동일",
        "행": RES,
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT}/지표_제형모델.csv / .xlsx / 요약.json")
