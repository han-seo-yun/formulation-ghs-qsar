#!/usr/bin/env python3
"""모델 현황 · 데이터 현황 · 성능(표) · 한계 및 개선점 보고서(docx) 생성.

수치는 04_모델산출물/v6_jurisdiction/전체지표_ROC_F1.csv 와
보고_관할별_라벨레이어.md 에서 그대로 가져온다. 이 스크립트에서 새로 계산하지
않는다 — 재계산은 measure_full_metrics_v6.py 가 이미 했고 그 결과와
소수점까지 대조 확인됐다. 추측 수치 없음, 성능 조작 없음.
"""
import json
from pathlib import Path

import pandas as pd
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

ROOT = Path(__file__).resolve().parent.parent
JUR = ROOT / "04_모델산출물" / "v6_jurisdiction"
OUT = JUR / "보고서_모델현황_데이터현황_성능_한계.docx"

R = pd.read_csv(JUR / "전체지표_ROC_F1.csv")


def row(ep, jn, im, ct):
    d = R[(R.endpoint == ep) & (R.관할 == jn) & (R.결측처리 == im) & (R.CT == ct)]
    assert len(d) == 1, (ep, jn, im, ct)
    return d.iloc[0]


doc = Document()
doc.styles["Normal"].font.name = "맑은 고딕"
doc.styles["Normal"].font.size = Pt(10.5)
H1 = RGBColor(0x1F, 0x3A, 0x5F)


def h1(t):
    p = doc.add_heading(t, level=1)
    p.runs[0].font.color.rgb = H1


def h2(t):
    p = doc.add_heading(t, level=2)
    p.runs[0].font.color.rgb = H1


def para(t, bold=False, italic=False, size=None):
    p = doc.add_paragraph()
    r = p.add_run(t)
    r.bold, r.italic = bold, italic
    if size:
        r.font.size = Pt(size)
    return p


def bullet(t):
    doc.add_paragraph(t, style="List Bullet")


def table(headers, rows):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    for i, h in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = h
        c.paragraphs[0].runs[0].bold = True
    for rw in rows:
        cells = t.add_row().cells
        for i, v in enumerate(rw):
            cells[i].text = str(v)
    return t


# ==================================================================== 표지
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("농약 제형 GHS 독성분류 예측 모델")
r.bold, r.font.size = True, Pt(20)
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run("모델 현황 · 데이터 현황 · 성능 · 한계 및 개선점 (2026-09-02 기준)")
r.italic, r.font.size = True, Pt(12)
doc.add_paragraph()
para(
    "이 보고서는 관할(EU/UN GHS/한국 K-REACH/미국 OSHA)별로 분리한 라벨 레이어 "
    "구축과 성분표 가산(CT) 증강, negrec 마스크까지 반영한 v6 시점의 상태를 "
    "정리한 것입니다. 모든 수치는 v6_jurisdiction/전체지표_ROC_F1.csv 재현 산출과 "
    "일치하며, 성능이 좋아 보이도록 임계값이나 수식을 조정한 사실이 없습니다."
)

doc.add_page_break()

# ==================================================================== 1. 개요
h1("1. 과제 개요")
table(
    ["항목", "내용"],
    [
        ["과제", "농약 제형(혼합물)의 GHS 독성 분류 예측 — 눈 자극 / 피부 자극 / 피부감작성"],
        ["과제 형태", "이진 분류(양성/음성)"],
        ["목표 성능", "ROC-AUC 0.9 (완화 기준 0.8)"],
        ["현재 최고 성능",
         "ROC-AUC 0.770 (피부, EU/K-REACH/US 관할, 정규 베이스라인 nan→0 기준) "
         "— 완화 기준 0.8 도 미달. 결측 네이티브 규약에서는 0.775"],
        ["단위", "제형(포뮬레이션) 단위. 성분 단위 원료 데이터를 가산(CT)해 보강"],
        ["모델", "RandomForestClassifier (n_estimators=300, class_weight=balanced, min_samples_leaf=2)"],
        ["평가 방식", "StratifiedGroupKFold 5-fold × 5 시드, OOF(out-of-fold) 예측 기준"],
    ],
)

