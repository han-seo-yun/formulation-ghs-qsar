# -*- coding: utf-8 -*-
"""
데이터 현황 · 진척 · 향후 모델 개발 방향 리포트 (docx)
차트는 work/make_report_docx.py 가 report_assets/ 에 먼저 생성.
출력: 데이터현황리포트_20260830.docx
"""
import os
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

BASE = "/Users/hanseoyun/Desktop/260830"
AST = os.path.join(BASE, "report_assets")
OUT = os.path.join(BASE, "데이터현황리포트_20260830.docx")

KFONT = "맑은 고딕"
NAVY = RGBColor(0x1A, 0x52, 0x76)
DARK = RGBColor(0x2C, 0x3E, 0x50)
RED = RGBColor(0xC0, 0x39, 0x2B)
GREEN = RGBColor(0x1E, 0x84, 0x49)
GREY = RGBColor(0x7F, 0x8C, 0x8D)

doc = Document()

# ── 페이지·기본 서체 ──────────────────────────────────────────
sec = doc.sections[0]
sec.top_margin = sec.bottom_margin = Cm(2.0)
sec.left_margin = sec.right_margin = Cm(2.2)

st = doc.styles["Normal"]
st.font.name = KFONT
st.font.size = Pt(9.5)
st.element.rPr.rFonts.set(qn("w:eastAsia"), KFONT)
st.paragraph_format.space_after = Pt(4)
st.paragraph_format.line_spacing = 1.28

for nm, sz, col in [("Heading 1", 15, NAVY), ("Heading 2", 11.5, DARK), ("Heading 3", 10, DARK)]:
    s = doc.styles[nm]
    s.font.name = KFONT
    s.element.rPr.rFonts.set(qn("w:eastAsia"), KFONT)
    s.font.size = Pt(sz)
    s.font.color.rgb = col
    s.font.bold = True


def _run(p, text, bold=False, size=9.5, color=None, italic=False):
    r = p.add_run(text)
    r.font.name = KFONT
    r._element.rPr.rFonts.set(qn("w:eastAsia"), KFONT)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.italic = italic
    if color is not None:
        r.font.color.rgb = color
    return r


def h1(t):
    doc.add_paragraph(t, style="Heading 1").paragraph_format.space_before = Pt(16)


def h2(t):
    doc.add_paragraph(t, style="Heading 2").paragraph_format.space_before = Pt(11)


def h3(t):
    doc.add_paragraph(t, style="Heading 3").paragraph_format.space_before = Pt(8)


def para(*parts, align=None, space_after=4):
    """parts: str 또는 (str, {bold/size/color}) 튜플"""
    p = doc.add_paragraph()
    if align:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    for x in parts:
        if isinstance(x, tuple):
            _run(p, x[0], **x[1])
        else:
            _run(p, x)
    return p


def bullet(text, lvl=0, bold_head=None):
    p = doc.add_paragraph(style="List Bullet" if lvl == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.left_indent = Cm(0.6 + 0.5 * lvl)
    if bold_head:
        _run(p, bold_head, bold=True)
    _run(p, text)
    return p


def shade(cell, hexcolor):
    tcPr = cell._tc.get_or_add_tcPr()
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcolor)
    tcPr.append(el)


