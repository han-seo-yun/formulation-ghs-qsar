# -*- coding: utf-8 -*-
"""
현황 리포트(docx) 생성기.
- 차트: report_assets/*.png  (matplotlib, AppleGothic)
- 문서: 데이터_현황리포트_20260830.docx  (python-docx)
숫자는 out/v2 · input_dataset_v2.xlsx 에서 직접 읽거나 상단 상수에 고정.
"""
import os, json
import numpy as np
import pandas as pd
import matplotlib
from pathlib import Path
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

plt.rcParams["font.family"] = "AppleGothic"
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 160
plt.rcParams["savefig.bbox"] = "tight"

ROOT = str(Path(__file__).resolve().parent.parent)
BASE = ROOT
AST = os.path.join(BASE, "report_assets")
os.makedirs(AST, exist_ok=True)

# ── 색 팔레트 (인쇄 대비 고려) ────────────────────────────────
C_POS, C_NEG, C_MISS = "#c0392b", "#2e86c1", "#d5dbdb"
C_OK, C_GAP, C_WARN = "#27ae60", "#e67e22", "#8e44ad"
C_GREY, C_DARK = "#7f8c8d", "#2c3e50"

N_FORM = 1675
N_ING = 5287
EPS = ["눈", "피부", "감작"]

# ── 실측값 ────────────────────────────────────────────────────
LAB = {"눈": 1105, "피부": 992, "감작": 595}
NC = {"눈": 379, "피부": 614, "감작": 209}
POS = {"눈": 726, "피부": 378, "감작": 386}
RESID = {"눈": 456, "피부": 382, "감작": 184}
R0 = {"눈": 190, "피부": 260, "감작": 113}
RP = {"눈": 213, "피부": 94, "감작": 61}
RN = {"눈": 53, "피부": 28, "감작": 10}
CT = {"눈": 719, "피부": 719, "감작": 721}

# F0.5 이전 → 이후
LAB_BEFORE = {"눈": 955, "피부": 855, "감작": 411}
NC_BEFORE = {"눈": 266, "피부": 493, "감작": 50}

# SDS §11 원문 2차 판독 헤드룸
S11 = {  # (원문보유, 추출성공, 미추출, 미추출중 라벨부재)
    "눈": (874, 566, 308, 163),
    "피부": (801, 481, 320, 190),
    "감작": (788, 461, 327, 278),
}

# 베이스라인 (RF, StratifiedGroupKFold 5, 튜닝 전)
PERF = [
    ("눈 이진", 1105, 0.798, 0.624, 0.793),
    ("눈 순서형", 1105, 0.499, 0.496, 0.128),
    ("피부 이진", 992, 0.679, 0.752, 0.000),
    ("피부 순서형", 992, 0.507, 0.494, 0.191),
    ("감작 이진", 595, 0.808, 0.670, 0.787),
    ("감작 순서형", 595, 0.677, 0.670, 0.393),
]
TRACKB = [("Track B 눈", 456, 0.628), ("Track B 피부", 382, 0.618), ("Track B 감작", 184, 0.417)]

# 확보 현황 (항목, 확보, 전체, 단위)
COVER = [
    ("타깃(라벨) 보유 제형", 1525, N_FORM),
    ("눈 라벨", 1105, N_FORM),
    ("피부 라벨", 992, N_FORM),
    ("감작 라벨", 595, N_FORM),
    ("CT 가산식 산출 제형", 719, N_FORM),
    ("제형코드(CIPAC)", 1326, N_FORM),
    ("pH (pc2_ph)", 533, N_FORM),
    ("성분 구조(SMILES)", 3574, N_ING),
    ("성분 함량", 2769, N_ING),
    ("성분 GHS 구분", 1138, N_ING),
]


def pct(a, b):
    return 100.0 * a / b