# ==================================================================== 2. 데이터 현황
h1("2. 데이터 현황")

h2("2-1. 데이터 규모")
table(
    ["endpoint", "행 수(관할·조건별)", "비고"],
    [
        ["눈 자극(eye)", "1,153(원본) → 1,044(negrec 109행 학습 마스크 후)", "관할 무관 공통 행집합 기준"],
        ["피부 자극(skin)", "UN형 615 / EU·K-REACH·US형 1,059", "EPA 자극성 IV 유래 444행이 UN 관할에서만 판별불가 마스크"],
        ["감작성(sens)", "716", "4개 관할 라벨이 동일(투영표가 같음)"],
    ],
)

h2("2-2. 라벨 레이어 구조 (L0 → L1 → L2)")
para(
    "기본 원본 라벨은 그대로 두고, 국가별 세부 규제 차이만 별도 레이어로 "
    "매핑하는 3단 구조를 씁니다."
)
table(
    ["레이어", "내용", "상태"],
    [
        ["L0 원본", "y_{endpoint} 그대로", "불변 — 디스크와 100% 동일 확인"],
        ["L1 정본", "L0 + GHS 범주 표기 오류만 정정", "82셀 정정(피부 2A/2B → 2), 이진 라벨은 안 바뀜"],
        ["L2 관할투영", "L1 → 관할별(EU/UN/K-REACH/US) 이진 라벨", "관할마다 별도 컬럼, 서로 덮어쓰지 않음"],
    ],
)
para("관할이 실제로 갈리는 축은 두 가지뿐입니다:")
table(
    ["축", "영향 행수", "UN GHS", "EU CLP", "K-REACH", "US OSHA"],
    [
        ["Eye Irrit. 2B (H320)", "327", "양성", "음성(미채택)", "양성", "양성"],
        ["Skin Irrit. 3 (H316)", "15", "양성", "음성(미채택)", "음성", "음성"],
        ["피부 EPA IV 유래 NC", "444", "판별불가(마스크)", "음성", "음성", "음성"],
    ],
)
para(
    "결과적으로 라벨 프로파일이 3종으로 갈립니다 — EU형(둘 다 음성) / "
    "K-REACH·US형(2B만 양성) / UN형(둘 다 양성). 한국(K-REACH)은 EU·UN 어느 "
    "쪽과도 다른 제3의 패턴입니다."
)
para(
    "⚠ K-REACH·US-OSHA 투영은 아직 '부분확인'입니다 — 각국 고시·HCS 원문으로 "
    "세부범주 채택 여부를 확정하는 절차가 남아 있습니다(수동검토 작업파일 A시트). "
    "UN·EU 투영은 '확인' 완료입니다.",
    italic=True,
)

h2("2-3. 성분표 가산(CT) 증강")
para(
    "제형에 포함된 원료(성분)의 GHS 분류를 가산공식(GHS 혼합물 분류 규칙, 예: "
    "10×Cat1 합 + Cat2 합 ≥ 10 → Cat2)으로 합산해 파생 피처(f_ct_*)로 추가합니다. "
    "공급자 SDS 가 '분류 안 함/정보없음'으로만 적은 성분에 대해, 별도로 수집한 "
    "PubChem PUG-View / ECHA C&L 조사(고유 CAS 331종)를 보강해 커버리지를 넓힌 "
    "조합이 CT 권고안입니다."
)
para(
    "커버리지는 arm 마다 세는 규칙이 다르므로 arm 별로 분리해 적습니다. "
    "A2 는 '분류대상아님(NC)'을 미지로 버리고, A3 는 NC 를 아는 값으로 셉니다 "
    "— 따라서 A2 와 A3 의 커버리지는 직접 비교할 수 없습니다."
)
_cov = pd.read_csv(JUR / "C1_커버리지_격자.csv").set_index("arm")


