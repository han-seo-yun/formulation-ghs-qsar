#!/usr/bin/env python3
"""제형(혼합물) 단위 모델 — eye · skin. 산출: 04_모델산출물/v7_제형모델/

1행 = 1제형(1675). 피처는 성분 디스크립터 6모멘트 집계 + 역할별 농도합 +
계면활성제 전하 상호작용 + 제형 고유 물성(CIPAC 코드·pc2_*·t11_*) + CT 가산 블록.

대표 산출은 엔드포인트당 1개(제형×눈, 제형×피부)다 — 대표관할 `K_REACH` 계열 ×
결측규약 `native_복원` × CT `권고` arm. 나머지 관할·규약·arm 조합도 같은 파일에
부가 행으로 남겨(`대표` 열 = 0) 기준을 갈아탈 때 재실행이 필요 없게 한다.

v6 대비 달라지는 것은 (1) **감작을 학습하지 않는다**, (2) 결측규약 `native_복원`
추가다. 기존 `nan0`·`native` × CT arm 16셀은 전처리·폴드·추정기·임계값이 v6 과
동일하며, 스크립트 중간의 `v6_lock` 이 v6 산출물과 지표가 1e-9 이내로 같은지
검사한다. 다르면 분리 리팩터에 결함이 있다는 뜻이다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx
"""
from __future__ import annotations

import json

import openpyxl
import pandas as pd

import lib_model as L

EPS = ("eye", "skin")
CT_CANON = "CT_권고"          # 대표 arm. A0 도 부가 행으로 함께 남긴다.
EXCL_NOTES = [
    "`skin` × 구분3 채택 관할(UN_GHS): EPA 피부 4등급 444행 제외. EPA 4등급은 "
    "'EPA 기준 미분류'이지 'GHS 구분3'이 아니라 판정 불가다. 대표 기준(K_REACH)은 "
    "구분3 을 채택하지 않으므로 이 제외가 적용되지 않고 n 이 615 → 1059 로 늘어난다.",
    "`eye` 전 관할: 음성 회수분(`sds_v1_negative_recovered`) 109행 제외 — 라벨 출처 "
    "편향 감사(audit_label_source_bias) 결과 마스크 유지가 현행 결정이다.",
    "`skin` 원본 구분 `2A`/`2B` 82행은 GHS 에 없는 표기라 L1 정본에서 `2` 로 정정했다.",
]
OUT = L.ROOT / "04_모델산출물" / "v7_제형모델"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "run.log")

log("=== 제형 단위 모델 (eye · skin) ===")
log(f"대표 기준: 관할 {L.CANON_JUR} · 결측규약 {L.CANON_IMPUTE} · CT {CT_CANON}")
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
    for im in L.IMPUTES:
        for xn in ("CT_A0", "CT_권고"):
            row = L.cv_eval(XM[(xn, im)][k], y, folds,
                            {"단위": "제형", "endpoint": ep, "관할": "+".join(jns),
                             "피처": "제형집계+물성+CT", "결측처리": im, "CT": xn})
            RES.append(row)
            log(f"  {ep:4} {row['관할']:26} [{im:11}] {xn:7} n={row['n']:5} "
                f"p={row['유병률']:.3f} AUC={row['roc_auc']:.4f} "
                f"PR={row['pr_auc']:.4f} MCC={row['MCC']:.3f} BA={row['BA']:.3f} "
                f"재현율={row['recall']:.3f} 정밀도={row['precision']:.3f}")

R = pd.DataFrame(RES)
L.v6_lock(R, log, unit="제형")                   # nan0/native × CT arm 16셀 대조
R = L.order_cols(L.mark_canon(R, canon_ct=CT_CANON))
for _, r in R[R["대표"] == 1].iterrows():
    log(f"대표 ▶ 제형/{r['endpoint']:4} {r['관할']:26} n={r['n']} p={r['유병률']} "
        f"AUC={r['roc_auc']} MCC={r['MCC']} BA={r['BA']}")
L.write_jur_doc(OUT, "제형", R, extra=EXCL_NOTES)

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
        "대표모델": {
            "기준": {"관할": L.CANON_JUR, "결측처리": L.CANON_IMPUTE, "CT": CT_CANON},
            "관할근거": L.JUR_DOC[L.CANON_JUR],
            "선정이유": "관할 4개의 차이는 '눈 2B 를 분류로 보는가'와 '피부 구분3 을 "
                    "분류로 보는가' 두 스위치뿐이고, 둘을 동시에 만족하는 관할이 "
                    "K_REACH·US_OSHA(투영표 동일)다. 국내·미국 두 관할에 투영 손실이 "
                    "0 이고 눈은 UN GHS 원문과도 같다. 성능으로 고른 기준이 아니다.",
            "성능_주의": "눈은 이 기준(2B=양성)에서 유병률 0.683 · AUC 가 EU_CLP 기준"
                     "(2B=음성, 유병률 0.370)보다 낮다. 두 기준은 예측 대상이 다르므로 "
                     "AUC 가 높은 쪽을 고르는 것은 근거가 되지 않는다 — 부가 행으로 함께 남긴다.",
            "행": [r for r in R[R["대표"] == 1].to_dict("records")],
        },
        "규제처리_명시": "규제처리_명시.md 참조 (관할별 근거 법령 + 구분→라벨 투영표 전체)",
        "결측처리": {
            "대표": L.CANON_IMPUTE,
            "규약": {"nan0": "결측을 0 으로 채운다. v6 재현 대조용으로만 남긴다",
                   "native": "결측을 결측으로 둔다(sklearn 트리 네이티브 NaN 분기)",
                   "native_복원": "native + 빌드 단계에서 0 으로 굳은 결측을 되돌린다"},
            "복원열": list(L.ZERO_IS_MISSING),
            "복원_감사근거": "04_모델산출물/v7_결측감사/ (audit_zero_vs_missing.py). "
                       "이 2열 외에 제형·성분 행렬에 임퓨트된 0 은 없다",
        },
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
