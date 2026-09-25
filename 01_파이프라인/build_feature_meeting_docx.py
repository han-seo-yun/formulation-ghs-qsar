#!/usr/bin/env python3
"""L2 노선 피처 설계 회의자료 docx — 산출: v8_리포트/260920_회의자료_L2피처설계_한서윤.docx

`build_feature_design_docx.py`(= `L2_피처설계근거_260915.docx`) 와 **같은 내용**을
회의자료 양식으로 다시 적는다. 저쪽은 설계 근거를 논증 순서로 서술하고, 이쪽은
`회의_260915/모델개발_260915_한서윤.pdf` 양식에 맞춘다 — 절 번호는 `1-1.` 꼴,
굵은 소제목 + 짧은 줄, 결론은 `→` 파랑, 보충은 `cf.`, 표/그림 번호는 통번호.

**코드 블록은 넣지 않는다.** 표는 수치 대조가 필요한 자리에만 두고(5개), 나열은
글머리표로 적는다. 본문은 실험 4건을 각각 "바꾼 것 하나 → 결과 → 채택 여부"
순서로 적는다:

  실험 1 결측 처리      `nan0` ↔ `native_복원`
  실험 2 피처셋 범위     L2단독 31열 ↔ 노선혼합 arm 4개
  실험 3 규칙 출력 인코딩 원-핫 · 서수 · 원값 3형태
  실험 4 농도 피처 기여   규칙 묶음 ↔ 농도 묶음

수치는 전부 이미 있는 산출물에서 읽는다. 스크립트가 다시 계산하지 않고, 읽은
자리를 주석으로 남긴다:

  · arm 성능        04_모델산출물/v8_노선2/지표_노선2.csv
  · 결측 0 감사      04_모델산출물/v7_결측감사/감사보고.json
  · 중요도          04_모델산출물/v8_리포트/피처중요도_L2단독31.csv
  · 42↔31 묶음 대조  04_모델산출물/v8_노선2정리/중요도_묶음이동.csv
  · 제외 비용        04_모델산출물/v8_노선2정리/노선2정리_최종대조.csv
  · 커버리지 MCC     04_모델산출물/v8_리포트/모델개발_데이터현황_리포트.md §2-3

`L2_피처설계근거_260915.docx` 와 어긋나던 값 2개는 이쪽을 맞다고 본다 —
감작 CT 열은 8개가 아니라 **7개**(감작은 구분 1 만 나오므로 `_s2` 가 없다),
유효성분 농도 두 열 합은 15.1% 가 아니라 **15.2%**(42열 기준). 둘 다 중요도 CSV
재집계값이다. 31열 판에서는 그 두 열 중 한쪽이 빠져 합산 자체가 없어졌다.

사람 이름은 적지 않는다 — 이 저장소는 비공개지만 산출물은 팀에 돌아간다.

## 2026-09-21 — 42열 → 31열. 이미 나간 docx 와 다르다

`L2단독` arm 이 42 열에서 31 열로 줄었다(설계 결함 11열 제외, `lib_model.FEAT_DROP`).
이 소스의 본문 수치·표는 **31열 기준으로 고쳐 두었다.** 그러나 **이미 배포된
`260920_회의자료_L2피처설계_한서윤.docx` 파일 자체는 42열 판 그대로다** —
총책임자 결정으로 재빌드를 보류했다.

그래서 이 스크립트를 지금 그냥 돌리면 **파일 내용이 배포본과 달라진다.** 돌릴
거라면 파일명을 새 날짜로 바꾸고, 42열 판과 어느 수치가 다른지 함께 알려라.
`손수정_보관()` 은 손수정 보호 장치일 뿐 배포본과의 차이를 알려 주지 않는다.

**그림 두 계열이 모두 42열 판이다.**
  · `figures/figB3_lane_columns.png` — `build_feature_design_figs.py` 소유.
    **31열 판으로 다시 그렸다**(2026-09-21). 42열 판은 `figB3_*_42열.png` 에
    보존돼 있다. 즉 이미 나간 docx 안의 그림은 이제 이 폴더의 파일과도 다르다
  · `figures/fig_a_feature_importance.png` — **아직 42열 기준이고 이것을 만드는
    스크립트가 저장소에 없다.** 상위 12개 막대의 열 이름·수치가 4장 본문과
    어긋난다. 재빌드하려면 이 그림을 31열로 다시 그려야 하고, 그러려면 생성
    스크립트부터 새로 써야 한다 — 입력은
    `04_모델산출물/v8_리포트/피처중요도_L2단독31.csv`
"""
from __future__ import annotations

import os

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

import lib_model as L

OUTDIR = L.ROOT / "04_모델산출물" / "v8_리포트"
FIGDIR = OUTDIR / "figures"
NOTITLE = FIGDIR / "회의자료_머리글제거"     # untitled() 가 만드는 사본 자리
OUT = OUTDIR / "260920_회의자료_L2피처설계_한서윤.docx"

# 이미 나간 배포본을 말없이 지우지 않게 막는다. 이 소스는 31열 판이고 디스크의
# 파일은 42열 판이다(2026-09-21 결정으로 재빌드 보류). `.gitignore` 가 `*.docx` 라
# 덮어쓰면 git 으로도 못 되돌린다. 아래 `손수정_보관()` 은 **빌드지문이 다를 때만**
# 사본을 빼므로 지문이 일치하는 정상 배포본은 그냥 사라진다 — 그 구멍을 여기서 막는다
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
BLUE = RGBColor(0x1F, 0x49, 0x7D)
GRAY = RGBColor(0x59, 0x59, 0x59)
CODE = RGBColor(0x33, 0x33, 0x33)
LINK = RGBColor(0x00, 0x0E, 0xFA)
BAND = "DCE6F1"          # 짝행 옅은 파랑 — 회의자료 표와 같은 톤
TABLE_STYLE = "Table Grid"