def _cv(a):
    r = _cov.loc[a]
    return f"{r.eye*100:.1f}% / {r.skin*100:.1f}% / {r.sens*100:.1f}%"


table(
    ["CT arm", "성분 커버리지(눈/피부/감작)", "NC 취급", "비고"],
    [
        ["A0 (기본)", _cv("A0_base"), "SDS 값 그대로",
         "SDS 원본에 있는 성분 분류만 사용"],
        ["A2 (눈·피부 권고)", _cv("A2_aug_nc_unknown"), "미지로 버림",
         "PubChem·ECHA 보강분 중 실제 분류가 있는 것만 추가"],
        ["A3 (감작 권고, 현행)", _cv("A3_aug_annexvi"), "아는 값으로 셈",
         "Annex VI 조화분류 전용 — 단 출처등급 판정에 결함(§4 한계)"],
        ["A3s (A3 출처등급 교정)", _cv("A3s_aug_annexvi_strict"), "아는 값으로 셈",
         "조사 메모의 'Annex VI 존재' 기록으로 필터. 감작 49.6% → 44.9%"],
    ],
)
para(
    "이전 판 보고서는 '권고(A2/A3) 33.0% / 29.6% / 49.6%' 를 한 줄에 나란히 적어 "
    "감작 커버리지가 눈·피부의 1.5배인 것처럼 보이게 했습니다. 이는 규칙 혼용이었고 "
    "정정합니다 — 동일 규칙(A2)으로 재면 감작은 32.3% 로 눈(33.0%)·피부(29.6%)와 "
    "사실상 같습니다.",
    italic=True,
)
para(
    "CT 증강 이득은 결측 처리 규약(nan→0 / 결측 네이티브)과 라벨 정의(UN/EU) "
    "양쪽에 걸쳐 귀속 격자의 고유 10개 셀 전부에서 통계적으로 유의합니다"
    "(ROC-AUC Δ +0.014~+0.045, t=6.98~19.27, 5시드 paired). 순열 대조군에서도 "
    "인공물 비율이 낮았습니다(sens 0%, skin 12%, eye 38%) — 이 증강은 유지 "
    "권고합니다."
)
para(
    "다만 순열 대조군은 '값 벡터를 섞는' 설계이므로 어느 CAS 가 채워지는지는 "
    "보존됩니다. 즉 순열 대조군은 'NC 를 아는 값으로 세는 것 자체'가 만드는 이득은 "
    "검출하지 못하므로, 감작 인공물 0% 를 A3 출처등급 결함에 대한 반증으로 쓸 수 "
    "없습니다. 그 결함의 실제 성능 영향은 별도로 측정했습니다(§4 한계).",
    italic=True,
)

h2("2-4. negrec(회수 음성) 448셀과 눈 109행 마스크")
para(
    "SDS 원문 회수 당시 원문 대조 없이 '음성'으로 처리된 셀이 448개(눈 109 / "
    "피부 128 / 감작 211)입니다. negrec 을 뺀 학습→예측으로 위치를 살펴본 결과, "
    "눈 109행은 다른 출처의 음성보다 양성 쪽에 가까운 패턴을 보였습니다"
    "(위치지수 0.60, 양성 예측률이 다른 음성 대비 2.0배). 이 근거로 눈 109행을 "
    "**학습에서 마스크**했습니다(라벨 값 자체는 보존, 되돌릴 수 있음). 피부·감작은 "
    "근거가 약해(위치지수 0.36·0.41) 마스크하지 않고 플래그만 달았습니다."
)
para(
    "다만 마스크가 성능을 개선한다는 근거는 없습니다(Δ ROC-AUC −0.0035~+0.0028, "
    "유의한 셀은 하나뿐이고 그마저 음수). 마스크의 근거는 성능이 아니라 라벨 "
    "정합성입니다 — 라벨 자체가 의심되는 행을 AUC 로 정당화할 수 없기 때문입니다. "
    "원문 재수집이 최우선 과제이며, 448행 중 URL 이 있는 것은 199행, 로컬 문서가 "
    "있는 것은 5행뿐입니다(수동검토 작업파일 C시트).",
    italic=True,
)

