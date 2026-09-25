#!/usr/bin/env python3
"""L2 노선 피처 설계 근거 리포트 docx — 산출: v8_리포트/L2_피처설계근거_260915.docx

`L2_피처설계_성능지표_*.docx` 의 §1 을 대체하는 것이 아니라 **근거 쪽만 따로**
자세히 적는다. 저쪽은 "무엇이 들어갔나 + 성능 지표", 이쪽은 "왜 그것을 넣고
저것을 뺐나 + 실측으로 그 판단이 맞았나".

수치는 전부 이미 있는 산출물에서 읽거나 이 스크립트가 직접 계산한다.
그림은 `build_feature_design_figs.py` 가 만든다 — 본문과 그림을 한 스크립트에
넣으면 그림만 다시 그리려 할 때 문서까지 덮어쓴다.

양식은 `build_status_report_docx.py` 와 같다(머리글 NAVY/BLUE · 캡션 8.5pt 회색
왼쪽 · `Light Grid Accent 1` · `→` 결론줄 파랑). 사람 이름은 적지 않는다 —
이 저장소는 비공개지만 산출물은 팀에 돌아간다.

## 2026-09-21 — 42열 → 31열. 이미 나간 docx 와 다르다

`L2단독` arm 이 42 열에서 31 열로 줄었다(설계 결함 11열 제외, `lib_model.FEAT_DROP`).
이 소스의 본문 수치·표는 **31열 기준으로 고쳐 두었고**, `figures/figB*.png` 도
**31열 판으로 덮어썼다**(2026-09-21). 그러나 **이미 배포된
`L2_피처설계근거_260915.docx` 파일 자체는 42열 판 그대로다** — 총책임자 결정으로
재빌드를 보류했다. 그림까지 갱신됐으므로 그 docx 안에 박힌 그림은 이제
`figures/` 의 같은 이름 파일과도 다르다.

그래서 이 스크립트를 지금 그냥 돌리면 **파일 내용이 배포본과 달라진다.** 돌릴
거라면 파일명을 새 날짜로 바꾸고, 42열 판과 어느 수치가 다른지 함께 알려라.
42열 판 그림은 `figures/figB*_42열.png` 에 보존돼 있고 42열 중요도 표는
`04_모델산출물/v8_노선2정리/피처중요도_L2단독42_재현.csv` 다.
"""
from __future__ import annotations

import os

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

import lib_model as L

OUTDIR = L.ROOT / "04_모델산출물" / "v8_리포트"
FIGDIR = OUTDIR / "figures"
OUT = OUTDIR / "L2_피처설계근거_260915.docx"

# 이미 나간 배포본을 말없이 지우지 않게 막는다. 이 소스는 31열 판이고 디스크의
# 파일은 42열 판이다(2026-09-21 결정으로 재빌드 보류). `.gitignore` 가 `*.docx` 라
# 덮어쓰면 git 으로도 못 되돌린다. 다른 두 빌더의 `손수정_보관()` 은 손수정만
# 보호하므로 — 빌드지문이 일치하는 정상 배포본은 사본도 안 빠진다 — 이 자리를
# 대신 막아 주지 못한다
if OUT.exists() and os.environ.get("OVERWRITE") != "1":
    raise SystemExit(
        f"!! {OUT.name} 이 이미 있다 — 그 파일은 42열 판 배포본이다.\n"
        "   이 소스는 31열 판이라 돌리면 내용이 달라진다. 어디가 다른지는\n"
        "   04_모델산출물/v8_리포트/배포본_소스차이_260921.md 에 적혀 있다.\n"
        "   → 재빌드하려면 OUT 을 새 날짜 파일명으로 바꾼다.\n"
        "   같은 이름을 정말 덮어써야 하면 OVERWRITE=1 로 돌린다."
    )

KO, MONO = "Apple SD Gothic Neo", "Menlo"
BLACK = RGBColor(0x00, 0x00, 0x00)
NAVY = RGBColor(0x1F, 0x38, 0x64)
BLUE = RGBColor(0x4F, 0x81, 0xBD)
GRAY = RGBColor(0x55, 0x55, 0x55)
CODE = RGBColor(0x33, 0x33, 0x33)
LINK = RGBColor(0x00, 0x0E, 0xFA)
TABLE_STYLE = "Light Grid Accent 1"