def table(headers, rows, widths=None, head_fill="1A5276", zebra="F4F6F7",
          font=8.5, emph_col=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _run(p, h, bold=True, size=font, color=RGBColor(0xFF, 0xFF, 0xFF))
        shade(hdr[i], head_fill)
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for ci, v in enumerate(row):
            cells[ci].text = ""
            p = cells[ci].paragraphs[0]
            if ci > 0:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            bold = (emph_col is not None and ci == emph_col)
            _run(p, str(v), size=font, bold=bold)
            if zebra and ri % 2 == 1:
                shade(cells[ci], zebra)
    if widths:
        t.autofit = False
        for r in t.rows:
            for c, w in zip(r.cells, widths):
                c.width = Cm(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def figure(fname, caption, width=16.4):
    doc.add_picture(os.path.join(AST, fname), width=Cm(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = para((caption, dict(size=8.5, color=GREY)), align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    return p


def note(text, color=DARK, fill="EAF2F8"):
    """강조 박스 (1×1 표)"""
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    c = t.rows[0].cells[0]
    c.text = ""
    _run(c.paragraphs[0], text, size=9, color=color, bold=False)
    shade(c, fill)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


# ══════════════════════════════════════════════════════════════
# 표지
# ══════════════════════════════════════════════════════════════
doc.add_paragraph().paragraph_format.space_after = Pt(40)
para(("제형 독성 예측 모델", dict(bold=True, size=22, color=NAVY)),
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=2)
para(("데이터 현황 · 진척 보고 및 향후 모델 개발 방향", dict(bold=True, size=14, color=DARK)),
     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=18)
para(("눈 자극·손상 (GHS 3.3)  ·  피부 부식·자극 (3.2)  ·  피부 감작 (3.4)",
      dict(size=10.5, color=GREY)), align=WD_ALIGN_PARAGRAPH.CENTER, space_after=30)

table(["항목", "내용"],
      [["작성 기준일", "2026-08-20"],
       ["대상 데이터", "제형 1,675건 · 성분 5,287행"],
       ["모델 입력", "out/v2/  (X_formulation 1,675 × 518)"],
       ["사람 열람용", "input_dataset_v2.xlsx  (8시트) — 초안, 계속 업데이트 예정"],
       ["근거 문서", "README.md · LIMITATIONS.md · DESCRIPTOR_DESIGN.md · MEETING_REPORT.md"],
       ["다음 주 분담", "SDS Section 9 (pH) 수집 = 팀 / 데이터 정리·모델링 = 단독"]],
      widths=[4.2, 12.0], font=9)

doc.add_page_break()

# ══════════════════════════════════════════════════════════════
# 0. 요약
# ══════════════════════════════════════════════════════════════
h1("0. 요약")

table(["구분", "지표", "현재", "판정"],
      [["규모", "제형 / 성분 행", "1,675 / 5,287", "확정"],
       ["라벨", "타깃 보유 제형", "1,525 (91.0%)", "학습 착수 가능"],
       ["라벨", "눈 / 피부 / 감작", "1,105 / 992 / 595", "감작이 최소"],
       ["라벨", "음성(NC) 확보", "379 / 614 / 209", "이번 주 최대 성과"],
       ["학습", "Track A 학습 가능", "1,345 행", "가능"],
       ["학습", "Track B 학습 가능", "652 행", "제한적"],
       ["구조", "CT 가산식 산출 제형", "719 (42.9%)", "성분 GHS 결측이 병목"],
       ["물성", "pH 보유", "533 (31.8%)", "그중 286행이 제품값 아님"],
       ["누출", "X ∩ label_source", "0", "차단 확인"],
       ["성능", "순서형 macro-F1 (눈/피부/감작)", "0.499 / 0.507 / 0.677", "더미 대비 1.7~3.9배"],
       ["성능", "Track B 잔차부호 (눈/피부)", "0.628 / 0.618", "핵심 가설 유효"]],
      widths=[1.9, 6.3, 4.0, 4.0], font=8.5)

para(("이번 보고의 핵심 세 가지", dict(bold=True, size=10, color=NAVY)))
bullet("웹 접근 0회로 라벨을 대량 확보. SDS Section 11 자유서술문을 규칙 기반으로 재파싱해 "
       "눈 +150 · 피부 +137 · 감작 +184 행을 회수하고, 특히 음성 라벨이 감작에서 50 → 209 행으로 증가. "
       "감작 양성률 87.8% → 64.9%.", bold_head="① 데이터: ")
bullet("GHS 농도가산식(CT)이 언제 틀리는지를 상호작용 피처로 예측 가능(잔차 부호 3클래스 0.628, "
       "우연 0.33). 그리고 CT 는 눈에서 47%를 과소예측. 이 비대칭이 논문의 주장.",
       bold_head="② 가설: ")
bullet("모델이 아니라 데이터. 다만 남은 결측 중 상당 부분이 이미 보유한 파일만으로 해소 가능하며 "
       "(SDS §11 원문 미판독 308 / 320 / 327 행), 외부 수집이 반드시 필요한 항목은 pH 로 좁혀짐.",
       bold_head="③ 병목: ")

note("이 문서의 성능 수치는 전부 튜닝 전 · 리샘플링 전 · 지문 미적용 상태의 스모크 베이스라인. "
     "상한이 아니라 하한으로 읽을 것.")

# ══════════════════════════════════════════════════════════════
# 1. 데이터 현황
# ══════════════════════════════════════════════════════════════
doc.add_page_break()
h1("1. 데이터 현황")

h2("1.1 전체 규모와 구성")

table(["단위", "행 수", "피처 수", "파일", "설명"],
      [["제형", "1,675", "518", "X_formulation.parquet",
        "농도가중 모멘트 + 상호작용 항 + 실측 물성 + CT baseline + SDS §11 수치"],
       ["성분", "5,287", "174", "X_ingredient.parquet",
        "RDKit 디스크립터 31종 + 구조경보 24종 + 역할 + 계면활성제 전하클래스"],
       ["타깃", "1,675", "35", "y_formulation.parquet",
        "엔드포인트별 원본 · 순서형 · 이진 · 출처 · 충돌 플래그 · Track B 잔차"],
       ["CV 키", "1,675", "1", "groups.parquet", "주성분 CAS 기준 664 그룹 (최대 46행)"],
       ["지문", "5,287", "2,048 / 167", "fp_ing_*.npz", "Morgan(ECFP4) · MACCS"],
       ["지문", "1,675", "4,096 / 334", "fp_form_pooled.npz", "제형 단위 [max | 농도가중mean] pooling"],
       ["사전", "1,070", "8", "feature_manifest.csv",
        "role = feature 442 / feature_derived 345 / label_source 228 / provenance 47 / id 8"]],
      widths=[1.5, 1.7, 2.2, 4.3, 6.7], font=8)

h2("1.2 라벨 확보 현황")
figure("fig1_labels.png",
       "↑ 감작은 라벨 595행(35.5%)으로 세 축 중 최소. 음성(NC)은 \"자극 없음이 확인된 제품\".")

para("제형 1,675건 중 ", ("1,525건(91.0%)", dict(bold=True)),
     "이 최소 하나의 엔드포인트에 라벨을 보유. 나머지 150건은 어떤 엔드포인트에도 라벨이 없어 "
     "학습에는 미사용하고 예측 대상으로만 활용.")

para(("음성 라벨의 의미", dict(bold=True)),
     " — 표에서 NC(Not Classified, 분류 대상 아님)로 표기한 행은 \"자극이 없음이 확인된 제품\". "
     "이 값이 없으면 모델이 모든 제품을 위험하다고 답해도 점수가 유지되므로, 음성 표본 확보가 "
     "양성 확보보다 우선순위가 높음.")

h2("1.3 이번 주 진척 — SDS Section 11 재파싱 (F0.5)")

para("독성코드 수집 하네스는 SDS 에서 15개 파라미터를 회수했으나, 시트 기입 단계에서 신호어와 "
     "H코드 2개만 기록하고 나머지 13개는 반영되지 않은 상태였음. 원본 수집 결과 파일"
     "(final_merged_results.json)에는 전량 보존되어 있어, 이를 규칙 기반으로 재파싱해 라벨과 "
     "수치 피처로 편입. ", ("웹 접근 0회 · 추가 수집 인력 0명.", dict(bold=True)))

figure("fig2_f05.png",
       "↑ 왼쪽은 전체 라벨, 오른쪽은 음성(NC) 라벨. 감작 음성 50 → 209 행 증가가 최대 성과.")

table(["항목", "이전", "이후", "증감"],
      [["눈 라벨", "955", "1,105", "+150"],
       ["피부 라벨", "855", "992", "+137"],
       ["감작 라벨", "411", "595", "+184"],
       ["눈 음성(NC)", "266", "379", "+113"],
       ["피부 음성(NC)", "493", "614", "+121"],
       ["감작 음성(NC)", "50", "209", "+159"],
       ["Track B 잔차 타깃 (눈/피부/감작)", "412 / 339 / 117", "456 / 382 / 184", "+44 / +43 / +67"],
       ["경구 LD50 (신규 수치 피처)", "0", "617 행", "신규"],
       ["경피 LD50 / 흡입 LC50", "0 / 0", "531 / 392 행", "신규"],
       ["발암·변이원·생식·STOT·흡인 순서형", "0", "149 / 101 / 101 / 117 / 103 / 99", "신규"]],
      widths=[6.4, 3.2, 3.5, 3.3], font=8.5)

para(("대가도 함께 기록", dict(bold=True, color=RED)),
     " — 라벨 충돌이 눈 69→124 · 피부 67→88 · 감작 1→10 으로 증가하고, 현재 라벨의 "
     "13.6%(눈) / 13.8%(피부) / 30.9%(감작)가 정규식으로 파싱한 자유서술. 신규 수치 피처의 "
     "성능 기여는 사실상 0(ablation Δ = +0.001~+0.040)이며, ",
     ("F0.5 의 이득은 피처가 아니라 라벨에서 발생", dict(bold=True)), ".")

h2("1.4 항목별 확보율")
figure("fig5_coverage.png",
       "↑ 점선은 60% 기준선. 확보율이 낮은 세 항목(성분 GHS · pH · 감작 라벨)이 그대로 병목.")

para("확보율이 가장 낮은 세 항목이 그대로 병목. ",
     ("성분 GHS 구분 21.5%", dict(bold=True)), " → CT 가산식 산출률 제약, ",
     ("pH 31.8%", dict(bold=True)), " → CT 예외 게이트 사용 불가, ",
     ("감작 라벨 35.5%", dict(bold=True)), " → 감작 Track B 표본 부족.")

# ══════════════════════════════════════════════════════════════
# 2. 데이터 품질 — 알려진 결함
# ══════════════════════════════════════════════════════════════
doc.add_page_break()
h1("2. 데이터 품질 — 알려진 결함과 한계")

h2("2.1 라벨의 성격 — 예측 대상이 무엇인가")

note("현재 라벨은 대부분 \"GHS 분류 결과\"이며 \"생체 독성 반응\" 자체가 아님. "
     "NTP 실측(눈 608 · 피부 544)을 제외한 나머지는 SDS 의 H코드·신호어·서술문에서 유도. "
     "감작 라벨 595건은 실측값 0건으로 전부 H317/서술문 유도. "
     "논문에서는 이 구분을 Limitations 절에 명시할 필요.", fill="FDEDEC", color=DARK)

para("라벨 출처 티어는 다섯 단계이며 위가 우선. 하위 티어가 상위 티어를 덮지 않으므로 "
     "신규 라벨만 추가되고 기존 값은 불변.")

table(["우선", "출처", "눈", "피부", "감작", "성격"],
      [["1", "ntp_measured", "608", "544", "—", "NTP 실측 시험값"],
       ["2", "sds_v1", "181", "157", "—", "v1 SDS H코드"],
       ["3", "sds_2nd", "135", "123", "198", "2차 수집 H코드·신호어"],
       ["4", "phase1_sds", "31", "31", "29", "Phase 1 에이전트 회수"],
       ["5", "sds_2nd_sec11", "150", "137", "184", "F0.5 — Section 11 자유서술 (최하위)"]],
      widths=[1.4, 4.2, 2.0, 2.0, 2.0, 4.8], font=8.5)

h2("2.2 확인된 결함 3개")

table(["#", "결함", "실측", "영향", "해소 경로"],
      [["1", "특성 시트 pH 의 제품값 여부가 분리되지 않음",
        "메모 469건이 \"제품 값 아님\" 명시 · pH 830행 중 286행 해당",
        "CT 예외 게이트(pH ≤ 2 / ≥ 11.5)가 활성성분 원제 pH 를 제품 pH 로 취급해 오작동 중",
        "메모 텍스트 파싱 → 별도 열 신설. 단독 · 반나절"],
       ["2", "라벨 충돌 미판정",
        "눈 124 · 피부 88 · 감작 10 = 222건",
        "출처 간 값이 달라 어느 쪽이 정답인지 미확정. 성능 상한을 제약",
        "티어 우선순위 자동 해소 + 잔여 육안 판정. 단독"],
       ["3", "감작성 실측값 0건",
        "라벨 595건 전부 H317·서술문 유도",
        "\"SDS 기재 내용 예측\"과 \"실제 감작성 예측\"의 차이. 심사에서 지적 예상",
        "LLNA / DPRA / h-CLAT 외부 DB 필요 — 현 조건에서 해소 불가"]],
      widths=[0.9, 3.4, 3.6, 4.6, 3.7], font=8)

h2("2.3 구조적 한계 (해소 불가, 논문에 명시)")

bullet("제형을 성분 목록과 농도로만 표현. 미셀 크기·액적 분포·상 거동·제조 공정은 SDS 에 부재.",
       bold_head="제형 표현의 축약: ")
bullet("GHS 구분은 원액 기준이나 일부 제품은 희석 사용이 전제. 희석배수 정보 없음.",
       bold_head="노출 시나리오 혼재: ")
bullet("SDS 개정판마다 분류가 변경되는데 개정일을 컬럼으로 확보하지 못함.", bold_head="시간 축 부재: ")
bullet("성분 5,287행 중 3,574행(67.6%)만 SMILES 확보. 미확보 1,713행 중 로컬 회수 가능분은 "
       "동일 CAS 16 + 동일명 41 = 57행(3.3%)뿐.", bold_head="구조 미확보 32.4%: ")
bullet("그룹 분할 키가 주성분 CAS 기준이며 성분별 active 플래그가 없어, 활성성분 판정이 "
       "농도 순위 기반 대리지표에 의존.", bold_head="active 플래그 부재: ")

# ══════════════════════════════════════════════════════════════
# 3. 모델 구조와 현재 성능
# ══════════════════════════════════════════════════════════════
doc.add_page_break()
h1("3. 모델 구조와 현재 성능")

h2("3.1 2층 디스크립터와 Track A / Track B")
figure("fig7_pipeline.png",
       "↑ 성분 블록을 제형 블록으로 집계하고, 가산 성분(①)과 비가산 성분(②)을 분리해 두 Track 에 다르게 투입하는 구조.")

para("성분 층과 제형 층을 분리한 이유 두 가지. ",
     ("① 해석 가능성", dict(bold=True)),
     " — 모델이 \"logP 가 높아서\"라고 답할 때 어느 성분의 logP 인지 특정되지 않으면 제형 설계에 "
     "활용 불가. ", ("② CT 와의 접점", dict(bold=True)),
     " — GHS 가산식은 정의상 Σ(성분 구분 × 성분 농도)이므로, 성분 단위 표현이 없으면 Track B 구성 자체가 불가.")

para(("Track A (직접 예측)", dict(bold=True, color=NAVY)),
     " — 입력은 농도가중 모멘트 + 상호작용 항 + 실측 물성 + 지문. 학습 가능 1,345행. "
     "타깃은 순서형(y_*_ord)과 이진(y_*_bin).")
para(("Track B (CT 잔차 보정)", dict(bold=True, color=NAVY)),
     " — 타깃은 resid = 실제 순서형 − CT 예측 순서형. 학습 가능 652행. "
     "입력은 상호작용 항과 예외 게이트, CT 합계로 ", ("한정", dict(bold=True)),
     " — 가산 항은 이미 CT 가 설명하므로 재투입하면 표본 효율이 떨어짐.")

h2("3.2 CT 가산식은 어느 방향으로 틀리는가")
figure("fig3_ct_resid.png",
       "↑ 라벨과 CT 예측이 모두 존재하는 행의 잔차 방향. 눈에서 47%가 과소예측(실제가 CT 보다 심함).")

para("눈에서 ", ("213/456 (47%)", dict(bold=True, color=RED)),
     "가 과소예측이고 적중은 42%. 피부는 CT 가 68%를 적중. 이 비대칭이 "
     "\"상호작용을 반영하지 않은 결과\"에 해당하는 잔차이며 Track B 가 포착해야 할 신호. "
     "감작은 음성 표본 확보 후 과대예측이 0 → 10건 발생해 실질 2클래스에서 3클래스 과제로 전환.")

h2("3.3 베이스라인 성능")
figure("fig4_baseline.png",
       "↑ RandomForest 300trees · StratifiedGroupKFold(5) · class_weight=balanced. 하단 Δ 는 모델 F1 − 더미 F1.")

note("F1 절대값만 보면 해석이 역전됨. F0.5 이전 눈 이진 F1 은 0.832, 현재는 0.798 로 하락. "
     "단, 같은 구간에서 더미가 0.838 → 0.793 으로 더 크게 하락했고 balAcc 은 0.593 → 0.624 로 상승. "
     "음성 라벨 유입으로 과제 난이도가 정상화된 결과이며 모델 성능 저하가 아님. "
     "→ 성능은 \"더미 대비 + balAcc\" 로만 판단할 것.", fill="FEF9E7")

table(["과제", "n", "F1", "balAcc", "더미 F1", "판정"],
      [["눈 이진", "1,105", "0.798 ± 0.022", "0.624", "0.793", "더미 근소 초과 (+0.005)"],
       ["눈 순서형", "1,105", "0.499 ± 0.042", "0.496", "0.128", "신호 있음 (더미 3.9배)"],
       ["피부 이진", "992", "0.679 ± 0.045", "0.752", "0.000", "신호 있음"],
       ["피부 순서형", "992", "0.507 ± 0.040", "0.494", "0.191", "신호 있음 (더미 2.7배)"],
       ["감작 이진", "595", "0.808 ± 0.027", "0.670", "0.787", "더미 근소 초과 (+0.021)"],
       ["감작 순서형", "595", "0.677 ± 0.040", "0.670", "0.393", "신호 있음"],
       ["Track B 눈 잔차부호", "456", "0.628 (macro)", "—", "~0.33", "가설 유효"],
       ["Track B 피부 잔차부호", "382", "0.618 (macro)", "—", "~0.33", "가설 유효"],
       ["Track B 감작 잔차부호", "184", "0.417 (macro)", "—", "~0.33", "보고 금지 (소수클래스 10행)"]],
      widths=[4.4, 1.8, 3.0, 1.9, 1.8, 3.5], font=8.5)

bullet("Track B(0.628 / 0.618)가 Track A 순서형(0.499 / 0.507)보다 적중률이 높음. 동일 피처로 "
       "절대 구분보다 CT 오차 예측이 더 용이하다는 의미이며, 잔차 보정 접근의 근거.")
bullet("소수점 둘째 자리는 신뢰하지 말 것. 성분 GHS 레코드 1건만 증가한 재빌드에서 눈 순서형이 "
       "0.475 → 0.495 로 변동한 전례가 있음. 순서형 점수는 ±0.03 수준의 불안정성을 가지므로 "
       "최종 수치는 반복 CV 평균으로 보고.")
bullet("누출 차단은 검증 완료 — label_source 228열을 X 에서 물리적으로 제외하고 교집합 0 확인. "
       "sds_coderived 54열은 사용 가능하나 논문에는 이 열을 뺀 ablation 을 함께 보고할 필요.")

# ══════════════════════════════════════════════════════════════
# 4. 남은 회수 여지
# ══════════════════════════════════════════════════════════════
doc.add_page_break()
h1("4. 남은 회수 여지 — 무엇이 단독으로 가능한가")

para("남은 결측을 \"이미 보유한 파일로 해소 가능한 것\"과 \"외부 접근이 필요한 것\"으로 실측 분리.")

h2("4.1 웹 접근 없이 가능 — SDS Section 11 원문 2차 판독")
figure("fig6_headroom.png",
       "↑ 왼쪽은 §11 원문 보유 행의 판독 상태, 오른쪽은 미판독 행 중 최종 라벨이 없는 행을 전량 회수했을 때의 상한.")

table(["엔드포인트", "원문 보유", "추출 성공", "미추출", "그중 라벨 부재", "현재 → 상한"],
      [["눈", "874", "566", "308", "163", "1,105 → 1,268"],
       ["피부", "801", "481", "320", "190", "992 → 1,182"],
       ["감작", "788", "461", "327", "278", "595 → 873 (+47%)"]],
      widths=[2.5, 2.3, 2.3, 2.0, 2.7, 4.4], font=8.5)

para("미추출 사유는 대부분 정성 서술만 존재하거나 표현 변형으로 규칙에 미포착된 경우. "
     "규칙 5원칙(결측/음성 구분 · 정성서술 승격 금지 · EPA↛GHS · 엔드포인트 교차오염 차단 · "
     "단위 없는 값 폐기)을 유지한 상태로 규칙을 확장하고 잔여는 육안 판독. ",
     ("감작 Track B 의 소수클래스 10행 문제는 이 경로로만 해소 가능.", dict(bold=True)))

h2("4.2 웹 접근 없이 가능 — 나머지")
table(["작업", "실측 규모", "효과"],
      [["특성 시트 제품값여부 열 분리", "pH 830행 중 286행", "CT 예외 게이트 정상화"],
       ["라벨 충돌 판정", "222건 (124 / 88 / 10)", "출처 신뢰도 순위 근거 확보"],
       ["t11_*_ambig 확인", "99건 (62 / 19 / 18)", "민감도 분석용 제외군 확정"],
       ["모델링 전체", "—", "반복 CV · 누적 이진 · 지문 · SHAP · 라우팅"]],
      widths=[5.6, 4.4, 6.4], font=8.5)

h2("4.3 외부 접근이 필요 — 닫힌 경로")
table(["항목", "왜 단독 불가", "처리 방침"],
      [["제품 SDS Section 9 (pH)", "제품 단위 실측값이라 구조·성분에서 유도 불가. 체계적 수집 이력 0",
        "★ 팀에 요청 (다음 주 유일한 분담 항목)"],
       ["성분 GHS 구분 1,963행", "PubChem PUG-View / ECHA C&L 접근 필요. 동일 CAS 전역 재사용(871)과 "
        "이름 매칭(144)은 이미 소진",
        "한계로 기록. CT 산출 719 유지"],
       ["감작성 실측값", "LLNA / DPRA / h-CLAT 외부 DB. 라이선스 문제로 인력 배정으로 해결되지 않음",
        "논문 Limitations 에 명시"],
       ["백로그 P2 460행 / P4 298행", "일부 제품은 SDS 가 원리적으로 부재(단종·규제금지·개발코드·"
        "NTP 조제 혼합물). 회수율 낮음",
        "배치 파일 보존, 목표에서 제외"],
       ["SMILES 미확보 1,713행", "로컬 회수 가능분 57행(3.3%)뿐", "한계로 기록"],
       ["검증 워크플로 4종 실행", "웹 접근 필요. 신뢰도 low 277 + medium 408 재확인 대상",
        "미실행 상태 명시 유지"]],
      widths=[4.0, 7.4, 5.0], font=8)

note("확인 결과 닫힌 경로 2건: formulation_harness/_run/ 에는 미반영 1차 수집 결과가 없고 "
     "(row_mapping.json + unique_products.json 만 존재), 성분 함량 텍스트 2,053행은 파싱 100% 성공이라 "
     "재파싱 여유분이 0. CT 산출률의 병목은 함량이 아니라 성분 GHS 구분 — 함량은 있는데 GHS 가 없는 "
     "성분 행이 1,963개.", fill="EAF2F8")

# ══════════════════════════════════════════════════════════════
# 5. 향후 모델 개발 방향
# ══════════════════════════════════════════════════════════════
doc.add_page_break()
h1("5. 향후 모델 개발 방향")

h2("5.1 1단계 (1주) — 성능 수치를 신뢰 가능하게 만들기")

para("현재 상태의 문제는 성능이 낮은 것이 아니라 ", ("성능을 비교할 수 없는 것", dict(bold=True)),
     ". 순서형 점수가 ±0.03 흔들리므로 개선과 노이즈의 구분이 불가. 이 단계를 먼저 완료해야 "
     "이후 모든 실험이 의미를 가짐.")

table(["#", "작업", "구체 내용", "판단 기준"],
      [["1", "반복 CV 전환",
        "5×5 repeated StratifiedGroupKFold 로 평균 ± 표준편차 산출. groups.parquet(664 그룹) 필수",
        "표준편차가 개선폭보다 작아야 보고 가능"],
       ["2", "순서형 정식 구현",
        "현재 다중분류 → 누적 이진분류 스택 P(≥2B) · P(≥2A) · P(=1) 로 전환",
        "순서 관계 위반율 감소 + macro-F1 상승"],
       ["3", "지문 투입",
        "fp_form_pooled.npz 를 스모크에 편입. MACCS(해석 가능·선행연구 비교군) 먼저, Morgan 은 비교군",
        "더미 대비 마진 확대"],
       ["4", "불균형 대응",
        "class_weight='balanced' → SMOTE → SMOTEENN 순 비교. 리샘플링은 CV 폴드 내부에서만",
        "balAcc 개선 + 폴드 간 분산 감소"],
       ["5", "임계값 튜닝",
        "F1 / Youden J 기준 폴드별 최적화. 기본 0.5 는 불균형에서 무의미",
        "이진 과제의 더미 초과 마진"]],
      widths=[0.9, 3.0, 7.6, 4.9], font=8)

h2("5.2 2단계 (2~3주) — 가설 검증과 해석")

bullet("성능 숫자보다 이 연구의 실질적 산출물. \"계면활성제 전하쌍(f_surf_charge_pair)이나 "
       "용매×logP(f_solvent_x_logp) 항이 실제로 CT 오차를 설명하는가\"를 확인. "
       "설명된다면 논문의 주장이 성립하고, 설명되지 않는다면 상호작용 항 설계를 재검토.",
       bold_head="Track B SHAP 분석: ")
bullet("f_ct_*_coverage 로 게이팅. 커버리지가 높은 행은 Track B 가중을 상향하고, 구조는 있으나 "
       "성분 GHS 가 없는 행은 Track A 로 이관. 단순 평균보다 이 데이터의 결측 구조에 적합.",
       bold_head="커버리지 기반 앙상블 라우팅: ")
bullet("sds_coderived 54열을 제외한 성능을 함께 보고. 라벨과 같은 SDS 문서에서 나온 피처이므로 "
       "동일문서 상관 가능성을 배제해야 심사에서 방어 가능.", bold_head="ablation 표 작성: ")
bullet("t11_*_ambig 99건과 t11_*_weak_nc 50건을 제외하고 성능이 유지되는지 확인. "
       "유지되지 않으면 해당 라벨 처리 방식을 재검토.", bold_head="민감도 분석: ")

h2("5.3 3단계 (4주 이후) — 논문용 실험 설계")

table(["실험", "목적", "구성"],
      [["데이터 기여 곡선", "\"데이터를 늘리면 성능이 오르는가\"를 정량화",
        "라벨 수를 25/50/75/100%로 층화 subsample 하고 축별(§11 판독분 · pH 반영분) 재빌드 성능 비교"],
       ["Track A vs Track B vs 앙상블", "잔차 보정 접근의 우위 입증",
        "동일 폴드·동일 피처에서 세 방식 비교. 커버리지 구간별로 분리 보고"],
       ["CT 단독 baseline", "규제 표준 대비 개선폭 제시",
        "가산식만 사용한 예측을 baseline 으로 두고 모델의 추가 이득을 측정"],
       ["선행연구 비교군", "docx 가 인용한 MACCS+SMOTE 조합 재현",
        "동일 지표로 비교해 본 연구의 상대 위치 제시"],
       ["기전 해석", "예측 근거가 화학적으로 타당한지 확인",
        "SHAP 상위 피처를 알려진 기전(각질층 투과 · 단백질 변성 · KE1 반응성)과 대조"]],
      widths=[3.6, 4.6, 8.2], font=8)

h2("5.4 하지 말 것")

for t in ["성분 SDS 를 사람이 합성해 제품 GHS 분류를 생성·기입 — Track B 의 타깃이 오염됨",
          "EPA Tox Category(I~IV) ↔ GHS 구분 환산 — 컷오프가 다른 별개 체계",
          "정성 서술(\"중등도 자극성\")을 구분 숫자로 승격 — 구분을 특정하지 않는 진술",
          "리샘플링을 CV 폴드 밖에서 수행 — 누출",
          "groups.parquet 없이 CV — 같은 활성성분 제형이 갈리면 성능 과대평가",
          "input_dataset_v2.xlsx 를 모델에 직접 투입 — 라벨 유도 컬럼 228개 포함",
          "감작 Track B 점수(0.417) 보고 — 소수클래스 10행 규모의 3클래스 전환 인공물",
          "F1 단독 판단 — 더미 대비와 balAcc 을 함께 볼 것"]:
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_after = Pt(2)
    _run(p, "✗  ", bold=True, color=RED)
    _run(p, t)

# ══════════════════════════════════════════════════════════════
# 6. 다음 주 진행
# ══════════════════════════════════════════════════════════════
doc.add_page_break()
h1("6. 다음 주 진행 — 분담과 일정")

para("데이터 수집 인력 배분 없음을 전제로 재구성. 데이터 정리·추가 추출·모델링은 단독 진행하고, "
     "단독으로 대체 불가능한 ", ("SDS Section 9 (pH) 수집만 팀에 요청", dict(bold=True)), ".")

figure("fig8_roadmap.png",
       "↑ 파란색은 단독, 빨간색은 팀, 녹색은 공동. 데이터 정리와 모델링을 병행하고 금요일에 통합 재빌드.")

h2("6.1 단독 진행 항목")
table(["순위", "작업", "산출물", "규모"],
      [["1", "SDS §11 원문 2차 판독", "라벨 증가 (감작 최우선)", "3일"],
       ["2", "특성 시트 제품값여부 열 분리", "pc2_ph_is_product 열 + ct_not_applicable 재산출", "반나절"],
       ["3", "라벨 충돌 222건 판정", "충돌 판정표 + 출처 신뢰도 순위", "1.5일"],
       ["4", "반복 CV 전환 · 누적 이진 스택", "smoke_report_v3.json", "2일"],
       ["5", "지문 투입 · Track B SHAP · 라우팅", "SHAP 그림 + 논문용 성능표 1장", "2일"],
       ["6", "input_dataset 초안 갱신", "input_dataset_v3.xlsx + out/v3/", "재빌드 시 자동"]],
      widths=[1.2, 5.4, 6.8, 3.0], font=8.5)

h2("6.2 팀 요청 — SDS Section 9 (pH)")

note("pH 만 요청하는 근거 세 가지 — ① 모델 구조에 직결: 라벨 품질이 아니라 CT 가산식의 적용 가능 "
     "여부를 결정하는 게이트. 현재 강산 4건 · 강염기 8건 · CT 예외 총 23건만 식별된 상태이고 실제 "
     "예외 제형은 더 많을 것으로 추정. ② 로컬 유도 불가: 제품 단위 실측값이라 구조·성분에서 계산 "
     "경로가 없음. ③ 분담 효율 최고: SDS 한 섹션의 숫자 하나여서 화학 지식 없이도 수집 가능.",
     fill="E8F8F5")

table(["항목", "내용"],
      [["대상", "제품 SDS Section 9 (물리화학적 성질) 의 pH 값"],
       ["우선순위", "제형코드 SL / SC / EC (액상) 부터 — 액상은 pH 기재율이 높고 고체는 누락이 잦음"],
       ["필수", "근거 URL. 못 찾으면 비우고 메모에 [SDS없음] 또는 [접근차단]"],
       ["함께 기록", "희석 조건 (예: \"1% 수용액 pH 6.5\") — 원액 pH 와 다른 값"],
       ["삭제 금지", "값의 조건 표기(at 25 C)와 estimated 표기"],
       ["금지", "추정값 생성. 비우고 사유를 메모하는 쪽이 항상 우선"],
       ["목표", "pH 제품값 유효행 약 247 → 400 이상"],
       ["미요청", "기존 830행의 제품값 여부 판별은 단독 처리하므로 신규 수집만 담당"]],
      widths=[2.8, 13.4], font=8.5)

para(("메모 태그 규칙", dict(bold=True)),
     " — 자유서술 메모는 파싱 오차가 발생하므로 앞에 대괄호 태그를 부착. "
     "[SDS없음] [접근차단] [성분불일치] [대체:성분SDS] [대체:라벨] [제품값아님] [구형MSDS] "
     "[EPA체계] [부분] [추정:제품명]. "
     "예) [대체:성분SDS][제품값아님] 활성성분 Beta-Cyfluthrin 원제 물성. 제품 SDS 미확인")

para("참고로 특성 시트 메모는 4시트 중 품질이 가장 높았음 — 메모 976건 중 63.6%가 "
     "\"활성성분 원제 물성\", 48.1%가 \"제품 값 아님\"을 명시하고 있어 이 값들을 제품 물성으로 "
     "잘못 사용하는 상황을 방지. pH 수집도 같은 수준으로 유지하면 충분.")

h2("6.3 다음 주 확인 지표")
table(["지표", "현재", "목표", "담당"],
      [["감작 라벨", "595", "750 이상", "단독"],
       ["눈 라벨", "1,105", "1,200 이상", "단독"],
       ["라벨 충돌 미판정", "222", "0", "단독"],
       ["pH 제품값 확정 플래그", "미분리", "분리 완료", "단독"],
       ["순서형 성능 (반복 CV)", "미측정", "측정 완료 + 표 1장", "단독"],
       ["pH 유효값 (제품값)", "약 247", "400 이상", "팀"],
       ["CT 산출 제형", "719", "유지 (개선 불가)", "—"]],
      widths=[6.0, 3.2, 4.4, 2.6], font=8.5)

# ══════════════════════════════════════════════════════════════
# 7. 산출물과 업데이트 계획
# ══════════════════════════════════════════════════════════════
doc.add_page_break()
h1("7. 산출물과 업데이트 계획")

h2("7.1 모델용 데이터와 사람용 데이터의 분리")

table(["구분", "파일", "용도", "주의"],
      [["모델용", "out/v2/*.parquet + *.npz", "학습·평가에 사용하는 본체",
        "label_source 228열은 이미 제외됨"],
       ["사람용", "input_dataset_v2.xlsx (8시트)", "눈으로 검토·공유·출처 추적",
        "모델에 직접 투입 금지 — 라벨 유도 컬럼 포함"],
       ["팀 반환용", "dataset_2차배정_verified.xlsx", "검증여부·검증근거·검증방식 기입본",
        "원본 dataset_2차배정.xlsx 는 미수정"]],
      widths=[2.0, 5.2, 4.8, 4.2], font=8.5)

para("사람용 파일의 provenance 시트(4,552행)가 어느 값이 어디서 왔는지를 보관하므로, "
     "값에 의문이 생기면 이 시트에서 역추적 가능.")

h2("7.2 input_dataset 갱신 절차")

para("현재 input_dataset_v2.xlsx 는 ", ("초안", dict(bold=True)),
     "이며 데이터 정리가 진행되는 동안 계속 갱신 예정. 빌더는 멱등하므로 재실행 시 같은 입력에서 "
     "같은 결과를 산출.")

for i, t in enumerate([
    "2차 시트 갱신 (§11 판독 결과 · 제품값여부 열 · 충돌 판정 반영)",
    "python3 work/verify_sheets.py — 검증란 규칙 엔진 재실행",
    "python3 work/build_input_v2.py — out/v2/ 와 input_dataset_v2.xlsx 전량 재생성 (5~10분)",
    "python3 work/smoke_baseline.py — 누출 점검 + 베이스라인",
    "누출 점검값 X ∩ label_source = 0 확인. 0 이 아니면 즉시 중단",
    "변경 전후 지표를 축별로 기록 — 어느 축이 성능을 움직였는지 추적 가능하게 유지",
], 1):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.6)
    p.paragraph_format.space_after = Pt(2)
    _run(p, f"{i}.  ", bold=True, color=NAVY)
    _run(p, t)

note("원본 2개(dataset_2차배정.xlsx, input_dataset.xlsx)는 이번 작업에서 한 번도 수정하지 않았고, "
     "모든 산출물은 새 파일로 생성. 재현성과 되돌리기의 근거이므로 이 규칙을 유지.", fill="EAF2F8")

h2("7.3 축을 하나씩 투입하는 이유")

para("수집과 재빌드를 분리하고 축을 하나씩 넣어 재빌드하면 ",
     ("\"감작 라벨 155건 추가로 balAcc 이 얼마 상승\"", dict(bold=True)),
     ", ", ("\"pH 150건 추가로 CT 예외 게이트가 몇 건 변경\"", dict(bold=True)),
     " 을 제시할 수 있음. 이 내용이 논문의 데이터 기여 섹션이 되며, 동시에 예상과 다른 결과가 "
     "나올 때 원인 축을 특정하는 수단이 됨.")

h2("7.4 참고 문서")
table(["문서", "내용"],
      [["README.md", "산출물 지도 + 독성코드 시트 작업 전체 기록(§5)"],
       ["LIMITATIONS.md", "데이터 한계 L0~L11 + 후속작업 F0~F8. 논문 Limitations 절 초안 포함"],
       ["DESCRIPTOR_DESIGN.md", "성분/제형 2층 디스크립터 설계 근거 + 학습 레시피"],
       ["MEETING_REPORT.md", "시트별 수집 방법 · 팀 요청사항 · Track 설계 · 다음 주 계획"],
       ["회의_전달_메시지.md", "채팅 붙여넣기용 축약본 (전체 / 짧은 / pH 담당 DM)"],
       ["work/lib_tox11.py", "SDS Section 11 파싱 규칙 5원칙의 정본 (docstring)"]],
      widths=[4.4, 11.8], font=8.5)

doc.add_paragraph()
para(("작성 기준 2026-08-20 · 모든 수치는 out/v2/ 및 input_dataset_v2.xlsx 실측값",
      dict(size=8, color=GREY)), align=WD_ALIGN_PARAGRAPH.CENTER)

doc.save(OUT)
print("saved →", OUT)
print("size  =", round(os.path.getsize(OUT) / 1024, 1), "KB")
