"""내부 점검본(`07_화학연구원_공유_260918/`)에서 **배포본**만 뽑아 낸다.

배포본에 넣는 것은 두 가지다 — 제품코드별 성분 리스트와 MSDS 하이라이트 사본.
그 외는 넣지 않는다.

  - 시트는 `제형`·`성분` 둘뿐이다. 요약·원문대조·문서불일치·데이터정정후보·검토필요
    ·값출처·기준차이·데이터_이슈는 우리 점검용이라 뺀다
  - 열은 **v6 원값 + 근거파일 경로**뿐이다. 재수집 판정·원문확인 수·정정 후보·성분명
    정규화·농도 환산검증 같은 **우리 판정 열은 전부 뺀다**. 검수는 받는 쪽이 한다
  - md·csv·json 은 한 개도 넣지 않는다

하이라이트 사본은 점검본 것을 그대로 쓴다 — 값을 원문에서 확인한 뒤 필드별 3색으로
칠하고 주석에 공유값을 병기한 그 파일이다. 경로 이름(`MSDS_하이라이트/`,
`대체근거_참고/`)도 그대로 둬야 시트의 `근거파일` 값이 그대로 열린다.

    python3 build_share_dist.py
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import fitz
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_model as L                                            # noqa: E402

SRC = L.ROOT / "07_화학연구원_공유_260918"
OUT = L.ROOT / "08_화학연구원_배포_260920"
XLSX = "제품코드별_성분리스트.xlsx"
DOCDIRS = ("MSDS_하이라이트", "대체근거_참고")
# 로그는 main() 에서 연다 — 다른 스크립트가 relabel() 만 import 해도 로그가 비워지면 안 된다
log = print

# v6 원값 + 근거파일 경로. 오른쪽은 배포본에서 쓸 이름이다
FORM_COLS = {
    "Formulation_ID": "Formulation_ID", "product_name": "product_name",
    "formulation_type_ko": "formulation_type_ko",
    "y_eye": "y_eye", "y_skin": "y_skin", "y_sens": "y_sens",
    "ph_best": "ph_best", "대조_근거파일": "근거파일",
}
ING_COLS = {
    "Formulation_ID": "Formulation_ID", "ing_idx": "ing_idx",
    "ing_name_best": "ing_name_best", "ing_cas_best": "ing_cas_best",
    "ing_pct_best": "ing_pct_best", "ing_pct_kind_best": "ing_pct_kind_best",
    "ing_role_final": "ing_role_final",
    "ing_ghs_eye": "ing_ghs_eye", "ing_ghs_skin": "ing_ghs_skin",
    "ing_ghs_sens": "ing_ghs_sens",
    "ing_ghs_indep_eye_cat": "ing_ghs_indep_eye_cat",
    "ing_ghs_indep_eye_tier": "ing_ghs_indep_eye_tier",
    "ing_ghs_indep_skin_cat": "ing_ghs_indep_skin_cat",
    "ing_ghs_indep_skin_tier": "ing_ghs_indep_skin_tier",
    "ing_ghs_indep_sens_cat": "ing_ghs_indep_sens_cat",
    "ing_ghs_indep_sens_tier": "ing_ghs_indep_sens_tier",
    "ing_src": "ing_src", "대조_근거파일": "근거파일",
}


def pick(sheet: str, cols: dict[str, str]) -> pd.DataFrame:
    d = pd.read_excel(SRC / "02_제품코드별_성분리스트.xlsx", sheet_name=sheet)
    missing = [c for c in cols if c not in d.columns]
    if missing:
        raise SystemExit(f"{sheet} 시트에 없는 열: {missing}")
    dropped = [c for c in d.columns if c not in cols]
    log(f"{sheet}: {len(d):,}행 · 남긴 열 {len(cols)} · 뺀 열 {len(dropped)} — "
        f"{', '.join(dropped)}")
    return d[list(cols)].rename(columns=cols)


NORM_RE = re.compile(r"성분명\(정규화\)=원문「(.*?)」 → 공유값 (.*?) / 이전값「(.*?)」")


def relabel(root: Path, ING: pd.DataFrame) -> tuple[int, int]:
    """정규화 58행의 주석 문구를 **배포본 엑셀에 실제로 들어간 이름** 기준으로 고쳐 쓴다.

    점검본 주석은 정규화한 이름을 `공유값`, v6 원값을 `이전값` 으로 적는다. 배포본
    엑셀은 v6 원값만 싣기 때문에 그대로 두면 받는 쪽이 여는 문서에서 `공유값` 이
    엑셀에 없는 이름을 가리킨다. 문구만 바꾸고 색칸 좌표·색은 건드리지 않는다
    (`update()` 를 부르지 않아 모양 스트림을 다시 만들지 않는다).

    잃는 정보는 없다 — 정규화한 이름은 애초에 **그 문서에 적힌 글자**라서
    `원문「…」` 에 그대로 남는다."""
    key = {(r.Formulation_ID, r.ing_idx): str(r.ing_name_best)
           for r in ING.itertuples()}
    bad, n_pdf, n_annot = [], 0, 0

    def new_text(m: re.Match, fid: str, idx: str) -> str:
        src, _, prev = m.group(1), m.group(2), m.group(3)
        # 엑셀 값과 다른 이름을 공유값으로 적는 실수를 반복하지 않도록 대조한다
        want = key.get((fid, int(idx)))
        if want is not None and want != prev:
            bad.append(f"{fid} #{idx}: 주석 이전값「{prev}」≠ 엑셀 「{want}」")
        return (f"성분명=원문「{src}」 → 공유값「{prev}」 · 표기 다름(같은 CAS)")

    for p in sorted(root.rglob("*")):
        if p.suffix.lower() == ".pdf":
            try:
                doc = fitz.open(p)
            except Exception:
                continue
            hit = 0
            for pg in doc:
                for a in pg.annots() or []:
                    c = a.info.get("content", "")
                    m = NORM_RE.search(c)
                    if not m:
                        continue
                    h = re.match(r"\[(\S+) #(\d+)\]", c)
                    fid, idx = (h.group(1), h.group(2)) if h else ("", "0")
                    a.set_info(content=c[:m.start()] + new_text(m, fid, idx)
                               + c[m.end():])
                    hit += 1
            if hit:
                doc.save(p, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
                n_pdf, n_annot = n_pdf + 1, n_annot + hit
            doc.close()
        elif p.suffix.lower() in (".html", ".htm"):
            t = p.read_text("utf-8", "ignore")
            if "성분명(정규화)" not in t:
                continue
            out, last = [], 0
            for m in NORM_RE.finditer(t):
                h = re.search(r"\[(\S+) #(\d+)\]", t[max(0, m.start() - 80):m.start()])
                out.append(t[last:m.start()])
                out.append(new_text(m, h.group(1) if h else "", h.group(2) if h else "0"))
                last = m.end()
                n_annot += 1
            p.write_text("".join(out) + t[last:], "utf-8")
            n_pdf += 1
    if bad:
        raise SystemExit(f"주석과 엑셀 이름이 어긋난다 {len(bad)}건: {bad[:3]}")
    return n_pdf, n_annot


def main() -> None:
    global log
    log = L.make_logger(L.ROOT / "01_파이프라인" / "배포본.log")
    FORM, ING = pick("제형", FORM_COLS), pick("성분", ING_COLS)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    with pd.ExcelWriter(OUT / XLSX, engine="openpyxl") as w:
        FORM.to_excel(w, sheet_name="제형", index=False)
        ING.to_excel(w, sheet_name="성분", index=False)

    for d in DOCDIRS:
        shutil.copytree(SRC / d, OUT / d)
        log(f"{d}/ — 제형 {len(list((OUT / d).iterdir())):,}개 · "
            f"파일 {sum(1 for _ in (OUT / d).rglob('*') if _.is_file()):,}건")

    n_pdf, n_annot = relabel(OUT, ING)
    log(f"정규화 주석 문구 교체 — 문서 {n_pdf}건 · 주석 {n_annot}개")

    # 검사 — 하나라도 어긋나면 배포하지 않는다
    left = [p.name for p in OUT.rglob("*") if p.suffix.lower() in
            (".html", ".htm") and "성분명(정규화)" in p.read_text("utf-8", "ignore")]
    if left:
        raise SystemExit(f"HTML 에 옛 문구가 남았다: {left}")
    bad_ext = sorted({p.suffix.lower() for p in OUT.rglob("*") if p.is_file()}
                     - {".pdf", ".html", ".htm", ".xlsx"})
    if bad_ext:
        raise SystemExit(f"배포본에 있어서는 안 되는 확장자: {bad_ext}")
    sheets = pd.ExcelFile(OUT / XLSX).sheet_names
    if sheets != ["제형", "성분"]:
        raise SystemExit(f"시트 구성이 다르다: {sheets}")
    # 한 칸에 `|` 로 여러 경로가 들어 있는 행이 있다 (대체근거가 성분마다 따로 붙는 경우)
    paths = {q.strip() for p in pd.concat([FORM.근거파일, ING.근거파일]).dropna().unique()
             for q in str(p).split("|") if q.strip()}
    miss = [p for p in sorted(paths) if not (OUT / p).exists()]
    if miss:
        raise SystemExit(f"근거파일 경로가 배포본 안에서 안 열린다 {len(miss)}건: {miss[:3]}")
    n_file = sum(1 for p in OUT.rglob("*") if p.is_file())
    log("")
    log(f"제형 {len(FORM):,}행(근거파일 {int(FORM.근거파일.notna().sum()):,}) · "
        f"성분 {len(ING):,}행(근거파일 {int(ING.근거파일.notna().sum()):,})")
    log(f"완료 → {OUT.name}/ (파일 {n_file:,}건 · "
        f"{sum(p.stat().st_size for p in OUT.rglob('*') if p.is_file()) / 1024 ** 2:.0f} MB)")
    print(f"→ {OUT.name}")


if __name__ == "__main__":
    main()
