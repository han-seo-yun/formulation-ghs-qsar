#!/usr/bin/env python3
"""감작(sensitization) 기록 동결 보존 — 산출: 04_모델산출물/v7_감작/

방침 (2026-09-09 총책임자 지시)
  감작은 **앞으로 학습에 쓰지 않는다. 삭제하지도 않는다.** 지금까지의 측정·데이터
  기록을 한 디렉터리로 모아 동결 보존하고, v7 모델(성분·제형)은 eye·skin 만 다룬다.

이 스크립트가 하는 일
  1) v6 까지의 감작 관련 산출물을 v7_감작/ 로 **복사**한다(이동 아님).
     원본을 옮기면 `measure_full_metrics_v6.py` 의 재현 lock 과 기존 보고서의
     경로 참조가 깨진다. 원본은 제자리에 남긴다.
  2) 관할별 지표 파일에서 감작 행만 뽑아 별도 CSV 로 만든다.
  3) 복사본의 sha256 을 보존목록에 기록해 이후 변조를 감지할 수 있게 한다.
  4) 동결 시점의 라벨 현황과 재개 절차를 README.md 에 남긴다.

이 스크립트는 모델을 학습하지 않는다. 감작을 다시 학습하려면 기존
`measure_full_metrics_v6.py` / `measure_sens_arm_grid.py` 가 그대로 남아 있으므로
그것을 쓴다 — v7 에 감작 학습 코드를 새로 만들지 않는다.

읽기 전용(원본 무수정). 쓰기는 v7_감작/ 안에서만.
"""
from __future__ import annotations

import hashlib
import json
import shutil

import pandas as pd

import lib_model as L

OUT = L.ROOT / "04_모델산출물" / "v7_감작"
OUT.mkdir(parents=True, exist_ok=True)
JUR6 = L.ROOT / "04_모델산출물" / "v6_jurisdiction"
SKIN6 = L.ROOT / "04_모델산출물" / "v6_skinmap_ct"
log = L.make_logger(OUT / "run.log")

log("=== 감작 기록 동결 보존 (학습 없음) ===")

# ------------------------------------------------------------------ 1) 복사
COPY = [
    (JUR6 / "감작_CTarm_격자.csv", "감작_CTarm_격자.csv",
     "A0/A2/A3/A3s 네 arm 을 동일 폴드에서 비교한 격자"),
    (JUR6 / "감작_CTarm_대조.csv", "감작_CTarm_대조.csv", "arm 간 쌍대조"),
    (JUR6 / "감작_CTarm_요약.json", "감작_CTarm_요약.json", "arm 격자 요약·해석"),
    (JUR6 / "run_sens_arm_grid.log", "run_sens_arm_grid.log", "arm 격자 실행 로그"),
    (JUR6 / "그림" / "F4_감작arm_결측규약.png", "F4_감작arm_결측규약.png",
     "arm × 결측규약 성능 그림"),
    (SKIN6 / "감작충돌_32건_장부.csv", "감작충돌_32건_장부.csv",
     "감작 라벨 충돌 32건 장부(모델 무영향 no-op 기록)"),
    (SKIN6 / "감작충돌_32건_장부.xlsx", "감작충돌_32건_장부.xlsx", "위 장부 xlsx"),
]
manifest = []


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


for src, name, desc in COPY:
    if not src.exists():
        log(f"  ※ 없음, 건너뜀: {src.relative_to(L.ROOT)}")
        manifest.append({"파일": name, "원본경로": str(src.relative_to(L.ROOT)),
                         "설명": desc, "상태": "원본없음", "sha256": "", "바이트": 0})
        continue
    dst = OUT / name
    shutil.copy2(src, dst)
    manifest.append({"파일": name, "원본경로": str(src.relative_to(L.ROOT)),
                     "설명": desc, "상태": "복사완료",
                     "sha256": sha256(dst), "바이트": dst.stat().st_size})
    log(f"  복사 {src.relative_to(L.ROOT)} → {name}")

