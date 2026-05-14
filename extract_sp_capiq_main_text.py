import argparse
import json
import re
from pathlib import Path

import fitz  # PyMuPDF


HEADER_MARKERS = (
    "Morningstar Equity Research",
    "Last Price",
    "Fair Value",
    "Consider Buy",
    "Consider Sell",
    "Morningstar Credit Rating",
    "Vital Statistics",
)

STOP_MARKERS = (
    "Important Disclosure",
    "General Disclosure",
    "Conflicts of Interest",
    "For Recipients in",
    "The concept of the Morningstar Economic Moat",
    "The Morningstar Rating for stocks",
    "This Research Report is current as of the date",
    "Analyst considers past financial results",
)

APPENDIX_STOP_MARKERS = (
    "Additional estimates and scenarios available for download",
    "Discounted Cash Flow Valuation",
    "Morningstar Margin of Safety and Star Rating Bands",
    "The Morningstar Corporate Credit Rating builds on",
    "Morningstar Corporate Credit Ratings are available on",
    "The Morningstar Corporate Credit Rating measures the ability",
    "We feel it’s important to perform credit analysis",
    "We feel it's important to perform credit analysis",
    "Investor Access",
    "Research Methodology for Valuing Companies",
    "Morningstar Research Methodology for Valuing Companies",
    "Overall Credit Rating",
)

TABLE_MARKERS = (
    "Valuation Summary and Forecasts",
    "Financial Summary and Forecasts",
    "Income Statement",
    "Balance Sheet",
    "Cash Flow Statement",
    "Market Cap",
    "52-Week High",
    "52-Week Low",
    "52-Week Total Return",
    "YTD Total Return",
    "Last Fiscal Year End",
    "5-Yr Forward Revenue CAGR",
    "5-Yr Forward EPS CAGR",
    "Price/Fair Value",
    "Shares Held",
    "% of Fund Assets",
    "Portfolio Date",
    "Management Activity",
    "Fund Ownership",
    "InsiderActivity",
    "Adjusted Cash Flow Summary",
    "Five Year Adjusted Cash Flow Forecast",
    "Cash Flow Cushion",
)

CALL_OUT_MARKERS = (
    "Bulls Say",
    "Bears Say",
)

SECTION_HEADINGS = (
    "Management & Ownership",
    "Analyst Notes",
    "Management",
    "Enterprise Risk",
    "Financial Health",
    "Capital Structure",
    "Economic Moat",
    "Moat Trend",
    "Scenario Analysis",
    "Thesis",
    "Valuation, Growth and Profitability",
)

ANALYST_NOTES_MARKERS = (
    "Analyst Notes",
    "SAP Demonstrates Commitment to Cloud",
    "Updated Forecasts and Estimates from",
)


def normalize_text(text: str) -> str:
    text = text.replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def is_sp_capiq_layout(first_page_text: str) -> bool:
    lowered = first_page_text.lower()
    return "consider buy" in lowered or "morningstar credit rating" in lowered


def is_page_number(text: str) -> bool:
    lowered = normalize_text(text).lower()
    return bool(re.fullmatch(r"page \d+( of \d+)?", lowered) or re.fullmatch(r"\d+", lowered))


def alpha_ratio(text: str) -> float:
    stripped = re.sub(r"\s+", "", text)
    if not stripped:
        return 0.0
    return sum(ch.isalpha() for ch in stripped) / len(stripped)


def number_count(text: str) -> int:
    return len(re.findall(r"\b\d[\d,.\-%/]*\b", text))


def is_numeric_heavy(text: str) -> bool:
    stripped = re.sub(r"\s+", "", text)
    if not stripped:
        return False
    digit_ratio = sum(ch.isdigit() for ch in stripped) / len(stripped)
    return digit_ratio >= 0.18 or number_count(text) >= 14


def has_sentence_shape(text: str) -> bool:
    sentence_like = len(re.findall(r"[.!?](?:\s|$)", text))
    long_words = len(re.findall(r"\b[A-Za-z]{4,}\b", text))
    return sentence_like >= 1 or long_words >= 14


def is_table_like(text: str) -> bool:
    lowered = text.lower()
    if alpha_ratio(text) >= 0.72 and has_sentence_shape(text) and len(text) >= 140:
        return False
    if any(marker.lower() in lowered for marker in TABLE_MARKERS):
        return True
    if is_numeric_heavy(text) and alpha_ratio(text) < 0.62:
        return True
    if len(text.splitlines()) >= 4 and number_count(text) >= 8:
        return True
    return False


def is_callout_like(text: str, x0: float, x1: float) -> bool:
    lowered = text.lower()
    width = x1 - x0
    if any(marker.lower() in lowered for marker in CALL_OUT_MARKERS):
        return True
    if width < 110 and len(text.splitlines()) >= 2:
        return True
    if width < 240 and len(text) < 180 and "\n" in text and alpha_ratio(text) < 0.72:
        return True
    if re.match(r"^[3O0*\-]\s", text):
        return True
    return False


