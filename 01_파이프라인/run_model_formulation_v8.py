#!/usr/bin/env python3
"""제형(혼합물) 단위 모델 v8 — 피처 정리판. 산출: 04_모델산출물/v8_제형모델/

`run_model_formulation.py`(v7) 와 다른 점은 **피처셋 한 가지**다. 2026-09-21
총책임자 결정으로 설계 결함 3건에 해당하는 11열을 학습 피처에서 뺀다
(`lib_model.FEAT_DROP`): 죽은 열 1 · 완전 중복쌍의 한쪽 4 · 감작 CT 6.
전처리·폴드·추정기·임계값·관할 투영은 v7 과 완전히 동일하다.

왜 v7 러너를 고치지 않고 새로 만드는가
  `run_model_formulation.py` 는 동결 스크립트다 — v7 대표 산출물의 재현 근거이고,
  그 안의 `v6_lock` 이 v6 수치와의 동일성을 계속 증명해야 한다. 이 스크립트는
  `v6_원본` 피처셋을 같은 표에 부가 행으로 함께 남겨 그 lock 을 이어받는다.
  즉 한 파일 안에서 (a) v6 재현이 아직 성립하는지, (b) 정리가 수치를 얼마나
  바꾸는지를 동시에 읽을 수 있다.

대표 산출은 엔드포인트당 1개(제형×눈, 제형×피부) — 대표관할 `K_REACH` 계열 ×
결측규약 `native_복원` × CT `권고` arm × 피처셋 `v7_정리`.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx
"""
from __future__ import annotations

import json

import openpyxl
import pandas as pd

import lib_model as L

EPS = ("eye", "skin")
CT_CANON = "CT_권고"
FS_ORDER = ("v6_원본", "v7_정리")      # 표에 남기는 순서. 대표는 L.CANON_FEATSET.
EXCL_NOTES = [
    "`skin` × 구분3 채택 관할(UN_GHS): EPA 피부 4등급 444행 제외. EPA 4등급은 "
    "'EPA 기준 미분류'이지 'GHS 구분3'이 아니라 판정 불가다. 대표 기준(K_REACH)은 "
    "구분3 을 채택하지 않으므로 이 제외가 적용되지 않고 n 이 615 → 1059 로 늘어난다.",
    "`eye` 전 관할: 음성 회수분(`sds_v1_negative_recovered`) 109행 제외 — 라벨 출처 "
    "편향 감사(audit_label_source_bias) 결과 마스크 유지가 현행 결정이다.",
    "`skin` 원본 구분 `2A`/`2B` 82행은 GHS 에 없는 표기라 L1 정본에서 `2` 로 정정했다.",
    "피처 11열 제외(`피처정리` = `v7_정리`): 죽은 열 `ct_not_applicable` 1, 완전 "
    "중복쌍의 한쪽 4(`ct_{eye,skin,sens}_ord` · `f_pct_active`), 감작 CT 6. 행을 "
    "빼는 것이 아니라 열을 빼는 것이므로 n 은 바뀌지 않는다.",
]
OUT = L.ROOT / "04_모델산출물" / "v8_제형모델"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "run.log")

log("=== 제형 단위 모델 v8 (eye · skin) — 피처 정리판 ===")
log(f"대표 기준: 관할 {L.CANON_JUR} · 결측규약 {L.CANON_IMPUTE} · CT {CT_CANON} "
    f"· 피처셋 {L.CANON_FEATSET}")
log(f"제외 11열: {dict((k, list(v)) for k, v in L.FEAT_DROP.items())}")
data = L.FormulationData(log)
LAYER = data.layers(EPS)
XM = data.matrices()
FS = {fs: data.featset(fs) for fs in FS_ORDER}
assert XM[(CT_CANON, L.CANON_IMPUTE)].shape[1] == len(data.CHEM), \
    "matrices() 폭이 CHEM 과 다르다 — 동결 러너가 깨지는 변경이다"
GROUPS = L.jur_groups(data, EPS, LAYER)
log("계산 단위: " + "; ".join(f"{ep}:{'+'.join(j)}" for ep, j in GROUPS))