h2("2-5. 팀원 배정 데이터 현황")
para(
    "제출 여부와 모델 반영 여부는 다른 문제입니다. 아래 표는 둘을 분리해 적습니다 "
    "— ACTIVE(모델이 실제로 쓰는 값) / STAGED(통합됐으나 미적용) / 미도착."
)
table(
    ["담당", "배정", "제출·검수 결과", "모델 반영"],
    [
        ["정채윤", "성분 GHS 분류, 고유 CAS 331종 (PubChem PUG-View + ECHA C&L)",
         "331 CAS 전건 조사 완료(파싱 실패 0). 분류 있음 눈 110 / 피부 81 / 감작 70, "
         "분류대상아님 눈 161 / 피부 190 / 감작 201, 정보없음 60",
         "ACTIVE — CT 증강 A2/A3 arm 입력. 단 A3 출처등급에 결함 발견(§4 한계 참조)"],
        ["이서윤", "pH 약 280건, SL 코드 우선",
         "제출·검수 완료. 배정 280 전건 기입(배정 외 침범 0). pH 확보 94건 "
         "(근거URL 132, 근거 없는 값 0). 미확보 186건 사유 분류 완료 — 정당한 미확보 "
         "106(문서상 pH 미기재 39 + 근거 확보 실패 67), 자체 품질기각 80. "
         "귀속등급: 제품 귀속 18 / 참고값 69 / 타브랜드 7",
         "STAGED — 미반영. GHS 극단 pH 비가산 예외 게이트가 코드에 없고, "
         "원액 실측 기준 게이트 발동 제형은 0건(2건 모두 희석액 측정)"],
        ["최소빈", "독성 라벨 재확인 총 797건 (눈 268 / 피부 269 / 감작 260)",
         "판독 확정 223 / 미판독 574. 원문 지지 215, 불지지 8. 근거 없는 판독 0, "
         "GHS 범주 위반 0. 기존 라벨과 충돌 19건(그중 NTP 실측 충돌 6건). "
         "negrec 448셀 중 판독이 닿은 것 47셀(10.5%), 라벨 대체 가능 8셀(1.8%)",
         "STAGED — 라벨 미반영. 라벨 변경 경로(PHASE 2)이므로 총책임자 승인 필요. "
         "피처로는 절대 쓰지 않음(label_coderived)"],
        ["김보경", "pH 약 274건, SC·EC 코드 우선",
         "미도착. 배정 274행 중 기입 0행 — 코드로 확인했습니다"
         "(build_input_v6.py 의 assert)",
         "없음 — SC·EC 제형 pH 공백 전량 미해결"],
    ],
)
para(
    "정정: 이전 판 보고서는 이서윤을 '확인 불가', 최소빈을 '일부 반영'으로 적었습니다. "
    "둘 다 부정확했습니다 — 이서윤 자료는 제출·검수가 끝나 있었고(축소 서술), "
    "최소빈 판독은 라벨에 반영된 적이 없습니다(과대 서술). 실제로는 정채윤 조사만 "
    "모델에 반영되어 있습니다. 배정 범위는 임의로 바꾸지 않았습니다.",
    italic=True,
)

doc.add_page_break()

# ==================================================================== 3. 성능
h1("3. 성능")
para(
    "지표: ROC-AUC(주 지표) / MCC / 균형정확도(BA) / F1 / PR-AUC. 임계값은 전 "
    "구간 0.5 고정이며, F1 을 좋게 보이려는 임계값 조정은 하지 않았습니다. "
    "평가는 StratifiedGroupKFold 5-fold × 5시드 OOF 기준입니다."
)