# ══════════════════════════════════════════════════════════════
# fig1 — 라벨 확보 현황
# ══════════════════════════════════════════════════════════════
def fig1():
    fig, ax = plt.subplots(figsize=(7.4, 3.5))
    x = np.arange(3)
    p = [POS[e] for e in EPS]
    n = [NC[e] for e in EPS]
    m = [N_FORM - LAB[e] for e in EPS]
    ax.bar(x, p, 0.58, label="양성(구분 1/2/3)", color=C_POS)
    ax.bar(x, n, 0.58, bottom=p, label="음성(NC = 분류 대상 아님)", color=C_NEG)
    ax.bar(x, m, 0.58, bottom=np.array(p) + np.array(n), label="라벨 미확보", color=C_MISS)
    for i, e in enumerate(EPS):
        ax.text(i, p[i] / 2, f"{p[i]}", ha="center", va="center", color="w", fontsize=9, weight="bold")
        ax.text(i, p[i] + n[i] / 2, f"{n[i]}", ha="center", va="center", color="w", fontsize=9, weight="bold")
        ax.text(i, N_FORM + 30, f"라벨 {LAB[e]}  ({pct(LAB[e], N_FORM):.0f}%)",
                ha="center", fontsize=9, weight="bold", color=C_DARK)
    ax.set_xticks(x); ax.set_xticklabels([f"{e} 자극·손상" if e != "감작" else "피부 감작" for e in EPS])
    ax.set_ylim(0, N_FORM * 1.16); ax.set_ylabel("제형 수 (전체 1,675)")
    ax.set_title("그림 1. 엔드포인트별 라벨 확보 현황 — 양성 / 음성 / 미확보", weight="bold", pad=26)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.30), ncol=3, frameon=False, fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(f"{AST}/fig1_labels.png"); plt.close(fig)


# ══════════════════════════════════════════════════════════════
# fig2 — F0.5(SDS §11 재파싱) 전후
# ══════════════════════════════════════════════════════════════
def fig2():
    fig, axes = plt.subplots(1, 2, figsize=(7.8, 3.2))
    x = np.arange(3); w = 0.36
    for ax, (D0, D1, ttl, unit) in zip(
        axes,
        [(LAB_BEFORE, LAB, "전체 라벨 수", "행"), (NC_BEFORE, NC, "음성(NC) 라벨 수", "행")],
    ):
        b0 = [D0[e] for e in EPS]; b1 = [D1[e] for e in EPS]
        ax.bar(x - w / 2, b0, w, label="F0.5 이전", color=C_GREY)
        ax.bar(x + w / 2, b1, w, label="F0.5 이후", color=C_OK)
        for i in range(3):
            ax.text(i + w / 2, b1[i] + max(b1) * 0.03, f"+{b1[i]-b0[i]}",
                    ha="center", fontsize=9, weight="bold", color=C_OK)
        ax.set_xticks(x); ax.set_xticklabels(EPS)
        ax.set_title(ttl, fontsize=10, weight="bold")
        ax.set_ylim(0, max(b1) * 1.22); ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("행 수"); axes[0].legend(frameon=False, fontsize=9)
    fig.suptitle("그림 2. SDS Section 11 서술문 재파싱(F0.5) 효과 — 웹 접근 0회",
                 weight="bold", y=1.04)
    fig.savefig(f"{AST}/fig2_f05.png"); plt.close(fig)


# ══════════════════════════════════════════════════════════════
# fig3 — CT 가산식 잔차 분포
# ══════════════════════════════════════════════════════════════
def fig3():
    fig, ax = plt.subplots(figsize=(7.4, 2.9))
    y = np.arange(3)[::-1]
    tot = [RESID[e] for e in EPS]
    a = np.array([RP[e] / RESID[e] for e in EPS]) * 100
    b = np.array([R0[e] / RESID[e] for e in EPS]) * 100
    c = np.array([RN[e] / RESID[e] for e in EPS]) * 100
    ax.barh(y, a, 0.5, color=C_POS, label="CT 과소예측 (실제가 더 심함)")
    ax.barh(y, b, 0.5, left=a, color=C_OK, label="CT 적중 (잔차 0)")
    ax.barh(y, c, 0.5, left=a + b, color=C_NEG, label="CT 과대예측")
    for i, e in enumerate(EPS):
        yy = y[i]
        ax.text(a[i] / 2, yy, f"{RP[e]}\n{a[i]:.0f}%", ha="center", va="center", color="w", fontsize=8, weight="bold")
        ax.text(a[i] + b[i] / 2, yy, f"{R0[e]}\n{b[i]:.0f}%", ha="center", va="center", color="w", fontsize=8, weight="bold")
        if c[i] > 6:
            ax.text(a[i] + b[i] + c[i] / 2, yy, f"{RN[e]}", ha="center", va="center", color="w", fontsize=8, weight="bold")
        ax.text(101.5, yy, f"n={tot[i]}", va="center", fontsize=9, color=C_DARK)
    ax.set_yticks(y); ax.set_yticklabels(EPS); ax.set_xlim(0, 100); ax.set_xlabel("비율 (%)")
    ax.set_title("그림 3. GHS 농도가산식(CT)의 오차 방향 — Track B 의 근거",
                 weight="bold", pad=24)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, -0.55), ncol=3, frameon=False, fontsize=8.5)
    ax.spines[["top", "right", "left"]].set_visible(False)
    fig.savefig(f"{AST}/fig3_ct_resid.png"); plt.close(fig)