doc = Document()
for nm, sz, bold, col in (("Normal", 12, False, BLACK), ("Heading 1", 16, True, NAVY),
                          ("Heading 2", 14, True, BLUE), ("Body Text", 12, False, BLACK),
                          ("List Bullet", 11, False, BLACK)):
    s = doc.styles[nm]
    s.font.name, s.font.size, s.font.bold, s.font.color.rgb = KO, Pt(sz), bold, col
    s._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
for nm in ("Normal", "Body Text"):
    pf = doc.styles[nm].paragraph_format
    pf.space_after, pf.line_spacing = Pt(6), 1.3
cap = doc.styles.add_style("Image Caption", WD_STYLE_TYPE.PARAGRAPH)
cap.base_style = doc.styles["Normal"]
cap.font.name, cap.font.size, cap.font.bold = KO, Pt(8.5), False
cap.font.color.rgb = GRAY
cap._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.LEFT
cap.paragraph_format.space_after = Pt(14)
for sec in doc.sections:
    sec.left_margin = sec.right_margin = Cm(2.54)
    sec.top_margin = sec.bottom_margin = Cm(2.54)


def runs(p, text, size=12, color=None, force_bold=False):
    """`**굵게**` · `` `코드` `` 만 해석한다."""
    import re
    for tok in re.split(r"(\*\*.+?\*\*|`[^`]+`|\n)", text):
        if not tok:
            continue
        if tok == "\n":
            p.add_run().add_break()
            continue
        is_code = tok.startswith("`")
        # `**...`코드`...**` 처럼 겹쳐 쓰면 strip 이 양쪽 기호를 함께 떼어내
        # 백틱 한 짝이 본문에 그대로 찍힌다. 조용히 틀리지 않게 막는다
        assert not (tok.startswith("**") and "`" in tok), \
            f"굵게 안에 백틱을 겹쳐 쓸 수 없다: {tok}"
        r = p.add_run(tok.strip("*`") if tok[:1] in "*`" else tok)
        r.font.name = MONO if is_code else KO
        r.font.size = Pt(size - 1.5 if is_code else size)
        r._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
        r.bold = force_bold or tok.startswith("**")
        r.font.color.rgb = CODE if is_code and color is None else (color or BLACK)
    return p


def body(text, color=None):
    return runs(doc.add_paragraph(style="Body Text"), text, color=color)


def h1(t):
    doc.add_paragraph(t, style="Heading 1").paragraph_format.space_before = Pt(18)


def h2(t):
    doc.add_paragraph(t, style="Heading 2").paragraph_format.space_before = Pt(12)


def caption(t):
    # 백틱을 runs() 로 넘겨야 캡션 안의 코드명도 Menlo 로 나온다. 그냥
    # add_paragraph 로 넣으면 백틱 문자가 그대로 찍힌다
    p = doc.add_paragraph(style="Image Caption")
    runs(p, t, size=8.5, color=GRAY)
    return p


def bullet(t):
    p = doc.add_paragraph(style="List Bullet")
    runs(p, t, size=11)
    p.paragraph_format.space_after = Pt(3)


def table(head, rows, bold_rows=()):
    t = doc.add_table(rows=1, cols=len(head))
    t.style = TABLE_STYLE
    for c, h in zip(t.rows[0].cells, head):
        runs(c.paragraphs[0], h, size=10.5)
    for i, row in enumerate(rows):
        for j, (c, v) in enumerate(zip(t.add_row().cells, row)):
            p = c.paragraphs[0]
            p.alignment = (WD_ALIGN_PARAGRAPH.LEFT if j == 0
                           else WD_ALIGN_PARAGRAPH.CENTER)
            runs(p, v, size=10.5, force_bold=i in bold_rows)
    return t


def picture(fn, w=15.5):
    doc.add_picture(str(FIGDIR / fn), width=Cm(w))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER


# ============================================================ 표제
p = runs(doc.add_paragraph(), "L2 노선 피처 설계 근거", size=20, force_bold=True)
p.paragraph_format.space_after = Pt(4)
p = runs(doc.add_paragraph(), "무엇을 피처로 넣었고, 왜 그것을 넣고 저것을 뺐는가",
         size=13, color=GRAY)
p.paragraph_format.space_after = Pt(10)
p = doc.add_paragraph()
for k, (lab, val) in enumerate((("문서 정보", ""), ("작성일", "2026-09-15"),
                                ("갱신", "2026-09-21 (42열 → 31열)"),
                                ("대상", "L2단독 31열 · EU CLP · 제형 눈/피부"))):
    if k:
        runs(p, "  |  ")
    runs(p, lab, force_bold=True)
    if val:
        runs(p, " " + val)
p.paragraph_format.space_after = Pt(2)
body("**결론 먼저.** 피처는 31열이고 설계 원칙은 4개다. 실측 중요도로 보면 "
     "가장 공들여 만든 **규칙의 최종 판정(원-핫 8열)이 가장 안 쓰이고**(합 7.4%), "
     "그 판정을 내기 전의 **가산식 중간 원값 6열이 가장 많이 쓰인다**(합 36.8%). "
     "42열이던 것을 31열로 줄였다 — 완전 중복쌍의 한쪽 4열, 값이 거의 한 가지인 "
     "죽은 열 1열, 그리고 감작 CT 6열을 뺐다(§4).", color=LINK)

# ============================================================ 1. 설계 원칙
h1("1. 설계 원칙 — 무엇을 기준으로 골랐는가")

body("피처를 성능으로 고르지 않았다. L2 노선의 질문이 "
     "**'성분 정보와 GHS 혼합물 가산 규칙만으로 완제품 분류를 어디까지 되짚을 수 "
     "있는가'** 이므로, 성능을 올리는 열이라도 그 질문을 반증 불가로 만들면 뺐다. "
     "원칙 4개는 코드에서 강제되고 있으며 그 지점을 함께 적는다.")

table(["원칙", "무엇을 뜻하는가", "강제되는 지점"], [
    ["① 노선 반증가능성",
     "다른 팀원 노선의 정보원이 있어야 계산되는 열은 단독 arm 에 넣지 않는다",
     "`피처노선_arm.json`\n`audit_feature_lane.py`"],
    ["② 규칙을 라벨이 아니라 입력으로",
     "가산식 판정을 정답으로 쓰지 않고 피처로 준다. 규칙이 틀린 자리를 모델이 "
     "고칠 수 있게 남긴다", "`lib_model.build_ct`\n`ct_to_X`"],
    ["③ 결측은 결측으로",
     "농도·pH 결측에 0 이나 7 을 넣지 않는다. 트리의 native NaN 분기로 처리",
     "`native_복원` 규약\n`ZERO_IS_MISSING`"],
    ["④ 라벨과 문서를 공유하는 열은 배제",
     "라벨을 읽은 문서 절에서 나온 값은 피처로 쓰지 않는다",
     "`label_coderived` 태깅\n최소빈 S11 배제"],
], bold_rows=())
caption("표 1. 설계 원칙과 그것을 지키는 코드 지점. 성능이 아니라 질문의 형태가 기준이다")

body("**원칙 ①이 가장 비싸다.** 이것 때문에 L2단독에는 완제품 물성 14종"
     "(`pc2_ph`·`pc2_logkow`·`pc2_vp`·`pc2_bp` 등)이 **한 열도 들어가지 않는다**. "
     "그 값은 완제품 SDS 선언값이라 L1 노선 소유다. 농도가중 디스크립터 19열과 "
     "계면활성제 전하별 농도 6열도 마찬가지로 빠졌다 — 전하 클래스가 RDKit SMARTS "
     "유래라 L3 정보원이 필요하다. 넣으면 성능은 오르지만 그 순간 '성분 정보만으로' "
     "라는 전제가 깨진다.")