h2("3-1. 현행 최적 조합(CT 권고, 결측처리 nan→0 기준)")
rows = []
for ep, jn, label in [
    ("skin", "EU_CLP+K_REACH+US_OSHA", "피부 · EU/K-REACH/US"),
    ("eye", "EU_CLP", "눈 · EU"),
    ("sens", "UN_GHS+EU_CLP+K_REACH+US_OSHA", "감작 · 전 관할"),
    ("skin", "UN_GHS", "피부 · UN"),
    ("eye", "UN_GHS+K_REACH+US_OSHA", "눈 · UN/K-REACH/US"),
]:
    d = row(ep, jn, "nan0", "CT_권고")
    rows.append([
        label, d.n, f"{d.유병률:.3f}", f"{d.roc_auc:.4f}", f"{d.MCC:.3f}",
        f"{d.BA:.3f}", f"{d.f1_pos:.4f}", f"{d.pr_auc:.4f}",
    ])
table(
    ["endpoint · 관할", "n", "유병률", "ROC-AUC", "MCC", "BA", "F1(양성)", "PR-AUC"],
    rows,
)
para(
    "목표 0.9, 완화 기준 0.8 모두 미달입니다. 위 표(정규 베이스라인 nan→0)의 "
    "최고값은 피부(EU/K-REACH/US) ROC-AUC 0.7697 입니다.",
    bold=True,
)
h2("3-1-b. 결측 네이티브 규약 병기 (같은 조합, 규약만 교체)")
para(
    "위 표는 이 프로젝트의 정규 베이스라인인 nan→0 규약입니다. 방법론적으로는 "
    "결측을 결측으로 두는 native 가 옳으므로(nan→0 은 '미측정'과 '무위험'을 같은 "
    "값으로 만듭니다) 같은 조합을 native 로도 병기합니다. 두 규약을 섞어 높은 쪽만 "
    "헤드라인으로 쓰지 않기 위한 표입니다."
)
rows_nat = []
for ep, jn, label in [
    ("skin", "EU_CLP+K_REACH+US_OSHA", "피부 · EU/K-REACH/US"),
    ("eye", "EU_CLP", "눈 · EU"),
    ("sens", "UN_GHS+EU_CLP+K_REACH+US_OSHA", "감작 · 전 관할"),
    ("skin", "UN_GHS", "피부 · UN"),
    ("eye", "UN_GHS+K_REACH+US_OSHA", "눈 · UN/K-REACH/US"),
]:
    a, b = row(ep, jn, "nan0", "CT_권고"), row(ep, jn, "native", "CT_권고")
    rows_nat.append([label, f"{a.roc_auc:.4f}", f"{b.roc_auc:.4f}",
                     f"{b.roc_auc - a.roc_auc:+.4f}", f"{b.MCC:.3f}",
                     f"{b.f1_pos:.4f}"])
table(["endpoint · 관할", "ROC-AUC (nan→0)", "ROC-AUC (native)", "Δ",
       "MCC (native)", "F1 (native)"], rows_nat)
para(
    "native 규약 최고값은 피부 0.7754 입니다. 어느 규약으로도 완화 기준 0.8 에 "
    "닿지 않습니다. 규약 확정은 총책임자 판단 사안입니다(§4 한계, 작업파일 D7).",
    italic=True,
)

