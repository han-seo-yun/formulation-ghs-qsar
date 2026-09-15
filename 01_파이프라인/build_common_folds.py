#!/usr/bin/env python3
"""3노선 공통 폴드·라벨 배포본 생성. 산출: 04_모델산출물/v8_공통/

세 노선(L1 완제품 SDS / L2 성분위험정보×농도 / L3 구조)이 **같은 행·같은 라벨·
같은 폴드**를 써야 성능 차이를 정보원 차이로 해석할 수 있다. 각자 폴드를 생성하면
StratifiedGroupKFold 의 셔플이 달라져 비교가 깨지므로, 이 스크립트가 만든 파일을
세 명이 물리적으로 공유한다.

대표 관할은 `EU_CLP` 다(2026-09-14 총책임자 승인). 근거는 혼합물 가산식의 출력
어휘 정합성 — 05_제안/노선분리_비교규약.md §2.1. 부가 관할(K_REACH·UN_GHS)도
같은 파일에 함께 담아 기준 대조에 재실행이 필요 없게 한다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx
"""
from __future__ import annotations

import json

import numpy as np

import lib_model as L

EPS = ("eye", "skin")
CANON_JUR_V8 = "EU_CLP"          # v7 의 K_REACH 를 대체. 규약 §2.1
OUT = L.ROOT / "04_모델산출물" / "v8_공통"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "build_common_folds.log")

log("=== 3노선 공통 폴드·라벨 배포본 ===")
log(f"대표 관할 {CANON_JUR_V8} · group=group_key · "
    f"StratifiedGroupKFold {L.N_SPLITS}-fold × {len(L.SEEDS)} seed")

data = L.FormulationData(log)
LAYER = data.layers(EPS)
FID = data.FID
GK = data.GK
assert len(FID) == 1675, f"제형 행수 {len(FID)} 이탈"

payload = {"규약": "05_제안/노선분리_비교규약.md",
           "대표관할": CANON_JUR_V8,
           "n_전체행": int(len(FID)),
           "seed": list(L.SEEDS), "n_splits": L.N_SPLITS,
           "group": "group_key", "endpoint": {}}
npz = {"Formulation_ID": FID.astype(str), "group_key": GK.astype(str)}

for ep in EPS:
    ent = {}
    for jn in ("EU_CLP", "K_REACH", "UN_GHS", "US_OSHA"):
        b, k = LAYER[(jn, ep)]
        y = b[k].astype(np.int8)
        ent[jn] = {"n": int(k.sum()), "양성": int(y.sum()),
                   "유병률": round(float(y.mean()), 4)}
        log(f"  {ep:4} {jn:9} n={int(k.sum()):5} 양성={int(y.sum()):4} "
            f"유병률={y.mean():.4f}")
    # 폴드는 대표 관할에서만 만든다. 관할마다 마스크가 달라 폴드를 공유할 수 없다.
    b, k = LAYER[(CANON_JUR_V8, ep)]
    y = b[k].astype(np.int8)
    g = GK[k]
    folds = L.make_folds(y, g)
    # (seed, fold) → 검증 인덱스. 마스크 적용 후 배열 기준 인덱스다.
    te = np.full((len(L.SEEDS), L.N_SPLITS), None, dtype=object)
    for si, per_seed in enumerate(folds):
        cover = np.zeros(len(y), dtype=bool)
        for fi, (_tr, _te) in enumerate(per_seed):
            te[si, fi] = np.asarray(_te, dtype=np.int32)
            assert not cover[_te].any(), f"{ep}/seed{si} 검증분 중복"
            cover[_te] = True
        assert cover.all(), f"{ep}/seed{si} 미포함 행 존재"
    npz[f"mask_{ep}"] = k
    npz[f"y_{ep}"] = y
    npz[f"group_{ep}"] = g.astype(str)
    for si in range(len(L.SEEDS)):
        for fi in range(L.N_SPLITS):
            npz[f"te_{ep}_s{si}_f{fi}"] = te[si, fi]
    ent["폴드"] = {"기준관할": CANON_JUR_V8,
                 "형식": f"te_{ep}_s{{seed_idx}}_f{{fold_idx}}, "
                       f"마스크 적용 후 배열 기준 0-based 검증 인덱스",
                 "그룹수": int(len(set(g)))}
    payload["endpoint"][ep] = ent
    log(f"  {ep:4} 폴드 생성 완료 — 그룹 {len(set(g))}종, "
        f"{len(L.SEEDS)}×{L.N_SPLITS}={len(L.SEEDS)*L.N_SPLITS}폴드")

np.savez_compressed(OUT / "공통폴드.npz", **npz)
with open(OUT / "공통폴드_요약.json", "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
# 절대경로를 로그에 남기지 않는다 — 산출물이 공유 대상이고 저장소는 사용자명을
# 익명화한 상태다. 새 로그가 그것을 되돌리면 안 된다.
log(f"완료 → {OUT.relative_to(L.ROOT)}/공통폴드.npz / 공통폴드_요약.json")
log("사용법: mask_{ep} 로 1675행에서 학습행을 고르고, y_{ep} 를 라벨로, "
    "te_{ep}_s*_f* 를 검증 인덱스로 쓴다. 폴드를 직접 생성하지 않는다.")