doc = Document()
for nm, sz, bold, col in (("Normal", 11, False, BLACK), ("Heading 1", 15, True, NAVY),
                          ("Heading 2", 12.5, True, BLUE), ("Body Text", 11, False, BLACK),
                          ("List Bullet", 10.5, False, BLACK)):
    s = doc.styles[nm]
    s.font.name, s.font.size, s.font.bold, s.font.color.rgb = KO, Pt(sz), bold, col
    s._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
for nm in ("Normal", "Body Text"):
    pf = doc.styles[nm].paragraph_format
    pf.space_after, pf.line_spacing = Pt(6), 1.35
cap = doc.styles.add_style("Image Caption", WD_STYLE_TYPE.PARAGRAPH)
cap.base_style = doc.styles["Normal"]
cap.font.name, cap.font.size, cap.font.bold = KO, Pt(8.5), False
cap.font.color.rgb = GRAY
cap._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
cap.paragraph_format.space_after = Pt(14)
for sec in doc.sections:
    sec.left_margin = sec.right_margin = Cm(2.4)
    sec.top_margin = sec.bottom_margin = Cm(2.4)


def runs(p, text, size=11, color=None, force_bold=False, mark=False):
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
        # strip("*`") 을 쓰면 `f_ct_sens_*` 처럼 끝이 별표인 열 이름에서 별표까지
        # 떼어내 열 이름이 조용히 바뀐다. 표시 기호만 정확히 한 짝 벗긴다
        if is_code:
            r = p.add_run(tok[1:-1])
        elif tok.startswith("**"):
            r = p.add_run(tok[2:-2])
        else:
            r = p.add_run(tok)
        r.font.name = MONO if is_code else KO
        r.font.size = Pt(size - 1.5 if is_code else size)
        r._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
        r.bold = force_bold or tok.startswith("**")
        r.font.color.rgb = CODE if is_code and color is None else (color or BLACK)
        if mark:
            r.font.highlight_color = 7  # WD_COLOR_INDEX.YELLOW
    return p


def body(text, color=None):
    return runs(doc.add_paragraph(style="Body Text"), text, color=color)


def h1(t, mark=False):
    p = doc.add_paragraph(style="Heading 1")
    runs(p, t, size=15, color=NAVY, force_bold=True, mark=mark)
    p.paragraph_format.space_before = Pt(20)


def h2(t, mark=False):
    p = doc.add_paragraph(style="Heading 2")
    runs(p, t, size=12.5, color=BLUE, force_bold=True, mark=mark)
    p.paragraph_format.space_before = Pt(12)


def caption(t):
    # 백틱을 runs() 로 넘겨야 캡션 안의 코드명도 Menlo 로 나온다. 그냥
    # add_paragraph 로 넣으면 백틱 문자가 그대로 찍힌다
    p = doc.add_paragraph(style="Image Caption")
    runs(p, t, size=8.5, color=GRAY)
    return p


def bullet(t, color=None):
    """나열은 표 대신 글머리표로 — 회의자료 쪽 규약·결함 목록과 같은 꼴."""
    p = doc.add_paragraph(style="List Bullet")
    runs(p, t, size=10.5, color=color)
    p.paragraph_format.space_after = Pt(3)
    return p


def shade(cell, hexcolor):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcolor)
    cell._tc.get_or_add_tcPr().append(el)


def keep_together(t):
    """표가 페이지 경계에서 쪼개지지 않게 한다.

    `cantSplit` 은 **한 행**이 갈라지는 것만 막고 표 전체는 여전히 나뉜다. 그래서
    모든 칸 문단에 `keep_with_next` 를 걸어 표 + 바로 뒤 캡션까지 한 페이지에
    묶는다. 표가 한 페이지보다 길면 이 규칙은 무시되므로 넘칠 걱정은 없다.
    """
    for row in t.rows:
        el = OxmlElement("w:cantSplit")
        row._tr.get_or_add_trPr().append(el)
        for c in row.cells:
            for p in c.paragraphs:
                p.paragraph_format.keep_with_next = True


def table(head, rows, bold_rows=(), widths=None, left_align_first=True):
    """회의자료 표 — 가운데 정렬 · 머리행 굵게 · 짝행 옅은 파랑."""
    t = doc.add_table(rows=1, cols=len(head))
    t.style = TABLE_STYLE
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for c, h in zip(t.rows[0].cells, head):
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        runs(p, h, size=9.5, force_bold=True)
        shade(c, BAND)
    for i, row in enumerate(rows):
        cells = t.add_row().cells
        for j, (c, v) in enumerate(zip(cells, row)):
            p = c.paragraphs[0]
            p.alignment = (WD_ALIGN_PARAGRAPH.LEFT if j == 0 and left_align_first
                           else WD_ALIGN_PARAGRAPH.CENTER)
            p.paragraph_format.space_after = Pt(2)
            runs(p, v, size=9.5, force_bold=i in bold_rows)
            if i % 2:
                shade(c, "F2F7FC")
    if widths:
        for row in t.rows:
            for c, w in zip(row.cells, widths):
                c.width = Cm(w)
    keep_together(t)
    return t


def untitled(fn):
    """그림 PNG 안에 찍혀 있는 `[그림 B1]` 머리글을 떼어낸 사본 경로를 준다.

    원본 PNG 는 그림 번호를 이미지 안에 박아 두었고 그 번호는
    `L2_피처설계근거_260915.docx` 것이다(A · B1 · B2 · B3). 이 회의자료의 그림 번호는
    1~4 라서 그대로 넣으면 캡션과 어긋난다. 원본과 그림 스크립트는 저쪽 문서 소유라
    건드리지 않고, 맨 위 한 줄만 잘라낸 사본을 따로 만들어 쓴다. 번호와 설명은
    캡션이 맡는다.
    """
    import numpy as np
    from PIL import Image

    NOTITLE.mkdir(exist_ok=True)
    out = NOTITLE / fn
    src = FIGDIR / fn
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    im = Image.open(src)
    ink = (np.array(im.convert("L")) < 245).any(axis=1)
    top = int(np.argmax(ink))                       # 첫 잉크 = 머리글 줄
    end = top
    while end + 1 < len(ink) and ink[end + 1]:
        end += 1
    nxt = end + 1
    while nxt < len(ink) and not ink[nxt]:
        nxt += 1
    # 머리글 밴드와 그림 본체 사이 공백의 가운데서 자른다. 공백이 없으면
    # 머리글이 아니라 그림 자체라는 뜻이므로 손대지 않는다
    cut = (end + nxt) // 2 if nxt - end > 8 else 0
    im.crop((0, cut, im.width, im.height)).save(out)
    return out