h2("3-2. 한국 규제(K-REACH) 실질 조합")
table(
    ["endpoint", "ROC-AUC", "MCC", "F1(양성)"],
    [
        ["눈 (UN형 투영 적용)", f"{row('eye','UN_GHS+K_REACH+US_OSHA','nan0','CT_권고').roc_auc:.4f}",
         f"{row('eye','UN_GHS+K_REACH+US_OSHA','nan0','CT_권고').MCC:.3f}",
         f"{row('eye','UN_GHS+K_REACH+US_OSHA','nan0','CT_권고').f1_pos:.4f}"],
        ["피부 (EU형 투영 적용)", f"{row('skin','EU_CLP+K_REACH+US_OSHA','nan0','CT_권고').roc_auc:.4f}",
         f"{row('skin','EU_CLP+K_REACH+US_OSHA','nan0','CT_권고').MCC:.3f}",
         f"{row('skin','EU_CLP+K_REACH+US_OSHA','nan0','CT_권고').f1_pos:.4f}"],
        ["감작", f"{row('sens','UN_GHS+EU_CLP+K_REACH+US_OSHA','nan0','CT_권고').roc_auc:.4f}",
         f"{row('sens','UN_GHS+EU_CLP+K_REACH+US_OSHA','nan0','CT_권고').MCC:.3f}",
         f"{row('sens','UN_GHS+EU_CLP+K_REACH+US_OSHA','nan0','CT_권고').f1_pos:.4f}"],
    ],
)
para(
    "눈이 가장 약합니다(ROC-AUC 0.686). K-REACH 가 Eye 2B 를 채택해 눈 유병률이 "
    "0.683 으로 높아지고 경계 사례가 많아지기 때문입니다."
)

h2("3-3. CT 증강 전(A0) 대비 개선폭")
rows = []
for ep, jn, label in [
    ("eye", "EU_CLP", "눈 · EU"),
    ("eye", "UN_GHS+K_REACH+US_OSHA", "눈 · UN/K-REACH/US"),
    ("skin", "EU_CLP+K_REACH+US_OSHA", "피부 · EU/K-REACH/US"),
    ("skin", "UN_GHS", "피부 · UN"),
    ("sens", "UN_GHS+EU_CLP+K_REACH+US_OSHA", "감작 · 전 관할"),
]:
    a0 = row(ep, jn, "nan0", "CT_A0")
    rec = row(ep, jn, "nan0", "CT_권고")
    rows.append([label, f"{a0.roc_auc:.4f}", f"{rec.roc_auc:.4f}",
                f"{rec.roc_auc - a0.roc_auc:+.4f}"])
table(["endpoint · 관할", "A0(기본)", "권고(CT 증강)", "Δ ROC-AUC"], rows)
para(
    "위 5개 셀 전부에서 CT 증강이 유의하게 개선합니다(5시드 paired t-검정, "
    "t = 5.28 ~ 26.69). 눈 두 행의 t 는 negrec 마스크 후 측정값(UN형 6.41 / "
    "EU형 5.28, negrec마스크_성능영향.csv M5)입니다 — 마스크 전 1153행에서 잰 "
    "t(8.05 / 7.87)를 마스크 후 표에 붙이지 않았습니다. 눈의 CT 이득은 마스크 후 "
    "줄어듭니다(UN형 +0.0277 → +0.0114). 어느 쪽이든 목표 0.9 와의 격차를 "
    "닫을 정도는 아닙니다."
)

h2("3-4. F1 을 헤드라인으로 쓰지 않는 이유")
para(
    "눈(UN형)의 F1(양성)=0.798 로 표 전체에서 가장 높지만, 같은 조합의 "
    "ROC-AUC 는 0.686 으로 가장 낮고 MCC 도 0.237 로 가장 낮습니다. 유병률이 "
    "0.683 으로 높아 모델이 양성 쪽으로 예측을 밀면(재현율 0.869) F1 이 자동으로 "
    "오르지만, 특이도는 0.334 — 음성 3건 중 2건을 오분류합니다. F1 은 진음성을 "
    "계산에 넣지 않아 이 실패를 가리므로, 관할 간·엔드포인트 간 비교와 헤드라인 "
    "지표는 ROC-AUC 와 MCC 로 유지합니다."
)

doc.add_page_break()

