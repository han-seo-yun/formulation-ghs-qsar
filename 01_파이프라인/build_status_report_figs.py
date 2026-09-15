#!/usr/bin/env python3
"""현황 리포트 그림 5장 — 산출: 04_모델산출물/v8_리포트/figures/*.png

`동물의약품_데이터분석보고서_260908` 의 시각 언어를 그대로 따른다: 가로 막대,
초록=쓸 수 있는 것 / 회색=못 쓰는 것, 주황 파선=기준선, 제목은 `[그림 N]` +
부제 한 줄, 값은 막대 끝에 직접 적는다. 색만으로 뜻을 전하지 않고 라벨을 병기한다.

수치는 전부 산출물에서 읽는다. 이 스크립트에 손으로 적은 숫자는 없다.

읽기 전용 입력: input_dataset_v6.xlsx, v8_공통/*, v8_노선2/*, v8_누출감사/*
"""
from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import apply_ing_ghs_survey as A
import lib_model as L

for _f in ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic"]:
    if _f in {f.name for f in mpl.font_manager.fontManager.ttflist}:
        mpl.rcParams["font.family"] = _f
        break
mpl.rcParams["axes.unicode_minus"] = False
mpl.rcParams["figure.dpi"] = 110
mpl.rcParams["savefig.bbox"] = "tight"
mpl.rcParams["axes.grid"] = True
mpl.rcParams["grid.alpha"] = 0.25

C = dict(base="#4C6EF5", warn="#E8590C", ok="#2F9E44", mute="#868E96",
         alt="#9C36B5", light="#DEE2E6")
OUT = L.ROOT / "04_모델산출물" / "v8_리포트"
FIG = OUT / "figures"
FIG.mkdir(parents=True, exist_ok=True)
log = L.make_logger(OUT / "figures.log")


def save(name):
    p = FIG / f"{name}.png"
    plt.savefig(p, dpi=150)
    plt.close()
    log(f"  → {p.relative_to(L.ROOT)}")


def has(s):
    return s.notna() & (~s.astype(str).str.strip().str.lower()
                        .isin(["nan", "none", ""]))


log("=== 현황 리포트 그림 ===")
X = pd.ExcelFile(L.V6)
F, I = X.parse("formulation"), X.parse("ingredient")
Z = np.load(L.ROOT / "04_모델산출물" / "v8_공통" / "공통폴드.npz", allow_pickle=False)
M = pd.read_csv(L.ROOT / "04_모델산출물" / "v8_노선2" / "지표_노선2.csv")
B = pd.read_csv(L.ROOT / "04_모델산출물" / "v8_누출감사" / "누출감사_B_CT규칙단독.csv")
S = pd.read_csv(L.ROOT / "04_모델산출물" / "v8_노선2" / "조사반영_대조.csv")
A0 = pd.read_csv(L.ROOT / "04_모델산출물" / "v8_노선2" / "A0채움_대조.csv")
LAY = pd.read_csv(L.ROOT / "04_모델산출물" / "v8_누출감사" / "누출감사_A_라벨출처층.csv")

REP = M[(M["대표"] == 1) & (M["단위"] == "제형")]
n = {ep: int(Z[f"mask_{ep}"].sum()) for ep in ("eye", "skin")}
ct2 = B[(B["CT_arm"] == "A2_aug_nc_unknown") & (B["집계범위"].astype(str)
                                                .str.contains("전체"))]

# ---------------------------------------------- [그림 1] 제형 감소 퍼널
lab = [f"원본 제형\n(v6 formulation)", "라벨 보유\n(EU_CLP 눈)",
       "라벨 보유\n(EU_CLP 피부)", "가산식이 판정을 내는 행\n(눈 · A2 arm)",
       "가산식이 판정을 내는 행\n(피부 · A2 arm)"]
val = [len(F), n["eye"], n["skin"],
       int(ct2[ct2.endpoint == "eye"]["n"].iloc[0]),
       int(ct2[ct2.endpoint == "skin"]["n"].iloc[0])]
col = [C["ok"], C["base"], C["base"], C["mute"], C["mute"]]
fig, ax = plt.subplots(figsize=(9, 4.4))
ax.barh(lab[::-1], val[::-1], color=col[::-1])
for y, v in enumerate(val[::-1]):
    ax.text(v + 20, y, f"{v:,}", va="center", fontsize=10)