def picture(fn, w=15.0):
    doc.add_picture(str(untitled(fn)), width=Cm(w))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.space_before = Pt(6)


# ============================================================ 표제
p = runs(doc.add_paragraph(), "L2 노선 피처 설계 — 실험 4건", size=17, force_bold=True)
p.paragraph_format.space_after = Pt(4)
p = doc.add_paragraph()
for k, (lab, val) in enumerate((("문서 정보", ""), ("회의일", "2026-09-19"),
                                ("작성자", "한서윤"))):
    if k:
        runs(p, "  |  ", size=10, color=GRAY)
    runs(p, lab, size=10, color=GRAY, force_bold=True)
    if val:
        runs(p, " " + val, size=10, color=GRAY)
p.paragraph_format.space_after = Pt(2)
p = runs(doc.add_paragraph(), "① 피처 구성 ② 실험 4건 ③ 확인된 결함",
         size=10.5, force_bold=True)
p.paragraph_format.space_after = Pt(12)

body("**결론 먼저.** 대표 구성은 **L2단독 31열** · 결측 규약 `native_복원` 임. "
     "성능이 더 나온 구성이 2개 있었지만 둘 다 남의 노선 정보를 쓴 것이라 채택하지 "
     "않았음. 그리고 가장 공들여 만든 규칙의 최종 판정(원-핫 8열)이 가장 안 쓰이고, "
     "그 판정을 내기 전의 가산식 원값 6열이 가장 많이 쓰임. 42열이던 것을 **31열로 "
     "줄였음** — 설계 결함 11열을 뺀 것이고 비용도 함께 적었음(6).", color=LINK)

# ============================================================ 1. 피처 구성
h1("1. 피처 구성")

h2("1-1. 현재")

body("**1. 노선**")
body("이 노선은 성분별 MSDS 위험정보(눈·피부 몇 등급) × 농도만 씀. 완제품 SDS 선언값과 "
     "성분 SMILES 구조는 각각 다른 노선 소유.")

body("**2. 산출물**")
body("제형 단위 눈(eye)·피부(skin) 2개 모델. 라벨 보유행 눈 1,044 / 피부 1,059.")
bullet("**라벨**(맞혀야 하는 정답)은 제형 단위 — '이 제품이 눈에 위험한지 아닌지' 는 "
       "제품(제형) 하나당 하나의 답")
bullet("**피처**(입력 재료)는 성분 단위 정보 — 그 제품에 들어간 성분들 각각의 MSDS "
       "위험정보 × 농도")

body("**3. 열 수**")
body("L2단독 31열 — 성분 정보와 농도만으로 만든 피처임. 성질이 다른 6묶음이고, "
     "묶음마다 만든 이유가 달라서 묶음 단위로 적음. 2026-09-21 결정으로 42열에서 "
     "11열을 뺀 결과임(6).")

body("**넣은 것 (6묶음, 31열)**")
bullet("**① 규칙이 내린 최종 판정 (8열)** — 눈/피부 각각 몇 등급인지, 원-핫으로")
bullet("**② 같은 판정을 순서값으로 (2열)** — 심각도 순서(없음 < 2 < 1)로도 한 번 더 줌")
bullet("**③ 판정 내기 직전의 원값 (6열)** — 등급으로 자르기 전의 가중합 숫자 그대로")
bullet("**④ 역할별 농도 (12열)** — 활성성분·계면활성제·용매 등이 각각 몇 % 들었는지")
bullet("**⑤ 조성이 얼마나 복잡한지 (2열)** — 성분이 한 개인지 여러 개 섞였는지")
bullet("**⑥ 협력제 유무 (1열)**")
body("→ ①②③에 감작이 없는 것이 42열 판과 다른 점임. 감작 등급은 원래 학습 정답으로 "
     "쓰지 않는데 가산식 중간값으로는 피처에 남아 있었고, 그 비대칭을 없앴음(6).",
     color=LINK)

body("**뺀 것 — 계산하려면 다른 노선 정보가 필요해서**")
bullet("**완제품 물성 14열** (pH·점도 등) — 완제품 SDS 에 적힌 값이라 L1 노선 것")
bullet("**농도×구조 디스크립터 19열** — 농도(L2)와 성분 구조(L3)를 둘 다 알아야 계산됨")
bullet("**전하별 농도 6열 + 농도×구조 상호작용 3열** — 전하 종류를 알려면 "
       "성분 구조(L3)가 필요")
bullet("**pH 최종값 1열** — 아직 어느 노선 것인지 정해지지 않음")
body("→ 빼는 이유는 성능이 아니라 '성분 정보만으로 맞혀야 한다' 는 이 노선의 전제를 "
     "지키기 위해서임. 저 열들을 넣으면 성능은 오르지만 더 이상 순수하게 성분 정보만 "
     "쓴 게 아니게 됨.", color=LINK)

body("**묶음마다 계산이 무엇인가**")
body("**①②③ — 같은 수식에서 나옴 (UN GHS 3.3 가산식).** 성분마다 등급(구분 1 / 구분 2 / "
     "구분없음) × 농도를 곱해서 다 더하는 공식임.")
bullet("구분 1 성분 농도합 = `s1` · 구분 2 성분 농도합 = `s2` · 최종 가중값 "
       "`add` = 10×`s1` + `s2`")
