#!/usr/bin/env python3
"""피처 설계 근거 리포트용 그림 — 산출: v8_리포트/figures/figB*.png

`build_feature_design_docx.py` 가 이 그림들을 넣는다. 본문과 그림을 한 스크립트에
두면 그림만 다시 그릴 때 문서까지 덮어쓰므로 나눠 둔다.

중요도는 **설계 검토용 순위**다. 교차검증 채점(`run_model_lane2.py`)과 무관하며
전체 학습행에 RF 를 시드 5개로 적합해 `feature_importances_` 를 평균한다.
상관된 열끼리 중요도를 나눠 갖는 성질이 있어 절대값이 아니라 묶음 합으로 읽는다.

열 수는 `피처노선_arm.json` 의 `L2단독` 을 따른다 — 2026-09-21 결정으로 42 → 31 열이
됐다. **그림 파일명은 고정이고 덮어쓴다**(`build_feature_design_docx.py` 가 그 이름으로
넣는다). 42열 판은 `figures/figB*_42열.png` 로 따로 보존해 두었고, arm 이 이미 31열이라
재생성은 불가능하다. 중요도 CSV 는 반대로 파일명에 열 수를 박아 두 판이 나란히 남는다.

읽기 전용 입력: v4_fixed/*.parquet, input_dataset_v6.xlsx, v8_공통/*
"""
from __future__ import annotations

import json

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt                                      # noqa: E402
from matplotlib.gridspec import GridSpec                              # noqa: E402

import lib_model as L                                                # noqa: E402

plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False

COMMON = L.ROOT / "04_모델산출물" / "v8_공통"
FIG = L.ROOT / "04_모델산출물" / "v8_리포트" / "figures"
FIG.mkdir(parents=True, exist_ok=True)
log = L.make_logger(L.ROOT / "04_모델산출물" / "v8_리포트" / "피처설계_그림.log")

ARM = json.load(open(COMMON / "피처노선_arm.json", encoding="utf-8"))["arm"]
COLS = ARM["L2단독"]
Z = np.load(COMMON / "공통폴드.npz", allow_pickle=False)
data = L.FormulationData(log)
assert (data.FID == Z["Formulation_ID"]).all(), "행 정렬 이탈"
IDX = {c: i for i, c in enumerate(data.CHEM)}
A = data.matrices()[("CT_권고", "native_복원")][:, [IDX[c] for c in COLS]]
D = pd.DataFrame(A, columns=COLS)

# --- 완전 중복 열 쌍. 표 5 의 근거이므로 그림 생성 때마다 다시 확인한다 -----
# 기대값은 arm 내용에서 끌어온다. 2026-09-21 결정으로 `audit_feature_lane.py` 가
# 중복쌍의 한쪽을 arm 에서 빼므로, 갱신된 arm 에서는 0쌍이어야 하고 갱신 전
# 42열 arm 에서는 4쌍이어야 한다. `== 4` 로 못 박아 두면 갱신 직후 assert 가
# 터지고, 반대로 무검사로 두면 중복이 되살아나도 표 5 가 조용히 틀린다.
DUP = [(COLS[i], COLS[j]) for i in range(len(COLS)) for j in range(i + 1, len(COLS))
       if np.array_equal(A[:, i], A[:, j], equal_nan=True)]
# `FEAT_DROP["중복쌍"]` 의 원소 하나가 정확히 한 쌍을 만든다. arm 에 남아 있는
# 원소 수가 그대로 기대 쌍 수다 — 개수만 세면 기존 쌍이 사라지고 무관한 새 쌍이
# 생겨도 통과하므로, 어느 쌍인지까지 대조한다.
TWIN = {"ct_eye_ord": "f_ct_eye_ord", "ct_skin_ord": "f_ct_skin_ord",
        "ct_sens_ord": "f_ct_sens_ord", "f_pct_active": "f_pct_active_presumed"}
assert set(TWIN) == set(L.FEAT_DROP["중복쌍"]), "중복쌍 정의가 lib_model 과 갈렸다"
EXP = {frozenset((c, TWIN[c])) for c in set(TWIN) & set(COLS)}
assert {frozenset(p) for p in DUP} == EXP, (
    f"완전 중복 쌍이 기대와 다르다 (arm {len(COLS)}열). 실제 {DUP} / 기대 "
    f"{[tuple(sorted(p)) for p in EXP]} — 표 5 를 갱신해야 한다")