# ══════════════════════════════════════════════════════════════
# fig4 — 베이스라인 성능 vs 더미
# ══════════════════════════════════════════════════════════════
def fig4():
    fig, ax = plt.subplots(figsize=(7.6, 3.4))
    names = [p[0] for p in PERF]
    x = np.arange(len(names)); w = 0.27
    f1 = [p[2] for p in PERF]; ba = [p[3] for p in PERF]; dm = [p[4] for p in PERF]
    ax.bar(x - w, f1, w, label="모델 F1", color=C_POS)
    ax.bar(x, ba, w, label="모델 balAcc", color=C_NEG)
    ax.bar(x + w, dm, w, label="더미 F1 (다수클래스)", color=C_MISS, edgecolor=C_GREY)
    for i in range(len(names)):
        d = f1[i] - dm[i]
        ax.text(x[i] - w, f1[i] + 0.02, f"{f1[i]:.3f}", ha="center", fontsize=7.5)
        ax.text(x[i] + w, dm[i] + 0.02, f"{dm[i]:.3f}", ha="center", fontsize=7.5, color=C_GREY)
        ax.text(x[i], -0.10, f"Δ{d:+.3f}", ha="center", fontsize=8,
                weight="bold", color=C_OK if d > 0 else C_POS)
    ax.set_xticks(x); ax.set_xticklabels(names, fontsize=9)
    ax.set_ylim(-0.14, 1.02); ax.axhline(0, color="k", lw=0.6)
    ax.set_ylabel("점수"); ax.legend(frameon=False, fontsize=9, ncol=3, loc="upper center",
                                   bbox_to_anchor=(0.5, 1.16))
    ax.set_title("그림 4. 베이스라인 성능 — 더미 대비로 읽어야 하는 이유 (튜닝·리샘플링·지문 미적용)",
                 weight="bold", pad=34, fontsize=10.5)
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(f"{AST}/fig4_baseline.png"); plt.close(fig)


# ══════════════════════════════════════════════════════════════
# fig5 — 데이터 확보율 지도
# ══════════════════════════════════════════════════════════════
def fig5():
    fig, ax = plt.subplots(figsize=(7.4, 4.0))
    labs = [c[0] for c in COVER][::-1]
    got = [c[1] for c in COVER][::-1]
    tot = [c[2] for c in COVER][::-1]
    rate = [pct(g, t) for g, t in zip(got, tot)]
    y = np.arange(len(labs))
    cols = [C_OK if r >= 60 else (C_GAP if r >= 35 else C_POS) for r in rate]
    ax.barh(y, rate, 0.6, color=cols)
    ax.barh(y, [100] * len(y), 0.6, color="none", edgecolor=C_MISS, lw=0.8, zorder=0)
    for i in range(len(labs)):
        ax.text(rate[i] + 1.2, y[i], f"{got[i]:,} / {tot[i]:,}  ({rate[i]:.0f}%)",
                va="center", fontsize=8.5, color=C_DARK)
    ax.set_yticks(y); ax.set_yticklabels(labs, fontsize=9)
    ax.set_xlim(0, 132); ax.set_xlabel("확보율 (%)")
    ax.set_title("그림 5. 항목별 데이터 확보율 — 위 7개는 제형(1,675) 기준, 아래 3개는 성분 행(5,287) 기준",
                 weight="bold", pad=12, fontsize=10.5)
    ax.spines[["top", "right"]].set_visible(False)
    ax.axvline(60, color=C_GREY, ls=":", lw=0.8)
    fig.savefig(f"{AST}/fig5_coverage.png"); plt.close(fig)