bullet("이 `add` 를 임계값 3·10·20(가산식 공식 컷오프 농도)에 대입해서 등급을 정함")
body("→ ③은 이 계산의 중간 숫자(`s1`·`s2`·`add`) 그대로, ②는 그 등급을 순서값으로, "
     "①은 그 등급을 원-핫으로 표현한 것. 즉 하나의 수식 결과를 세 가지 형태로 반복해서 "
     "준 거임.", color=LINK)
body("**④ 역할별 농도 — 수식이라기보단 단순 합산.** '활성성분으로 추정된 성분들의 농도를 "
     "다 더한다' 정도. 등급 계산 없이 그냥 % 합.")
body("**⑤ 조성 다양성 — 진짜 수학 공식(엔트로피).** Shannon −Σp·ln(p) · Simpson 1−Σp² "
     "(p = 각 성분 농도 비율). 성분이 골고루 섞였는지 계산하는 정보이론 공식임.")
body("**⑥ 이진 플래그 — 계산 없음.** 조건 만족하면 1, 아니면 0. 수식 아니고 그냥 판별.")

table(["묶음", "열", "열 이름", "무엇인가"], [
    ["① 규칙 판정 (원-핫)", "8", "`f_ct_{eye,skin}_cat_*`",
     "가산식이 낸 최종 구분. `__NA__` 를 별도 수준으로 둬서 "
     "'규칙이 판정을 못 했다' 를 결측이 아니라 정보로 넘김"],
    ["② 규칙 판정 (서수)", "2", "`f_ct_{eye,skin}_ord`",
     "심각도 순서(NC < 2 < 1). 원-핫과 중복이지만 트리 분기 깊이가 다름"],
    ["③ 가산식 원값", "6", "`f_ct_{eye,skin}_s1` · `_s2` · `_add`",
     "판정 **직전**의 가중합. 임계값(3·10·20)에 걸리기 전의 연속량"],
    ["④ 역할별 농도", "12", "`f_pct_*` 11열 + `f_max_pct`",
     "성분명으로 역할을 추정해 역할별 농도를 합산. 가산식이 못 보는 자리"],
    ["⑤ 조성 다양성", "2", "`f_shannon` · `f_simpson`",
     "농도 분포 엔트로피. 단일물질 제형과 복합 제형을 가름"],
    ["⑥ 이진 플래그", "1", "`f_has_synergist`",
     "협력제 유무. 짝이던 `ct_not_applicable` 은 죽은 열이라 뺐음(6)"],
], widths=(3.6, 1.0, 4.4, 6.0))
caption("표 1. L2단독 31열의 6묶음. 스케일링은 안 함 — 트리는 단조변환에 불변이고, "
        "표준화하면 농도의 절대 수준(가산식 임계값이 걸리는 바로 그 양)이 사라짐. "
        "①②③에서 감작(`f_ct_sens` 계열)이 전부 빠진 것이 42열 판과 다른 점임")

h2("1-2. 실험 조건 — 4건에서 공통으로 고정한 것")

bullet("**모델** RandomForestClassifier(n_estimators=300, class_weight=balanced, "
       "min_samples_leaf=2) · 눈·피부 독립 학습")
bullet("**검증** StratifiedGroupKFold 5-fold(그룹=활성성분 587그룹) × 시드 5개. "
       "같은 제형에서 나온 성분행이 학습/검증에 쪼개져 들어가지 않게 묶음")
bullet("**지표** pooled AUC 와 누출보정 AUC 를 항상 병기. 게이트 판정은 "
       "**누출보정 AUC ≥ 0.70** 으로만 함")
bullet("**임계값** τ 는 폴드마다 따로 구해 그 폴드 검증행에만 적용. 폴드 간 평균 금지 — "
       "라벨이 새어 들어옴")

body("→ 아래 실험 4건은 이 조건을 고정하고 **한 번에 한 가지만** 바꿔 견줌.", color=LINK)

h2("1-3. 실험 4건 요약")

table(["실험", "바꾼 것", "채점", "결과"], [
    ["1. 결측 처리", "`nan0` ↔ `native_복원`", "보정 AUC · MCC · BA",
     "`native_복원` **채택**"],
    ["2. 피처셋 범위", "L2단독 31열 ↔ 노선혼합 arm 4개 (최대 209열)", "보정 AUC · MCC",
     "혼합이 더 높지만 **미채택**"],
    ["3. 규칙 출력 인코딩", "원-핫 · 서수 · 원값 3형태를 동시 투입", "묶음별 중요도",
     "원값이 1위, 3형태 **유지**"],
    ["4. 농도 피처 기여", "규칙 묶음 ↔ 농도 묶음", "묶음별 중요도",
     "농도가 규칙과 **대등**"],
], widths=(3.4, 6.0, 3.0, 3.2))
caption("표 2. 실험 3·4 는 성능 채점이 아니라 중요도 되짚기임 — 교차검증 점수가 아니라 "
        "설계 검토용 순위로만 읽음")

# ============================================================ 2. 실험 1
h1("2. 실험 1 — 결측을 0 으로 채울 것인가")

h2("2-1. 문제: 구조적 0 과 만들어 넣은 0 이 한 열에 섞임")

body("0 이라고 다 같은 0 이 아님. 정보원이 그 행에 있는데 값이 0 이면 실측값이고, "
     "정보원이 아예 없는데 0 이면 값을 만든 것임.")

body("입력 생성 쪽에서 `None` 을 0.0 으로 바꾸는 관용구(`or 0.0`)가 계면활성제 농도 "
     "4열을 합산할 때 걸려 있었음. 그래서 구성 4열은 결측인데 **합계 2열만 0** 이 되고, "
     "결측 플래그열(`*_isna`)조차 생성되지 않아 나중에 되짚을 단서도 없었음.")