log(f"완전 중복 쌍 {len(DUP)}개 {DUP}")

IMP = {}
for ep in ("eye", "skin"):
    Xi = A[Z[f"mask_{ep}"]]
    y = Z[f"y_{ep}"].astype(int)
    IMP[ep] = np.mean([L.rf(si).fit(Xi, y).feature_importances_
                       for si in range(len(L.SEEDS))], axis=0)
IMP = pd.DataFrame(IMP, index=COLS)


def grp(c):
    if "_cat_" in c:
        return "① 규칙 판정 (원-핫)"
    if c.endswith("_ord"):
        return "② 규칙 판정 (서수)"
    if c.startswith("f_ct_"):
        return "③ 가산식 원값"
    if c in ("f_shannon", "f_simpson"):
        return "⑤ 조성 다양성"
    if c in ("ct_not_applicable", "f_has_synergist"):
        return "⑥ 이진 플래그"
    return "④ 역할별 농도"


GRP = {c: grp(c) for c in COLS}
ORDER = ["① 규칙 판정 (원-핫)", "② 규칙 판정 (서수)", "③ 가산식 원값",
         "④ 역할별 농도", "⑤ 조성 다양성", "⑥ 이진 플래그"]
CGRP = dict(zip(ORDER, ["#4F81BD", "#8DB4E2", "#1F3864", "#E8A33D",
                        "#70AD47", "#A6A6A6"]))
ordered = [c for g in ORDER for c in COLS if GRP[c] == g]
# 파일명에 열 수를 박는다. 고정 이름이면 31열 표가 `..._L2단독42.csv` 를 덮어써서
# `measure_lane2_cleanup.py` 의 42열 재현 검증 근거가 사라진다. 두 판이 나란히 남아야 한다.
IMP.reindex(ordered).assign(묶음=[GRP[c] for c in ordered]) \
   .to_csv(FIG.parent / f"피처중요도_L2단독{len(COLS)}.csv", encoding="utf-8-sig")
log("묶음별 중요도 합(%)\n" + (IMP.assign(g=[GRP[c] for c in IMP.index])
                              .groupby("g").sum() * 100).round(1).to_string())

# ============================================== figB1 — arm 전경(현행 31열)
na = D.isna().mean() * 100
fig = plt.figure(figsize=(11.5, 10.5))
gs = GridSpec(1, 3, width_ratios=[2.0, 1.0, 0.85], wspace=0.08)
yy = np.arange(len(ordered))[::-1]
ax0 = fig.add_subplot(gs[0])
ax0.axis("off")
ax0.set_xlim(0, 1.02)
ax0.set_ylim(-1, len(ordered))
seen = set()
for y, c in zip(yy, ordered):
    ax0.text(1.0, y, c, ha="right", va="center", fontsize=8, family="Menlo",
             color=CGRP[GRP[c]],
             fontweight="bold" if GRP[c][0] in "①②③" else "normal")
    if GRP[c] not in seen:
        seen.add(GRP[c])
        ax0.text(0.0, y, GRP[c], ha="left", va="center", fontsize=8.5,
                 color=CGRP[GRP[c]], fontweight="bold")
ax1 = fig.add_subplot(gs[1])
ax1.barh(yy, [na[c] for c in ordered],
         color=[CGRP[GRP[c]] for c in ordered], height=0.72)
ax1.axvline(16.78, ls="--", lw=1, color="#C00000")
ax1.text(17.6, len(ordered) - 1.5, "16.8% = 조성미상 281행",
         fontsize=7.5, color="#C00000")
ax1.set_ylim(-1, len(ordered))
ax1.set_yticks([])
ax1.set_xlim(0, 60)
ax1.set_xlabel("결측률 (%)", fontsize=9)
ax1.tick_params(labelsize=8)
for s in ("top", "right"):
    ax1.spines[s].set_visible(False)
ax2 = fig.add_subplot(gs[2])
Mi = IMP.reindex(ordered)[["eye", "skin"]].to_numpy()
ax2.imshow(Mi, aspect="auto", cmap="YlOrRd", vmin=0, vmax=Mi.max())
ax2.set_xticks([0, 1])
ax2.set_xticklabels(["눈", "피부"], fontsize=9)
ax2.set_yticks([])
for i in range(len(ordered)):
    for j in range(2):
        ax2.text(j, i, f"{Mi[i, j] * 100:.1f}", ha="center", va="center",
                 fontsize=6.5,
                 color="white" if Mi[i, j] > Mi.max() * 0.6 else "#333333")