picture("figB3_lane_columns.png")
caption("그림 B1. 노선별 열 수. L2단독 31열은 다른 노선 정보가 한 열도 섞이지 않은 "
        "유일한 arm 이다. 게이트를 넘은 arm 2개(피부 전체_노선혼합 0.7120 · "
        "피부 L2+L1 0.7026)는 모두 남의 정보를 더한 것이라 이 가설의 근거로 쓸 수 없다")

# ============================================================ 2. 31열 전수
h1("2. 정확히 무엇이 피처인가 — 31열 전수")

body("31열은 성질이 다른 6개 묶음이다. 묶음마다 왜 그 형태로 만들었는지가 다르므로 "
     "묶음 단위로 적는다.")

table(["묶음", "열", "열 이름", "왜 이 형태인가"], [
    ["① 규칙 판정 (원-핫)", "8",
     "`f_ct_{eye,skin}_cat_*`",
     "가산식이 낸 최종 구분. `__NA__` 를 별도 수준으로 둔 것이 핵심 — "
     "'규칙이 판정을 못 했다' 를 결측이 아니라 **정보**로 넘긴다"],
    ["② 규칙 판정 (서수)", "2",
     "`f_ct_{eye,skin}_ord`",
     "심각도 순서(NC < 2 < 1)를 트리가 한 번의 분기로 자를 수 있게 한 것. "
     "원-핫과 중복이지만 분기 깊이가 다르다"],
    ["③ 가산식 원값", "6",
     "`f_ct_{eye,skin}_s1` · `_s2` · `_add`",
     "판정 **직전**의 가중합. 구분 1 농도합·구분 2 농도합·`10·s1+s2`. "
     "임계값(3·10·20)에 걸리기 전의 연속량이라 판정보다 정보가 많다"],
    ["④ 역할별 농도", "12",
     "`f_pct_*` 11열 + `f_max_pct`",
     "성분명으로 역할을 추정해 역할별 농도를 합산. 가산식이 못 보는 "
     "'무엇이 얼마나 들었나' 를 넣는 자리"],
    ["⑤ 조성 다양성", "2", "`f_shannon` · `f_simpson`",
     "농도 분포의 엔트로피. 단일물질 제형과 복합 제형을 가른다"],
    ["⑥ 이진 플래그", "1", "`f_has_synergist`",
     "협력제 유무. 짝이던 `ct_not_applicable` 은 죽은 열이라 뺐다(§4)"],
], bold_rows=())
caption("표 2. L2단독 31열의 6개 묶음. 스케일링은 하지 않는다 — 트리는 단조변환에 "
        "불변이고, 표준화하면 농도의 절대 수준(가산식 임계값이 걸리는 바로 그 양)이 "
        "사라진다. 세 엔드포인트 중 감작(`f_ct_sens` 계열)은 ①②③에서 전부 빠졌다 — "
        "감작을 학습에 쓰지 않는다는 원칙을 피처에도 맞췄다(§4)")

body("**①②③이 같은 것을 세 번 말한다.** 일부러 그렇게 뒀다. 가산식은 임계값 함수라 "
     "`add = 9.8` 과 `add = 10.2` 가 같은 구분으로 압축된다. 원값을 함께 주면 "
     "모델이 그 경계를 다시 정할 수 있다. 실측(§3)에서 이 판단이 맞았다.")

body("**④의 역할 추정은 정확하지 않다.** `lib_desc.guess_role` 이 성분명 정규식으로 "
     "역할을 찍고, 못 찍은 것 중 농도 1위만 `active_presumed` 로 올린다. 그 결과 "
     "성분행 5,287개 중 **3,153개(59.6%)가 여전히 역할 미상**이고 그 농도가 "
     "`f_pct_unknown` 한 열에 뭉쳐 들어간다. 이 열이 죽은 열이 아니라는 것이 문제다 "
     "— 중요도 눈 6.8% · 피부 6.8% 로 상위권이다. 즉 모델은 "
     "'이름을 규칙으로 못 읽은 성분의 질량' 을 유용한 신호로 쓰고 있다.")

