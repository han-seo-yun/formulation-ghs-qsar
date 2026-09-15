#!/usr/bin/env python3
"""현황 리포트 docx — 산출: 04_모델산출물/v8_리포트/*.docx

`04_모델산출물/v8_리포트/모델개발_데이터현황_리포트.md` 를 읽어 그대로 옮긴다.
**본문을 이 스크립트에 다시 적지 않는다** — 두 곳에 같은 문장이 있으면 한쪽만
고쳐지고, 그 순간 어느 쪽이 정본인지 알 수 없게 된다.

문서 양식은 `동물의약품_데이터분석보고서_260914_만성데이터검토` 를 따른다.
앞판의 260908 양식(파란 머리글 · 회색 문서정보 띠 · 색칠한 표 머리)과 다른 점:

- 본문 Apple SD Gothic Neo 12pt · 머리글 1 16pt / 2 14pt, **전부 검정**
- 여백 사방 2.54 cm
- 문서정보 줄은 띠가 아니라 그냥 한 줄. 라벨(`문서 정보`·`작성일`·`작성`)만 굵게
- 항목은 `**1. 제목**` 첫 줄만 굵게 두고 설명을 **같은 단락 안에서** 줄바꿈으로 잇는다
  (머리글을 늘리지 않고 목차를 얕게 유지하는 방식)
- 표는 `Light Grid Accent 1` — 칸 색칠을 직접 하지 않는다. 캡션은 **표 아래**
- 그림·표 캡션은 `Image Caption`(가운데, 10pt 굵게), `→` 결론줄은 파랑 000EFA
"""
from __future__ import annotations

import re

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

import lib_model as L

SRC = L.ROOT / "04_모델산출물" / "v8_리포트" / "모델개발_데이터현황_리포트.md"
OUT = SRC.with_suffix(".docx")

KO = "Apple SD Gothic Neo"
MONO = "Menlo"
BLACK = RGBColor(0x00, 0x00, 0x00)
LINK = RGBColor(0x00, 0x0E, 0xFA)      # `→` 결론줄
TABLE_STYLE = "Light Grid Accent 1"

doc = Document()
for nm, sz, bold in (("Normal", 12, False), ("Heading 1", 16, True),
                     ("Heading 2", 14, True), ("Heading 3", 12, True),
                     ("Body Text", 12, False), ("List Bullet", 10, False)):
    s = doc.styles[nm]
    s.font.name = KO
    s.font.size = Pt(sz)
    s.font.bold = bold
    s.font.color.rgb = BLACK
    s._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
for nm in ("Normal", "Body Text"):
    pf = doc.styles[nm].paragraph_format
    pf.space_after = Pt(6)
    pf.line_spacing = 1.3
# 참조 문서의 캡션 스타일. 기본 템플릿에 없으므로 만들어 둔다
cap = doc.styles.add_style("Image Caption", WD_STYLE_TYPE.PARAGRAPH)
cap.base_style = doc.styles["Normal"]
cap.font.name = KO
cap.font.size = Pt(10)
cap.font.bold = True
cap.font.color.rgb = BLACK
cap._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
cap.paragraph_format.space_after = Pt(14)
for sec in doc.sections:
    sec.left_margin = sec.right_margin = Cm(2.54)
    sec.top_margin = sec.bottom_margin = Cm(2.54)


def runs(p, text, size=12, color=None, force_bold=False):
    """`**굵게**` · `` `코드` `` 만 해석한다. `\\n` 은 같은 단락 안 줄바꿈이다."""
    for tok in re.split(r"(\*\*.+?\*\*|`[^`]+`|\n)", text):
        if not tok:
            continue
        if tok == "\n":
            p.add_run().add_break()
            continue
        code = tok.startswith("`")
        r = p.add_run(tok.strip("*`") if tok[:1] in "*`" else tok)
        r.font.name = MONO if code else KO
        r.font.size = Pt(11 if code else size)
        r._element.rPr.rFonts.set(qn("w:eastAsia"), KO)
        r.bold = force_bold or tok.startswith("**")
        if color is not None:
            r.font.color.rgb = color
    return p


def caption(text):
    doc.add_paragraph(text, style="Image Caption")