ax2.set_xlabel("RF 중요도 (%)", fontsize=9)
fig.suptitle(f"[그림 B2] L2단독 {len(COLS)}열 전경 — 무엇이 피처인가 · 얼마나 비어 "
             "있는가 · 얼마나 쓰이는가", fontsize=11.5, fontweight="bold", y=0.955)
fig.text(0.5, 0.02, "중요도 = 랜덤포레스트 feature_importances_ 시드 5개 평균. "
                    "색 = 설계 근거 묶음", ha="center", fontsize=8, color="#555555")
fig.savefig(FIG / "figB1_feature_map.png", dpi=200, bbox_inches="tight")
plt.close(fig)

# ============================================== figB2 — 상관 히트맵
S = D[ordered].corr(method="spearman")
fig, ax = plt.subplots(figsize=(9.6, 8.6))
im = ax.imshow(S.values, cmap="RdBu_r", vmin=-1, vmax=1)
ax.set_xticks(range(len(ordered)))
ax.set_yticks(range(len(ordered)))
ax.set_xticklabels(ordered, rotation=90, fontsize=6, family="Menlo")
ax.set_yticklabels(ordered, fontsize=6, family="Menlo")
for t, c in zip(ax.get_xticklabels(), ordered):
    t.set_color(CGRP[GRP[c]])
for t, c in zip(ax.get_yticklabels(), ordered):
    t.set_color(CGRP[GRP[c]])
b = 0
for g in ORDER:
    k = sum(1 for c in ordered if GRP[c] == g)
    ax.add_patch(plt.Rectangle((b - .5, b - .5), k, k, fill=False, ec="black", lw=1.4))
    b += k
for a_, b_ in DUP:
    i, j = ordered.index(a_), ordered.index(b_)
    for u, v in ((i, j), (j, i)):
        ax.plot(v, u, marker="o", ms=7, mfc="none", mec="#00A000", mew=1.8)
plt.colorbar(im, ax=ax, shrink=0.7, label="Spearman ρ")
ax.set_title(f"[그림 B3] {len(COLS)}열 상관 구조 — 검은 테두리 = 설계 묶음"
             + (f", 초록 원 = 완전 중복 {len(DUP)}쌍" if DUP else " (완전 중복 쌍 없음)"),
             fontsize=11, fontweight="bold", pad=12)
fig.savefig(FIG / "figB2_corr_heatmap.png", dpi=200, bbox_inches="tight")
plt.close(fig)

# ============================================== figB3 — 노선별 열 수
names = ["L2단독", "L2+결합", "L2+L1", "L2+L3", "전체_노선혼합", "L1단독", "L3단독"]
vals = [len(ARM[n]) for n in names]
fig, ax = plt.subplots(figsize=(9.6, 3.9))
ax.barh(range(len(names))[::-1], vals,
        color=["#1F3864" if n == "L2단독" else "#A6A6A6" for n in names], height=0.62)
for y, (n, v) in zip(range(len(names))[::-1], zip(names, vals)):
    ax.text(v + 2, y, f"{v}열", va="center", fontsize=9,
            fontweight="bold" if n == "L2단독" else "normal")
ax.set_yticks(range(len(names))[::-1])
ax.set_yticklabels(names, fontsize=9.5)
ax.set_xlim(0, 250)
ax.set_xlabel("학습에 들어가는 열 수", fontsize=9)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
# 막대 라벨("31열")이 `v + 2` 에 붙으므로 주석은 그보다 충분히 뒤에서 시작해야
# 한다. 종전 하드코딩 46(=42+4)은 42열 그림에서 이미 라벨과 붙어 화살표가 먹혔다.
ax.text(vals[0] + 24, len(names) - 1,
        "← 대표 셀. 다른 노선의 정보가 한 열도 섞이지 않은 유일한 arm",
        fontsize=8.5, color="#1F3864", va="center")
ax.set_title("[그림 B1] 노선별 열 수 — L2단독을 대표로 두는 이유",
             fontsize=11, fontweight="bold", pad=10)
fig.savefig(FIG / "figB3_lane_columns.png", dpi=200, bbox_inches="tight")
plt.close(fig)
log(f"완료 → {FIG.relative_to(L.ROOT)}/figB1~B3.png")