picture("figB1_feature_map.png", w=16.0)
caption("그림 B2. 31열 전경 — 이름 · 결측률 · 중요도. 원-핫 8열은 결측이 구조적으로 "
        "0 이라 막대가 없다(`__NA__` 수준이 결측을 흡수). 붉은 파선 16.8% = 조성미상 281행")

h2("2-1. 뺀 것과 그 이유")

table(["뺀 것", "열 수", "이유"], [
    ["완제품 물성", "14",
     "`pc2_ph`·`pc2_logkow`·`pc2_vp`·`pc2_bp`·`pc2_fp`·`pc2_mp`·`pc2_density` 등. "
     "완제품 SDS 선언값 = L1 노선 소유"],
    ["농도가중 디스크립터", "19",
     "`f_MolLogP_wmean` 등. 농도(L2)와 구조(L3)가 둘 다 있어야 계산된다"],
    ["전하별 농도 · 상호작용", "9",
     "`f_pct_surf_anionic` 등 6열 + `f_solvent_x_logp` 등 3열. "
     "전하 클래스가 RDKit SMARTS 유래"],
    ["`ph_best`", "1",
     "노선 귀속 미결. 강산·강염기 비가산 예외 게이트가 아직 구현 안 됨(STAGED)"],
    ["최소빈 S11 판독", "—",
     "라벨과 같은 문서 절에서 읽은 값. 피처로 쓰면 정답이 샌다"],
    ["`f_pct_thickener`", "1",
     "정상 탈락. 증점제로 찍힌 성분행이 2개뿐이고 둘 다 농도 미상이라 "
     "전 제형에서 값이 0 인 상수열"],
    ["설계 결함 정리분", "11",
     "완전 중복쌍의 한쪽 4 + 죽은 열 1 + 감작 CT 6. 2026-09-21 결정. "
     "§4 에 열 이름과 실측 비용을 적는다"],
], bold_rows=())
caption("표 3. L2단독에서 제외한 열. 위 3개는 노선 반증가능성 때문에, 그 다음 3개는 "
        "각각 미결·누출·상수 때문에, 마지막 11열은 설계 결함으로 빠졌다. 앞의 6개는 "
        "노선 귀속 자체가 L2 가 아니거나 상수인 것이고, 11열은 **L2 귀속인데 "
        "대표 자격만 뺀 것**이라 `피처노선_판정.csv` 에는 L2 로 남아 있다")

# ============================================================ 3. 실측 평가
h1("3. 설계가 맞았는가 — 중요도로 되짚기")

body("랜덤포레스트 `feature_importances_` 를 시드 5개 평균으로 냈다. 이것은 "
     "교차검증 채점이 아니라 **설계 검토용 순위**다. 상관된 열끼리 중요도를 나눠 "
     "갖는 성질이 있어서 절대값을 인용하지 않고 묶음 합만 읽는다.")

table(["묶음", "열 수", "눈 중요도 합", "피부 중요도 합"], [
    ["③ 가산식 원값", "6", "**36.8%**", "33.9%"],
    ["④ 역할별 농도", "12", "**36.8%**", "**40.7%**"],
    ["⑤ 조성 다양성", "2", "13.1%", "14.3%"],
    ["① 규칙 판정 (원-핫)", "8", "7.4%", "5.7%"],
    ["② 규칙 판정 (서수)", "2", "5.8%", "5.1%"],
    ["⑥ 이진 플래그", "1", "0.2%", "0.3%"],
], bold_rows=(0, 1))
caption("표 4. 묶음별 중요도 합(31열 기준). 열 수로 나누면 격차가 더 벌어진다 — "
        "가산식 원값은 열당 6.1%, 원-핫은 열당 0.9%. 42열 판의 같은 표는 "
        "`04_모델산출물/v8_노선2정리/중요도_묶음이동.csv` 에 나란히 남겨 뒀다")

