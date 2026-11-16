"""Turn course documents into Markdown that an AI assistant can read cheaply.

Engine order:
  1. Filee (filee-cli.exe) if installed - fast, offline, keeps slide images.
  2. markitdown (pip install markitdown[all]) as a fallback on machines without Filee.
Jupyter notebooks are converted natively (no engine needed).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

CONVERTIBLE = {".pdf", ".pptx", ".docx", ".xlsx", ".ppt", ".doc", ".xls", ".rtf", ".odt", ".odp"}


def find_filee(configured: str | None = None) -> Path | None:
    cands = []
    if configured:
        cands.append(Path(configured))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(Path(local) / "Programs" / "Filee" / "filee-cli.exe")
    for pf in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if pf:
            cands.append(Path(pf) / "Filee" / "filee-cli.exe")
    w = shutil.which("filee-cli") or shutil.which("filee")
    if w:
        cands.append(Path(w))
    for c in cands:
        if c and c.exists():
            return c
    return None


def notebook_to_md(src: Path, dst: Path):
    nb = json.loads(src.read_text(encoding="utf-8", errors="replace"))
    out = [f"# {src.stem}\n", f"> 由 Jupyter Notebook 自动转换：`{src.name}`\n"]
    lang = (nb.get("metadata", {}).get("kernelspec", {}) or {}).get("language", "python")
    for cell in nb.get("cells", []):
        text = "".join(cell.get("source", []))
        if not text.strip():
            continue
        if cell.get("cell_type") == "markdown":
            out.append(text + "\n")
        elif cell.get("cell_type") == "code":
            out.append(f"```{lang}\n{text}\n```\n")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(out), encoding="utf-8")


class Converter:
    def __init__(self, filee_path: str | None = None, log=print):
        self.filee = find_filee(filee_path)
        self.log = log
        self._markitdown = None
        if not self.filee:
            try:
                from markitdown import MarkItDown  # type: ignore
                self._markitdown = MarkItDown()
            except Exception:
                pass

    @property
    def engine(self) -> str:
        if self.filee:
            return f"Filee ({self.filee})"
        if self._markitdown:
            return "markitdown"
        return "none (只能转换 .ipynb；请安装 Filee 或 pip install markitdown[all])"

    def needed(self, src: Path, dst: Path) -> bool:
        return not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime

    def convert_many(self, jobs: list[tuple[Path, Path]]) -> dict[Path, str]:
        """jobs: (source, destination .md). Returns {source: 'ok' | error}."""
        result: dict[Path, str] = {}
        filee_jobs: dict[Path, list[tuple[Path, Path]]] = {}
        for src, dst in jobs:
            if not self.needed(src, dst):
                result[src] = "ok"
                continue
            ext = src.suffix.lower()
            if ext in (".html", ".htm"):
                try:
                    from markdownify import markdownify
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(src.read_text(encoding="utf-8", errors="replace"), "html.parser")
                    for t in soup.select("script, style"):
                        t.decompose()
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(markdownify(str(soup), heading_style="ATX"), encoding="utf-8")
                    result[src] = "ok"
                except Exception as e:
                    result[src] = f"html error: {e}"
            elif ext == ".ipynb":
                try:
                    notebook_to_md(src, dst)
                    result[src] = "ok"
                except Exception as e:
                    result[src] = f"notebook error: {e}"
            elif ext in CONVERTIBLE and self.filee:
                filee_jobs.setdefault(dst.parent, []).append((src, dst))
            elif ext in CONVERTIBLE and self._markitdown:
                try:
                    md = self._markitdown.convert(str(src)).text_content
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    dst.write_text(md, encoding="utf-8")
                    result[src] = "ok"
                except Exception as e:
                    result[src] = f"markitdown error: {e}"
            else:
                result[src] = "unsupported"
        for outdir, items in filee_jobs.items():
            outdir.mkdir(parents=True, exist_ok=True)
            # Filee names the output <stem>.md next to output folder; convert in batches.
            for i in range(0, len(items), 20):
                batch = items[i : i + 20]
                cmd = [str(self.filee), "convert", *[str(s) for s, _ in batch],
                       "--to", "md", "-o", str(outdir), "--overwrite", "--json", "--quiet"]
                try:
                    cp = subprocess.run(cmd, capture_output=True, timeout=1800)
                    out = cp.stdout.decode("utf-8", errors="replace")
                    data = json.loads(out[out.find("{"):]) if "{" in out else {}
                    states = {Path(f["source"]).resolve(): f for f in data.get("files", [])}
                except Exception as e:
                    states = {}
                    err = str(e)
                else:
                    err = cp.stderr.decode("utf-8", errors="replace")[-300:]
                for src, dst in batch:
                    f = states.get(src.resolve())
                    if f and f.get("state") == "done":
                        produced = [Path(o) for o in f.get("outputs", [])]
                        md = next((o for o in produced if o.suffix == ".md"), None)
                        if md and md.resolve() != dst.resolve() and md.exists():
                            os.replace(md, dst)
                        result[src] = "ok"
                    else:
                        result[src] = f"filee failed: {(f or {}).get('error') or err or 'unknown'}"
        return result
