#!/usr/bin/env python3
"""L2 노선 누출 감사. 산출: 04_모델산출물/v8_누출감사/

규약 05_제안/노선분리_비교규약.md §4 의 두 감사를 수행한다.

  감사 A  피처와 라벨이 같은 문서에서 나왔는지 대조한다. 노선별 위험도가 다르다는
          점이 핵심이다 — L1 은 라벨과 피처가 같은 완제품 SDS 문서라 구조적으로
          위험이 높고, L2 는 라벨이 완제품 SDS 2 절 / 피처가 성분 위험정보 + 조성표라
          문서가 다르다. 라벨 출처별 부분집합 마스크를 산출해 P3 모델이 층별로
          성능을 분리 측정하게 한다.
  감사 B  CT 가산식 출력만으로 라벨을 얼마나 맞추는지 학습 없이 측정한다.
          **CT 는 규제 규칙 자체이고, 이것이 라벨을 잘 재현하는 것은 누출이 아니라
          이 연구의 측정 대상이다.** 감사 B 의 목적은 누출 판정이 아니라 모델 성능
          중 '규칙이 이미 설명하는 몫'과 '학습이 추가로 설명하는 몫'을 분리하는 것.
          진짜 누출 신호는 별개다 — CT 입력인 성분 구분이 완제품 라벨을 역산한
          것이 아닌지 확인한다.

이 감사를 통과하지 않은 성능 수치는 보고하지 않는다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx, v8_공통/*
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, matthews_corrcoef

import lib_model as L

EPS = ("eye", "skin")
CANON_JUR_V8 = "EU_CLP"
COMMON = L.ROOT / "04_모델산출물" / "v8_공통"
OUT = L.ROOT / "04_모델산출물" / "v8_누출감사"
OUT.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "run.log")

# ------------------------------------------------------------- 문서 출처 분류
# 라벨 출처(`y_{ep}_src`) → 그 라벨이 어느 문서의 어느 부분에서 나왔는가.
# `층` 은 피처와의 문서 공유 위험도다.
LABEL_SRC = {
    "ntp_measured": ("NTP 시험 측정값", "문서독립",
                     "완제품 SDS 와 무관한 독립 시험데이터. SDS 유래 피처와 문서 공유 없음"),
    "sds_v1": ("완제품 SDS 2·3절 분류 문구", "문서공유_분류절",
               "라벨이 완제품 SDS 의 분류 선언에서 왔다. L1 피처(9절 물성)와 같은 문서"),
    "sds_2nd": ("완제품 SDS 2차 수집 분류 문구", "문서공유_분류절",
                "위와 동일. 2차 수집분"),
    "phase1_sds": ("Phase 1 수집 완제품 SDS 분류", "문서공유_분류절", "위와 동일"),
    "sds_2nd_sec11": ("완제품 SDS 11절 독성정보에서 역산", "문서공유_11절",
                      "라벨을 11절에서 읽었다. 금지 피처 t11_* 와 동일 절 — "
                      "최고 위험 층. t11_* 배제가 이 층을 지키는 유일한 장치다"),
}
# 노선별 피처의 정보원 문서.
LANE_DOC = {
    "L1": ("완제품 SDS 9절 물성 · 제형코드", ["문서공유_분류절", "문서공유_11절"],
           "라벨이 SDS 유래인 행에서 라벨과 같은 문서를 본다. 구조적 고위험"),
    "L2": ("성분별 위험정보(GHS 구분) + 조성표 농도", [],
           "라벨은 완제품 문서, 피처는 성분 문서 + 조성표. 문서가 다르다"),
    "L3": ("성분 SMILES 구조", [],
           "라벨과 문서 공유 없음. 구조는 화학물질 식별자에서 계산된다"),
}


def rule_metrics(y, pred, mask=None):
    """규칙 baseline 지표. mask 는 규칙이 판정을 낸 행."""
    if mask is None:
        mask = np.ones(len(y), dtype=bool)
    yy, pp = y[mask], pred[mask]
    if len(yy) == 0 or len(set(yy)) < 2:
        return {"n": int(len(yy)), "판정불가": True}
    tn, fp, fn, tp = confusion_matrix(yy, pp, labels=[0, 1]).ravel()
    rec = tp / (tp + fn) if tp + fn else float("nan")
    spec = tn / (tn + fp) if tn + fp else float("nan")
    return {
        "n": int(len(yy)), "양성": int(yy.sum()),
        "유병률": round(float(yy.mean()), 4),
        "정확도": round(float((tp + tn) / len(yy)), 4),
        "재현율": round(float(rec), 4), "특이도": round(float(spec), 4),
        "정밀도": round(float(tp / (tp + fp)), 4) if tp + fp else None,
        "BA": round(float((rec + spec) / 2), 4),
        "MCC": round(float(matthews_corrcoef(yy, pp)), 4),
        "tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
    }


log("=== L2 노선 누출 감사 ===")
log(f"대표 관할 {CANON_JUR_V8} · 규약 §4")
Z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
ARM = json.load(open(COMMON / "피처노선_arm.json", encoding="utf-8"))["arm"]
Y = pd.read_parquet(L.SRC / "y_formulation.parquet")
X = pd.read_parquet(L.SRC / "X_formulation.parquet")
assert (Y["Formulation_ID"].astype(str).to_numpy() == Z["Formulation_ID"]).all(), \
    "행 정렬 이탈 — 배포 폴드와 y_formulation 의 Formulation_ID 순서가 다르다"

# ============================================================ 감사 A
log("--- 감사 A: 피처·라벨 문서 출처 대조 ---")
ROWS_A, SUBGROUP = [], {}
for ep in EPS:
    m = Z[f"mask_{ep}"]
    y = Z[f"y_{ep}"].astype(int)
    src = Y.loc[m, f"y_{ep}_src"].fillna("__없음__").to_numpy()
    unknown = sorted(set(src) - set(LABEL_SRC))
    assert not unknown, f"{ep}: 분류 규칙 없는 라벨 출처 {unknown}"
    tiers = {}
    for s in sorted(set(src)):
        desc, tier, why = LABEL_SRC[s]
        sm = src == s
        tiers.setdefault(tier, np.zeros(len(y), bool))
        tiers[tier] |= sm
        ROWS_A.append({"endpoint": ep, "라벨출처": s, "라벨문서": desc, "층": tier,
                       "n": int(sm.sum()), "양성": int(y[sm].sum()),
                       "유병률": round(float(y[sm].mean()), 4), "근거": why})
        log(f"  {ep:4} {s:14} [{tier:14}] n={int(sm.sum()):4} "
            f"유병률={y[sm].mean():.4f}")
    for tier, tm in tiers.items():
        SUBGROUP[f"{ep}__{tier}"] = tm
        log(f"  {ep:4} 층 {tier:14} n={int(tm.sum()):4} "
            f"유병률={y[tm].mean():.4f}  → 부분집합 마스크 산출")

# 노선별 문서 공유 위험도와 피처 가용성. L3 는 구조 미해석 행에서 전열 결측이 되는데
# 이는 누출이 아니라 노선 간 비교 공정성 문제라 별도 열로 기록한다.
ROWS_LANE = []
for ep in EPS:
    m = Z[f"mask_{ep}"]
    for lane in ("L1", "L2", "L3"):
        cols = ARM[f"{lane}단독"]
        sub = X.loc[m, cols]
        doc, risk_tiers, why = LANE_DOC[lane]
        risk_n = int(sum(int(SUBGROUP[f"{ep}__{t}"].sum())
                         for t in risk_tiers if f"{ep}__{t}" in SUBGROUP))
        ROWS_LANE.append({
            "endpoint": ep, "노선": lane, "피처_정보원": doc, "열수": len(cols),
            # 선언값이다 — LANE_DOC 의 리터럴에서 계산된다. L2·L3 은 위험 층 목록이
            # 비어 있으므로 항상 0 이 나오고, 그것은 측정 결과가 아니라 노선 정의의
            # 되풀이다. 실제 문서 교차는 아래 `문서교차_측정` 이 계산한다.
            "문서공유_위험행_선언값": risk_n,
            "문서공유_위험행_선언값_비율": round(risk_n / int(m.sum()), 4),
            "평균결측률": round(float(sub.isna().mean().mean()), 4),
            "전열결측_행": int(sub.isna().all(axis=1).sum()),
            "판정": why})
        log(f"  {ep:4} {lane} 열={len(cols):4} 문서공유위험행={risk_n:4} "
            f"({risk_n/int(m.sum()):.1%}) 전열결측={int(sub.isna().all(axis=1).sum()):3}")

# ---------------------------------------------------------- 감사 A-2 (결측 경로)
# 층별 유병률이 크게 다르면 **층을 맞히는 피처는 라벨도 맞힌다.** 값이 아니라 결측
# 패턴이 그 통로가 될 수 있다. 노선별로 층간 행평균 결측률 차이를 재서 이 통로의
# 크기를 정량화한다. 값 누출과 달리 열을 배제해서 막을 수 없고, 층별 분리 측정으로
# 드러내는 수밖에 없다.
log("--- 감사 A-2: 결측 패턴이 라벨 출처 층의 대리변수인지 ---")
TIERS = ("문서독립", "문서공유_분류절", "문서공유_11절")
ROWS_A2 = []
for ep in EPS:
    m = Z[f"mask_{ep}"]
    y = Z[f"y_{ep}"].astype(int)
    prev = {t: float(y[SUBGROUP[f"{ep}__{t}"]].mean()) for t in TIERS}
    for lane in ("L1", "L2", "L3"):
        na = X.loc[m, ARM[f"{lane}단독"]].isna().mean(axis=1).to_numpy()
        per = {t: float(na[SUBGROUP[f"{ep}__{t}"]].mean()) for t in TIERS}
        spread = max(per.values()) - min(per.values())
        ROWS_A2.append({
            "endpoint": ep, "노선": lane,
            **{f"결측률_{t}": round(per[t], 4) for t in TIERS},
            **{f"유병률_{t}": round(prev[t], 4) for t in TIERS},
            "결측률_층간최대차": round(spread, 4),
            "판정": ("고위험 — 결측 패턴만으로 층을 상당히 식별할 수 있다"
                   if spread >= 0.15 else
                   "중위험 — 층간 차이가 있다" if spread >= 0.05 else
                   "저위험 — 층간 차이가 작다")})
        log(f"  {ep:4} {lane} 결측률 " +
            " ".join(f"{t}={per[t]:.3f}" for t in TIERS) +
            f" 최대차={spread:.3f} → {ROWS_A2[-1]['판정']}")
    log(f"  {ep:4} 층별 유병률 " + " ".join(f"{t}={prev[t]:.3f}" for t in TIERS) +
        " ← 층간 격차가 크면 층 식별이 곧 라벨 예측이 된다")

# 금지 피처가 실제로 학습 arm 에 없는지 확인한다 — t11_* 배제가 11절 층을 지키는
# 유일한 장치이므로 여기서 무너지면 감사 전체가 무의미하다.
MAN = pd.read_csv(L.SRC / "feature_role_manifest.csv")
BAN = set(MAN[(MAN["sheet"] == "formulation")
              & (MAN["sds_coderived"].astype(bool)
                 | (MAN["feature_role"] == "label_coderived"))]["column"])
for k, cols in ARM.items():
    bad = sorted(set(cols) & BAN)
    assert not bad, f"arm {k} 에 라벨 공파생 피처 {bad[:5]}"
log(f"  금지 피처 {len(BAN)}열이 모든 arm 에서 배제됨 — 확인")

# ============================================================ 감사 B
log("--- 감사 B: CT 규칙 단독의 라벨 재현 ---")
data = L.FormulationData(log)
CT_ARMS = {"A0_base": "A0_base", "A2_aug_nc_unknown": "A2_aug_nc_unknown"}
ROWS_B = []
for arm_name, arm_key in CT_ARMS.items():
    ct = data.build_ct({ep: arm_key for ep in L.EPS_ALL}, L.EPS_ALL)
    for ep in EPS:
        m = Z[f"mask_{ep}"]
        y = Z[f"y_{ep}"].astype(int)
        cat = ct.loc[:, f"f_ct_{ep}_cat"].to_numpy()[m]
        proj = L.JUR[CANON_JUR_V8][ep]
        # 규칙이 판정을 낸 행(성분 구분이 하나라도 알려진 행)
        covered = np.array([c is not None and not pd.isna(c) for c in cat])
        pred = np.zeros(len(y), dtype=int)
        unmapped = set()
        for i, c in enumerate(cat):
            if not covered[i]:
                continue
            key = str(c)
            if key not in proj:
                unmapped.add(key)
                covered[i] = False
                continue
            pred[i] = proj[key]
        assert not unmapped, f"{ep}/{arm_name}: 투영표에 없는 CT 출력 {unmapped}"
        # `행커버리지` = 규칙이 판정을 낸 **행**의 비율. 규약 §4.2 의 `성분커버리지`
        # (제형당 구분이 알려진 성분의 평균 비율)와 다른 양이다. 두 수치가 ~1.9 배
        # 차이나므로 같은 이름을 쓰지 않는다.
        icov = ct.loc[:, f"f_ct_{ep}_coverage"].to_numpy(dtype=float)[m]
        base = {"CT_arm": arm_name, "endpoint": ep, "관할": CANON_JUR_V8,
                "행커버리지": round(float(covered.mean()), 4),
                "성분커버리지_평균": round(float(np.nanmean(icov[covered])), 4)}
        # P3 가 '규칙이 판정한 행'에 한정한 모델 성능을 재서 규칙 단독과 같은 n 으로
        # 비교할 수 있게 마스크를 함께 배포한다. 이것 없이 비교하면 n 이 달라 무의미하다.
        SUBGROUP[f"{ep}__CT판정행_{arm_name}"] = covered.copy()
        # 규칙이 판정한 행만
        ROWS_B.append({**base, "집계범위": "규칙판정행만",
                       **rule_metrics(y, pred, covered)})
        # 미판정을 음성으로 두고 전체 — 실제 규칙 단독 운용에 해당한다
        ROWS_B.append({**base, "집계범위": "전체(미판정=음성)",
                       **rule_metrics(y, pred)})
        # 성분커버리지 구간별. `판정행` 조건은 성분 하나만 알려져도 참이므로
        # (lib_model.py:456-457, n_known >= 1) 집계값은 입력이 대부분 결측인 행과
        # 완전히 특성화된 행을 섞는다. 가산합은 특성화된 질량에 단조 증가하므로
        # 저커버리지 행은 구조적으로 NC 쪽으로 치우친다 — 즉 집계값은 규칙의 재현
        # 능력을 **과소** 보고한다. 연구 질문에 답하려면 구간을 나눠 봐야 한다.
        for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)):
            bm = covered & (icov >= lo) & (icov < hi)
            if bm.sum() == 0:
                continue
            ROWS_B.append({**base, "집계범위": f"성분커버리지_[{lo:.2f},{hi:.2f})",
                           **rule_metrics(y, pred, bm)})
            SUBGROUP[f"{ep}__CT커버리지_{arm_name}_{lo:.2f}"] = bm.copy()
        c = next(r for r in reversed(ROWS_B) if r["집계범위"] == "규칙판정행만")
        hi_ = next((r for r in reversed(ROWS_B)
                    if str(r["집계범위"]).startswith("성분커버리지_[0.75")), None)
        log(f"  {arm_name:18} {ep:4} 행커버리지={covered.mean():.3f} "
            f"성분커버리지={np.nanmean(icov[covered]):.3f} "
            f"규칙판정행 n={c['n']:4} 재현율={c.get('재현율')} MCC={c.get('MCC')}"
            + (f" | 성분커버리지>=0.75 n={hi_['n']} MCC={hi_.get('MCC')}"
               if hi_ else ""))

# 진짜 누출 신호: CT 입력인 성분 구분의 출처가 완제품 라벨을 역산한 것인지.
log("--- 감사 B-2: CT 입력 성분 구분의 출처 ---")
ING = data.ING
# A0 은 ing_ghs_{ep}(출처 = ing_ghs_src / ing_ghs_src_url)를 읽고, A2 는
# ing_ghs_indep_*(출처 = *_tier_src)로 증강한다(lib_model.py:337·344). 두 계열을
# 모두 감사한다. `_src_url` 을 빼면 출처 라벨만 보고 실제 문서를 안 보는 셈이다.
SRC_COLS = [c for c in ("ing_ghs_src", "ing_ghs_src_url",
                        "ing_ghs_indep_eye_tier", "ing_ghs_indep_eye_tier_src",
                        "ing_ghs_indep_skin_tier", "ing_ghs_indep_skin_tier_src")
            if c in ING.columns]
B2 = []
for c in SRC_COLS:
    if c.endswith("_url"):          # URL 원문은 남기지 않고 도메인으로 집계한다
        v = ING[c].dropna().astype(str)
        vc = v.str.extract(r"^https?://([^/]+)")[0].fillna("__비URL__") \
              .value_counts(dropna=False)
    else:
        vc = ING[c].value_counts(dropna=False)
    for k, v_ in vc.items():
        B2.append({"열": c, "값": str(k), "성분행수": int(v_)})
    log(f"  {c}: {dict(list(vc.items())[:6])}")

# 실측 1 — phase1_row 의 문서 귀속. build_input_v5.py:675-680 에서 phase1_row 는
# **같은 Formulation_ID 안의** Phase 1 성분 행과 매칭될 때 붙는다. 제형 키로 맞춘
# 값이므로 완제품 문서를 역산했을 가능성을 실제로 확인해야 한다.
PR = ING[ING["ing_ghs_src"] == "phase1_row"]
pr_dom = PR["ing_ghs_src_url"].fillna("__없음__").astype(str) \
    .str.extract(r"^https?://([^/]+)")[0].fillna("__비URL__")
B2_PR = {"성분행수": int(len(PR)),
         "src_url_보유": int(PR["ing_ghs_src_url"].notna().sum()),
         "도메인": {str(k): int(v) for k, v in pr_dom.value_counts().items()}}
log(f"  phase1_row {B2_PR['성분행수']}행 → 도메인 {B2_PR['도메인']}")

# 실측 2 — 공급사 SDS 페이지 유래분. PubChem/ECHA 가 아닌 도메인은 성분 SDS 를
# 게시한 상용 사이트다. **성분** 문서이므로 완제품 라벨과 문서를 공유하지는 않지만,
# "전부 공공 DB 조회" 라고 말할 수 없으므로 수를 밝힌다.
PUB = ("pubchem.ncbi.nlm.nih.gov", "echa.europa.eu")
alldom = ING.loc[ING["ing_ghs_src"].notna(), "ing_ghs_src_url"] \
    .fillna("__없음__").astype(str).str.extract(r"^https?://([^/]+)")[0] \
    .fillna("__비URL__")
vend = {str(k): int(v) for k, v in alldom.value_counts().items() if k not in PUB}
B2_VEND = {"공공DB_행": int(alldom.isin(PUB).sum()), "비공공_행": int(sum(vend.values())),
           "비공공_도메인": vend}
log(f"  출처 URL 도메인: 공공DB {B2_VEND['공공DB_행']}행 / "
    f"공급사 등 {B2_VEND['비공공_행']}행 {list(vend)}")

# 실측 3 — 배정된 tier 와 메모에 기록된 출처의 불일치(measure_ct_tier_correction.py
# 가 재는 것과 같은 축). 불일치 행은 CT 입력의 출처 주장이 확정적이지 않다.
B2_MM = {}
for ep in EPS:
    a, b = ING[f"ing_ghs_indep_{ep}_tier"], ING[f"ing_ghs_indep_{ep}_tier_src"]
    both = a.notna() & b.notna()
    B2_MM[ep] = {"둘다보유": int(both.sum()),
                 "불일치": int((a[both].astype(str) != b[both].astype(str)).sum())}
    log(f"  {ep} tier vs tier_src 불일치 {B2_MM[ep]['불일치']}/{B2_MM[ep]['둘다보유']}")

B2_NOTE = ("성분 구분의 출처가 PubChem LCSS · ECHA C&L · 독립 GHS 조사이면 완제품 "
           "라벨과 무관하다. 완제품 SDS 에서 역산한 값이 섞이면 CT 블록이 라벨을 "
           "우회 참조하게 되므로 그 출처는 CT 입력에서 배제해야 한다. "
           "실측 결과 완제품 SDS 유래 출처는 없다 — phase1_row 는 제형 키로 매칭되지만 "
           "문서는 전부 PubChem PUG-View 이고, 나머지도 공공 DB 또는 성분 공급사 SDS "
           "페이지다. 단 tier 와 tier_src 가 불일치하는 행이 있어 A2 증강분의 출처 "
           "주장은 행 단위로 확정적이지 않다.")

# ============================================================ 산출
pd.DataFrame(ROWS_A).to_csv(OUT / "누출감사_A_라벨출처층.csv",
                            index=False, encoding="utf-8-sig")
pd.DataFrame(ROWS_LANE).to_csv(OUT / "누출감사_A_노선별위험.csv",
                               index=False, encoding="utf-8-sig")
pd.DataFrame(ROWS_A2).to_csv(OUT / "누출감사_A2_결측경로.csv",
                             index=False, encoding="utf-8-sig")
pd.DataFrame(ROWS_B).to_csv(OUT / "누출감사_B_CT규칙단독.csv",
                            index=False, encoding="utf-8-sig")
pd.DataFrame(B2).to_csv(OUT / "누출감사_B2_성분구분출처.csv",
                        index=False, encoding="utf-8-sig")
np.savez_compressed(OUT / "부분집합마스크.npz",
                    **{k: v for k, v in SUBGROUP.items()})
with open(OUT / "요약.json", "w", encoding="utf-8") as f:
    json.dump({
        "규약": "05_제안/노선분리_비교규약.md §4",
        "대표관할": CANON_JUR_V8,
        "감사A": {
            "목적": "피처와 라벨의 문서 출처가 겹치는지 확인하고 층별 부분집합을 정의",
            "층": {"문서독립": "라벨이 NTP 시험 측정값 — SDS 유래 피처와 문서 공유 없음",
                  "문서공유_분류절": "라벨이 완제품 SDS 2·3절 분류 문구",
                  "문서공유_11절": "라벨을 완제품 SDS 11절에서 역산 — 최고 위험"},
            "노선별_비대칭": "L1 은 라벨과 피처가 같은 완제품 SDS 문서라 구조적 고위험. "
                       "L2·L3 은 문서 공유가 없다. **이것은 감사 출력이 아니라 노선 "
                       "정의의 가정이다** — `문서공유_위험행_선언값` 열은 LANE_DOC "
                       "리터럴에서 계산되며 L2·L3 은 항상 0 이 나온다. 실제 문서 귀속은 "
                       "감사 B-2 가 성분 출처 URL 로 확인한다.",
            "P3_에서_할_것": "부분집합마스크.npz 의 층별 마스크로 성능을 분리 측정한다. "
                       "**판정 기준은 '문서공유 층에서만 성능이 높은가' 가 아니다** — "
                       "그 기준은 A-2 가 지목한 통로(층간 유병률 격차 자체)를 보지 "
                       "못한다. 층 유병률이 0.19 vs 0.75 로 갈리면 '어느 층인가'를 "
                       "맞히는 것만으로 pooled AUC 가 올라가고, 그 몫은 층 **내부** "
                       "성능에는 없다. 따라서 기준은: 층별 AUC 를 일치쌍수"
                       "(n_pos x n_neg)로 가중평균한 값을 누출보정 추정치로 삼고, "
                       "pooled 값과 함께 보고한다. 두 값의 차이가 곧 라벨 출처 "
                       "판별에 기댄 몫이다. 성능 게이트는 보정 추정치로 판정한다.",
            "금지피처_확인": f"{len(BAN)}열이 모든 arm 에서 배제됨(assert)",
            "행": ROWS_A, "노선별": ROWS_LANE,
        },
        "감사A2": {
            "목적": "층별 유병률 격차가 크면 층을 맞히는 피처가 라벨도 맞힌다. "
                  "값이 아니라 결측 패턴이 그 통로가 되는지 정량화한다.",
            "왜_열배제로_막을_수_없는가": "결측은 특정 열의 값이 아니라 행 전체의 "
                            "관측 가능성이다. 열을 빼도 남은 열의 결측 패턴에 같은 "
                            "정보가 남는다. 층별 분리 측정으로 드러내는 수밖에 없다.",
            "P3_에서_할_것": "층별 성능을 반드시 분리 보고하고, 일치쌍 가중 층내부 AUC 를 "
                       "누출보정 대표값으로 함께 낸다. 층 내부에서 성능이 무너지면 "
                       "전체 수치는 라벨 출처 판별에 기댄 것이다. 층별 수치를 CSV 에만 "
                       "남기고 결론을 쓰지 않는 것은 이 감사를 통과한 것이 아니다.",
            "행": ROWS_A2,
        },
        "감사B_집계주의": {
            "행커버리지_vs_성분커버리지": "`행커버리지` 는 규칙이 판정을 낸 행의 비율이고 "
                            "`성분커버리지_평균` 은 그 행들에서 구분이 알려진 성분의 "
                            "평균 비율이다. 규약 §4.2 의 커버리지는 후자다. 두 수치는 "
                            "약 1.9 배 차이나므로 같은 이름으로 부르지 않는다.",
            "성분커버리지_구간별을_봐야_하는_이유": "판정행 조건은 성분 하나만 알려져도 "
                            "참이다(n_known >= 1). 가산합은 특성화된 질량에 단조 "
                            "증가하므로 저커버리지 행은 구조적으로 NC 쪽으로 치우친다. "
                            "집계값은 규칙의 재현 능력을 과소 보고하며, 연구 질문의 "
                            "답은 커버리지 의존적이다.",
        },
        "감사B": {
            "목적": "성능 중 '규칙이 설명하는 몫'과 '학습 증분'을 분리",
            "해석규정": "CT 는 규제 규칙 자체다. 규칙이 라벨을 잘 재현하는 것은 누출이 "
                    "아니라 이 연구의 측정 대상이다. 이 수치를 누출 근거로 읽지 않는다.",
            "집계범위_주의": "'규칙판정행만' 은 성분 구분이 알려진 행에 한정한 값이고, "
                       "'전체(미판정=음성)' 는 규칙 단독 운용 시의 실제 성능이다. "
                       "커버리지가 낮으므로 둘을 반드시 함께 읽는다.",
            "행": ROWS_B,
        },
        "감사B2": {"목적": "CT 입력 성분 구분이 완제품 라벨을 역산한 것이 아닌지",
                  "해석": B2_NOTE, "phase1_row_문서귀속": B2_PR,
                  "출처URL_공공DB여부": B2_VEND, "tier_대_tier_src_불일치": B2_MM,
                  "행": B2},
    }, f, ensure_ascii=False, indent=2, default=str)
log(f"완료 → {OUT.relative_to(L.ROOT)}/ (감사 A·B·B2 csv, 부분집합마스크.npz, 요약.json)")
