#!/usr/bin/env python3
"""설계 결함 4건 정리의 성능 비용 측정 — 산출: 04_모델산출물/v8_피처정리/

총책임자가 42열 전수 점검에서 확인한 결함 3건(완전 중복 4쌍 · 죽은 열 1개 ·
감작 CT 7열)을 **단계별로 누적 제외**하면서 대표 조건의 지표를 잰다. 정리를
실행하기 전에 각 단계의 비용을 따로 밝혀 두는 것이 목적이다.

왜 "중복 제거는 성능 무영향"을 그냥 믿지 않는가
  랜덤포레스트 기본 `max_features="sqrt"` 는 분기마다 `max(1, int(√열수))`개를
  후보로 뽑는다. 221열이든 210열이든 그 값은 14 로 같다 — 후보 **수**는 바뀌지
  않는다. 바뀌는 것은 추첨의 구성이다: 값이 완전히 같은 두 열은 후보 추첨에서
  표를 두 장 쥐고 있어서, 한쪽을 지우면 그 분기에서 다른 열이 뽑힐 확률이 오른다.
  그래서 완전 중복 제거조차 수치를 흔들 수 있다. 무영향이라는 주장은 측정으로
  확인할 사항이고 이 스크립트가 그 측정이다.

`lib_model.py` 를 수정하지 않는다 — 열 제외는 `matrices()` 가 돌려준 행렬의
열을 인덱스로 골라내는 방식으로만 한다. 따라서 이 측정은 현행 전처리·폴드·
추정기·임계값을 그대로 쓴다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

import lib_model as L

EPS = ("eye", "skin")
JN = L.CANON_JUR              # K_REACH — 대표 관할
IM = L.CANON_IMPUTE           # native_복원
CT = "CT_권고"                 # 대표 arm
OUT = L.ROOT / "04_모델산출물" / "v8_피처정리"

# 결함별 제외 열. 이름은 총책임자 보고서의 항목 번호를 따른다.
DROP_DUP = ("ct_eye_ord", "ct_skin_ord", "ct_sens_ord")   # 완전 중복 3쌍 중 비접두 쪽
DROP_DUP_PCT = ("f_pct_active",)                          # = f_pct_active_presumed 별칭
DROP_DEAD = ("ct_not_applicable",)                        # 98.4% 한 값 · 중요도 0.0%
DROP_SENS = ("f_ct_sens_add", "f_ct_sens_cat_1", "f_ct_sens_cat_NC",
             "f_ct_sens_cat___NA__", "f_ct_sens_ord", "f_ct_sens_s1")
# 주의: ct_sens_ord 는 중복쌍이면서 감작 CT 열이다. DROP_DUP 에 이미 들어 있으므로
# DROP_SENS 에는 넣지 않는다 — 감작 CT 7열 = DROP_SENS 6열 + ct_sens_ord.

STEPS = [
    ("① v6_원본", ()),
    ("② 중복4쌍정리", DROP_DUP + DROP_DUP_PCT),
    ("③ +죽은열제외", DROP_DUP + DROP_DUP_PCT + DROP_DEAD),
    ("④ +감작CT제외", DROP_DUP + DROP_DUP_PCT + DROP_DEAD + DROP_SENS),
]
KEY = ("roc_auc", "pr_auc", "MCC", "BA", "recall", "specificity")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    log = L.make_logger(OUT / "피처정리_대조.log")
    log("=== 설계 결함 정리 단계별 성능 비용 ===")
    log(f"대표 조건 고정: 관할 {JN} · 결측규약 {IM} · {CT} · 임계값 {L.THRESHOLD}")

    data = L.FormulationData(log)
    LAYER = data.layers(EPS)
    XM = data.matrices()
    M = XM[(CT, IM)]
    assert M.shape[1] == len(data.CHEM), "행렬 열 수 != CHEM"

    # 제외 대상이 실제로 CHEM 에 있는지, 그리고 값이 정말 중복인지 먼저 확인한다.
    PAIRS = [("ct_eye_ord", "f_ct_eye_ord"), ("ct_skin_ord", "f_ct_skin_ord"),
             ("ct_sens_ord", "f_ct_sens_ord"),
             ("f_pct_active", "f_pct_active_presumed")]
    for a, b in PAIRS:
        ia, ib = data.CHEM.index(a), data.CHEM.index(b)
        va, vb = M[:, ia], M[:, ib]
        assert np.array_equal(np.isnan(va), np.isnan(vb)) and \
            np.array_equal(va[~np.isnan(va)], vb[~np.isnan(vb)]), \
            f"{a} != {b} — 중복 전제 이탈"
    log(f"중복 4쌍 값 동일성 재확인 완료: " + " · ".join(f"{a}={b}" for a, b in PAIRS))

    dead = data.CHEM.index("ct_not_applicable")
    vd = M[:, dead]
    top = pd.Series(vd).value_counts(dropna=False)
    log(f"ct_not_applicable 값분포 {top.to_dict()} · 최빈 "
        f"{top.max()/len(vd)*100:.2f}%")

    sens7 = tuple(DROP_DUP[2:]) + DROP_SENS
    log(f"감작 CT {len(sens7)}열: {sorted(sens7)}")
    assert len(sens7) == 7, f"감작 CT {len(sens7)} != 7"
    for c in sum((list(d) for _, d in STEPS), []):
        assert c in data.CHEM, f"{c} 가 CHEM 에 없다"

    RES = []
    for ep in EPS:
        b, k = LAYER[(JN, ep)]
        y = b[k]
        folds = L.make_folds(y, data.GK[k])     # 단계 간 폴드 고정
        for name, drop in STEPS:
            keep = [j for j, c in enumerate(data.CHEM) if c not in drop]
            row = L.cv_eval(M[k][:, keep], y, folds,
                            {"단위": "제형", "endpoint": ep, "관할": JN,
                             "단계": name, "열수": len(keep),
                             "제외열수": len(drop), "결측처리": IM, "CT": CT})
            RES.append(row)
            log(f"  {ep:4} {name:14} {len(keep):3}열 AUC={row['roc_auc']:.4f}"
                f"±{row['roc_auc_sd']:.4f} PR={row['pr_auc']:.4f} "
                f"MCC={row['MCC']:.4f} BA={row['BA']:.4f} "
                f"재현율={row['recall']:.3f} 특이도={row['specificity']:.3f}")

    R = pd.DataFrame(RES)
    R.to_csv(OUT / "피처정리_대조.csv", index=False, encoding="utf-8-sig")

    # --- 단계별 증분 판정 ------------------------------------------------
    # |차이| 가 두 조건의 시드간 표준편차 합보다 작으면 잡음 범위로 본다. 유의성
    # 검정이 아니라 시드 변동 대비 크기 비교이며, 이 구분을 결론에 그대로 적는다.
    # 노선2 쪽(`measure_sens_drop_lane2.py`·`measure_lane2_cleanup.py`)은 `2*max(sd)`
    # 를 쓴다. `2*max(sd) >= sd_a+sd_b` 이므로 여기가 더 엄격하다 — 두 표의
    # '잡음 범위' 건수를 같은 문장에서 비교하면 안 된다.
    JUD = []
    log("--- 단계별 증분 (직전 단계 대비) ---")
    for ep in EPS:
        S = R[R["endpoint"] == ep].reset_index(drop=True)
        for i in range(1, len(S)):
            prev, cur = S.loc[i - 1], S.loc[i]
            for m in KEY:
                d = float(cur[m]) - float(prev[m])
                band = float(prev.get(f"{m}_sd", 0) or 0) + float(cur.get(f"{m}_sd", 0) or 0)
                verdict = "잡음 범위" if abs(d) <= band else \
                          ("개선" if d > 0 else "손실")
                JUD.append({"endpoint": ep, "단계": cur["단계"], "지표": m,
                            "직전": round(float(prev[m]), 4),
                            "현재": round(float(cur[m]), 4),
                            "차이": round(d, 4), "잡음폭": round(band, 4),
                            "판정": verdict})
                if verdict != "잡음 범위":
                    log(f"  {ep:4} {cur['단계']:14} {m:11} "
                        f"{float(prev[m]):.4f} → {float(cur[m]):.4f} "
                        f"차이={d:+.4f} (잡음폭 ±{band:.4f}) → {verdict}")
    J = pd.DataFrame(JUD)
    J.to_csv(OUT / "피처정리_판정.csv", index=False, encoding="utf-8-sig")
    n_noise = int((J["판정"] == "잡음 범위").sum())
    log(f"판정 {len(J)}건 중 잡음 범위 {n_noise}건 · 범위 밖 {len(J)-n_noise}건")

    # --- 최종 대조: ① vs ④ ------------------------------------------------
    log("--- 최종 (① v6_원본 vs ④ 전부정리) ---")
    FIN = []
    for ep in EPS:
        S = R[R["endpoint"] == ep].reset_index(drop=True)
        a, z = S.loc[0], S.loc[len(S) - 1]
        for m in KEY:
            d = float(z[m]) - float(a[m])
            band = float(a.get(f"{m}_sd", 0) or 0) + float(z.get(f"{m}_sd", 0) or 0)
            FIN.append({"endpoint": ep, "지표": m, "v6_원본": round(float(a[m]), 4),
                        "전부정리": round(float(z[m]), 4), "차이": round(d, 4),
                        "잡음폭": round(band, 4),
                        "판정": "잡음 범위" if abs(d) <= band
                                else ("개선" if d > 0 else "손실")})
            log(f"  {ep:4} {m:11} {float(a[m]):.4f} → {float(z[m]):.4f} "
                f"차이={d:+.4f} (잡음폭 ±{band:.4f}) → {FIN[-1]['판정']}")
    pd.DataFrame(FIN).to_csv(OUT / "피처정리_최종대조.csv", index=False,
                             encoding="utf-8-sig")

    with open(OUT / "요약.json", "w", encoding="utf-8") as f:
        json.dump({
            "목적": "설계 결함 4건 정리의 성능 비용을 단계별로 분리 측정. 정리 실행 "
                  "전 근거 확보용이며 대표 산출물이 아니다",
            "고정조건": {"관할": JN, "결측처리": IM, "CT": CT,
                     "임계값": L.THRESHOLD, "시드": L.SEEDS, "폴드": L.N_SPLITS},
            "단계": {n: {"제외열": list(d), "제외열수": len(d)} for n, d in STEPS},
            "남긴쪽_근거": {
                "f_ct_{ep}_ord": "f_ 는 엔지니어링 피처 접두어 규약이고 중요도 보고 "
                                 "목록에 쓰인 이름이다. ct_{ep}_ord 를 제외한다",
                "f_pct_active_presumed": "`active` 역할은 존재하지 않는다. 역할 미상인 "
                                         "함량 1위 성분을 유효성분으로 추정한 값이므로 "
                                         "추정임이 드러나는 이름을 남기고 f_pct_active "
                                         "별칭을 제외한다",
            },
            "판정기준": "|차이| <= 두 조건의 시드간 표준편차 합이면 잡음 범위. "
                    "유의성 검정이 아니라 시드 변동 대비 크기 비교다",
            "주의": "감작 CT 제외 결정의 근거는 수치가 아니라 '감작을 학습에 쓰지 "
                  "않는다'는 원칙이다. 이 표는 그 원칙을 지키는 비용을 밝힌다",
            "행": RES,
        }, f, ensure_ascii=False, indent=2, default=str)
    log(f"완료 → {OUT}/피처정리_대조.csv · 피처정리_판정.csv · 피처정리_최종대조.csv")


if __name__ == "__main__":
    main()