# ==================================================================== 4. 한계
h1("4. 한계")
bullet(
    "목표(0.9)·완화 기준(0.8) 모두 미달 — 최고 ROC-AUC 0.770(피부, nan→0) / "
    "0.775(피부, native)."
)

h2("4-1. 감작 CT 권고안(A3)의 출처등급 결함 — 새로 발견, 미해결")
para(
    "정합 검수 중 A3 arm 정의에 결함을 발견했습니다. A3 는 '유럽 조화분류"
    "(CLP Annex VI) 근거만 사용' 이 정의인데, 실제 필터가 조사 셀의 문구를 잘못 "
    "읽어 **'분류대상아님' 셀 전량을 조화분류 근거로 받아들이고** 있었습니다."
)
_c1 = json.loads((JUR / "C1_요약.json").read_text(encoding="utf-8"))
table(
    ["endpoint", "분류대상아님 CAS", "그 중 Annex VI 기록 있음", "기록 없음(잘못 채택)"],
    [[ep, _c1["오염규모_CAS"][ep]["NC_CAS"],
      _c1["오염규모_CAS"][ep]["NC_중_AnnexVI기록_있음"],
      _c1["오염규모_CAS"][ep]["NC_중_AnnexVI기록_없음"]]
     for ep in ("eye", "skin", "sens")],
)
para(
    "감작의 경우 '분류대상아님' 201건 중 81건은 그 물질이 Annex VI 목록에 아예 "
    "없고 ECHA 자가신고 데이터만 있는 CAS 입니다. 목록에 없는 물질에 대해 "
    "'목록에 H317 이 없다'는 것은 음성 근거가 아니라 **자료의 부재**입니다. "
    "즉 이 프로젝트가 금지한 '미시험을 음성으로 쓰는' 결함이 형태를 바꿔 "
    "재발한 것입니다. 팀 자체 QA 가 이미 경고했으나(채윤_GHS_검수요약.json) "
    "지표 파이프라인에 반영되지 않았습니다."
)
para("교정판(A3s)으로 다시 측정한 결과:", bold=True)
_c1r = pd.read_csv(JUR / "C1_출처등급교정_영향.csv")
table(
    ["endpoint · 관할", "결측처리", "현행 A3", "교정 A3s", "Δ", "t(df4)", "시드양수"],
    [[f"{r.endpoint} · {r['관할']}", r.결측처리, f"{r.AUC_권고:.4f}",
      f"{r.AUC_권고_교정:.4f}", f"{r['Δ(교정−현행)']:+.4f}", f"{r.t_df4:+.2f}",
      f"{r.시드양수}/5"] for _, r in _c1r.iterrows()],
)
para(
    "결과를 있는 대로 적습니다: **교정하면 감작 성능이 떨어집니다** "
    "(nan→0 0.7333 → 0.7262, t=−4.99 / native 0.7558 → 0.7425, t=−5.87, "
    "두 규약 모두 5시드 전부 음수). 눈·피부는 변화가 없습니다(|Δ| ≤ 0.002).",
    bold=True,
)
para(
    "해석: 잘못 채택된 81개 CAS 가 실제로 예측력을 갖고 있었다는 뜻입니다. 다만 그 "
    "예측력이 독성학적 근거에서 온 것이라고 볼 수 없습니다 — 'Annex VI 목록에 없다'는 "
    "것은 그 성분이 소량·비활성 부재료일 가능성과 상관되므로, 독성 신호가 아니라 "
    "다른 것의 대리변수일 수 있습니다. 자료 부재를 음성으로 써서 얻은 이득이므로 "
    "**저는 교정판(A3s) 채택을 권고합니다** — ROC-AUC 0.007~0.013 을 잃습니다. "
    "다만 이는 권고안 정의를 바꾸는 일이므로 총책임자 결정 사항으로 올립니다."
)
para(
    "현재 상태: 기존 A3 값을 덮지 않았습니다. 통합 데이터셋에 교정 출처등급을 "
    "별도 컬럼(ing_ghs_indep_*_tier_src)으로 병기했고, 위 표의 모든 수치는 "
    "재현 가능합니다(measure_ct_tier_correction.py). §3 의 성능 표는 아직 "
    "**현행 A3 기준**입니다.",
    italic=True,
)
bullet(
    "K-REACH·US-OSHA 투영이 '부분확인' 상태 — 고시·HCS 원문 대조가 끝나지 않아 "
    "현재 수치는 채택 여부를 가정한 값입니다. 확정되면 눈 327행, 피부 15+444행이 "
    "영향을 받습니다."
)
bullet(
    "negrec 448셀 중 원문 재대조가 가능한 것은 199행(URL)·5행(로컬 문서)뿐 — "
    "나머지는 SDS 를 제품명으로 새로 찾아야 합니다."
)
bullet("이서윤(pH 280건)·김보경(pH 274건 SC·EC) 배정 자료가 아직 입력 데이터에 없습니다.")
bullet(
    "결측 처리 규약이 확정되지 않음 — 정규 베이스라인(nan→0)은 '미측정'과 "
    "'무위험'을 같은 값으로 취급하는 결함이 있으나, 결측 네이티브 처리로 바꾸면 "
    "과거 수치 전체와 비교가 끊깁니다."
)
bullet("임계값 0.5 가 최적이 아님(BA 0.59~0.71) — 비용비(과소분류/과대분류) 결정이 선행되어야 임계값을 조정할 수 있습니다.")
bullet(
    "EyeIrritation6pack_PID81 등 출처 간 실제 라벨 모순 1건이 원문 미확인 상태로 "
    "남아 있습니다."
)
bullet("ct_not_applicable 게이트(극단 pH 비가산 예외) 미구현.")