# ══════════════════════════════════════════════════════════════
# fig6 — 남은 헤드룸 (§11 원문 2차 판독)
# ══════════════════════════════════════════════════════════════
def fig6():
    fig, axes = plt.subplots(1, 2, figsize=(7.8, 3.3),
                             gridspec_kw={"width_ratios": [1.15, 1]})
    ax = axes[0]
    x = np.arange(3); w = 0.6
    ok = [S11[e][1] for e in EPS]
    miss = [S11[e][2] for e in EPS]
    nolab = [S11[e][3] for e in EPS]
    ax.bar(x, ok, w, color=C_OK, label="라벨 추출 성공")
    ax.bar(x, miss, w, bottom=ok, color=C_GAP, label="원문 보유 · 추출 실패")
    for i, e in enumerate(EPS):
        ax.text(i, ok[i] / 2, f"{ok[i]}", ha="center", va="center", color="w", fontsize=9, weight="bold")
        ax.text(i, ok[i] + miss[i] / 2, f"{miss[i]}", ha="center", va="center", color="w", fontsize=9, weight="bold")
        ax.text(i, ok[i] + miss[i] + 22, f"(라벨부재 {nolab[i]})", ha="center", fontsize=8, color=C_POS)
    ax.set_xticks(x); ax.set_xticklabels(EPS); ax.set_ylabel("행 수")
    ax.set_ylim(0, 1050)
    ax.set_title("SDS §11 원문 판독 상태", fontsize=10, weight="bold")
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)

    ax = axes[1]
    cur = [LAB[e] for e in EPS]
    add = [S11[e][3] for e in EPS]
    ax.bar(x, cur, w, color=C_NEG, label="현재 라벨")
    ax.bar(x, add, w, bottom=cur, color=C_WARN, hatch="//", edgecolor="w", label="2차 판독 상한")
    for i, e in enumerate(EPS):
        ax.text(i, cur[i] + add[i] + 25, f"{cur[i]+add[i]}", ha="center", fontsize=9, weight="bold", color=C_WARN)
        ax.text(i, cur[i] / 2, f"{cur[i]}", ha="center", va="center", color="w", fontsize=9, weight="bold")
    ax.set_xticks(x); ax.set_xticklabels(EPS); ax.set_ylim(0, 1620)
    ax.set_title("단독·웹 0회로 도달 가능한 상한", fontsize=10, weight="bold")
    ax.legend(frameon=False, fontsize=8.5, loc="upper right")
    ax.spines[["top", "right"]].set_visible(False)

    fig.suptitle("그림 6. 남은 회수 여지 — 수집은 끝났고 판독이 남은 구간", weight="bold", y=1.04)
    fig.savefig(f"{AST}/fig6_headroom.png"); plt.close(fig)


# ══════════════════════════════════════════════════════════════
# fig7 — Track A / Track B 구조도
# ══════════════════════════════════════════════════════════════
def box(ax, xy, w, h, text, fc, ec=None, fs=8.5, tc="white", bold=True):
    x, y = xy
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012,rounding_size=0.02",
                                fc=fc, ec=ec or fc, lw=1.2, zorder=2))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fs,
            color=tc, weight="bold" if bold else "normal", zorder=3, linespacing=1.45)


def arrow(ax, p1, p2, color=C_GREY, style="-|>", lw=1.3, rad=0.0):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, mutation_scale=11,
                                 color=color, lw=lw, zorder=1,
                                 connectionstyle=f"arc3,rad={rad}"))


