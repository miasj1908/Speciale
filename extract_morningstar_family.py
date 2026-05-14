import argparse
import json
import re
from pathlib import Path

import extract_reports_full_narrative as narrative


DEFAULT_SOURCE_ROOT = Path(r"C:\OneDrive_1_2026-03-03")
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\xtarim\Downloads\Analyst reports\morningstar_equity_residual_output_v2")
DEFAULT_EXCLUDE_JSONL = Path(
    r"C:\Users\xtarim\Downloads\Analyst reports\sp_capiq_full_output_fullsource_v2\sp_capiq_full_output_fullsource_v2.jsonl"
)
SOURCE_FAMILY = "morningstar_equity"

INVESTMENT_RESEARCH_RE = re.compile(r"^investment research(?: \(\d+\))?$", re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract the Morningstar Equity Research family from the sector-organized OneDrive drop."
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--exclude-jsonl",
        type=Path,
        default=DEFAULT_EXCLUDE_JSONL,
        help="JSONL of already-extracted reports to exclude by full_path.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on the number of residual PDFs to process, for trials.",
    )
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def classify_pdf(pdf_path: Path) -> str | None:
    name = pdf_path.name.lower()
    stem = pdf_path.stem
    if "morningstar" in name:
        return "filename_contains_morningstar"
    if INVESTMENT_RESEARCH_RE.fullmatch(stem):
        return "investment_research_basename"
    return None


def load_excluded_paths(exclude_jsonl: Path | None) -> set[str]:
    if exclude_jsonl is None or not exclude_jsonl.exists():
        return set()
    excluded = set()
    with exclude_jsonl.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            full_path = obj.get("full_path")
            if isinstance(full_path, str) and full_path:
                excluded.add(full_path)
    return excluded


def build_manifest(source_root: Path, excluded_paths: set[str]) -> tuple[list[dict], dict[str, int]]:
    manifest = []
    overlap_count = 0
    morningstar_candidate_count = 0
    for pdf_path in sorted(source_root.rglob("*.pdf")):
        reason = classify_pdf(pdf_path)
        if reason is None:
            continue
        morningstar_candidate_count += 1
        full_path = str(pdf_path)
        if full_path in excluded_paths:
            overlap_count += 1
            continue
        rel_parts = pdf_path.relative_to(source_root).parts
        manifest.append(
            {
                "full_path": full_path,
                "source_file": pdf_path.name,
                "selection_reason": reason,
                "sector": rel_parts[0] if len(rel_parts) >= 1 else None,
                "issuer_folder": rel_parts[1] if len(rel_parts) >= 2 else None,
            }
        )
    stats = {
        "morningstar_candidate_count": morningstar_candidate_count,
        "excluded_overlap_count": overlap_count,
        "residual_selected_count": len(manifest),
    }
    return manifest, stats


