#!/usr/bin/env python3
"""회의 자료에 인용할 시각화 4개를 산출물에서 직접 만든다.

값은 전부 04_모델산출물 의 csv/xlsx 에서 읽는다. 그림 안에 숫자를 직접 적어서
그림만 봐도 표와 대조할 수 있게 한다.

출력: 04_모델산출물/v6_jurisdiction/그림/F1~F4.png
이 스크립트는 make_meeting_lecture_docx.py 가 import 해서 쓴다.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib import font_manager

ROOT = Path("/Users/hanseoyun/Desktop/신작물보호제")
J = ROOT / "04_모델산출물" / "v6_jurisdiction"
FIG = J / "그림"

_KO = next((n for n in ("AppleGothic", "Apple SD Gothic Neo", "NanumGothic")
            if any(f.name == n for f in font_manager.fontManager.ttflist)), None)
plt.rcParams.update({
    "font.family": _KO or "DejaVu Sans",
    "axes.unicode_minus": False,
    "figure.dpi": 200,
    "savefig.dpi": 200,
    "axes.edgecolor": "#8C8C8C",
    "axes.labelcolor": "#262626",
    "text.color": "#262626",
    "xtick.color": "#595959",
    "ytick.color": "#595959",
    "font.size": 8,
})

NAVY, TEAL, ORANGE, GRAY = "#1F4E79", "#2E8B8B", "#C55A11", "#A6A6A6"

# 3-2 라벨 출처 — 표와 그림이 같은 값을 쓰도록 여기 한 곳에만 둔다
LABEL_SRC = {
    #                 NTP 실측  SDS v1  SDS 2차  S11 재파싱  Phase1  합계  출처충돌
    "눈 (eye)":   dict(NTP=608, SDS1=290, SDS2=127, S11=106, P1=22, total=1153, conf=147),
    "피부 (skin)": dict(NTP=544, SDS1=285, SDS2=115, S11=94, P1=21, total=1059, conf=96),
    "감작 (sens)": dict(NTP=0, SDS1=395, SDS2=192, S11=109, P1=20, total=716, conf=15),
}

CELLS = [("eye", "UN_GHS+K_REACH+US_OSHA", "눈 — UN/한국/미국"),
         ("eye", "EU_CLP", "눈 — EU"),
         ("skin", "UN_GHS", "피부 — UN"),
         ("skin", "EU_CLP+K_REACH+US_OSHA", "피부 — EU/한국/미국"),
         ("sens", "UN_GHS+EU_CLP+K_REACH+US_OSHA", "감작 — 전 관할")]


def _save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    out = FIG / name
    fig.savefig(out, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"  {out.relative_to(ROOT)}")
    return out


def f1_performance(MET):
    """F1. 성능 5셀 대 목표선 — 4장 표와 같은 값."""
    rows = []
    for ep, jur, nm in CELLS:
        r = MET[(MET.endpoint == ep) & (MET["관할"] == jur)
                & (MET.CT == "CT_권고") & (MET["결측처리"] == "nan0")].iloc[0]
        rows.append((nm, r.roc_auc, r.roc_auc_sd, r.MCC, int(r.n)))
    rows.reverse()
    y = range(len(rows))
    fig, ax = plt.subplots(figsize=(6.6, 2.9))
    ax.barh([i + 0.18 for i in y], [r[1] for r in rows], height=0.36,
            xerr=[r[2] for r in rows], color=NAVY, error_kw=dict(ecolor="#404040", lw=0.8),
            label="ROC-AUC")
    ax.barh([i - 0.18 for i in y], [r[3] for r in rows], height=0.36,
            color=TEAL, label="MCC")
    for i, r in enumerate(rows):
        ax.text(r[1] + 0.012, i + 0.18, f"{r[1]:.3f}", va="center", fontsize=7.5,
                color=NAVY, fontweight="bold")
        ax.text(r[3] + 0.012, i - 0.18, f"{r[3]:.3f}", va="center", fontsize=7.5,
                color=TEAL)
    ax.axvline(0.8, color=ORANGE, ls="--", lw=1)
    ax.axvline(0.9, color="#C00000", ls="--", lw=1)
    top = len(rows) - 0.35
    ax.text(0.79, top, "실무 채택선 0.8", color=ORANGE, fontsize=7.5,
            ha="right", va="center")
    ax.text(0.905, top, "목표 0.9", color="#C00000", fontsize=7.5,
            ha="left", va="center")
    ax.set_yticks(list(y))
    ax.set_yticklabels([f"{r[0]}  (n={r[4]:,})" for r in rows], fontsize=8)
    ax.set_xlim(0, 1.0)
    ax.set_ylim(-0.6, len(rows) - 0.05)
    ax.set_xlabel("5-fold × 5시드 평균 (오차막대 = 시드 표준편차)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=2,
              fontsize=7.5, frameon=False)
    ax.set_title("F1. 현재 성능 — 전 셀이 0.8 미달, MCC 는 그보다 훨씬 낮다",
                 fontsize=9.5, color=NAVY, fontweight="bold", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    return _save(fig, "F1_성능_목표대비.png")


def f2_label_source():
    """F2. 라벨 출처 구성과 출처 충돌 — 3장."""
    order = [("NTP", "NTP 실측(동물시험)", NAVY), ("SDS1", "SDS v1", TEAL),
             ("SDS2", "SDS 2차", "#8FBFBF"), ("S11", "S11 재파싱", "#F0B27A"),
             ("P1", "Phase1", GRAY)]
    eps = list(LABEL_SRC)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(6.9, 2.6),
                                  gridspec_kw=dict(width_ratios=[2.5, 1]))
    left = [0] * len(eps)
    for k, lab, col in order:
        v = [LABEL_SRC[e][k] for e in eps]
        ax.barh(eps, v, left=left, color=col, label=lab, height=0.55)
        for i, (vv, ll) in enumerate(zip(v, left)):
            if vv >= 90:
                ax.text(ll + vv / 2, i, f"{vv}", ha="center", va="center",
                        fontsize=7, color="white", fontweight="bold")
        left = [a + b for a, b in zip(left, v)]
    for i, e in enumerate(eps):
        ax.text(LABEL_SRC[e]["total"] + 15, i, f"계 {LABEL_SRC[e]['total']:,}",
                va="center", fontsize=7.5)
    ax.set_xlim(0, 1350)
    ax.invert_yaxis()
    ax.set_xlabel("라벨 보유 제형 수")
    ax.legend(fontsize=6.8, frameon=False, ncol=3, loc="upper center",
              bbox_to_anchor=(0.5, -0.24))
    ax.set_title("F2. 라벨은 어디서 왔나 — 감작은 실측 자료가 0",
                 fontsize=9.5, color=NAVY, fontweight="bold", loc="left")
    ax.spines[["top", "right"]].set_visible(False)

    conf = [LABEL_SRC[e]["conf"] for e in eps]
    ax2.barh(eps, conf, color=ORANGE, height=0.55)
    for i, c in enumerate(conf):
        ax2.text(c + 4, i, f"{c}", va="center", fontsize=7.5, color=ORANGE,
                 fontweight="bold")
    ax2.invert_yaxis()
    ax2.set_yticks([])
    ax2.set_xlim(0, 185)
    ax2.set_xlabel("출처 충돌 (건)")
    ax2.set_title("계 258건 — 원문 재판정 대상", fontsize=8.5, color=ORANGE,
                  loc="left")
    ax2.spines[["top", "right"]].set_visible(False)
    return _save(fig, "F2_라벨출처_충돌.png")


def f3_jurisdiction(MET):
    """F3. 관할 분기가 라벨을 몇 개 뒤집는가 — 5장.

    같은 데이터인데 관할 정의만 바꾸면 양성 라벨 수가 이만큼 달라진다.
    차이의 정체가 눈 2B 327행 · 피부 3 15행이다.
    """
    def pos(ep, jur):
        r = MET[(MET.endpoint == ep) & (MET["관할"] == jur)
                & (MET.CT == "CT_권고") & (MET["결측처리"] == "nan0")].iloc[0]
        return int(r["양성"]), int(r.n)

    e_un, n_e = pos("eye", "UN_GHS+K_REACH+US_OSHA")
    e_eu, _ = pos("eye", "EU_CLP")
    s_un, n_s = pos("skin", "UN_GHS")
    s_eu, n_s2 = pos("skin", "EU_CLP+K_REACH+US_OSHA")

    fig, ax = plt.subplots(figsize=(6.6, 2.5))
    xs = [0, 1, 2.6, 3.6]
    vals = [e_un, e_eu, s_un, s_eu]
    labs = ["눈 · UN/한국/미국\n(2B 를 양성)", "눈 · EU CLP\n(2B 미채택)",
            "피부 · UN\n(Cat 3 을 양성)", "피부 · EU/한국/미국\n(Cat 3 미채택)"]
    ax.bar(xs, vals, width=0.72, color=[NAVY, "#9DC3E6", NAVY, "#9DC3E6"])
    for x, v, n in zip(xs, vals, [n_e, n_e, n_s, n_s2]):
        ax.text(x, v + 12, f"{v:,} / {n:,}\n(유병률 {v / n:.1%})", ha="center",
                fontsize=7.5)
    ax.annotate("", xy=(1, 900), xytext=(0, 900),
                arrowprops=dict(arrowstyle="<->", color=ORANGE, lw=1.2))
    ax.text(0.5, 930, f"Eye Irrit. 2B = {e_un - e_eu}행\n"
            f"한국·미국 채택 여부 미확정 → 이 {e_un - e_eu}행이 뒤집힌다",
            ha="center", fontsize=7.5, color=ORANGE, fontweight="bold")
    ax.annotate("", xy=(3.6, 560), xytext=(2.6, 560),
                arrowprops=dict(arrowstyle="<->", color=ORANGE, lw=1.2))
    ax.text(3.1, 585, f"Skin Irrit. 3 = {s_un - s_eu}행",
            ha="center", fontsize=7.5, color=ORANGE, fontweight="bold")
    ax.text(0, -0.34, "피부 UN 막대는 UN 투영이 가능한 615행만이고 EU 계열은 "
            "1,059행이다. 두 막대의 n 이 다르므로 높이 차이를 그대로 15행으로 읽지 "
            "않는다.", transform=ax.transAxes, fontsize=6.8, color="#7F7F7F")
    ax.set_xticks(xs)
    ax.set_xticklabels(labs, fontsize=7.5)
    ax.set_ylim(0, 1130)
    ax.set_ylabel("양성 라벨 수")
    ax.set_title("F3. 관할 정의만 바꿔도 정답이 이만큼 달라진다 — 규제 담당 작업의 크기",
                 fontsize=9.5, color=NAVY, fontweight="bold", loc="left")
    ax.spines[["top", "right"]].set_visible(False)
    return _save(fig, "F3_관할분기_영향.png")


def f4_arm_grid(ARM):
    """F4. 감작 CT arm × 결측 규약 — 결론이 규약에 따라 뒤집히는 실제 사례."""
    nm = {"A0_base": "A0 기본", "A2_aug_nc_unknown": "A2\nNC→미지",
          "A3_aug_annexvi": "A3\nAnnex VI", "A3s_aug_annexvi_strict": "A3s\n교정판"}
    arms = list(nm)
    x = range(len(arms))
    fig, ax = plt.subplots(figsize=(6.6, 2.7))
    cov = [ARM[ARM.arm == a]["감작커버리지"].iloc[0] for a in arms]
    axb = ax.twinx()
    axb.bar(x, cov, width=0.5, color="#E7EEF5", zorder=0)
    axb.set_ylim(0, 1.15)
    axb.set_ylabel("CT 커버리지", color="#7F7F7F", fontsize=8)
    axb.tick_params(axis="y", colors="#7F7F7F")
    axb.spines[["top"]].set_visible(False)

    for na, col, lab, dy, va in (("nan0", NAVY, "0 채움 (현행 정규 규약)",
                                  -0.008, "top"),
                                 ("native", ORANGE, "sklearn 네이티브",
                                  0.008, "bottom")):
        d = ARM[ARM["결측처리"] == na].set_index("arm").loc[arms]
        ax.errorbar(x, d.ROC_AUC, yerr=d.ROC_AUC_sd, marker="o", ms=5, lw=1.6,
                    color=col, label=lab, capsize=3, zorder=3)
        for i, (v, a) in enumerate(zip(d.ROC_AUC, arms)):
            ax.text(i, v + dy, f"{v:.4f}", ha="center", va=va, fontsize=7.5,
                    color=col,
                    fontweight="bold" if a in ("A2_aug_nc_unknown",
                                               "A3_aug_annexvi") else "normal")
    ax.set_zorder(axb.get_zorder() + 1)
    ax.patch.set_visible(False)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{nm[a]}\n커버리지 {c:.2f}" for a, c in zip(arms, cov)],
                       fontsize=8)
    ax.set_ylim(0.66, 0.79)
    ax.set_ylabel("감작 ROC-AUC")
    ax.legend(fontsize=7.5, frameon=False, loc="upper left")
    ax.set_title("F4. A3 가 A2 보다 나은가 — 결측 규약에 따라 답이 갈린다 (D7·D11 한 쌍)",
                 fontsize=9.5, color=NAVY, fontweight="bold", loc="left")
    ax.annotate("0 채움: +0.0016, t=1.04 → 유의하지 않음\n"
                "네이티브: +0.0219, t=7.56 → 뚜렷함",
                xy=(0.30, 0.03), xycoords="axes fraction", fontsize=7.5,
                color="#404040",
                bbox=dict(fc="#FFF6E5", ec=ORANGE, lw=0.7, boxstyle="round,pad=0.35"))
    ax.spines[["top"]].set_visible(False)
    return _save(fig, "F4_감작arm_결측규약.png")


def build(MET=None, ARM=None):
    """네 그림을 만들고 {키: 경로} 를 준다."""
    if MET is None:
        MET = pd.read_csv(J / "전체지표_ROC_F1.csv")
    if ARM is None:
        ARM = pd.read_csv(J / "감작_CTarm_격자.csv")
    print("그림 생성:")
    return {"F1": f1_performance(MET), "F2": f2_label_source(),
            "F3": f3_jurisdiction(MET), "F4": f4_arm_grid(ARM)}


if __name__ == "__main__":
    build()