def table(head, rows):
    t = doc.add_table(rows=1, cols=len(head))
    t.style = TABLE_STYLE          # 칸 색칠은 스타일에 맡긴다
    for cell, h in zip(t.rows[0].cells, head):
        runs(cell.paragraphs[0], h, size=11)
    for row in rows:
        for j, (cell, v) in enumerate(zip(t.add_row().cells, row)):
            p = cell.paragraphs[0]
            p.alignment = (WD_ALIGN_PARAGRAPH.LEFT if j == 0
                           else WD_ALIGN_PARAGRAPH.CENTER)
            runs(p, v, size=11)


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


SPECIAL = ("|", "# ", "## ", "### ", "![", "→ ", "- ", "문서 정보", "①")


def plain(ln):
    """항목 본문 줄인가 — 특수 접두어가 없고 `*캡션*` 도 아니면 그렇다."""
    if not ln or ln == "---" or ln.startswith(SPECIAL):
        return False
    return not (ln.startswith("*") and ln.endswith("*")
                and not ln.startswith("**"))


# ------------------------------------------------------------------ 본문 변환
lines = SRC.read_text(encoding="utf-8").split("\n")
i = 0
while i < len(lines):
    ln = lines[i].rstrip()
    nxt = lines[i + 1].rstrip() if i + 1 < len(lines) else ""

    # 표 — 머리 + 구분줄 + 본문. 구분줄이 없으면 표가 아니다
    if ln.startswith("|") and nxt.startswith("|") \
            and set(nxt.replace("|", "").strip()) <= {"-", ":", " "}:
        head, i = cells(ln), i + 2
        body = []
        while i < len(lines) and lines[i].startswith("|"):
            body.append(cells(lines[i]))
            i += 1
        table(head, body)
        continue

    if not ln or ln == "---":
        i += 1
        continue

    if ln.startswith("# "):
        # 표제. 머리글 1 을 절 번호에 쓰므로 표제는 직접 꾸민다
        p = runs(doc.add_paragraph(), ln[2:], size=20, force_bold=True)
        p.paragraph_format.space_after = Pt(12)
    elif ln.startswith("### "):
        doc.add_paragraph(ln[4:], style="Heading 2") \
           .paragraph_format.space_before = Pt(12)
    elif ln.startswith("## "):
        doc.add_paragraph(ln[3:], style="Heading 1") \
           .paragraph_format.space_before = Pt(18)
    elif ln.startswith("문서 정보"):
        # 라벨만 굵게. `문서 정보 | 작성일 2026-09-14 | 작성 한서윤`
        p = doc.add_paragraph()
        for k, seg in enumerate(ln.split("|")):
            seg = seg.strip()
            lab, _, val = seg.partition(" ")
            if k:
                runs(p, "  |  ")
            runs(p, lab, force_bold=True)
            if val:
                runs(p, " " + val)
        p.paragraph_format.space_after = Pt(2)
    elif ln.startswith("①"):
        p = runs(doc.add_paragraph(style="Body Text"), ln, force_bold=True)
        p.paragraph_format.space_after = Pt(16)
    elif ln.startswith("!["):
        doc.add_picture(str(SRC.parent / re.search(r"\((.+?)\)", ln).group(1)),
                        width=Cm(15.5))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    elif ln.startswith("*") and ln.endswith("*") and not ln.startswith("**"):
        caption(ln.strip("*"))
    elif ln.startswith("→ "):
        runs(doc.add_paragraph(style="Body Text"), ln, color=LINK)
    elif ln.startswith("- "):
        p = doc.add_paragraph(style="List Bullet")
        runs(p, ln[2:], size=11)
        p.paragraph_format.space_after = Pt(3)
    else:
        # 항목 한 덩어리 = 한 단락. 이어지는 본문 줄을 같은 단락에 줄바꿈으로 붙인다
        buf = [ln]
        while i + 1 < len(lines) and plain(lines[i + 1].rstrip()):
            i += 1
            buf.append(lines[i].rstrip())
        runs(doc.add_paragraph(style="Body Text"), "\n".join(buf))
    i += 1

doc.save(OUT)
print(f"→ {OUT.relative_to(L.ROOT)}")