h1("5. 개선점(우선순위 순)")
table(
    ["순위", "항목", "기대 효과"],
    [
        ["1", "감작 CT 권고안을 A3 → A3s(출처등급 교정)로 교체할지 결정 (§4-1)", "자료 부재를 음성으로 쓰는 결함 해소. 대가는 ROC-AUC −0.007~−0.013"],
        ["1", "수동검토 작업파일 A시트 확정(K-REACH·US-OSHA 세부범주 채택 여부)", "관할 레이어 신뢰도 확보 — 최대 327+15+444행 라벨 확정"],
        ["2", "눈 negrec 109행 원문 재수집(URL 56행 우선)", "라벨 정합성 개선, 마스크 해제 여부 결정 근거"],
        ["3", "김보경·이서윤 pH 배정 자료 회수 및 병합", "입력 피처 결측 감소"],
        ["4", "결측 처리 규약(native) 전환 여부 결정", "피처 수준 '미시험=음성' 결함 해소"],
        ["5", "임계값 비용비 결정 후 재조정", "BA·재현율/정밀도 균형 개선"],
        ["6", "피부·감작 negrec(339셀) 원문 재수집", "CT 증강·라벨 신뢰도 추가 개선"],
    ],
)
para(
    "이 개선안들을 모두 적용해도 앞서 추정한 성능 천장(0.75~0.82)이 뒤집힐 "
    "근거는 아직 없습니다. 0.9 목표에 도달하려면 데이터 품질 개선을 넘어서는 "
    "구조적 검토(예: 피처 확장, 다른 모델군 시도)가 필요할 수 있습니다.",
    italic=True,
)

para("")
para(
    "산출 근거: 04_모델산출물/v6_jurisdiction/전체지표_ROC_F1.csv, "
    "보고_관할별_라벨레이어.md. 재현 스크립트: measure_full_metrics_v6.py, "
    "build_jurisdiction_label_layers.py, apply_negrec_mask_and_split_review.py.",
    size=9,
    italic=True,
)

OUT.parent.mkdir(parents=True, exist_ok=True)
doc.save(OUT)
print("saved", OUT)