RES = []
for ep, jns in GROUPS:
    b, k = LAYER[(jns[0], ep)]
    y = b[k]
    folds = L.make_folds(y, data.GK[k])      # 피처셋·CT arm·결측규약 간 고정
    for fs in FS_ORDER:
        cols, idx = FS[fs]
        for im in L.IMPUTES:
            for xn in ("CT_A0", "CT_권고"):
                row = L.cv_eval(XM[(xn, im)][k][:, idx], y, folds,
                                {"단위": "제형", "endpoint": ep, "관할": "+".join(jns),
                                 "피처": "제형집계+물성+CT", "피처정리": fs,
                                 "열수": len(cols), "결측처리": im, "CT": xn})
                RES.append(row)
                log(f"  {ep:4} {row['관할']:26} [{fs:7}|{im:11}] {xn:7} "
                    f"{len(cols):3}열 n={row['n']:5} p={row['유병률']:.3f} "
                    f"AUC={row['roc_auc']:.4f} PR={row['pr_auc']:.4f} "
                    f"MCC={row['MCC']:.3f} BA={row['BA']:.3f}")

R = pd.DataFrame(RES)
# v6 재현 lock 은 `v6_원본` 행에만 걸린다. 정리판은 수치가 달라야 정상이다.
L.v6_lock(R, log, unit="제형", featset_col="피처정리", featset="v6_원본")
R = L.order_cols(L.mark_canon(R, canon_ct=CT_CANON, featset_col="피처정리"))
for _, r in R[R["대표"] == 1].iterrows():
    log(f"대표 ▶ 제형/{r['endpoint']:4} {r['관할']:26} {r['열수']}열 n={r['n']} "
        f"p={r['유병률']} AUC={r['roc_auc']} MCC={r['MCC']} BA={r['BA']}")

# --- 정리 효과: 같은 셀끼리 v6_원본 대비 차이 -------------------------------
KEY = ["endpoint", "관할", "결측처리", "CT"]
MET = ("roc_auc", "pr_auc", "MCC", "BA", "recall", "specificity")
A = R[R["피처정리"] == "v6_원본"].set_index(KEY)
B = R[R["피처정리"] == "v7_정리"].set_index(KEY)
DIFF = []
log("--- 정리 효과 (v7_정리 − v6_원본, 같은 셀) ---")
for ix in B.index:
    for m in MET:
        d = float(B.loc[ix, m]) - float(A.loc[ix, m])
        band = float(A.loc[ix, f"{m}_sd"]) + float(B.loc[ix, f"{m}_sd"]) \
            if f"{m}_sd" in A.columns else 0.0
        DIFF.append(dict(zip(KEY, ix)) | {
            "지표": m, "v6_원본": round(float(A.loc[ix, m]), 4),
            "v7_정리": round(float(B.loc[ix, m]), 4), "차이": round(d, 4),
            "잡음폭": round(band, 4),
            "판정": "잡음 범위" if abs(d) <= band else ("개선" if d > 0 else "손실")})
D = pd.DataFrame(DIFF)
D.to_csv(OUT / "피처정리_효과.csv", index=False, encoding="utf-8-sig")
n_out = int((D["판정"] != "잡음 범위").sum())
log(f"  {len(D)}건 중 잡음 범위 {len(D)-n_out}건 · 범위 밖 {n_out}건")
for _, r in D[D["판정"] != "잡음 범위"].iterrows():
    log(f"  ※ {r['endpoint']:4} {r['결측처리']:11} {r['CT']:7} {r['지표']:11} "
        f"{r['v6_원본']} → {r['v7_정리']} ({r['차이']:+.4f}, 잡음폭 ±{r['잡음폭']}) "
        f"→ {r['판정']}")

L.write_jur_doc(OUT, "제형", R, extra=EXCL_NOTES)
R.to_csv(OUT / "지표_제형모델_v8.csv", index=False, encoding="utf-8-sig")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "제형모델v8"
ws.append(list(R.columns))
for _, r in R.iterrows():
    ws.append(["" if v is None else v for v in r.tolist()])
ws.freeze_panes = "K2"
ws.auto_filter.ref = ws.dimensions
wb.save(OUT / "지표_제형모델_v8.xlsx")

