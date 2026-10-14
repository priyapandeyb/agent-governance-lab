#!/usr/bin/env python3
"""Render all diagrams and codelab Markdown tables to high-resolution JPEG files.

Outputs:
- codelab/img/<diagram>.jpg (and docs/diagrams/<diagram>.jpg) for every SVG diagram
  plus codelab/img/healthcare-gateway-hero.jpg
- codelab/img/tables/table-XX-<slug>.jpg (and .svg) for every Markdown table in
  codelab/codelab.md, plus codelab/img/tables/INDEX.md mapping each table to its file.
"""

from __future__ import annotations

import html
import pathlib
import re
import shutil
import subprocess
import tempfile
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent
CODELAB_MD = ROOT / "codelab" / "codelab.md"
CODELAB_IMG = ROOT / "codelab" / "img"
DOCS_DIAGRAMS = ROOT / "docs" / "diagrams"
TABLES_DIR = CODELAB_IMG / "tables"


def _parse_viewbox(svg_text: str) -> tuple[float, float]:
    m = re.search(r'viewBox="\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+([\d.]+)\s*"', svg_text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return 1200.0, 800.0


def _normalize_svg_for_qlmanage(svg_text: str) -> str:
    """Remove fixed width/height on the root <svg> tag so qlmanage fits the full viewBox."""

    def repl(match: re.Match) -> str:
        tag = match.group(0)
        tag = re.sub(r'\s+width="[^"]*"', "", tag)
        tag = re.sub(r'\s+height="[^"]*"', "", tag)
        return tag

    return re.sub(r"<svg\b[^>]*>", repl, svg_text, count=1)


def svg_or_png_to_jpg(src: pathlib.Path, dst_jpg: pathlib.Path, size: int = 2200) -> None:
    """Convert an SVG or PNG file to a tightly cropped, high-resolution JPEG."""
    dst_jpg.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = pathlib.Path(tmpdir)
        if src.suffix.lower() == ".svg":
            svg_raw = src.read_text()
            vb_w, vb_h = _parse_viewbox(svg_raw)
            norm_svg = tmp / src.name
            norm_svg.write_text(_normalize_svg_for_qlmanage(svg_raw))
            subprocess.run(
                ["qlmanage", "-t", "-s", str(size), "-o", str(tmp), str(norm_svg)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            png_path = tmp / f"{norm_svg.name}.png"
            if not png_path.exists():
                candidates = list(tmp.glob("*.png"))
                if not candidates:
                    raise RuntimeError(f"qlmanage did not produce a PNG for {src}")
                png_path = candidates[0]

            # Crop square letterbox added by qlmanage to match viewBox aspect ratio
            if vb_w >= vb_h:
                crop_w = size
                crop_h = max(1, int(round(size * (vb_h / vb_w))))
            else:
                crop_h = size
                crop_w = max(1, int(round(size * (vb_w / vb_h))))
            subprocess.run(
                ["sips", "--cropToHeightWidth", str(crop_h), str(crop_w), str(png_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            png_path = src

        subprocess.run(
            [
                "sips",
                "-s",
                "format",
                "jpeg",
                "-s",
                "formatOptions",
                "95",
                str(png_path),
                "--out",
                str(dst_jpg),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def strip_md_inline(text: str) -> str:
    """Clean Markdown inline formatting for plain text measurement/rendering."""
    text = text.strip()
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    return text


def parse_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def extract_tables(md_text: str) -> list[dict]:
    lines = md_text.splitlines()
    tables = []
    cur_h2 = "Overview"
    cur_h3 = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            cur_h2 = line[3:].strip()
            cur_h3 = ""
        elif line.startswith("### "):
            cur_h3 = line[4:].strip()
        elif (
            line.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|[\s\-:|]+\|$", lines[i + 1].strip())
        ):
            start_line = i + 1
            raw_rows = []
            while i < len(lines) and lines[i].startswith("|"):
                raw_rows.append(lines[i])
                i += 1
            headers = parse_row(raw_rows[0])
            body = [parse_row(r) for r in raw_rows[2:]]
            ncols = len(headers)
            norm_body = []
            for r in body:
                if len(r) < ncols:
                    r = r + [""] * (ncols - len(r))
                elif len(r) > ncols:
                    r = r[:ncols]
                norm_body.append(r)
            tables.append(
                {
                    "index": len(tables) + 1,
                    "line": start_line,
                    "h2": cur_h2,
                    "h3": cur_h3,
                    "headers": headers,
                    "rows": norm_body,
                }
            )
            continue
        i += 1
    return tables


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    return text[:48].strip("-") or "table"


def render_table_svg(table: dict) -> str:
    headers = [strip_md_inline(h) for h in table["headers"]]
    rows = [[strip_md_inline(c) for c in r] for r in table["rows"]]
    ncols = len(headers)

    col_weights = []
    for c in range(ncols):
        lengths = [len(headers[c])] + [len(r[c]) for r in rows]
        max_len = max(lengths) if lengths else 10
        avg_len = sum(lengths) / max(1, len(lengths))
        weight = max(10.0, (max_len * 0.55 + avg_len * 0.45) ** 0.72)
        col_weights.append(weight)

    total_weight = sum(col_weights)
    canvas_w = 1280
    outer_pad = 32
    table_w = canvas_w - 2 * outer_pad
    col_px = [max(100, int(round(table_w * (w / total_weight)))) for w in col_weights]
    col_px[-1] += table_w - sum(col_px)

    font_size = 15
    line_h = 22
    cell_pad_x = 16
    cell_pad_y = 14

    def wrap_cell(text: str, width_px: int) -> list[str]:
        avail_px = max(40, width_px - 2 * cell_pad_x)
        # Conservative character width (~8.8px at 15px font) so text never overflows a cell
        max_chars = max(10, int(avail_px / 8.8))
        if not text:
            return [""]
        wrapped = textwrap.wrap(
            text,
            width=max_chars,
            break_long_words=True,
            break_on_hyphens=False,
        )
        return wrapped or [""]

    header_wrapped = [wrap_cell(headers[c], col_px[c]) for c in range(ncols)]
    header_lines = max(len(w) for w in header_wrapped)
    header_h = header_lines * line_h + 2 * cell_pad_y

    body_wrapped = []
    row_heights = []
    for r in rows:
        rw = [wrap_cell(r[c], col_px[c]) for c in range(ncols)]
        body_wrapped.append(rw)
        max_l = max(len(w) for w in rw)
        row_heights.append(max_l * line_h + 2 * cell_pad_y)

    title_bar_h = 52
    table_top = outer_pad + title_bar_h
    table_h = header_h + sum(row_heights)
    canvas_h = table_top + table_h + outer_pad

    subtitle = table["h2"]
    if table["h3"]:
        subtitle += f" — {table['h3']}"
    subtitle = strip_md_inline(subtitle)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {canvas_w} {canvas_h}" '
        f'font-family="\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif">',
        f'  <rect x="0" y="0" width="{canvas_w}" height="{canvas_h}" fill="#FBFCFE"/>',
        f'  <text x="{outer_pad}" y="{outer_pad + 26}" font-size="18" font-weight="700" fill="#1A2430">'
        f"{html.escape(subtitle)}</text>",
        f'  <rect x="{outer_pad}" y="{table_top}" width="{table_w}" height="{table_h}" rx="10" '
        f'fill="#FFFFFF" stroke="#C9D4DE" stroke-width="1.5"/>',
        f'  <rect x="{outer_pad}" y="{table_top}" width="{table_w}" height="{header_h}" rx="10" fill="#1A2430"/>',
        f'  <rect x="{outer_pad}" y="{table_top + header_h - 10}" width="{table_w}" height="10" fill="#1A2430"/>',
    ]

    x = outer_pad
    for c in range(ncols):
        for li, line_str in enumerate(header_wrapped[c]):
            ty = table_top + cell_pad_y + (li + 1) * line_h - 5
            svg.append(
                f'  <text x="{x + cell_pad_x}" y="{ty}" font-size="{font_size}" '
                f'font-weight="700" fill="#FFFFFF">{html.escape(line_str)}</text>'
            )
        x += col_px[c]

    y = table_top + header_h
    for ri, rw in enumerate(body_wrapped):
        rh = row_heights[ri]
        bg = "#FFFFFF" if ri % 2 == 0 else "#F4F7FA"
        if ri == len(body_wrapped) - 1:
            svg.append(
                f'  <rect x="{outer_pad + 1}" y="{y}" width="{table_w - 2}" height="{rh - 1}" rx="9" fill="{bg}"/>'
            )
            svg.append(
                f'  <rect x="{outer_pad + 1}" y="{y}" width="{table_w - 2}" height="10" fill="{bg}"/>'
            )
        else:
            svg.append(
                f'  <rect x="{outer_pad + 1}" y="{y}" width="{table_w - 2}" height="{rh}" fill="{bg}"/>'
            )
        svg.append(
            f'  <line x1="{outer_pad}" y1="{y}" x2="{outer_pad + table_w}" y2="{y}" '
            f'stroke="#D9E2EC" stroke-width="1"/>'
        )
        x = outer_pad
        for c in range(ncols):
            weight = "600" if c == 0 else "400"
            color = "#123247" if c == 0 else "#2B3A48"
            for li, line_str in enumerate(rw[c]):
                ty = y + cell_pad_y + (li + 1) * line_h - 5
                svg.append(
                    f'  <text x="{x + cell_pad_x}" y="{ty}" font-size="{font_size}" '
                    f'font-weight="{weight}" fill="{color}">{html.escape(line_str)}</text>'
                )
            x += col_px[c]
        y += rh

    x = outer_pad
    for c in range(ncols - 1):
        x += col_px[c]
        svg.append(
            f'  <line x1="{x}" y1="{table_top}" x2="{x}" y2="{table_top + table_h}" '
            f'stroke="#D9E2EC" stroke-width="1" stroke-opacity="0.7"/>'
        )

    svg.append(
        f'  <rect x="{outer_pad}" y="{table_top}" width="{table_w}" height="{table_h}" rx="10" '
        f'fill="none" stroke="#C9D4DE" stroke-width="1.5"/>'
    )
    svg.append("</svg>\n")
    return "\n".join(svg)


def main() -> None:
    diagram_count = 0
    for svg_path in sorted(DOCS_DIAGRAMS.glob("*.svg")):
        codelab_svg = CODELAB_IMG / svg_path.name
        shutil.copy2(svg_path, codelab_svg)
        codelab_jpg = CODELAB_IMG / f"{svg_path.stem}.jpg"
        docs_jpg = DOCS_DIAGRAMS / f"{svg_path.stem}.jpg"
        svg_or_png_to_jpg(codelab_svg, codelab_jpg, size=2200)
        shutil.copy2(codelab_jpg, docs_jpg)
        diagram_count += 1

    hero_png = CODELAB_IMG / "healthcare-gateway-hero.png"
    if hero_png.exists():
        svg_or_png_to_jpg(hero_png, CODELAB_IMG / "healthcare-gateway-hero.jpg", size=2200)
        diagram_count += 1

    print(f"Converted {diagram_count} diagrams to .jpg in codelab/img/ and docs/diagrams/.")

    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    for old in TABLES_DIR.glob("*"):
        if old.is_file():
            old.unlink()

    tables = extract_tables(CODELAB_MD.read_text())
    index_lines = [
        "# Codelab Tables (JPEG Exports)",
        "",
        "Generated from `codelab/codelab.md` by `python3 codelab/render_jpegs.py`.",
        "",
        "| # | Task / Section | Line | Columns | JPEG File |",
        "|---|---|---|---|---|",
    ]

    for t in tables:
        idx = t["index"]
        section_label = t["h2"].split(":")[0].strip()
        sub_label = t["h3"] or t["headers"][0]
        slug = slugify(f"{section_label}-{sub_label}")
        stem = f"table-{idx:02d}-{slug}"
        svg_file = TABLES_DIR / f"{stem}.svg"
        jpg_file = TABLES_DIR / f"{stem}.jpg"
        svg_content = render_table_svg(t)
        svg_file.write_text(svg_content)
        svg_or_png_to_jpg(svg_file, jpg_file, size=2200)
        cols_preview = ", ".join(strip_md_inline(h) for h in t["headers"])
        sec_full = f"{t['h2']}" + (f" › {t['h3']}" if t["h3"] else "")
        index_lines.append(
            f"| {idx:02d} | {sec_full} | L{t['line']} | {cols_preview} | `{jpg_file.name}` |"
        )

    (TABLES_DIR / "INDEX.md").write_text("\n".join(index_lines) + "\n")
    print(f"Rendered {len(tables)} tables from codelab/codelab.md to codelab/img/tables/*.jpg.")


if __name__ == "__main__":
    main()