# --------------------------------------------------- 2) 감작 행만 추출한 지표
EXTRACT = [
    (JUR6 / "전체지표_ROC_F1.csv", "지표_감작_전체_v6동결.csv",
     "v6 전체지표에서 감작 행만 (ROC-AUC·PR-AUC·F1·MCC·BA·혼동행렬)"),
    (JUR6 / "관할별_성능.csv", "지표_감작_관할별_v6동결.csv", "관할별 성능 중 감작 행"),
    (JUR6 / "관할간_불일치.csv", "관할간_불일치_감작_v6동결.csv", "관할간 불일치 중 감작 행"),
]
sens_rows = None
for src, name, desc in EXTRACT:
    if not src.exists():
        log(f"  ※ 없음, 건너뜀: {src.relative_to(L.ROOT)}")
        continue
    d = pd.read_csv(src)
    assert "endpoint" in d.columns, f"{src.name} 에 endpoint 열이 없다"
    s = d[d["endpoint"] == "sens"].copy()
    assert len(s) > 0, f"{src.name} 에 감작 행이 없다 — 원본이 이미 바뀌었는지 확인"
    s.to_csv(OUT / name, index=False, encoding="utf-8-sig")
    manifest.append({"파일": name, "원본경로": str(src.relative_to(L.ROOT)),
                     "설명": desc, "상태": f"감작 {len(s)}행 추출",
                     "sha256": sha256(OUT / name), "바이트": (OUT / name).stat().st_size})
    log(f"  추출 {src.name} → {name} ({len(s)}행)")
    if src.name == "전체지표_ROC_F1.csv":
        sens_rows = s

pd.DataFrame(manifest).to_csv(OUT / "보존목록.csv", index=False, encoding="utf-8-sig")

# --------------------------------------------------- 3) 동결 시점 라벨 현황
Y = pd.read_parquet(L.SRC / "y_formulation.parquet")
ING = pd.ExcelFile(L.V6).parse("ingredient")
state = {
    "제형라벨": {
        "구분분포": {str(k): int(v) for k, v in
                 Y["y_sens"].fillna("결측").value_counts().items()},
        "출처분포": {str(k): int(v) for k, v in
                 Y["y_sens_src"].fillna("결측").value_counts().items()},
        "학습가능행_UN_GHS": 716,
        "negrec_행": int((Y["y_sens_src_detail"].fillna("") == L.NEGREC).sum()),
    },
    "성분라벨": {
        "phase1_ing_ghs_sens": int(ING["ing_ghs_sens"].notna().sum()),
        "정채윤_ing_ghs_indep_sens_cat": int(ING["ing_ghs_indep_sens_cat"].notna().sum()),
        "구분분포_phase1": {str(k): int(v) for k, v in
                        ING["ing_ghs_sens"].value_counts().items()},
        "구분분포_정채윤": {str(k): int(v) for k, v in
                       ING["ing_ghs_indep_sens_cat"].value_counts().items()},
    },
}
log(f"동결 시점 라벨: 제형 감작 학습가능 716행 / 성분 감작 "
    f"{state['성분라벨']['phase1_ing_ghs_sens']}+"
    f"{state['성분라벨']['정채윤_ing_ghs_indep_sens_cat']}행")

with open(OUT / "동결상태.json", "w", encoding="utf-8") as f:
    json.dump({
        "동결일": "2026-09-09",
        "방침": "감작은 학습에 사용하지 않는다. 삭제하지 않고 기록만 보존한다.",
        "적용범위": "v7 이후 모델(run_model_formulation.py · run_model_ingredient.py)은 "
                "eye·skin 만 학습한다. 감작 학습 코드를 v7 에 새로 만들지 않는다.",
        "예외": "f_ct_sens_* 는 라벨이 아니라 혼합물 가산식으로 계산한 디스크립터이므로 "
              "eye/skin 모델의 피처에 그대로 남는다. 라벨 y_sens 는 쓰지 않는다.",
        "라벨현황": state,
        "임계값_주의": L.THRESHOLD_NOTE,
        "재개절차": [
            "감작 학습을 재개하려면 기존 measure_full_metrics_v6.py (전 엔드포인트) 와 "
            "measure_sens_arm_grid.py (감작 CT arm 격자) 를 그대로 실행한다. "
            "두 스크립트는 수정하지 않고 남겨두었다.",
            "성분 단위 감작 모델이 필요하면 run_model_ingredient.py 의 EPS 에 "
            "'sens' 를 추가하면 동작한다(lib_model 은 3 엔드포인트 전부 지원한다). "
            "다만 이는 총책임자 결정 사안이다.",
        ],
        "미해결_한계": [
            "감작 음성 라벨 상당수가 'Annex VI 항목에 감작 분류가 없음'에서 왔고, "
            "그 중 일부는 미시험을 음성으로 읽은 것이다(감작충돌_32건_장부.csv). "
            "농약 활성성분은 실제로 Skin Sens. 1 일 개연성이 높다.",
            "감작 CT arm 선택 근거(A3, 커버리지 49.6%)는 A3 가 NC 를 '아는 값'으로 "
            "세기 때문에 나온 수치라 A2 와 직접 비교할 수 없다 — 감작_CTarm_요약.json 참조.",
        ],
    }, f, ensure_ascii=False, indent=2, default=str)

