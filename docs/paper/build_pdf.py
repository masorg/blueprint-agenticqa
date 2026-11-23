"""Typeset docs/paper/draft.md as an IEEE conference paper (IEEEtran) and report the page count.

    python docs/paper/build_pdf.py            # builds docs/paper/ieee/main.pdf
    python docs/paper/build_pdf.py --no-draft # skip regenerating draft.md first

Needs `pandoc`, `tectonic` and `pdfinfo` (brew install pandoc tectonic poppler). The Markdown draft stays the single
source: numbers still come from build_draft.py, and this script only changes the typesetting.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "ieee"

PREAMBLE = r"""\documentclass[conference]{IEEEtran}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{booktabs,tabularx,array}
\usepackage{fontspec}
\setmainfont{texgyretermes}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setmonofont{texgyrecursor}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic,Scale=0.88]
\usepackage{xurl}
% The text refers to "Section 5.1", so number sections 1, 1.1 rather than I, I-A.
\renewcommand{\thesection}{\arabic{section}}
\renewcommand{\thesubsection}{\thesection.\arabic{subsection}}
\renewcommand{\thesectiondis}{\arabic{section}.}
\renewcommand{\thesubsectiondis}{\thesection.\arabic{subsection}}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}
\usepackage{algorithm}
\usepackage{algpseudocode}
\usepackage{etoolbox}
\AtBeginEnvironment{verbatim}{\footnotesize}
\sloppy
\setlength{\emergencystretch}{2em}
\graphicspath{{./}}
"""


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r


def pandoc(md: str, *, filt: bool = False, extra: list[str] | None = None) -> str:
    cmd = ["pandoc", "-f", "markdown+tex_math_dollars", "-t", "latex", "--wrap=none"]
    if filt:
        cmd += ["--lua-filter", str(OUT / "filter.lua"), "--shift-heading-level-by=-1"]
    cmd += extra or []
    return subprocess.run(cmd, input=md, capture_output=True, text=True, check=True).stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-draft", action="store_true")
    args = ap.parse_args()
    if not args.no_draft:
        run([sys.executable, str(HERE / "build_draft.py")], cwd=HERE)

    text = re.sub(r"<!--.*?-->", "", (HERE / "draft.md").read_text(), flags=re.S)
    title = re.search(r"^# (.+)$", text, flags=re.M).group(1).strip()
    am = re.search(r"## Abstract\s+(.*?)\n\*\*Index Terms:\*\*", text, flags=re.S)  # optional while drafting
    abstract = am.group(1).strip() if am else None
    km = re.search(r"\*\*Index Terms:\*\*\s*(.+)", text)
    keywords = km.group(1).strip() if km else None
    first = re.search(r"^## (\d+)\.", text, flags=re.M)  # first numbered section; keep its number
    ref_at = text.find("## References")
    body = text[first.start():ref_at if ref_at >= 0 else len(text)]
    start_section = int(first.group(1)) - 1
    tab = re.search(r"\*\*Table (\d+)\.\*\*", body)
    fig = re.search(r"!\[Figure (\d+)\.", body)
    refs_block = text[ref_at:] if ref_at >= 0 else ""
    refs = re.findall(r"^\[(\d+)\] (.+)$", refs_block, flags=re.M)

    sup = str.maketrans("⁻⁰¹²³⁴⁵⁶⁷⁸⁹", "-0123456789")  # the Times clone has no superscript glyphs
    body = re.sub(r"×10([⁻⁰¹²³⁴⁵⁶⁷⁸⁹]+)", lambda m: "$\\times 10^{" + m.group(1).translate(sup) + "}$", body)
    body_tex = pandoc(body, filt=True)
    abstract_tex = pandoc(abstract).strip() if abstract else None
    ref_tex = [pandoc(t).strip() for _, t in refs]
    bib = "\n".join(f"\\bibitem{{r{n}}} {t}" for (n, _), t in zip(refs, ref_tex))

    main = (
        PREAMBLE
        + "\\begin{document}\n"
        + f"\\title{{{title}}}\n"
        + "\\author{\\IEEEauthorblockN{[AUTHOR: names]}\\IEEEauthorblockA{[AUTHOR: affiliations and emails]}}\n"
        + "\\maketitle\n"
        + (f"\\begin{{abstract}}\n{abstract_tex}\n\\end{{abstract}}\n" if abstract_tex else "")
        + (f"\\begin{{IEEEkeywords}}\n{keywords}\n\\end{{IEEEkeywords}}\n" if keywords else "")
        + f"\\setcounter{{section}}{{{start_section}}}\n"  # sections keep the numbers the text refers to
        + (f"\\setcounter{{table}}{{{int(tab.group(1)) - 1}}}\n" if tab else "")
        + (f"\\setcounter{{figure}}{{{int(fig.group(1)) - 1}}}\n" if fig else "")
        + body_tex
        + (f"\n\\begin{{thebibliography}}{{99}}\n{bib}\n\\end{{thebibliography}}\n" if refs else "")
        + "\\end{document}\n"
    )
    OUT.mkdir(exist_ok=True)
    (OUT / "main.tex").write_text(main)
    r = subprocess.run(["tectonic", "-X", "compile", "--keep-logs", "main.tex"], cwd=OUT, capture_output=True, text=True)
    (OUT / "build.log").write_text(r.stdout + "\n" + r.stderr)
    if r.returncode != 0:
        sys.exit(f"tectonic failed (see {OUT / 'build.log'}):\n" + (r.stderr or r.stdout)[-2500:])
    pdf = OUT / "main.pdf"
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    pages = re.search(r"^Pages:\s+(\d+)", info, flags=re.M)
    print(f"wrote {pdf} ({pages.group(1) if pages else '?'} pages, US letter; limit is 10 + 2 for references)")


if __name__ == "__main__":
    main()