body("→ 조성을 아예 모르는 281행이 '계면활성제가 0% 들어있다' 는 실측값과 구별되지 "
     "않게 됨. 열 2개 × 281행 = **562셀**.", color=LINK)

body("**cf. 전수 확인 결과 만들어 넣은 0 은 이 2열뿐이었음**")
body("결측값 0: `f_pct_surf_total` · `f_surf_anionic_nonionic`. 나머지 0 은 전부 구조적 "
     "0 으로 확인됨")

h2("2-2. 결과")

table(["endpoint", "결측 처리", "pooled AUC", "누출보정 AUC", "MCC", "BA"], [
    ["눈 (n=1,044)", "`nan0` (0 으로 채움)", "0.6814", "0.6244", "0.2578", "0.6286"],
    ["눈 (n=1,044)", "`native_복원`", "0.7114", "0.6510", "0.2792", "0.6430"],
    ["피부 (n=1,059)", "`nan0` (0 으로 채움)", "0.6878", "0.6341", "0.2900", "0.6489"],
    ["피부 (n=1,059)", "`native_복원`", "0.6999", "0.6388", "0.2802", "0.6463"],
], bold_rows=(1, 3), widths=(3.2, 3.6, 2.4, 2.6, 1.9, 1.9))
caption("표 3. 같은 31열 · 같은 폴드에서 결측 규약만 바꿈. 굵은 행이 채택값. 게이트 "
        "판정값인 누출보정 AUC 는 눈 +0.0266 · 피부 +0.0047 로 둘 다 복원 쪽이 높음")

body("좋아진 쪽으로 전 지표가 몰린 것은 아님 — 피부 MCC·BA 는 `nan0` 쪽이 각각 "
     "0.0098 · 0.0026 높음.")

h2("2-3. 결론")

body("→ 게이트 판정값이 눈·피부 둘 다 복원 쪽이 높아서 **원칙과 성능이 같은 방향**이었음. "
     "피부 MCC 가 0.0098 낮은 것은 남기고 대표값은 `native_복원` 으로 확정. `nan0` 은 "
     "v6 재현 대조용으로만 보존.", color=LINK)

h2("2-4. 해야 할 것")

body("결측으로 되돌린 것은 값을 바로잡은 게 아니라 **모른다고 정확히 표시한 것**임. 진짜 "
     "해결은 원본 문서에서 실제 농도를 찾아오는 것인데 아직 데이터가 없어서 미뤄 둔 상태임.")

bullet("**조성미상 281행 채우기** — 원본 MSDS·제품 조성표에서 계면활성제 조성을 다시 찾음. "
       "채워지면 합계 2열의 결측 562셀도 같이 사라짐")
bullet("**농도 결측 2,121행 채우기** — 성분행 5,287개 중 농도 보유는 3,166개(59.9%)뿐. "
       "가산식은 구분과 농도를 둘 다 받아야 계산되므로 농도가 없는 행은 규칙에 못 들어감")
bullet("**확인 방법** — 구분과 농도를 함께 가진 행이 2,558개(48.4%)에서 얼마나 늘었는지 "
       "세고, 그만큼 누출보정 AUC 가 올라가는지 재측정")

body("→ 구분만 채우는 것으로는 안 된다는 것은 이미 확인함 — 외부 규제 DB 조사로 구분 보유 "
     "성분행을 2,578 → 3,043 으로 늘렸는데, 누출보정 AUC 는 눈 0.6580→0.6532(−0.0048) · "
     "피부 0.6470→0.6497(+0.0027)로 시드 재현 범위(±0.01) 안이라 변화 없음. 병목은 구분이 "
     "아니라 농도임 (7).", color=LINK)
body("**cf.** 바로 위 조사 대조는 **42열 arm 에서 잰 것**이고 31열로 다시 재지 않았음. "
     "기준선 0.6580·0.6470 이 표 3 의 31열 값과 다른 이유임. 두 수치를 한 문장에 섞어 "
     "읽지 않음 — 조사의 효과는 같은 arm 안에서의 차이로만 읽음.")

body("**cf. 임퓨트(결측값을 0 으로 채우는 방식)를 안 해도 되는 이유**")
body("신버전 트리 모델인 sklearn ≥ 1.4 가 결측값을 그대로 분기 방향으로 학습하므로.")

# ============================================================ 3. 실험 2
h1("3. 실험 2 — 피처셋 범위")

h2("3-1. 대조 방법")

body("팀원 3명이 같은 제형을 다른 입력 문서로 나눠 맡고 있음. 남의 노선 열을 더한 "
     "arm 4개를 L2단독과 같은 폴드로 함께 돌려 봄. 더한 열이 무엇인지 먼저 적음.")

bullet("**L1 = 완제품 SDS 선언값 (52열)** — 완제품을 직접 측정해 SDS 에 적어 놓은 값. "
       "pH·끓는점·인화점·밀도·점도·용해도 같은 물성 수치와, 제형 유형·물리적 상태·용해성 "
       "표기의 원-핫")
bullet("**L3 = 성분 SMILES 구조 (98열)** — 성분의 화학 구조에서 RDKit 으로 뽑은 값. "
       "분자량·logP·고리 수·수소결합 자리 같은 디스크립터 19종을 제형 안 성분들에 대해 "
       "최대·평균·최소·범위·표준편차로 요약한 95열 + 성분끼리 구조가 얼마나 닮았는지 3열")
bullet("**L2·L3 결합 (28열)** — 어느 한 노선 것이 아니고 두 노선 정보가 다 있어야 계산되는 "
       "열. 구조값을 농도로 가중평균한 19열, 계면활성제 전하별 농도 6열(전하 종류를 구조에서 "
       "판별), 농도×구조 상호작용 3열")