def write_manifest(
    manifest: list[dict],
    output_dir: Path,
    source_root: Path,
    exclude_jsonl: Path | None,
    stats: dict[str, int],
    limit: int | None,
) -> None:
    manifest_path = output_dir / "morningstar_family_manifest.jsonl"
    summary_path = output_dir / "morningstar_family_summary.json"
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in manifest:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_reason: dict[str, int] = {}
    by_sector: dict[str, int] = {}
    for row in manifest:
        by_reason[row["selection_reason"]] = by_reason.get(row["selection_reason"], 0) + 1
        sector = row["sector"] or "UNKNOWN"
        by_sector[sector] = by_sector.get(sector, 0) + 1

    summary = {
        "source_root": str(source_root),
        "exclude_jsonl": str(exclude_jsonl) if exclude_jsonl else None,
        "limit": limit,
        "morningstar_candidate_count": stats["morningstar_candidate_count"],
        "excluded_overlap_count": stats["excluded_overlap_count"],
        "selected_pdf_count": stats["residual_selected_count"],
        "selection_breakdown": by_reason,
        "sector_breakdown": dict(sorted(by_sector.items())),
    }
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    excluded_paths = load_excluded_paths(args.exclude_jsonl)
    manifest, manifest_stats = build_manifest(args.source_root, excluded_paths)
    if not manifest:
        print(f"No Morningstar family PDFs found under {args.source_root}")
        return
    if args.limit is not None:
        manifest = manifest[: args.limit]
        manifest_stats = dict(manifest_stats)
        manifest_stats["residual_selected_count"] = len(manifest)

    write_manifest(manifest, output_dir, args.source_root, args.exclude_jsonl, manifest_stats, args.limit)

    input_paths = [Path(row["full_path"]) for row in manifest]
    print(
        f"Selected {len(input_paths)} residual Morningstar-family PDFs "
        f"after excluding {manifest_stats['excluded_overlap_count']} already extracted overlaps"
    )

    raw_csv = output_dir / "reports_raw.csv"
    narrative_csv = output_dir / "reports_narrative.csv"
    page_csv = output_dir / "pages.csv"
    block_csv = output_dir / "blocks.csv"
    jsonl_path = output_dir / "reports.jsonl"
    qa_csv = output_dir / "qa_summary.csv"
    preview_dir = output_dir / "previews"
    preview_dir.mkdir(parents=True, exist_ok=True)

    raw_fields = [
        "source_file",
        "full_path",
        "source_family",
        "family",
        "layout_family",
        "company",
        "ticker",
        "analyst",
        "report_date",
        "num_pages",
        "raw_text_len",
        "raw_text",
        "full_report_text_len",
        "full_report_text",
    ]
    narrative_fields = [
        "source_file",
        "full_path",
        "source_family",
        "family",
        "layout_family",
        "company",
        "ticker",
        "analyst",
        "report_date",
        "num_pages",
        "narrative_text_len",
        "narrative_text",
    ]
    page_fields = [
        "source_file",
        "source_family",
        "family",
        "layout_family",
        "page_idx",
        "page_num",
        "page_text",
        "page_text_len",
        "text_type",
    ]
    block_fields = [
        "source_file",
        "source_family",
        "family",
        "layout_family",
        "page_idx",
        "page_num",
        "block_idx",
        "x0",
        "y0",
        "x1",
        "y1",
        "width",
        "height",
        "block_text",
        "block_text_len",
        "keep_raw",
        "raw_drop_reason",
        "keep_narrative",
        "narrative_drop_reason",
    ]

    completed_paths = set()
    if args.resume and jsonl_path.exists():
        with jsonl_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    completed_paths.add(json.loads(line)["full_path"])
                except Exception:
                    continue
        if completed_paths:
            print(f"Resuming with {len(completed_paths)} completed PDFs already on disk")
    else:
        for path in [raw_csv, narrative_csv, page_csv, block_csv, jsonl_path, qa_csv]:
            if path.exists():
                path.unlink()

    for idx, pdf_path in enumerate(input_paths, start=1):
        if str(pdf_path) in completed_paths:
            continue
        print(f"[{idx}/{len(input_paths)}] {pdf_path.name}")
        result = narrative.extract_report(pdf_path)
        if result is None:
            continue

        meta = result["metadata"]
        layout_family = result["family"]
        raw_row = {
            "source_file": result["source_file"],
            "full_path": result["full_path"],
            "source_family": SOURCE_FAMILY,
            "family": SOURCE_FAMILY,
            "layout_family": layout_family,
            "company": meta.get("company"),
            "ticker": meta.get("ticker"),
            "analyst": meta.get("analyst"),
            "report_date": meta.get("report_date"),
            "num_pages": result["num_pages"],
            "raw_text_len": len(result["raw_text"]),
            "raw_text": result["raw_text"],
            "full_report_text_len": len(result["full_report_text"]),
            "full_report_text": result["full_report_text"],
        }
        narrative_row = {
            "source_file": result["source_file"],
            "full_path": result["full_path"],
            "source_family": SOURCE_FAMILY,
            "family": SOURCE_FAMILY,
            "layout_family": layout_family,
            "company": meta.get("company"),
            "ticker": meta.get("ticker"),
            "analyst": meta.get("analyst"),
            "report_date": meta.get("report_date"),
            "num_pages": result["num_pages"],
            "narrative_text_len": len(result["narrative_text"]),
            "narrative_text": result["narrative_text"],
        }
        narrative.append_csv_rows(raw_csv, [raw_row], raw_fields)
        narrative.append_csv_rows(narrative_csv, [narrative_row], narrative_fields)
        page_rows = []
        for row in result["pages"]:
            row = dict(row)
            row["source_family"] = SOURCE_FAMILY
            row["family"] = SOURCE_FAMILY
            row["layout_family"] = layout_family
            page_rows.append(row)
        block_rows = []
        for row in result["blocks"]:
            row = dict(row)
            row["source_family"] = SOURCE_FAMILY
            row["family"] = SOURCE_FAMILY
            row["layout_family"] = layout_family
            block_rows.append(row)
        narrative.append_csv_rows(page_csv, page_rows, page_fields)
        narrative.append_csv_rows(block_csv, block_rows, block_fields)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "source_file": result["source_file"],
                        "full_path": result["full_path"],
                        "source_family": SOURCE_FAMILY,
                        "family": SOURCE_FAMILY,
                        "layout_family": layout_family,
                        "metadata": meta,
                        "num_pages": result["num_pages"],
                        "raw_text": result["raw_text"],
                        "full_report_text": result["full_report_text"],
                        "narrative_text": result["narrative_text"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        preview_text = []
        for page in [p for p in result["pages"] if p["text_type"] == "narrative"][: narrative.PREVIEW_PAGES]:
            preview_text.append(f"===== PAGE {page['page_num']} =====\n{page['page_text']}")
        try:
            (preview_dir / narrative.safe_preview_name(pdf_path)).write_text(
                "\n\n".join(preview_text).strip(),
                encoding="utf-8",
            )
        except OSError:
            pass

    if narrative_csv.exists() and block_csv.exists():
        narrative.write_qa_summary(narrative_csv, block_csv, qa_csv)
        print(f"Saved outputs to {output_dir}")


if __name__ == "__main__":
    main()