body("**규칙의 최종 판정을 피처로 주는 것은 거의 효과가 없었다.** 원-핫 8열 + 서수 "
     "2열 = 10열이 합쳐서 13.2%(눈)인데, 그 판정을 만들어 낸 원값 6열 하나가 "
     "36.8% 다. 개별로도 `f_ct_eye_add` 12.2% 가 단일 최고이고 "
     "`f_ct_eye_cat_2A` 는 0.6% 다.")

body("→ 읽는 법: 가산식은 **임계값에서 정보를 버린다**. 설계 단계에서 "
     "'판정만 주면 될 텐데 원값까지 넣는 게 중복 아닌가' 를 실제로 고민했는데, "
     "원값을 넣은 쪽이 맞았다. 다음 노선 설계에서도 규칙의 출력은 "
     "**압축 전 형태로** 넘긴다.", color=LINK)

body("**농도가 규칙을 넘어섰다.** ④+⑤ = 49.9%(눈) 로 ③ 36.8% 보다 높고, 피부는 "
     "④ 한 묶음만으로 40.7% 가 되어 ③ 33.9% 를 앞선다. 그중 `f_max_pct` 11.7%, "
     "`f_pct_active_presumed` 11.0%, `f_pct_unknown` 6.8% 다. 규칙이 판정을 못 낸 "
     "제형에서도 '제일 많이 든 성분이 몇 %인가' 만으로 모델이 상당히 버틴다는 뜻이고, "
     "커버리지 0.00–0.25 구간에서 규칙 단독 MCC 는 찍기 수준인데 모델 전체는 "
     "그보다 나은 이유가 여기다.")

body("→ 42열 판에서는 ③ 36.8% > ④ 33.9% 였다. 순서가 뒤집힌 것은 농도 열이 좋아진 "
     "것이 아니라 **감작 CT 6열을 뺀 자리를 ④⑤가 받았기 때문**이다(§4). 중요도는 "
     "합이 1 로 고정된 상대량이므로 열을 빼면 남은 열의 값이 자동으로 올라간다 — "
     "묶음 간 순위 변화를 성능 개선으로 읽지 않는다.", color=LINK)

picture("figB2_corr_heatmap.png", w=15.5)
caption("그림 B3. 31열 상관 구조(Spearman). 검은 테두리 = 표 2 의 묶음. "
        "완전 중복 쌍은 이제 없다 — 42열 판에서 초록 원으로 표시했던 4쌍의 한쪽을 "
        "뺐다(§4). ①②③ 블록끼리 진한 붉은색인 것이 '같은 것을 세 번 말한' 결과이고, "
        "④ 블록은 서로 거의 독립이다")

# ============================================================ 4. 결함
h1("4. 설계 결함 — 42열 전수 점검과 그 정리")

body("42열을 전수로 훑어 4건을 찾았다. **2026-09-21 총책임자 결정으로 3건을 "
     "열 제외로 정리했다**(합 11열, 42 → 31). 값은 한 칸도 고치지 않았다 — "
     "`input_dataset_v6.xlsx` 와 manifest 에는 11열이 그대로 있고, 바뀐 것은 "
     "arm 선택자(`lib_model.FEAT_DROP`)뿐이다. 남은 1건은 열을 빼서 고칠 문제가 "
     "아니라 이월했다.")

table(["결함", "실측", "처리"], [
    ["완전 중복 4쌍",
     "`ct_eye_ord`=`f_ct_eye_ord` · skin · sens 3쌍, "
     "그리고 `f_pct_active`=`f_pct_active_presumed`. 결측 위치까지 같다",
     "**한쪽 4열 제외.** 중요도가 반씩 쪼개져 표를 오독하게 만들던 원인이 "
     "사라졌다. 성능 비용 없음"],
    ["감작 CT 7열이 남아 있었음",
     "`f_ct_sens_*` 합 눈 14.2% · **피부 18.2%**",
     "**6열 제외**(`ct_sens_ord` 는 위 중복쌍에서 이미 빠져 합이 11열이다). "
     "감작을 학습 라벨에서 뺐는데 피처로는 남겨 둔 비대칭을 없앴다. "
     "**이 1건에만 비용이 있다** — 아래 표 6"],
    ["`ct_not_applicable` 이 죽은 열",
     "98.4%가 한 값 · 중요도 0.0%",
     "**1열 제외.** 성능 비용 없음"],
    ["역할 추정 미상 59.6%",
     "성분행 3,153/5,287 이 `unknown`",
     "**이월.** `f_pct_unknown` 을 빼는 것으로 고칠 수 없다 — 그 열은 유용한 "
     "신호이고(중요도 6.8%), 문제는 역할 정규식이 성분명을 못 읽는다는 것이다"],
], bold_rows=())
caption("표 5. 확인된 설계 결함 4건과 처리. 감작 CT 는 라벨이 아니라 혼합물 가산 "
        "디스크립터지만, '감작은 학습에 쓰지 않는다' 는 원칙과 비대칭이어서 "
        "피처에서도 뺐다. 대신 그 비용을 재서 함께 적는다")