def is_metadata_like(text: str) -> bool:
    lowered = text.lower()
    if alpha_ratio(text) >= 0.72 and has_sentence_shape(text) and len(text) >= 140:
        return False
    patterns = (
        "research as of",
        "estimates as of",
        "pricing data through",
        "rating updated as of",
        "@morningstar.com",
        "director",
        "senior analyst",
        "strategist",
        "associate director",
    )
    return any(pattern in lowered for pattern in patterns)


def looks_like_prose_block(text: str) -> bool:
    cleaned = normalize_text(text)
    if len(cleaned) < 80:
        return False
    if is_table_like(cleaned):
        return False
    if alpha_ratio(cleaned) < 0.6:
        return False
    if not has_sentence_shape(cleaned):
        return False
    return True


def looks_like_heading_block(text: str) -> bool:
    cleaned = normalize_text(text)
    lowered = cleaned.lower()
    if any(heading.lower() in lowered for heading in SECTION_HEADINGS):
        return True
    if len(cleaned) <= 120 and alpha_ratio(cleaned) >= 0.7 and number_count(cleaned) <= 4:
        return True
    return False


def extract_blocks(page: fitz.Page) -> list[dict]:
    blocks = []
    for raw in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = raw
        cleaned = normalize_text(text)
        if not cleaned:
            continue
        blocks.append(
            {
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "text": cleaned,
            }
        )
    return sorted(blocks, key=lambda item: (round(item["y0"], 1), round(item["x0"], 1)))


def page_contains_any(blocks: list[dict], markers: tuple[str, ...]) -> bool:
    for block in blocks:
        lowered = block["text"].lower()
        if any(marker.lower() in lowered for marker in markers):
            return True
    return False


def classify_column(block: dict, page_number: int) -> str:
    x0 = block["x0"]
    x1 = block["x1"]
    width = x1 - x0
    if page_number == 1 and x0 < 220 and block["y0"] < 190:
        return "title"
    if x0 < 150 and width < 120:
        return "callout"
    if x0 >= 380:
        return "right"
    if x0 >= 150:
        return "left"
    return "other"


def should_skip_block(block: dict, page_number: int) -> bool:
    text = block["text"]
    lowered = text.lower()
    if page_number == 1 and block["y0"] < 140:
        return True
    if block["y0"] < 105 or block["y1"] > 735:
        return True
    if is_page_number(text):
        return True
    if lowered in {marker.lower() for marker in HEADER_MARKERS}:
        return True
    if any(marker.lower() in lowered for marker in STOP_MARKERS):
        return True
    if "all rights reserved" in lowered or "please see important disclosures" in lowered:
        return True
    if is_metadata_like(text):
        return True
    if is_callout_like(text, block["x0"], block["x1"]):
        return True
    return False


def keep_block(block: dict, page_number: int) -> bool:
    if should_skip_block(block, page_number):
        return False

    text = block["text"]
    column = classify_column(block, page_number)

    if page_number == 1:
        if column == "title":
            return looks_like_prose_block(text) or looks_like_heading_block(text)
        if column == "left":
            if looks_like_prose_block(text):
                return True
            # Page 1 often splits the thesis into short line blocks, including final sentence lines.
            if len(text) >= 20 and alpha_ratio(text) >= 0.72 and not is_table_like(text):
                return True
        if column == "right":
            if looks_like_prose_block(text):
                return True
            if len(text) >= 20 and alpha_ratio(text) >= 0.72 and not is_table_like(text):
                return True
        return False

    if column not in {"left", "right"}:
        return False

    if looks_like_heading_block(text):
        return True

    if looks_like_prose_block(text):
        return True

    if page_number >= 2 and len(text) >= 55 and alpha_ratio(text) >= 0.7 and not is_table_like(text):
        return True

    return False


