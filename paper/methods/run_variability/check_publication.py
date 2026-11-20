"""Compile proposed snippets in a temporary manuscript-style document."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile

BASE = Path(__file__).resolve().parent
PAPER = Path("external/manuscript")
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, default=BASE/"results_v1")
    args = parser.parse_args()
    out = args.results.resolve()
    manifest = json.loads((out/"input_manifest.json").read_text())
    assert all(sha(p)==h for p,h in manifest["protected_sha256"].items())
    work = Path(tempfile.mkdtemp(prefix="libstruct-execution-variability-latex-"))
    (work/"figures").mkdir()
    for filename in ["supplementary_section.tex", "section_3_2_paragraph.tex"]:
        shutil.copy2(out/filename, work/filename)
    figure = "figS_codex_execution_variability.pdf"
    shutil.copy2(out/figure, work/"figures"/figure)
    shutil.copy2(PAPER/"neurips_2026.sty", work/"neurips_2026.sty")
    # Reuse previously obtained TeX font resources only in the temporary build.
    old = BASE.parent/"camera_ready_v4_7_0/sensitivity/publication_20261017/compile_report.json"
    fonts = {}
    if old.exists():
        prior = Path(json.loads(old.read_text())["temporary_directory"])
        for path in prior.iterdir():
            if path.suffix in {".tfm", ".vf", ".pfb", ".enc", ".map", ".otf"}:
                shutil.copy2(path, work/path.name)
                fonts[path.name] = sha(path)
    preamble = (PAPER/"main.tex").read_text().split(r"\title{",1)[0]
    body = r"""
\begin{document}
\appendix
\input{supplementary_section.tex}
\clearpage
\paragraph{Suggested Section 3.2 insertion (review copy only).}
\input{section_3_2_paragraph.tex}
\end{document}
"""
    (work/"publication_check.tex").write_text(preamble+body)
    command = [shutil.which("tectonic"), "--only-cached", "--keep-logs",
               "--keep-intermediates", "publication_check.tex"]
    result = subprocess.run(command, cwd=work, capture_output=True, text=True)
    (out/"latex_layout_check.log").write_text(result.stdout+result.stderr)
    logpath = work/"publication_check.log"
    log = logpath.read_text() if logpath.exists() else ""
    report = {
        "command": command, "returncode": result.returncode,
        "temporary_directory": str(work),
        "manuscript_source_unchanged": sha(PAPER/"main.tex")==manifest["protected_sha256"][str(PAPER/"main.tex")],
        "unresolved_references": re.findall(r"Reference .+ undefined", log),
        "overfull_boxes": re.findall(r"Overfull .+", log),
        "errors": re.findall(r"^!.*", log, re.M),
        "temporary_font_hashes": fonts,
        "scope": "Standalone temporary document using the paper preamble/style, not a modified main.tex.",
    }
    if result.returncode==0:
        import fitz
        pdf = fitz.open(work/"publication_check.pdf")
        report["pages"] = len(pdf)
        report["preview_paths"] = []
        for i, page in enumerate(pdf):
            dest = work/f"page-{i+1}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.6,1.6)).save(dest)
            report["preview_paths"].append(str(dest))
    (out/"latex_layout_check.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k!="temporary_font_hashes"},indent=2))
    assert result.returncode==0 and not report["errors"] and not report["unresolved_references"]
    assert not report["overfull_boxes"]

if __name__=="__main__": main()