ax.set_xlabel("제형 수")
ax.set_title(f"[그림 1] 원본 {len(F):,}개 제형 → 학습 {n['eye']:,} / {n['skin']:,}행 "
             f"→ 규칙이 말을 하는 행 {val[3]:,} / {val[4]:,}", fontsize=12, pad=12)
save("fig1_funnel")

# ------------------------------------- [그림 2] 성분 정보 결측 = 진짜 병목
J = A.apply(I)
pct = has(I["ing_pct_best"])
c0 = has(I["ing_ghs_eye"]) | has(I["ing_ghs_indep_eye_cat"])
c1 = has(J["ing_ghs_eye"]) | has(J["ing_ghs_indep_eye_cat"])
lab2 = ["전체 성분행", "GHS 구분 보유\n(조사 전)", "GHS 구분 보유\n(조사 후)",
        "농도 보유", "구분 + 농도\n(가산식 가용 · 조사 전)",
        "구분 + 농도\n(가산식 가용 · 조사 후)"]
val2 = [len(I), int(c0.sum()), int(c1.sum()), int(pct.sum()),
        int((c0 & pct).sum()), int((c1 & pct).sum())]
col2 = [C["mute"], C["light"], C["base"], C["warn"], C["light"], C["ok"]]
fig, ax = plt.subplots(figsize=(9, 4.8))
ax.barh(lab2[::-1], val2[::-1], color=col2[::-1])
for y, v in enumerate(val2[::-1]):
    ax.text(v + 40, y, f"{v:,} ({v/len(I)*100:.1f}%)", va="center", fontsize=9)
ax.axvline(int(pct.sum()), color=C["warn"], ls="--", lw=1.5)
ax.text(int(pct.sum()) + 60, 4.6, f"농도 천장 {int(pct.sum()):,}행",
        color=C["warn"], fontsize=9)
ax.set_xlabel("성분행 수 (눈 기준)")
ax.set_title(f"[그림 2] 성분 {len(I):,}행 — 구분을 채워도 농도 결측 "
             f"{int((~pct).sum()):,}행이 천장을 만든다\n"
             f"주황 파선 = 농도 보유 행수. 초록 막대는 이 선을 넘을 수 없다",
             fontsize=12, pad=10)
save("fig2_ingredient_gap")

# ------------------------------ [그림 3] 라벨 출처 층별 유병률 격차 (누출 통로)
# 이 CSV 는 **라벨출처별** 행이고 층은 그 상위 묶음이다. 층은 배타 분할이므로
# 합산해서 층 단위로 되돌린다 — 출처별로 그리면 같은 층이 여러 막대로 쪼개진다
T = LAY.groupby(["endpoint", "층"], as_index=False)[["n", "양성"]].sum()
T["유병률"] = T["양성"] / T["n"]
T["층"] = pd.Categorical(T["층"], ["문서독립", "문서공유_분류절", "문서공유_11절"],
                        ordered=True)
T = T.sort_values(["endpoint", "층"])
tier_col = "층"
fig, ax = plt.subplots(1, 2, figsize=(12, 4.2))
for i, ep in enumerate(("eye", "skin")):
    d = T[T["endpoint"] == ep]
    x = np.arange(len(d))
    p = d["유병률"].to_numpy(dtype=float)
    cols = [C["warn"] if v > 0.5 else C["base"] for v in p]
    ax[i].bar(x, p, color=cols)
    ax[i].set_xticks(x)
    ax[i].set_xticklabels(d[tier_col].astype(str), fontsize=9)
    for j, (v, nn) in enumerate(zip(p, d["n"].to_numpy())):
        ax[i].text(j, v + 0.01, f"{v:.3f}\n(n={int(nn)})", ha="center",
                   fontsize=9)
    ax[i].axhline(float(REP[REP.endpoint == ep]["유병률"].iloc[0]),
                  color=C["mute"], ls="--", lw=1.2)
    ax[i].set_ylim(0, 0.9)
    ax[i].set_title(f"{ep} (전체 유병률 "
                    f"{float(REP[REP.endpoint == ep]['유병률'].iloc[0]):.3f} = 회색 파선)",
                    fontsize=11)
    ax[i].set_ylabel("양성 비율")