def clean_paragraph_text(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"^([A-Z][A-Za-z ]+)\s{2,}(\d{1,2} [A-Z][a-z]{2} \d{4})\n", r"\1 \2\n", text)
    text = re.sub(r"\n([a-z])", r" \1", text)
    text = re.sub(r"\n([A-Z][a-z]+(?: [A-Z][a-z]+){0,3} \d{2} [A-Z][a-z]{2} \d{4})", r"\n\1", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


def is_junk_paragraph(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) < 55 and stripped[0].islower():
        return True
    if re.fullmatch(r"(?:[A-Za-z0-9%./&+\-]+\n?){1,12}", stripped) and "\n" in stripped:
        return True
    return False


def merge_blocks(blocks: list[dict], gap_threshold: float = 16) -> list[dict]:
    if not blocks:
        return []
    merged: list[dict] = []
    current = dict(blocks[0])
    for block in blocks[1:]:
        gap = block["y0"] - current["y1"]
        if gap <= gap_threshold:
            current["text"] = current["text"] + "\n" + block["text"]
            current["x0"] = min(current["x0"], block["x0"])
            current["y0"] = min(current["y0"], block["y0"])
            current["x1"] = max(current["x1"], block["x1"])
            current["y1"] = max(current["y1"], block["y1"])
        else:
            merged.append(current)
            current = dict(block)
    merged.append(current)
    return merged


def merge_paragraph_continuations(blocks: list[dict], gap_threshold: float = 18) -> list[dict]:
    if not blocks:
        return []
    merged: list[dict] = []
    current = dict(blocks[0])
    for block in blocks[1:]:
        gap = block["y0"] - current["y1"]
        current_text = current["text"].strip()
        next_text = block["text"].strip()
        next_starts_like_continuation = bool(
            next_text
            and (
                next_text[0].islower()
                or next_text.startswith(("(", "%", "-", "and ", "or ", "but ", "while ", "with "))
                or len(next_text) < 140
            )
        )
        current_needs_continuation = bool(
            current_text
            and not re.search(r"[.!?:]$", current_text)
        )
        if gap <= gap_threshold and (current_needs_continuation or next_starts_like_continuation):
            current["text"] = current["text"] + "\n" + block["text"]
            current["x0"] = min(current["x0"], block["x0"])
            current["y0"] = min(current["y0"], block["y0"])
            current["x1"] = max(current["x1"], block["x1"])
            current["y1"] = max(current["y1"], block["y1"])
        else:
            merged.append(current)
            current = dict(block)
    merged.append(current)
    return merged


def build_page_paragraphs(page: fitz.Page, page_number: int) -> tuple[list[str], bool, bool]:
    blocks = extract_blocks(page)
    kept = []
    stop_document = False
    saw_analyst_notes = page_contains_any(blocks, ANALYST_NOTES_MARKERS)
    for block in blocks:
        if keep_block(block, page_number):
            kept.append({**block, "column": classify_column(block, page_number)})
            continue
        lowered = block["text"].lower()
        if any(marker.lower() in lowered for marker in APPENDIX_STOP_MARKERS):
            stop_document = True
            break

    paragraphs: list[str] = []
    column_order = ["title", "left"] if page_number == 1 else ["left", "right"]
    for column in column_order:
        column_blocks = [block for block in kept if block["column"] == column]
        if page_number == 1 and column == "left":
            column_blocks = merge_blocks(column_blocks, gap_threshold=18)
        column_blocks = merge_paragraph_continuations(column_blocks, gap_threshold=18)
        for block in column_blocks:
            cleaned = clean_paragraph_text(block["text"])
            if cleaned and not is_junk_paragraph(cleaned):
                paragraphs.append(cleaned)
    return paragraphs, stop_document, saw_analyst_notes


def extract_main_text(pdf_path: Path) -> dict:
    with fitz.open(pdf_path) as doc:
        first_page_text = doc[0].get_text("text")
        if not is_sp_capiq_layout(first_page_text):
            raise ValueError("PDF does not match the S&P CapitalIQ / Consider Buy layout")

        page_texts: list[str] = []
        seen: set[str] = set()
        in_analyst_notes = False
        for page_number, page in enumerate(doc, start=1):
            blocks = extract_blocks(page)
            if in_analyst_notes and page_contains_any(blocks, APPENDIX_STOP_MARKERS):
                break

            paragraphs, stop_document, saw_analyst_notes = build_page_paragraphs(page, page_number)
            cleaned_paragraphs = []
            for paragraph in paragraphs:
                norm = re.sub(r"\s+", " ", paragraph).strip().lower()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                cleaned_paragraphs.append(paragraph)
            if cleaned_paragraphs:
                page_texts.append("\n\n".join(cleaned_paragraphs))
            if saw_analyst_notes:
                in_analyst_notes = True
            if stop_document:
                break

    return {
        "source_file": pdf_path.name,
        "full_path": str(pdf_path),
        "page_count": len(page_texts),
        "main_note_text": "\n\n".join(page_texts).strip(),
    }


def iter_pdfs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.rglob("*.pdf"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract prose from S&P CapitalIQ-style Morningstar PDFs.")
    parser.add_argument("--input", type=Path, required=True, help="PDF file or directory")
    parser.add_argument("--output", type=Path, required=True, help="Output JSONL path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pdfs = iter_pdfs(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    skipped = 0
    with args.output.open("w", encoding="utf-8") as handle:
        for pdf_path in pdfs:
            try:
                row = extract_main_text(pdf_path)
            except Exception:
                skipped += 1
                continue
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            written += 1

    print(f"Wrote {written} records to {args.output}")
    print(f"Skipped {skipped} files")


if __name__ == "__main__":
    main()