def fig7():
    fig, ax = plt.subplots(figsize=(7.6, 4.6))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")

    box(ax, (0.01, 0.72), 0.235, 0.235,
        "성분 블록\n5,287 행 × 174\n\nRDKit 31종 · 지문\n구조경보 24 · 역할\n계면활성제 전하", "#34495e", fs=8)
    box(ax, (0.01, 0.44), 0.235, 0.21,
        "성분 자신의\nGHS 구분 · 함량\n\nGHS 1,138 / 함량 2,769", "#5d6d7e", fs=8)

    arrow(ax, (0.245, 0.84), (0.315, 0.85))
    arrow(ax, (0.245, 0.545), (0.315, 0.55))

    box(ax, (0.32, 0.795), 0.26, 0.155,
        "① 농도가중 모멘트\n(가산 성분) 19종 × 6집계", C_NEG, fs=8.5)
    box(ax, (0.32, 0.625), 0.26, 0.155,
        "② 상호작용 항 (비가산)\n전하쌍 · 용매×logP · Tanimoto", C_POS, fs=8)
    box(ax, (0.32, 0.455), 0.26, 0.155,
        "③ 제형 실측 물성\npH 533 · 점도 · 성상", C_GAP, fs=8.5)
    box(ax, (0.32, 0.285), 0.26, 0.155,
        "④ CT baseline 블록\nΣCat1 · ΣCat2 · coverage", C_WARN, fs=8.5)

    ax.text(0.45, 0.975, "제형 블록  X_formulation  1,675 × 518", ha="center",
            fontsize=9, weight="bold", color=C_DARK)

    # Track A
    arrow(ax, (0.585, 0.87), (0.665, 0.82), rad=-0.1)
    arrow(ax, (0.585, 0.70), (0.665, 0.80), rad=0.1)
    arrow(ax, (0.585, 0.53), (0.665, 0.78), rad=0.16)
    box(ax, (0.67, 0.685), 0.315, 0.225,
        "Track A — 직접 예측\n입력: ①+②+③+지문\n학습 가능 1,345 행\n순서형 0.499 / 0.507 / 0.677",
        "#1a5276", fs=8.5)

    # Track B
    arrow(ax, (0.585, 0.70), (0.665, 0.50), rad=-0.16)
    arrow(ax, (0.585, 0.53), (0.665, 0.48), rad=-0.1)
    arrow(ax, (0.585, 0.36), (0.665, 0.46), rad=0.1)
    box(ax, (0.67, 0.335), 0.315, 0.225,
        "Track B — CT 잔차 보정\n입력: ②+③+CT 합계만\n(① 가산항 투입 금지)\n"
        "학습 가능 652 행 · 0.628 / 0.618",
        "#7d3c98", fs=8.5)

    arrow(ax, (0.827, 0.685), (0.827, 0.565), color=C_DARK, style="<|-|>", lw=1.1)
    box(ax, (0.67, 0.155), 0.315, 0.13,
        "커버리지 기반 앙상블 라우팅\nf_ct_*_coverage 로 게이팅", C_OK, fs=8.5)
    arrow(ax, (0.79, 0.335), (0.79, 0.29), color=C_DARK)
    arrow(ax, (0.995, 0.685), (0.995, 0.29), color=C_DARK, rad=-0.35)

    box(ax, (0.01, 0.005), 0.975, 0.115,
        "누출 차단   ·  label_source 228열은 X 에서 물리적 제외   "
        "·  sds_coderived 54열은 ablation 대상\n"
        "·  StratifiedGroupKFold 664 그룹(주성분 CAS 기준) 필수   ·  리샘플링은 CV 폴드 내부에서만",
        "#f4f6f7", ec=C_GREY, fs=8, tc=C_DARK, bold=False)

    ax.set_title("그림 7. 2층 디스크립터와 Track A / Track B 구조",
                 weight="bold", fontsize=11, pad=6)
    fig.savefig(f"{AST}/fig7_pipeline.png"); plt.close(fig)


# ══════════════════════════════════════════════════════════════
# fig8 — 다음 주 작업 배분
# ══════════════════════════════════════════════════════════════
def fig8():
    tasks = [
        ("SDS §11 원문 2차 판독",          0, 3, "단독"),
        ("특성 [제품값아님] 열 분리",       0, 1, "단독"),
        ("라벨 충돌 222건 판정",            1, 3, "단독"),
        ("재빌드 #1 (라벨 반영)",           3, 3.4, "단독"),
        ("반복 CV(5×5) 전환",              0, 2, "단독"),
        ("순서형 → 누적 이진 스택",         1.5, 3.5, "단독"),
        ("지문(fp_form_pooled) 투입",       3, 4, "단독"),
        ("Track B SHAP · 라우팅",          3.5, 5, "단독"),
        ("SDS Section 9 pH 수집",          0, 4.2, "팀"),
        ("pH 반영 재빌드 + 스모크",         4.2, 5, "공동"),
    ]
    cmap = {"단독": C_NEG, "팀": C_POS, "공동": C_OK}
    fig, ax = plt.subplots(figsize=(7.6, 3.8))
    for i, (name, s, e, who) in enumerate(tasks[::-1]):
        y = i
        ax.barh(y, e - s, left=s, height=0.55, color=cmap[who],
                edgecolor="white", lw=0.8)
        ax.text(s + (e - s) / 2, y, who, ha="center", va="center",
                color="w", fontsize=7.5, weight="bold")
    ax.set_yticks(range(len(tasks)))
    ax.set_yticklabels([t[0] for t in tasks[::-1]], fontsize=8.5)
    ax.set_xticks([0, 1, 2, 3, 4, 5])
    ax.set_xticklabels(["월", "화", "수", "목", "금", ""])
    ax.set_xlim(0, 5.2)
    for xv in [1, 2, 3, 4]:
        ax.axvline(xv, color=C_MISS, lw=0.7, zorder=0)
    ax.set_title("그림 8. 다음 주 작업 배분 — 데이터 정리·모델링은 단독, Section 9 는 팀",
                 weight="bold", fontsize=10.5, pad=10)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.invert_yaxis(); ax.invert_yaxis()
    fig.savefig(f"{AST}/fig8_roadmap.png"); plt.close(fig)


for f in (fig1, fig2, fig3, fig4, fig5, fig6, fig7, fig8):
    f(); print("  ok", f.__name__)
print("charts →", AST)