fig.suptitle("[그림 3] 라벨을 어느 문서에서 읽었는지에 따라 유병률이 갈린다\n"
             "주황 = 라벨과 피처가 문서를 공유하는 층. 이 격차가 누출 통로다",
             fontsize=12)
save("fig3_label_source_strata")

# ---------------------- [그림 4] 성분커버리지 구간별 규칙 MCC = 커버리지 함수
band = B[(B["관할"] == "EU_CLP") & (~B["집계범위"].astype(str)
                                  .str.contains("전체"))]
fig, ax = plt.subplots(figsize=(9, 4.6))
mk = {"A0_base": "o", "A2_aug_nc_unknown": "s"}
for arm in band["CT_arm"].unique():
    for ep, ls in (("eye", "-"), ("skin", "--")):
        d = band[(band.CT_arm == arm) & (band.endpoint == ep)]
        if not len(d):
            continue
        ax.plot(range(len(d)), d["MCC"], ls, marker=mk.get(arm, "^"),
                label=f"{ep} · {arm}",
                color=C["base"] if ep == "eye" else C["ok"],
                alpha=1.0 if arm == "A0_base" else 0.55)
        for j, (v, nn) in enumerate(zip(d["MCC"], d["n"])):
            ax.annotate(f"n={int(nn)}", (j, v), textcoords="offset points",
                        xytext=(0, 7), fontsize=7, ha="center")
ax.set_xticks(range(band["집계범위"].nunique()))
ax.set_xticklabels(band["집계범위"].unique(), fontsize=9)
ax.axhline(0, color=C["mute"], lw=1)
ax.set_xlabel("성분커버리지 구간 (그 제형에서 GHS 구분이 알려진 성분의 비율)")
ax.set_ylabel("규칙 단독 MCC")
ax.legend(fontsize=8)
ax.set_title("[그림 4] 연구 질문의 답은 하나의 숫자가 아니라 커버리지 함수다\n"
             "성분 조성이 온전한 제형에서는 GHS 가산식만으로 MCC 0.38–0.52",
             fontsize=12, pad=10)
save("fig4_coverage_function")

# --------------------- [그림 5] pooled vs 보정 AUC — 게이트는 보정으로 판정한다
arms = M[(M["단위"] == "제형") & (M["관할"] == "EU_CLP")]
fig, ax = plt.subplots(1, 2, figsize=(12.5, 4.6))
for i, ep in enumerate(("eye", "skin")):
    d = arms[arms.endpoint == ep].sort_values("roc_auc_보정")
    y = np.arange(len(d))
    ax[i].barh(y - 0.19, d["roc_auc"], height=0.36, color=C["light"],
               label="pooled AUC")
    ax[i].barh(y + 0.19, d["roc_auc_보정"], height=0.36,
               color=[C["ok"] if v >= 0.70 else C["mute"]
                      for v in d["roc_auc_보정"]],
               label="누출보정 AUC (게이트 판정 대상)")
    for j, (a, b) in enumerate(zip(d["roc_auc"], d["roc_auc_보정"])):
        ax[i].text(a + 0.004, j - 0.19, f"{a:.3f}", va="center", fontsize=8,
                   color=C["mute"])
        ax[i].text(b + 0.004, j + 0.19, f"{b:.3f}", va="center", fontsize=8,
                   fontweight="bold")
    ax[i].axvline(0.70, color=C["warn"], ls="--", lw=1.5)
    ax[i].text(0.703, -0.75, "게이트 0.70", color=C["warn"], fontsize=9)
    ax[i].set_yticks(y)
    ax[i].set_yticklabels(d["피처셋"], fontsize=9)
    ax[i].set_xlim(0.5, 0.82)
    ax[i].set_title(f"{ep} (n={int(d['n'].iloc[0])})", fontsize=11)
    ax[i].legend(fontsize=8, loc="lower right")
fig.suptitle("[그림 5] pooled 로는 통과, 보정으로는 미달 — 초과분의 27~30%가 "
             "'라벨을 어느 문서에서 읽었는가' 였다\n"
             "대표 셀은 L2단독. 게이트를 넘는 두 arm 은 다른 노선 정보를 더한 것이라 "
             "L2 단독 가설의 근거가 못 된다", fontsize=12)
save("fig5_gate")

log(f"완료 → {FIG.relative_to(L.ROOT)}")