table(["피처셋", "열 수", "눈 보정 AUC", "눈 MCC", "피부 보정 AUC", "피부 MCC", "게이트 0.70"], [
    ["**L2단독**", "**31**", "**0.6510**", "**0.2792**", "**0.6388**", "**0.2802**", "미달"],
    ["L2+결합", "59", "0.6766", "0.3770", "0.6654", "0.3188", "미달"],
    ["L2+L1", "83", "0.6950", "0.3889", "**0.7026**", "0.3668", "**피부 통과**"],
    ["L2+L3", "129", "0.6893", "0.3998", "0.6843", "0.3525", "미달"],
    ["전체_노선혼합", "209", "0.6981", "0.4012", "**0.7120**", "0.3743", "**피부 통과**"],
], bold_rows=(0,), widths=(3.2, 1.6, 2.4, 1.8, 2.4, 1.8, 2.2))
caption("표 4. arm 5개(EU CLP · 공통폴드 · `native_복원`). 열을 더할수록 대체로 올라가고, "
        "게이트를 넘는 arm 2개는 모두 남의 노선 정보를 더한 것임. 혼합 arm 도 L2 몫이 "
        "31열로 줄어 열 수가 11 씩 함께 내려갔음")

picture("figB3_lane_columns.png", w=14.0)
caption("그림 1. 노선별 열 수. L2단독 31열은 다른 노선 정보가 한 열도 섞이지 않은 "
        "유일한 arm 임")

h2("3-2. 그래도 뺀 열")

body("L2 노선의 질문이 **'성분 정보와 GHS 혼합물 가산 규칙만으로 완제품 분류를 어디까지 "
     "되짚을 수 있는가'** 이므로, 다른 노선의 정보원이 있어야 계산되는 열은 성능을 "
     "올려도 뺐음. 이 규칙은 `피처노선_arm.json` 과 감사 스크립트에서 강제됨.")

bullet("**완제품 물성 14열** — `pc2_ph`·`pc2_logkow`·`pc2_vp`·`pc2_bp` 등. "
       "완제품 SDS 선언값이라 L1 노선 소유")
bullet("**농도가중 디스크립터 19열** — `f_MolLogP_wmean` 등. 농도(L2)와 구조(L3)가 "
       "둘 다 있어야 계산됨")
bullet("**전하별 농도·상호작용 9열** — `f_pct_surf_anionic` 등 6열 + "
       "`f_solvent_x_logp` 등 3열. 전하 클래스가 RDKit SMARTS 유래")
bullet("**노선 귀속 미결 1열** — `ph_best`. 강산·강염기 비가산 예외 게이트가 아직 "
       "구현 안 됨")
bullet("**최소빈 S11 판독** — 라벨을 읽은 문서 절에서 나온 값. 피처로 쓰면 정답이 샘")
bullet("**상수열 1열** — `f_pct_thickener`. 정상 탈락. 증점제로 찍힌 성분행이 2개뿐이고 "
       "둘 다 농도 미상이라 전 제형에서 값이 0")

h2("3-3. 결론")

body("→ 게이트를 넘은 것은 피부 2개 구성임(전체_노선혼합 0.7120 · L2+L1 0.7026). 둘 다 "
     "남의 노선 정보를 더한 것이고 눈은 어느 구성도 못 넘었음. 그래도 **남의 노선 정보를 "
     "더해 게이트를 넘긴 모델보다 성능이 좀 떨어져도 '성분 정보만으로' 라는 전제가 살아 "
     "있는 모델을** 대표로 쓰는 것이 맞다고 판단함. 혼합 arm 은 지우지 않고 부가 행으로 "
     "보존 — 다른 노선과의 상보성 자체는 따로 쓸 데가 있음.", color=LINK)

# ============================================================ 4. 실험 3
h1("4. 실험 3 — 규칙 출력을 어떤 형태로 줄 것인가", mark=True)

h2("4-1. 대조 방법: 압축 단계가 다른 세 형태를 동시에 넣음")

body("가산식의 출력을 어떤 형태로 모델에 줄지가 이 노선 설계의 핵심 결정이었음. 하나만 "
     "고르지 않고 원-핫(①)·서수(②)·원값(③) 세 형태를 같이 넣고 중요도로 되짚기로 함.")

body("**cf.** 중요도는 랜덤포레스트 `feature_importances_` 의 시드 5개 평균. 상관된 열끼리 "
     "중요도를 나눠 갖는 성질이 있어서 개별 절대값을 인용하지 않고 묶음 합만 읽음.")

table(["묶음", "열 수", "눈 합", "피부 합", "눈 열당", "피부 열당"], [
    ["③ 가산식 원값", "6", "**36.8%**", "33.9%", "6.14%", "5.65%"],
    ["④ 역할별 농도", "12", "**36.8%**", "**40.7%**", "3.07%", "3.39%"],
    ["⑤ 조성 다양성", "2", "13.1%", "14.3%", "**6.53%**", "**7.16%**"],
    ["① 규칙 판정 (원-핫)", "8", "7.4%", "5.7%", "0.92%", "0.71%"],
    ["② 규칙 판정 (서수)", "2", "5.8%", "5.1%", "2.89%", "2.55%"],
    ["⑥ 이진 플래그", "1", "0.2%", "0.3%", "0.15%", "0.32%"],
], bold_rows=(0,), widths=(4.0, 1.6, 2.2, 2.2, 2.2, 2.2))
caption("표 5. 묶음별 중요도(31열 기준). 합으로는 눈에서 가산식 원값과 역할별 농도가 "
        "동률이고 피부는 농도가 앞섬. 열당으로는 조성 다양성 2열이 1위(6.53%)이고 "
        "가산식 원값이 2위(6.14%). 42열 판과 나란한 대조는 "
        "`04_모델산출물/v8_노선2정리/중요도_묶음이동.csv`")

body("→ 묶음 간 순위가 42열 판과 달라진 것을 **성능 개선으로 읽지 않음**. 중요도는 합이 "
     "1 로 고정된 상대량이라 열 11개를 빼면 남은 열의 값이 자동으로 올라감. 눈에서 "
     "③ 36.8% > ④ 33.9% 였던 것이 동률이 되고 피부에서 ④ 가 ③ 을 앞선 것은, 감작 CT "
     "6열을 뺀 자리를 ④⑤가 받은 결과임(6).", color=LINK)