with open(OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "단위": "제형(혼합물). 1행 = 1제형, n=1675 중 관할별 라벨 보유분",
        "엔드포인트": list(EPS),
        "v7_대비_차이": "피처셋 하나뿐이다. 전처리·폴드·추정기·임계값·관할 투영은 "
                   "v7 과 동일하고, 학습 피처에서 11열을 뺐다",
        # v7 요약.json 의 "CT 가산 26열" 은 v7 에서는 맞는 서술이고 그 파일은 동결이다.
        # v8 대표는 CT 16열이므로 여기서 다시 적어 둔다 — 두 문서가 다른 게 정상이다.
        "피처": "성분 디스크립터 6모멘트 집계 + 역할별 농도합 + 계면활성제 전하 "
              "상호작용 + 제형 물성(CIPAC·pc2_*·t11_*) + CT 가산 16열(26 − 죽은열 1 "
              "− 중복 서수 3 − 감작 6) + 결측플래그",
        "피처정리": {
            "결정": "2026-09-21 총책임자. `L2단독` 42열 전수 점검에서 확인한 설계 결함 3건",
            "제외열": {k: list(v) for k, v in L.FEAT_DROP.items()},
            "제외열수": len(L.FEAT_DROP_ALL),
            "열수": {fs: len(FS[fs][0]) for fs in FS_ORDER},
            "남긴쪽_근거": {
                "f_ct_{ep}_ord": "`ct_{ep}_ord` 와 값이 완전히 같다(결측 위치까지). "
                                 "`f_` 가 엔지니어링 피처 접두어 규약이므로 그쪽을 남긴다",
                "f_pct_active_presumed": "`f_pct_active` 와 값이 완전히 같다. `active` "
                                         "역할은 파이프라인에 존재하지 않고 역할 미상인 "
                                         "함량 1위 성분을 유효성분으로 추정한 "
                                         "`active_presumed` 하나뿐이므로, 추정임이 "
                                         "이름에 드러나는 쪽을 남긴다",
            },
            "죽은열_근거": "`ct_not_applicable` 은 1 이 26행뿐(최빈 98.45%)이고 중요도 "
                      "0.0%. 'CT 신뢰도 게이트로 쓴다'고 선언됐지만 게이트는 구현되지 "
                      "않았다. 열 자체는 xlsx·parquet·manifest 에 남긴다 — 게이트 "
                      "미구현은 아직 열린 결함이고 그 감사 추적이 필요하다",
            "감작CT_근거": "감작을 학습에 쓰지 않는다는 원칙과의 비대칭을 없앤다. "
                      "라벨이 아니라 가산 디스크립터였다는 종전 근거는 유지되지만, "
                      "원칙을 지키는 비용이 0 으로 측정됐다",
            "효과": "피처정리_효과.csv — 같은 셀끼리 v6_원본 대비 차이와 판정",
        },
        "대표모델": {
            "기준": {"관할": L.CANON_JUR, "결측처리": L.CANON_IMPUTE, "CT": CT_CANON,
                   "피처정리": L.CANON_FEATSET},
            "관할근거": L.JUR_DOC[L.CANON_JUR],
            "행": [r for r in R[R["대표"] == 1].to_dict("records")],
        },
        "규제처리_명시": "규제처리_명시.md 참조",
        "결측처리": {"대표": L.CANON_IMPUTE, "복원열": list(L.ZERO_IS_MISSING)},
        "감작": "학습 라벨로 쓰지 않는다(종전과 동일). 여기에 더해 **CT 열도 피처에서 "
              "뺐다**. v6 까지의 감작 측정 기록은 04_모델산출물/v7_감작/ 에 동결 보존",
        "CV": "group_key(587) 기준 StratifiedGroupKFold 5-fold × 5 seed",
        "임계값": f"{L.THRESHOLD} 고정",
        "임계값_주의": L.THRESHOLD_NOTE,
        "v6_재현": "`피처정리`=`v6_원본` 행이 v6_jurisdiction/전체지표_ROC_F1.csv 와 "
                "1e-9 이내 동일함을 매 실행 검사한다. `v7_정리` 행은 대조 대상이 "
                "아니다 — 피처를 뺐으므로 수치가 달라야 정상이다",
        "행": RES,
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT}/지표_제형모델_v8.csv / .xlsx / 요약.json / 피처정리_효과.csv")
