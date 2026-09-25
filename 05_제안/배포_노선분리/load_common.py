#!/usr/bin/env python3
"""3노선 공통 배포본 로더. 이 파일 위치를 기준으로 상대경로를 해석하기 위함.

압축을 어디에 풀어도 동작한다. 자기 프로젝트에서 쓸 때는 이 폴더를 그대로 두고
경로만 넘기면 된다.

    import sys; sys.path.insert(0, "<이 폴더 경로>")
    from load_common import load_folds, load_arms

    F = load_folds("eye")               # 마스크·라벨·그룹·폴드
    X = select_arm(my_df, "L2단독", F)   # 열은 이름으로 고른다. 위치로 고르지 않는다
    y = F["y"]
    for si, fi, tr, te in F["folds"]:
        model.fit(X[tr], y[tr]); p[te] = model.predict_proba(X[te])[:, 1]

**열은 반드시 이름으로 고른다.** `피처노선_arm.json` 은 이름 목록이고 정본 열 순서는
`sorted(...)` 인데 `X_formulation.parquet` 의 열 순서도, `피처노선_판정.csv` 의 행
순서도 그것과 다르다. 위치 인덱스를 손으로 만들면 조용히 다른 열로 학습하게 되고
폴드 자체검사로는 잡히지 않는다. `select_arm()` 을 쓰면 그 사고가 원천 차단된다.

폴드를 직접 생성하지 않는다. 세 노선이 같은 인덱스를 써야 비교가 성립한다.
규약: 노선분리_비교규약.md
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
COMMON = HERE / "v8_공통"
N_SEEDS, N_FOLDS = 5, 5
N_ROWS = 1675          # 제형 원본 행수. 마스크 적용 전 기준


class 배포본오류(Exception):
    """배포본이 손상됐거나 버전이 어긋났다. assert 가 아니라 예외로 던진다 —
    팀원 환경에서 python -O 로 돌면 assert 는 전부 사라진다."""


def _need(cond, msg):
    if not cond:
        raise 배포본오류(msg)


def load_folds(endpoint: str) -> dict:
    """endpoint in ("eye", "skin"). 반환 dict:

    mask   (1675,) bool  — 원본 제형 행에서 이 엔드포인트의 학습행
    y      (n,) int8     — 대표 관할(EU_CLP) 이진 라벨
    group  (n,) str      — group_key. 폴드가 이미 이걸로 갈렸으니 참고용이다
    fid    (n,) str      — Formulation_ID
    folds  list[(seed_idx, fold_idx, train_idx, test_idx)]  — 마스크 적용 후 기준
    """
    if endpoint not in ("eye", "skin"):
        raise ValueError(f"endpoint 는 eye 또는 skin: {endpoint!r}")
    _need((COMMON / "공통폴드.npz").exists(),
          f"공통폴드.npz 가 없다. 배포본 폴더 구조가 깨졌다: {COMMON}")
    z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
    mask = z[f"mask_{endpoint}"]
    y = z[f"y_{endpoint}"]
    n = len(y)
    folds = []
    allidx = np.arange(n)
    for si in range(N_SEEDS):
        for fi in range(N_FOLDS):
            te = z[f"te_{endpoint}_s{si}_f{fi}"]
            folds.append((si, fi, np.setdiff1d(allidx, te), te))
    out = {"mask": mask, "y": y, "group": z[f"group_{endpoint}"],
           "fid": z["Formulation_ID"][mask], "folds": folds, "n": n}
    _selfcheck(out, endpoint)
    return out


def _selfcheck(F: dict, ep: str) -> None:
    """배포본 자체검사. 두 종류를 구분해서 던진다.

    (1) 구조 불변식 — 어떤 기준으로 재생성해도 반드시 성립해야 하는 성질.
        폴드 피복, 그룹 분리, 마스크·라벨 길이 정합, 원본 1675행.
    (2) 버전 정합 — n·양성·그룹수. **기대값을 코드에 적지 않고 같이 배포되는
        `공통폴드_요약.json` 에서 읽는다.** 로더 소스와 데이터 파일에 같은 진실을
        이중 기입하면, 기준을 갈아탈 때(예: 관할 변경) 팀원 전원이 로더를 손으로
        고쳐야 하고 '손상'과 '정상 갱신'이 같은 오류로 보인다.
    """
    _need(len(F["mask"]) == N_ROWS,
          f"{ep} 마스크 길이 {len(F['mask'])} != 원본 {N_ROWS}행")
    _need(int(F["mask"].sum()) == F["n"] == len(F["group"]) == len(F["fid"]),
          f"{ep} 마스크·라벨·그룹·ID 길이 불일치")
    for si in range(N_SEEDS):
        cov = np.concatenate([te for s, f, tr, te in F["folds"] if s == si])
        _need(np.array_equal(np.sort(cov), np.arange(F["n"])),
              f"{ep}/seed{si} 검증분이 전 행을 1회씩 덮지 않는다 — 배포본 손상")
    for si, fi, tr, te in F["folds"]:
        _need(not (set(F["group"][te]) & set(F["group"][tr])),
              f"{ep}/seed{si}/fold{fi} 그룹 누출 — 배포본 손상")

    summary = COMMON / "공통폴드_요약.json"
    if not summary.exists():
        return                      # 요약이 없으면 구조 검사만으로 통과시킨다
    with open(summary, encoding="utf-8") as f:
        S = json.load(f)
    jur = S.get("대표관할")
    exp = S.get("endpoint", {}).get(ep, {}).get(jur)
    if exp is None:
        return
    _need(F["n"] == exp["n"] and int(F["y"].sum()) == exp["양성"],
          f"{ep} npz(n={F['n']}, 양성={int(F['y'].sum())}) 와 요약json"
          f"(n={exp['n']}, 양성={exp['양성']}) 가 어긋난다 — 배포본 두 파일의 "
          f"버전이 다르다. 전체를 다시 받는다")
    grp = S["endpoint"][ep].get("폴드", {}).get("그룹수")
    if grp is not None:
        _need(len(set(F["group"])) == grp,
              f"{ep} 그룹수 {len(set(F['group']))} != 요약json {grp}")
    _need(S["endpoint"][ep].get("폴드", {}).get("기준관할") == jur,
          f"{ep} 폴드 기준관할과 대표관할이 다르다")


def load_arms() -> dict:
    """arm 이름 → 피처 열 목록.

    L1단독(52) / L2단독(31) / L3단독(98)   — 그 노선의 정보원만으로 계산 가능한 열
    L2+결합(59) / L3+결합(126)             — 두 정보원이 다 필요한 28열을 더한 부가 arm
    L2+L1(83) / L2+L3(129) / 전체_노선혼합(209)

    L2 가 낀 arm 의 열 수는 2026-09-21 결정으로 11열 줄었다(42→31 등). 설계 결함
    3건 — 죽은 열 `ct_not_applicable` 1 · 완전 중복쌍의 한쪽 4 · 감작 CT 6 — 을
    모든 arm 에서 뺀 결과다. 규약 §5-3 과 `피처노선_arm.json` 의 `피처정리_제외`
    필드에 근거와 실측 비용이 있다. 노선 귀속 자체는 바뀌지 않았다(L2 는 여전히
    42열) — `피처노선_판정.csv` 가 그 감사 기록이다.

    **열 수를 코드에 박지 말고 이 함수가 돌려준 목록의 길이를 쓴다.** 위 숫자는
    설명용이고, 정본은 `피처노선_arm.json` 이다.

    **대표 산출은 자기 노선 단독 arm 으로 낸다.** `+결합` 은 부가 행이다. 이유는
    성능이 아니라, 결합 열은 다른 노선의 정보원 없이 계산되지 않아서 자기 노선
    가설을 반증 불가로 만들기 때문이다. `피처노선_arm.json` 의 `L2L3결합_이유` 참고.
    """
    with open(COMMON / "피처노선_arm.json", encoding="utf-8") as f:
        return json.load(f)["arm"]


def select_arm(X, arm: str, F: dict | None = None):
    """arm 의 열을 **이름으로** 골라 float64 ndarray 로 돌려준다.

    X   pandas DataFrame. 1675행(마스크 미적용) 또는 F["n"]행(마스크 적용) 둘 다 받는다.
    F   load_folds() 결과. 주면 마스크를 적용해 폴드 인덱스 기준에 맞춘다.

    위치 인덱싱을 손으로 만들지 말고 이것을 쓴다 — 배포본 어디에도 열 이름 → 열
    위치 대응표가 없고, 순서를 잘못 짚으면 조용히 다른 열로 학습한다.
    """
    cols = load_arms()
    if arm not in cols:
        raise ValueError(f"모르는 arm {arm!r}. 가능한 값: {sorted(cols)}")
    want = cols[arm]
    missing = [c for c in want if c not in X.columns]
    _need(not missing, f"{arm}: X 에 없는 열 {len(missing)}개 — {missing[:5]}")
    sub = X[want]
    if F is not None:
        if len(sub) == N_ROWS:
            sub = sub[F["mask"]]
        _need(len(sub) == F["n"],
              f"{arm}: 행수 {len(sub)} 가 폴드 기준 {F['n']} 과 다르다")
    return sub.to_numpy(dtype="float64")


def load_lane_table():
    """열 단위 노선 판정 근거 표(pandas DataFrame)."""
    import pandas as pd
    return pd.read_csv(COMMON / "피처노선_판정.csv")


if __name__ == "__main__":
    arms = load_arms()
    print("arm 정의")
    for k, v in arms.items():
        print(f"  {k:14} {len(v):4}열")
    for ep in ("eye", "skin"):
        F = load_folds(ep)
        print(f"{ep:5} n={F['n']:5} 양성={int(F['y'].sum()):4} "
              f"유병률={F['y'].mean():.4f} 그룹={len(set(F['group'])):4} "
              f"폴드={len(F['folds'])}")
    print("배포본 자체검사 통과 — 폴드 피복·그룹 분리·요약json 버전 정합 정상")