h2("4-1. 제외 비용 — 재 봤다")

body("노선2 규약(`EU_CLP` · 공통폴드 · 폴드별 Youden-J τ · 일치쌍 가중 층내부 "
     "보정 AUC)을 그대로 복제해 `L2단독` arm 만 42열 · 31열로 두 번 돌렸다. "
     "판정 밴드는 시드 SD 2배다.")

table(["지표", "눈 42열", "눈 31열", "눈 판정", "피부 42열", "피부 31열", "피부 판정"], [
    ["보정 AUC", "0.6580", "0.6510", "잡음 범위", "0.6470", "0.6388", "손실"],
    ["MCC", "0.3422", "0.2792", "**손실**", "0.2797", "0.2802", "잡음 범위"],
    ["BA", "0.6738", "0.6430", "**손실**", "0.6465", "0.6463", "잡음 범위"],
], bold_rows=())
caption("표 6. 42열 → 31열 실측 비용(`04_모델산출물/v8_노선2정리/노선2정리_최종대조.csv`). "
        "단계별로 쪼개 보면 중복쌍 4열과 죽은 열 1열은 두 엔드포인트 모두 무비용이고, "
        "비용 전체가 감작 CT 6열을 뺀 단계(37 → 31열)에서 나온다")

body("**눈 MCC −0.063 은 실재하는 손실이다.** τ 선택 방식 때문이 아니다 — 고정 0.5 "
     "대조에서도 0.3412 → 0.2842(−0.057)로 같은 크기가 나온다. 라벨 출처가 완제품 "
     "SDS 와 겹치지 않는 `문서독립` 층(눈 n=608)에서도 MCC 0.2114 → 0.1668 로 줄어서, "
     "감작 CT 가 라벨 문서를 우회 참조하던 것이 드러난 결과도 아니다. 감작 CT 열이 "
     "**실제로 눈·피부 예측에 기여하고 있었다**는 뜻이고, 그것을 원칙 때문에 포기했다.")

body("**게이트 판정은 바뀌지 않는다.** 보정 AUC 0.70 기준으로 42열도 31열도 "
     "눈·피부 모두 미달이다(눈 0.6580 → 0.6510, 피부 0.6470 → 0.6388). "
     "즉 이 결정은 결론을 바꾸지 않고 원칙만 정리한다.")

body("**층별 수치를 유의성으로 읽지 않는다.** 층 지표는 시드별로 쪼개지 않고 풀링한 "
     "OOF 예측에서 한 번 계산하므로 시드 SD 가 없다 — 대조표의 층 행은 잡음 범위가 "
     "0.0000 으로 찍히고, 그래서 아무리 작은 차이도 '유의' 로 표시된다. 크기로만 "
     "읽어야 한다. 방향도 한결같지 않다 — 피부 `문서공유_분류절` 층은 MCC 가 "
     "0.2109 → 0.2498 로 오른다.")

body("→ 다음 판에서 손댈 순서: **① 농도 결측 2,121행 확보 → ② 역할 정규식 확장**. "
     "중복 4쌍·죽은 열·감작 CT 정리는 이번 판에서 끝냈다.", color=LINK)

doc.save(OUT)
print(f"→ {OUT.relative_to(L.ROOT)}")