picture("fig_a_feature_importance.png", w=13.0)
caption("그림 2. 상위 12개. 가산식 원값(`_add`·`_s1`)·최대농도·유효성분 농도·다양성 "
        "지수가 상위권이고, 원-핫 8열은 한 열도 올라오지 못함. 주의 — 이 그림은 아직 "
        "42열 기준이라 막대의 열 이름·수치가 아래 본문과 어긋남")

body("**규칙의 최종 판정을 피처로 주는 것은 거의 효과가 없었음.** 원-핫 8열 + 서수 2열 "
     "= 10열이 합쳐서 13.2%(눈)인데, 그 판정을 만들어 낸 원값 6열이 36.8%. 개별로도 "
     "`f_ct_eye_add` 12.2% 가 단일 최고이고 `f_ct_eye_cat_2A` 는 0.6% 임.")

h2("4-2. 결론", mark=True)

body("→ 가산식은 **임계값에서 정보를 버림**. `add`=9.8 과 10.2 가 같은 구분으로 압축되는데, "
     "원값을 함께 주면 모델이 그 경계를 다시 정할 수 있음. 설계 단계에서 '판정만 주면 될 "
     "텐데 원값까지 넣는 게 중복 아닌가' 를 실제로 고민했는데 원값을 넣은 쪽이 맞았음. "
     "다음 노선 설계에서도 규칙 출력은 **압축 전 형태로** 넘김.", color=LINK)

body("원-핫·서수 10열은 기여가 작지만 남김 — 세 형태를 같이 넣은 것이 이 실험의 설계 "
     "자체이고, 빼면 다음 판에서 같은 대조를 다시 못 함. 중복이라서 뺀 것은 값이 완전히 "
     "같았던 서수 쌍 한쪽뿐임(6).")

# ============================================================ 5. 실험 4
h1("5. 실험 4 — 농도 피처가 규칙만큼 쓰이는가")

h2("5-1. 결과: 농도 쪽이 규칙 원값보다 높음")

body("규칙 원값 ③ 36.8%(눈) 보다 농도 쪽 ④+⑤ 가 **49.9%** 로 오히려 높음(피부 55.0%). "
     "개별로는 `f_max_pct` 11.7%, `f_pct_active_presumed` 11.0%, `f_pct_unknown` 6.8%.")
body("**cf.** 42열 판에서는 `f_pct_active` 와 `f_pct_active_presumed` 가 값이 완전히 같은 "
     "두 열이라 15.2% 를 반씩 나눠 갖고 있었음. 한쪽을 빼서 이제 한 열에 모임(6).")

body("→ 규칙이 판정을 못 낸 제형에서도 **'제일 많이 든 성분이 몇 %인가' 만으로 모델이 "
     "상당히 버틴다**는 뜻임. 성분 커버리지 0.00–0.25 구간에서 규칙 단독 MCC 는 눈 0.174 · "
     "피부 −0.025 로 찍기 수준인데 모델 전체가 그보다 나은 이유가 여기 있다고 봄.", color=LINK)

body("**cf.** 커버리지 0.75–1.00 에서는 규칙 단독으로도 눈 0.516 · 피부 0.379 나옴. "
     "즉 농도 피처는 규칙을 대체하는 게 아니라 규칙이 비는 구간을 메우고 있음.")

h2("5-2. 문제: 역할 추정이 정확하지 않음")

body("역할은 성분명 정규식으로 찍고, 못 찍은 것 중 농도 1위만 `active_presumed` 로 올림. "
     "그 결과 성분행 5,287개 중 **3,153개(59.6%)가 여전히 역할 미상**이고 그 농도가 "
     "`f_pct_unknown` 한 열에 뭉쳐 들어감.")

body("→ 죽은 열이면 넘어갈 문제인데 중요도 눈 6.8% · 피부 6.8% 로 상위권임. 즉 모델은 "
     "**'이름을 규칙으로 못 읽은 성분의 질량'** 을 유용한 신호로 쓰고 있음. 역할 정규식 "
     "확장이 개선 후보가 되는 근거.", color=LINK)

# ============================================================ 6. 결함
h1("6. 확인된 설계 결함과 그 정리")

body("42열을 전수로 훑어 4건을 찾았고, **2026-09-21 결정으로 3건을 열 제외로 정리함**"
     "(합 11열, 42 → 31). 값은 한 칸도 고치지 않았음 — `input_dataset_v6.xlsx` 와 "
     "manifest 에는 11열이 그대로 있고 바뀐 것은 arm 선택자(`lib_model.FEAT_DROP`)뿐임.")

bullet("**완전 중복 4쌍 → 한쪽 4열 제외** — `ct_eye_ord`=`f_ct_eye_ord` 와 skin·sens "
       "3쌍, 그리고 `f_pct_active`=`f_pct_active_presumed`. 결측 위치까지 같았음. "
       "중요도가 반씩 쪼개져 표를 오독하게 만들던 원인이 사라졌고 **성능 비용 없음**")
bullet("**감작 CT 7열 → 6열 제외** — `ct_sens_ord` 는 위 중복쌍에서 이미 빠져 합이 "
       "11열이 됨. 감작을 학습 라벨에서 뺐는데 가산 디스크립터로는 피처에 남겨 둔 "
       "비대칭을 없앴음. 감작은 구분 1 만 나와서 `_s2` 가 없으므로 8열이 아니라 7열임. "
       "**이 1건에만 비용이 있음** — 아래 표 6")
bullet("**죽은 열 1개 → 제외** — `ct_not_applicable` 은 98.4%가 한 값 · 중요도 0.0% "
       "였음. **성능 비용 없음**")
bullet("**역할 추정 미상 59.6% → 이월** — 성분행 3,153/5,287 이 `unknown`. 열을 빼서 "
       "고칠 수 없음. `f_pct_unknown` 은 중요도 6.8% 의 유용한 신호이고 문제는 역할 "
       "정규식이 성분명을 못 읽는다는 것임 (7)")