# ------------------------------------------------------------------ 4) README
best = ""
if sens_rows is not None and len(sens_rows):
    b = sens_rows.sort_values("roc_auc", ascending=False).iloc[0]
    best = (f"| 최고 ROC-AUC | {b['roc_auc']:.4f} ({b['CT']} · {b['결측처리']}, "
            f"MCC {b['MCC']:.3f} · BA {b['BA']:.3f}) |\n")
    # to_markdown 은 tabulate 의존이라 쓰지 않는다(환경에 미설치).
    tcols = ["관할", "결측처리", "CT", "n", "유병률", "roc_auc", "pr_auc",
             "MCC", "BA", "recall", "precision"]
    tbl = ("| " + " | ".join(tcols) + " |\n|" + "---|" * len(tcols) + "\n"
           + "\n".join("| " + " | ".join(str(r[c]) for c in tcols) + " |"
                       for _, r in sens_rows[tcols].iterrows()))
else:
    tbl = "_(원본 지표 파일 없음)_"

(OUT / "README.md").write_text(f"""# 감작(sensitization) — 동결 보존

**2026-09-09 부로 감작은 학습에 사용하지 않는다. 삭제하지 않고 기록만 보존한다.**

v7 이후 모델은 눈·피부만 다룬다:
`01_파이프라인/run_model_ingredient.py` (성분 단위) · `run_model_formulation.py` (제형 단위).

## 동결 시점 상태

| 항목 | 값 |
|---|---|
| 제형 학습가능 행 (UN_GHS) | 716 |
| 제형 유병률 | 0.532 |
| 성분 라벨 (Phase 1 / 정채윤) | {state['성분라벨']['phase1_ing_ghs_sens']} / {state['성분라벨']['정채윤_ing_ghs_indep_sens_cat']} 행 |
{best}
### v6 최종 지표 (임계값 0.5 고정)

{tbl}

## 보존 파일

`보존목록.csv` 에 원본 경로·설명·sha256 을 기록했다. **원본은 옮기지 않고 복사했다** —
`measure_full_metrics_v6.py` 의 재현 lock 과 기존 보고서의 경로 참조가 원본을 참조하기 때문이다.

## 남겨둔 것 / 남기지 않은 것

- `f_ct_sens_*` (혼합물 가산 디스크립터) 는 눈·피부 모델의 **피처로 계속 남는다**.
  라벨이 아니고, v6 이 학습에 쓴 피처 구성과 같아야 재현 lock 이 성립한다.
- 라벨 `y_sens` · `ing_ghs_sens` · `ing_ghs_indep_sens_cat` 는 데이터셋에 그대로 있으나
  v7 모델은 읽지 않는다.
- 감작 학습 스크립트를 v7 에 새로 만들지 않았다. 재개 절차는 `동결상태.json` 참조.

## 미해결 한계 (재개 시 먼저 볼 것)

1. 감작 음성 라벨 일부가 "Annex VI 에 감작 분류 없음" = 미시험을 음성으로 읽은 것이다
   (`감작충돌_32건_장부.csv`). 농약 활성성분은 실제로 Skin Sens. 1 일 개연성이 높다.
2. 감작 CT arm 을 A3 로 고른 근거(커버리지 49.6%)는 A3 가 NC 를 '아는 값'으로 세기
   때문에 나온 수치라 A2 와 직접 비교할 수 없다 (`감작_CTarm_요약.json`).
""", encoding="utf-8")

log(f"완료 → {OUT}/ (README.md · 동결상태.json · 보존목록.csv + 보존파일 {len(manifest)}건)")