h2("6-1. 제외 비용 — 재 봤음")

table(["지표", "눈 42열", "눈 31열", "눈 판정", "피부 42열", "피부 31열", "피부 판정"], [
    ["누출보정 AUC", "0.6580", "0.6510", "잡음 범위", "0.6470", "0.6388", "**손실**"],
    ["MCC", "0.3422", "0.2792", "**손실**", "0.2797", "0.2802", "잡음 범위"],
    ["BA", "0.6738", "0.6430", "**손실**", "0.6465", "0.6463", "잡음 범위"],
], widths=(3.0, 1.8, 1.8, 2.0, 2.0, 2.0, 2.2))
caption("표 6. 42열 → 31열 실측 비용. 판정 밴드는 시드 SD 2배. 단계별로 쪼개 보면 중복쌍 "
        "4열과 죽은 열 1열은 두 엔드포인트 모두 무비용이고, 비용 전체가 감작 CT 6열을 "
        "뺀 단계에서 나옴 (`04_모델산출물/v8_노선2정리/노선2정리_최종대조.csv`)")

body("**눈 MCC −0.063 은 실재하는 손실임.** τ 선택 방식 때문이 아님 — 고정 0.5 대조에서도 "
     "0.3412 → 0.2842(−0.057)로 같은 크기가 나옴. 라벨 출처가 완제품 SDS 와 겹치지 않는 "
     "`문서독립` 층(눈 n=608)에서도 MCC 0.2114 → 0.1668 로 줄어서, 감작 CT 가 라벨 문서를 "
     "우회 참조하던 것이 드러난 결과도 아님. 감작 CT 열이 **실제로 눈·피부 예측에 기여하고 "
     "있었다**는 뜻이고 그것을 원칙 때문에 포기함.")

body("→ **게이트 판정은 바뀌지 않음.** 누출보정 AUC 0.70 기준으로 42열도 31열도 눈·피부 "
     "모두 미달임. 이 결정은 결론을 바꾸지 않고 원칙만 정리함.", color=LINK)

body("**cf. 층별 수치를 유의성으로 읽지 않음.** 층 지표는 시드별로 쪼개지 않고 풀링한 "
     "OOF 예측에서 한 번 계산하므로 시드 SD 가 없음 — 대조표의 층 행은 잡음 범위가 "
     "0.0000 으로 찍히고 그래서 아무리 작은 차이도 '유의' 로 표시됨. 크기로만 읽어야 함. "
     "방향도 한결같지 않음 — 피부 `문서공유_분류절` 층은 MCC 가 0.2109 → 0.2498 로 오름.")

# ============================================================ 7. 다음
h1("7. 다음 판에서 손댈 순서")

bullet("**① 농도 결측 2,121행 확보** — 가산식이 구분과 농도를 둘 다 받아야 계산됨. "
       "구분만 채워도 성능 천장이 안 올라감")
bullet("**② 역할 정규식 확장** — 역할 미상 59.6% 가 한 열에 뭉쳐 있고 그 열이 상위권 (5-2)")
bullet("중복 4쌍 · 죽은 열 · 감작 CT 정리는 **이번 판에서 끝냈음** (6)")

body("→ 이번 회의에서 받고 싶은 결정: **L2단독 31열을 대표 피처셋으로 고정하는 것에 "
     "동의하는가**(3-3). 눈 MCC 0.063 을 내주고 '감작은 학습에 쓰지 않는다' 를 피처까지 "
     "맞춘 것이므로, 성능이 아니라 노선 전제에 걸린 결정임 (6-1).", color=LINK)

def 손수정_보관(out):
    """직전 빌드와 다른 내용이면 Word 에서 손으로 고친 것이므로 먼저 치워 둔다.

    이 스크립트를 다시 돌려 docx 를 덮어쓰다가 손으로 고친 판을 날린 적이 있다.
    지문(sha256)을 남겨 두고, 지문과 안 맞는 파일이 놓여 있으면 덮어쓰기 전에
    `손수정_보관/` 으로 사본을 뺀다. 빌드를 막지는 않는다 — 잃지만 않게 한다.
    """
    import hashlib
    import shutil
    from datetime import datetime

    지문 = out.with_name("." + out.name + ".빌드지문")
    if out.exists():
        현재 = hashlib.sha256(out.read_bytes()).hexdigest()
        직전 = 지문.read_text().strip() if 지문.exists() else None
        if 직전 is not None and 현재 != 직전:
            보관 = out.parent / "손수정_보관"
            보관.mkdir(exist_ok=True)
            때 = datetime.fromtimestamp(out.stat().st_mtime).strftime("%y%m%d_%H%M%S")
            사본 = 보관 / f"{out.stem}(손수정_{때}){out.suffix}"
            shutil.copy2(out, 사본)
            print(f"!! 손으로 고친 판을 발견해 먼저 빼 둠 → {사본.relative_to(L.ROOT)}")
        elif 직전 is None:
            # 지문이 없으면 손수정본인지 알 수 없다. 모르는 쪽을 날리지 않는다
            보관 = out.parent / "손수정_보관"
            보관.mkdir(exist_ok=True)
            때 = datetime.fromtimestamp(out.stat().st_mtime).strftime("%y%m%d_%H%M%S")
            사본 = 보관 / f"{out.stem}(지문없음_{때}){out.suffix}"
            shutil.copy2(out, 사본)
            print(f"!! 빌드지문이 없어 판별 못 함 — 사본을 빼 둠 → {사본.relative_to(L.ROOT)}")
    return 지문


지문 = 손수정_보관(OUT)
doc.save(OUT)
import hashlib as _h

지문.write_text(_h.sha256(OUT.read_bytes()).hexdigest() + "\n")
print(f"→ {OUT.relative_to(L.ROOT)}")
