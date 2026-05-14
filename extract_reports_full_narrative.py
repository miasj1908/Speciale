import argparse
import json
import re
from pathlib import Path
import hashlib

import fitz  # PyMuPDF
import pandas as pd
import csv


DEFAULT_PDF_DIR = Path(r"C:\Users\xtarim\Downloads\Analyst reports\zip_sample_50")
DEFAULT_OUTPUT_DIR = Path(r"C:\Users\xtarim\Downloads\Analyst reports\full_narrative_output")

PREVIEW_PAGES = 3
REPEATED_THRESHOLD = 0.4
EQUITY_RESEARCH_STOP_MARKERS = [
    "Morningstar Analyst Forecasts",
    "Financial Summary and Forecasts",
    "Valuation Summary and Forecasts",
    "Income Statement (USD Mil)",
    "Balance Sheet (USD Mil)",
    "Cash Flow Statement (USD Mil)",
    "Key Valuation Drivers",
    "Discounted Cash Flow Valuation",
]
EQUITY_RESEARCH_PANEL_MARKERS = [
    "All values (except per share amounts) in:",
    "Price / Fair Value Estimate",
    "Fair Value Estimate",
    "Five-Star Price",
    "One-Star Price",
    "Estimated WACC",
    "Estimated COE",
    "Market Cap:",
    "Market Cap",
    "Valuation Multiples",
    "Scenarios",
    "Income Statement",
    "Balance Sheet",
    "Cash Flow Statement",
]
SP_CAPIQ_STOP_MARKERS = [
    "Income Statement (USD Mil)",
    "Pro Forma Income Statement (USD Mil)",
    "Balance Sheet (USD Mil)",
    "Cash Flow Statement (USD Mil)",
    "Financial Summary and Forecasts",
    "Valuation Summary and Forecasts",
    "Discounted Cash Flow Valuation",
    "Key Valuation Drivers",
    "Morningstar Analyst Forecasts",
    "Forecast",
]
APPENDIX_STOP_MARKERS = [
    "Last Price: Price of the stock as of the close",
    "Other Definitions:",
    "Analyst-Driven %",
    "Quantitative Fair Value Estimate",
    "Economic Moat Rating",
    "Morningstar Star Rating for Stocks",
    "Please see the Analyst Profile",
    "The Morningstar Rating for stocks",
    "Important Disclosure",
    "Unless otherwise provided in a separate agreement",
    "Capital Allocation Rating:",
    "Our star ratings are guideposts",
    "Market Price",
    "Please note, there is no predefined distribution of stars",
    "Four key components drive the Morningstar Rating",
    "The concept of an economic moat plays a vital role",
    "Our model is divided into three distinct stages",
    "Stage I: Explicit Forecast",
    "Qualitative Analysis Uncertainty Ratings",
    "The Morningstar Star Ratings for Stocks are defined below",
    "Quantitative Equity Research Overview",
    "Risk Warning",
    "Morningstar Historical Summary",
    "Morningstar Analyst Historical/Forecast Summary",
    "Operating Performance / Profitability as of",
    "Forward Valuation Estimates",
    "Quarterly Revenue & EPS",
    "Revenue Growth Year On Year %",
    "Profitability",
    "Financial Health",
]

ANALYST_REPORT_SIDEBAR_MARKERS = [
    "Sector",
    "Industry",
    "Business Description",
    "Competitors",
    "Close Competitors",
    "Company/Ticker",
    "Financials",
    "Analyst Notes Archive",
    "Research Methodology for Valuing Companies",
]

ANALYST_REPORT_TABLE_MARKERS = [
    "Economic Moat",
    "Moat Trend",
    "Capital Allocation",
    "Uncertainty",
    "Morningstar Rating",
    "Fair Value",
    "Last Close",
    "Price/Fair Value",
    "Price/Earnings",
    "Dividend Yield",
    "Market Cap",
    "52-Week Range",
    "Competitors",
    "Close Competitors",
]

RESIDUAL_SECTION_HEADINGS = [
    "Analyst Note",
    "Morningstar Analysis",
    "Investment Thesis",
    "Business Strategy and Outlook",
    "Business Strategy & Outlook",
    "Economic Moat",
    "Fair Value and Profit Drivers",
    "Fair Value & Profit Drivers",
    "Risk and Uncertainty",
    "Risk & Uncertainty",
    "Capital Allocation",
    "Stewardship",
    "Scenario Analysis",
    "Valuation, Growth and Profitability",
    "Bulls Say",
    "Bears Say",
    "Analyst Notes Archive",
]


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_block_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r", "\n")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    lines = [line.strip() for line in text.split("\n")]
    lines = [line for line in lines if line]
    return "\n".join(lines).strip()


def simplify_for_matching(text: str) -> str:
    text = normalize_block_text(text).lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def detect_family(first_page_text: str) -> str:
    text = first_page_text.lower()
    if "morningstar equity analyst report" in text:
        return "analyst_report"
    if "consider buy" in text or "morningstar credit rating" in text:
        return "sp_capiq"
    return "equity_research"


def is_page_number(text: str) -> bool:
    simplified = simplify_for_matching(text)
    return bool(re.fullmatch(r"page \d+ of \d+", simplified) or re.fullmatch(r"\d+", simplified))


def is_disclosure_block(text: str) -> bool:
    lowered = normalize_block_text(text).lower()
    markers = [
        "all rights reserved",
        "unless otherwise provided in a separate agreement",
        "proprietary property of morningstar",
        "please see important disclosures",
        "conflicts of interest",
        "for recipients in",
        "redistribution is prohibited",
        "investment research is produced and issued by subsidiaries of morningstar",
        "the conduct of morningstar",
        "code of ethics/code of conduct",
        "personal security trading policy",
        "for information regarding conflicts of interest",
        "please see important disclosures at the end of this report",
        "the primary analyst covering this company",
        "global.morningstar.com/equitydisclosures",
        "http://global.morningstar",
        "com/equitydisclosures",
    ]
    return any(marker in lowered for marker in markers)


def is_numeric_heavy(text: str) -> bool:
    simplified = re.sub(r"\s+", "", normalize_block_text(text))
    if not simplified:
        return False
    digit_ratio = sum(ch.isdigit() for ch in simplified) / len(simplified)
    number_count = len(re.findall(r"\b\d[\d,\.]*%?\b", text))
    return digit_ratio >= 0.18 or number_count >= 10


def is_prose_like_block(text: str) -> bool:
    cleaned = normalize_block_text(text)
    if len(cleaned) < 120:
        return False
    sentence_count = len(re.findall(r"[.!?](?:\s|$)", cleaned))
    alpha_chars = sum(ch.isalpha() for ch in cleaned)
    alpha_ratio = alpha_chars / max(len(cleaned), 1)
    long_words = len(re.findall(r"\b[a-zA-Z]{4,}\b", cleaned))
    return sentence_count >= 2 and alpha_ratio >= 0.55 and long_words >= 18


def has_stop_marker(text: str, markers: list[str]) -> bool:
    return any(marker in text for marker in markers)


def is_analyst_contact_block(text: str) -> bool:
    cleaned = normalize_block_text(text)
    return bool(
        re.search(r"@[A-Za-z0-9._-]+\.[A-Za-z]{2,}", cleaned)
        or re.search(r"\+\d[\d ()-]{7,}", cleaned)
        or re.search(r"\b(?:Equity Analyst|Senior Equity Analyst|Stock Analyst|Securities Analyst|Director|Sector Head)\b", cleaned)
    )


def is_pull_quote_block(x0: float, x1: float, text: str) -> bool:
    cleaned = normalize_block_text(text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if x0 > 90 or x1 > 190 or len(lines) < 4:
        return False
    if sum(len(line) <= 24 for line in lines) < len(lines) - 1:
        return False
    if cleaned.count(".") >= 2:
        return False
    return True


def strip_residual_disclosure_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(
        r"©\s*Morningstar[\s\S]{0,2500}?Please see important disclosures at\s*the end of this report\.?",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def is_heading_like(line: str) -> bool:
    line = line.strip()
    if not line:
        return False
    if line in RESIDUAL_SECTION_HEADINGS:
        return True
    if re.fullmatch(r"[A-Z][A-Za-z&/'’., -]{2,80}", line) and len(line.split()) <= 8:
        return True
    return False


def is_bullet_like(line: str) -> bool:
    line = line.strip()
    return bool(
        line.startswith("O ")
        or line.startswith("3 ")
        or line.startswith("- ")
        or line.startswith("* ")
        or re.match(r"^\d+\.\s", line)
    )


def should_join_lines(current: str, nxt: str) -> bool:
    current = current.rstrip()
    nxt = nxt.lstrip()
    if not current or not nxt:
        return False
    if is_heading_like(current) or is_heading_like(nxt):
        return False
    if is_bullet_like(current) and not is_bullet_like(nxt):
        return True
    if is_bullet_like(nxt):
        return False
    if re.search(r"[:;.!?]$|(?:\b[A-Z]{2,}\b)$", current):
        return False
    if re.match(r"^(?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|[A-Z][a-z]{2,8}\s+\d{1,2},\s*\d{4})$", nxt):
        return False
    if re.match(r"^[a-z(]", nxt):
        return True
    if re.match(r"^(?:and|but|or|as|if|when|while|with|to|of|for|in|on|at|from|by|which|that|who|whose|where)\b", nxt, flags=re.I):
        return True
    if re.search(r"[A-Za-z0-9,\-–—]$", current) and re.match(r"^[A-Za-z(]", nxt):
        return True
    return False


def format_narrative_paragraphs(text: str) -> str:
    lines = [re.sub(r"^O(?=[A-Z])", "O ", line.strip()) for line in text.splitlines()]
    paragraphs: list[str] = []
    buffer: list[str] = []

    for line in lines:
        if not line:
            if buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            continue

        if not buffer:
            buffer = [line]
            continue

        if should_join_lines(buffer[-1], line):
            buffer[-1] = f"{buffer[-1]} {line}"
        else:
            paragraphs.append(" ".join(buffer))
            buffer = [line]

    if buffer:
        paragraphs.append(" ".join(buffer))

    text = "\n\n".join(paragraphs)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_narrative_text(text: str) -> str:
    text = strip_residual_disclosure_text(text)
    if not text:
        return ""

    # Remove leaked KPI/header labels only when they appear as literal "\n"
    # sequences inside the text, without touching real newline-delimited prose.
    text = re.sub(
        r"(?:[A-Z][A-Za-z&/%().™\- ]{2,}\s*\\n){2,}[A-Z][A-Za-z&/%().™\- ]{2,}",
        "",
        text,
    )

    cleaned_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        if re.fullmatch(r"[A-Z?®ß]", line):
            continue
        if re.fullmatch(r"page \d+ of \d+", line, flags=re.I):
            continue
        if "Morningstar Equity Analyst Report" in line:
            continue
        if re.fullmatch(r"[A-Z][A-Za-z0-9&.,'’\- ]+\s+[A-Z]{1,5}\s+\((?:X?[A-Z]{2,5}|[A-Z]{2,6})\)", line):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(
        r"©\s*Morningstar\s+\d{4}\.[\s\S]{0,4000}?Please see important disclosures at the end of this report\.",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(?:\n\s*){2,}(?:52-Week High|52-Week Low|52-Week Total Return %|YTD Total Return %|Last Fiscal Year End|5-Yr Forward Revenue CAGR %|5-Yr Forward EPS CAGR %|Price/Fair Value)\n[\s\S]{0,1500}?(?=\n(?:Valuation Summary and Forecasts|Financial Summary and Forecasts|Fiscal Year:))",
        "\n\n",
        text,
        flags=re.I,
    )
    heading_union = "|".join(re.escape(item) for item in RESIDUAL_SECTION_HEADINGS)
    table_starts = [
        "Valuation Summary and Forecasts",
        "Financial Summary and Forecasts",
        "Income Statement",
        "Balance Sheet",
        "Cash Flow Statement",
        "Fiscal Year:",
        "Fiscal Year Ends in",
        "Revenue YoY %",
        "EBIT YoY %",
        "Net Income YoY %",
        "Diluted EPS",
        "Dividend Yield %",
        "Price/Earnings",
        "EV/EBITDA",
        "EV/EBIT",
        "Free Cash Flow Yield %",
    ]
    table_union = "|".join(re.escape(item) for item in table_starts)
    text = re.sub(
        rf"(?:\n\s*){{2,}}(?:{table_union})[\s\S]{{0,6000}}?(?=\n(?:{heading_union})\b|\Z)",
        "\n\n",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"(?:\n\s*){2,}(?:52-Week High|52-Week Low|52-Week Total Return %|YTD Total Return %|Last Fiscal Year End|5-Yr Forward Revenue CAGR %|5-Yr Forward EPS CAGR %|Price/Fair Value)\n[^\n]+",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"(?:\s|\.)K$", "", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return format_narrative_paragraphs(text)


ESG_META_START_RE = re.compile(r"(?:^|\b)(?:esg risk rating(?: assessment)?|sustainalytics|rating as of)\b", re.I)
ESG_META_LABEL_RE = re.compile(
    r"^(?:"
    r"esg risk rating(?: assessment)?|rating as of|sustainalytics|category|subindustry|industry group|"
    r"environmental risk score|social risk score|governance risk score|controversy level|"
    r"managed product involvement|country risk score|industry risk score|company risk score|"
    r"unmanaged risk score|portfolio risk score"
    r")\W*$",
    re.I,
)
ESG_META_VALUE_RE = re.compile(
    r"^(?:"
    r"\d+(?:\.\d+)?(?:\s*[A-Z]{2,4})?|"
    r"negligible|low|medium|high|severe|standard|average|below average|above average|"
    r"subdued|moderate|significant|strong|weak|none|limited"
    r")\W*$",
    re.I,
)


def is_esg_metadata_boundary(line: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:Vital Statistics|Business Strategy and Outlook|Morningstar Analysis|Scenario Analysis|"
            r"Economic Moat|Moat Trend|Risk & Uncertainty|Capital Allocation|Analyst Notes|"
            r"Analyst's Perspective(?: \d{1,2} [A-Za-z]{3,9} \d{4})?)",
            line,
            flags=re.I,
        )
    )


def is_report_content_boundary(line: str) -> bool:
    return bool(
        re.fullmatch(
            r"(?:"
            r"Vital Statistics|Business Strategy and Outlook|Morningstar Analysis|Scenario Analysis|"
            r"Economic Moat|Moat Trend|Risk & Uncertainty|Capital Allocation|Analyst Notes|"
            r"Analyst's Perspective(?: \d{1,2} [A-Za-z]{3,9} \d{4})?|"
            r"Key Investment Considerations|Fair Value & Profit Drivers|Bulls Say/Bears Say|"
            r"Bulls Say|Bears Say|Management & Ownership|Risk and Uncertainty|"
            r"Stewardship|Price vs Fair Value|Business Description|Investment Thesis|"
            r"Credit Overview|Overview and Outlook|Contents"
            r")",
            line,
            flags=re.I,
        )
    )


def is_esg_metadata_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if ESG_META_LABEL_RE.fullmatch(stripped):
        return True
    if ESG_META_VALUE_RE.fullmatch(stripped):
        return True
    if re.fullmatch(r"[;:,.']{3,}", stripped):
        return True
    return False


def clean_full_report_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(
        r"©\s*Morningstar\s+\d{4}\.[\s\S]{0,4000}?Please see important disclosures at the end of this report\.?",
        "",
        text,
        flags=re.I,
    )
    header_boundary_union = (
        r"Vital Statistics|Business Strategy and Outlook|Morningstar Analysis|Scenario Analysis|"
        r"Economic Moat|Moat Trend|Risk & Uncertainty|Capital Allocation|Analyst Notes|"
        r"Analyst's Perspective(?: \d{1,2} [A-Za-z]{3,9} \d{4})?|Key Investment Considerations|"
        r"Fair Value & Profit Drivers|Bulls Say/Bears Say|Bulls Say|Bears Say|"
        r"Management & Ownership|Risk and Uncertainty|Stewardship|Price vs Fair Value|"
        r"Business Description|Investment Thesis|Credit Overview|Overview and Outlook|Contents"
    )
    text = re.sub(
        rf"(?:^|\n\s*)(?:Page \d+\s*\n\s*)?"
        r"[A-Z][^\n]{6,}\([A-Z0-9]+\)\s*\|\s*[A-Z]+\s*\n"
        r"[\s\S]{0,2500}?"
        rf"(?=\n\s*(?:{header_boundary_union})\s*(?:\n|$))",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(
        rf"(?:^|\n\s*)(?:Last Price|Fair Value|Uncertainty|Economic Moat(?:™)?|Moat Trend(?:™)?|"
        r"Capital Allocation|Industry Group)\s*\n"
        r"[\s\S]{0,1800}?"
        rf"(?=\n\s*(?:{header_boundary_union})\s*(?:\n|$))",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(
        rf"(?:^|\n\s*)(?:Comparable Company Analysis|Methodology for Valuing Companies|"
        r"52-Week High \(USD\)|52-Week High|Valuation Summary and Forecasts|Financial Summary and Forecasts)"
        r"[\s\S]{0,9000}?"
        rf"(?=\n\s*(?:Historical/forecast data sources are Morningstar Estimates and may reflect adjustments|{header_boundary_union})\s*(?:\n|$)|\Z)",
        "\n",
        text,
        flags=re.I,
    )
    text = re.sub(
        rf"(?:^|\n\s*)(?:Comparable Company Analysis|Methodology for Valuing Companies|Morningstar Analyst Forecasts)"
        r"[\s\S]{0,20000}?"
        rf"(?=\n\s*(?:{header_boundary_union})\s*(?:\n|$)|\Z)",
        "\n",
        text,
        flags=re.I,
    )
    cleaned_lines = []
    in_esg_metadata_block = False
    in_page_header_block = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if in_esg_metadata_block:
                in_esg_metadata_block = False
            if in_page_header_block:
                continue
            cleaned_lines.append("")
            continue
        if re.fullmatch(r"page \d+ of \d+", line, flags=re.I):
            continue
        if re.fullmatch(r"page \d+", line, flags=re.I):
            in_page_header_block = True
            continue
        if in_page_header_block:
            if is_report_content_boundary(line):
                in_page_header_block = False
            else:
                continue
        lowered = line.lower()
        if ESG_META_START_RE.search(line):
            in_esg_metadata_block = True
            continue
        if in_esg_metadata_block:
            if is_esg_metadata_boundary(line):
                in_esg_metadata_block = False
            elif is_esg_metadata_line(line):
                continue
            else:
                in_esg_metadata_block = False
        if lowered in {"rating.", "rating"}:
            continue
        if lowered == "important disclosure":
            continue
        if any(
            marker in lowered
            for marker in [
                "esg risk",
                "sustainalytics",
                "rating as of",
                "the conduct of morningstar",
                "governed by code of ethics",
                "code of ethics/code of conduct",
                "personal security trading policy",
                "an equivalent of",
                "an equivalent of), and investment research",
                "investment research",
                "investment research policy",
                "for information regarding conflicts",
                "of interest, visit",
                "visit",
                "http://global.morningstar",
                "global.morningstar",
                "equitydisclosures",
                "the primary analyst covering this company",
                "primary analyst covering this company",
                "does not own its stock",
                "research as of",
                "estimates as of",
                "pricing data through",
                "pricing data as of",
                "rating updated as of",
            ]
        ):
            continue
        if re.fullmatch(r"[;:,.']{3,}", line):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return format_narrative_paragraphs(text)


def is_short_date_or_stamp(text: str) -> bool:
    cleaned = normalize_block_text(text)
    if not cleaned:
        return False
    if re.fullmatch(r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}", cleaned):
        return True
    if re.fullmatch(r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\s+\d{2}:\d{2},\s*UTC", cleaned):
        return True
    if re.fullmatch(r"[;:.\- ]{3,}", cleaned):
        return True
    return False


def is_analyst_report_sidebar_block(x0: float, x1: float, y0: float, text: str) -> bool:
    width = x1 - x0
    if x0 >= 205:
        return False
    if width <= 210 and y0 >= 135:
        return True
    if has_stop_marker(text, ANALYST_REPORT_SIDEBAR_MARKERS):
        return True
    return False


def is_analyst_report_top_panel_block(x0: float, x1: float, y0: float, text: str) -> bool:
    if y0 >= 140:
        return False
    width = x1 - x0
    if is_short_date_or_stamp(text):
        return True
    if x0 >= 280 and (
        has_stop_marker(text, ["Uncertainty", "Economic Moat", "Moat Trend", "Capital Allocation", "Sector", "Industry"])
        or is_numeric_heavy(text)
        or len(normalize_block_text(text)) <= 80
    ):
        return True
    if 210 <= x0 <= 320 and width <= 80 and is_short_date_or_stamp(text):
        return True
    return False


def is_analyst_report_metric_panel_block(x0: float, text: str) -> bool:
    cleaned = normalize_block_text(text)
    if cleaned == "Morningstar Rating":
        return True
    if x0 < 250:
        return False
    if "Fair Value" in cleaned and "Uncertainty :" in cleaned:
        return True
    if cleaned in {"Economic Moat", "Moat Trend", "Capital Allocation", "Sector", "Industry"}:
        return True
    if any(
        cleaned.startswith(marker)
        for marker in [
            "Last Close",
            "Price/Fair Value",
            "Price/Earnings",
            "Dividend Yield",
            "Market Cap",
            "52-Week Range",
            "Fair Value",
        ]
    ) and (is_numeric_heavy(cleaned) or "\n" in text):
        return True
    return False


def is_analyst_report_table_block(x0: float, text: str) -> bool:
    cleaned = normalize_block_text(text)
    if x0 >= 220:
        return False
    if not any(marker in cleaned for marker in ANALYST_REPORT_TABLE_MARKERS):
        return False
    if "\n" in text and (is_numeric_heavy(cleaned) or len(cleaned.splitlines()) >= 3):
        return True
    if re.search(r"\b(?:None|Narrow|Wide|Stable|Positive|Negative|High|Medium|Low|Exemplary|Standard|Poor)\b", cleaned):
        return True
    return False


def is_analyst_report_bullet_sidebar_block(x0: float, x1: float, text: str) -> bool:
    width = x1 - x0
    if x0 >= 205 or width > 230:
        return False
    lines = [line.strip() for line in normalize_block_text(text).splitlines() if line.strip()]
    if not lines:
        return False
    bullet_like = any(
        line.startswith(("u ", "•", "- "))
        or re.match(r"^O[A-Z]", line)
        or re.match(r"^O[A-Za-z]", line)
        for line in lines
    )
    return bullet_like and len(lines) <= 10


def is_financial_statement_heading(text: str) -> bool:
    return has_stop_marker(
        text,
        [
            "Income Statement (USD Mil)",
            "Pro Forma Income Statement (USD Mil)",
            "Balance Sheet (USD Mil)",
            "Cash Flow Statement (USD Mil)",
            "Financial Summary and Forecasts",
            "Valuation Summary and Forecasts",
            "Discounted Cash Flow Valuation",
            "Key Valuation Drivers",
        ],
    )


def is_long_prose_block(text: str) -> bool:
    cleaned = normalize_block_text(text)
    if len(cleaned) < 220:
        return False
    if is_numeric_heavy(cleaned):
        return False
    if is_disclosure_block(cleaned):
        return False
    lines = [line.strip() for line in cleaned.split("\n") if line.strip()]
    if len(lines) >= 8:
        avg_line_len = sum(len(line) for line in lines) / len(lines)
        short_line_count = sum(1 for line in lines if len(line) <= 24)
        if avg_line_len <= 30 or short_line_count >= max(5, int(len(lines) * 0.6)):
            return False
    if "." not in cleaned and ";" not in cleaned and ":" not in cleaned:
        return False
    return True


def page_prose_block_count(blocks: list[dict]) -> int:
    return sum(1 for block in blocks if is_long_prose_block(block["block_text"]))


def page_has_marker(blocks: list[dict], markers: list[str]) -> bool:
    return any(has_stop_marker(block["block_text"], markers) for block in blocks)


def is_prose_line_block(text: str) -> bool:
    cleaned = normalize_block_text(text)
    if len(cleaned) < 60:
        return False
    if is_numeric_heavy(cleaned):
        return False
    if is_disclosure_block(cleaned):
        return False
    alpha_chars = sum(ch.isalpha() for ch in cleaned)
    lower_chars = sum(ch.islower() for ch in cleaned)
    if alpha_chars < 30 or lower_chars < 15:
        return False
    if " " not in cleaned:
        return False
    return True


def page_has_split_prose_run(blocks: list[dict], family: str) -> bool:
    if family == "equity_research":
        candidates = [
            block
            for block in blocks
            if block["x0"] >= 185
            and block["x1"] >= 320
            and is_prose_line_block(block["block_text"])
        ]
    elif family == "sp_capiq":
        candidates = [
            block
            for block in blocks
            if block["x0"] >= 165
            and block["x1"] >= 320
            and is_prose_line_block(block["block_text"])
        ]
    else:
        return False

    if len(candidates) < 4:
        return False

    ordered = sorted(candidates, key=lambda item: (round(item["y0"], 1), item["x0"]))
    run_len = 1
    prev = ordered[0]
    for block in ordered[1:]:
        same_column = abs(block["x0"] - prev["x0"]) <= 24
        vertical_gap = block["y0"] - prev["y1"]
        if same_column and -2 <= vertical_gap <= 28:
            run_len += 1
            if run_len >= 4:
                return True
        else:
            run_len = 1
        prev = block
    return False


def is_table_heavy_page(blocks: list[dict]) -> bool:
    numeric_blocks = sum(1 for block in blocks if is_numeric_heavy(block["block_text"]))
    financial_blocks = sum(
        1
        for block in blocks
        if is_financial_statement_heading(block["block_text"]) or is_equity_research_panel_block(block["block_text"])
    )
    return numeric_blocks >= 4 or financial_blocks >= 2


def has_sentence_prose_page(blocks: list[dict]) -> bool:
    return any(is_long_prose_block(block["block_text"]) for block in blocks)


def is_comparison_or_ratio_page(blocks: list[dict]) -> bool:
    markers = [
        "Comparable Company Analysis",
        "Competitors Price vs. Fair Value",
        "Company/Ticker",
        "Profitability Analysis",
        "Leverage Analysis",
        "Liquidity Analysis",
        "Price/Earnings",
        "Price/Book",
        "Price/Sales",
        "EV/EBITDA",
        "Last Historical Year",
    ]
    return page_has_marker(blocks, markers)


def is_methodology_or_appendix_page(blocks: list[dict]) -> bool:
    markers = [
        "Research Methodology for Valuing Companies",
        "Methodology for Valuing Companies",
        "Morningstar Research Methodology",
        "Our model is divided into three distinct stages",
        "Stage I: Explicit Forecast",
        "The Morningstar Star Ratings for Stocks are defined below",
        "Morningstar Historical Summary",
        "General Disclosure",
        "Risk Warning",
        "About Morningstar",
    ]
    return page_has_marker(blocks, markers)


def is_equity_research_panel_block(text: str) -> bool:
    if has_stop_marker(text, EQUITY_RESEARCH_PANEL_MARKERS):
        return True
    if text.startswith("Income Statement") or text.startswith("Balance Sheet") or text.startswith("Cash Flow Statement"):
        return True
    return False


def is_peer_comparison_block(text: str) -> bool:
    ticker_count = len(re.findall(r"\b[A-Z]{1,5}\b", text))
    lowered = f" {text.lower()} "
    has_company_terms = any(
        term in lowered
        for term in [" inc ", " corp", " group", " holdings", "ordinary shares", "class a", " plc", " nv ", " sa "]
    )
    return ticker_count >= 2 and has_company_terms


def is_company_ticker_block(text: str) -> bool:
    lowered = f" {text.lower()} "
    ticker_count = len(re.findall(r"\b[A-Z]{1,5}\b", text))
    has_company_terms = any(
        term in lowered
        for term in [" inc ", " corp", " group", " holdings", "ordinary shares", "class a", " plc", " nv ", " sa "]
    )
    return ticker_count >= 1 and has_company_terms


def is_analyst_report_peer_page(blocks: list[dict], page_num: int) -> bool:
    if page_num < 3:
        return False

    has_competitor_header = False
    has_last_close = False
    has_total_return = False
    has_rating = False
    company_ticker_blocks = 0

    for block in blocks:
        text = block["block_text"]
        x0 = block["x0"]
        y0 = block["y0"]

        if x0 <= 320 and y0 <= 180 and "Competitors Price vs. Fair Value" in text:
            has_competitor_header = True
        if x0 >= 420 and "Last Close:" in text and "Fair Value:" in text:
            has_last_close = True
        if "Total Return % as of" in text:
            has_total_return = True
        if "Morningstar Rating" in text and x0 >= 400:
            has_rating = True
        if x0 <= 220 and y0 <= 430 and is_company_ticker_block(text):
            company_ticker_blocks += 1

    if has_competitor_header and (has_last_close or has_rating):
        return True
    if company_ticker_blocks >= 2 and has_last_close and has_rating:
        return True
    if company_ticker_blocks >= 1 and has_last_close and has_total_return and has_rating:
        return True
    return False


def should_drop_equity_research_page(blocks: list[dict], page_num: int) -> bool:
    if page_num <= 1:
        return False
    prose_blocks = page_prose_block_count(blocks)
    has_prose_flow = has_sentence_prose_page(blocks) or page_has_split_prose_run(blocks, "equity_research")
    if page_has_marker(blocks, ["Financial Summary and Forecasts", "Valuation Summary and Forecasts", "Forecast", "Income Statement", "Balance Sheet", "Cash Flow Statement"]) and not has_prose_flow:
        return True
    if is_table_heavy_page(blocks) and prose_blocks <= 1 and not has_prose_flow:
        return True
    if is_comparison_or_ratio_page(blocks) and prose_blocks <= 1:
        return True
    if page_has_marker(blocks, ["Management & Ownership", "Ownership Structure"]) and prose_blocks == 0:
        return True
    return False


def should_drop_sp_capiq_page(blocks: list[dict], page_num: int) -> bool:
    if page_num <= 1:
        return False
    prose_blocks = page_prose_block_count(blocks)
    has_prose_flow = has_sentence_prose_page(blocks) or page_has_split_prose_run(blocks, "sp_capiq")
    if page_has_marker(blocks, ["Financial Summary and Forecasts", "Valuation Summary and Forecasts", "Forecast", "Income Statement", "Balance Sheet", "Cash Flow Statement"]) and not has_prose_flow:
        return True
    if is_table_heavy_page(blocks) and prose_blocks <= 1 and not has_prose_flow:
        return True
    if is_comparison_or_ratio_page(blocks) and prose_blocks <= 1:
        return True
    if page_has_marker(blocks, ["Management & Ownership", "Management & Ownership", "Ownership Structure"]) and prose_blocks == 0:
        return True
    if page_has_marker(blocks, ["Principal Payments", "Cash Obligations and Commitments"]) and prose_blocks == 0:
        return True
    return False


def extract_metadata(first_page_text: str, source_file: str) -> dict:
    metadata = {"company": None, "ticker": None, "analyst": None, "report_date": None}
    lines = [line.strip() for line in first_page_text.split("\n") if line.strip()]

    for line in lines:
        match = re.search(
            r"\b(?:Research|Pricing data|Rating updated|Report as of)\s+as of\s+(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b",
            line,
            re.I,
        )
        if match:
            metadata["report_date"] = match.group(1)
            break

    if not metadata["report_date"]:
        for pattern in [
            r"\b(\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})\b",
            r"\b([A-Za-z]{3,9}\s+\d{1,2},\s*\d{4})\b",
            r"\b(\d{4}-\d{2}-\d{2})\b",
        ]:
            match = re.search(pattern, first_page_text)
            if match:
                metadata["report_date"] = match.group(1)
                break

    for idx, line in enumerate(lines):
        if re.fullmatch(r"(?:Equity\s+Analyst|Analyst|Stock Analyst|Senior Analyst)", line, flags=re.I) and idx > 0:
            prev = lines[idx - 1]
            if re.fullmatch(r"[A-Z][A-Za-z\-',. ]{2,80}", prev):
                metadata["analyst"] = prev
                break

    for line in lines:
        match = re.match(r"^([A-Z][A-Za-z0-9&.,'\- ]+?)\s+([A-Z]{1,6})\s+\(([A-Z]{2,6}|NAS|NYS|XLON)\)", line)
        if match:
            metadata["company"] = normalize_text(match.group(1))
            metadata["ticker"] = match.group(2)
            break

    if not metadata["company"]:
        match = re.match(r"^([A-Z][A-Za-z0-9&.,'\- ]+?)\s+([A-Z]{1,6})\s+Q\b", first_page_text)
        if match:
            metadata["company"] = normalize_text(match.group(1))
            metadata["ticker"] = match.group(2)

    if not metadata["ticker"]:
        match = re.search(r"\(([A-Z]{1,6})\)", first_page_text)
        if match:
            metadata["ticker"] = match.group(1)

    if not metadata["company"]:
        match = re.search(r"\b([A-Z][A-Za-z0-9&.,'\- ]{2,100})\s+\b[A-Z]{1,6}\b", first_page_text)
        if match:
            metadata["company"] = normalize_text(match.group(1))

    if not metadata["company"]:
        metadata["company"] = Path(source_file).stem[:120]

    return metadata


def collect_repeated_margin_text(doc: fitz.Document) -> set[str]:
    repeated: dict[str, set[int]] = {}
    threshold = max(2, int(doc.page_count * REPEATED_THRESHOLD))

    for page_idx in range(doc.page_count):
        page = doc.load_page(page_idx)
        page_height = float(page.rect.height)
        seen = set()
        for block in page.get_text("blocks") or []:
            if len(block) < 7:
                continue
            x0, y0, x1, y1, text, _, block_type = block
            if block_type != 0:
                continue
            simplified = simplify_for_matching(text)
            if not simplified or len(simplified) > 220:
                continue
            if not (y0 < 70 or y1 > page_height - 45):
                continue
            if simplified in seen:
                continue
            seen.add(simplified)
            repeated.setdefault(simplified, set()).add(page_idx)

    return {text for text, pages in repeated.items() if len(pages) >= threshold}


def should_keep_narrative_block(block: dict, family: str, page_num: int) -> tuple[bool, str]:
    x0 = block["x0"]
    x1 = block["x1"]
    y0 = block["y0"]
    text = block["block_text"]
    simplified = simplify_for_matching(text)

    if not simplified:
        return False, "empty"
    if len(simplified) <= 2 and any(ord(ch) > 127 for ch in text):
        return False, "symbol_noise"
    if is_page_number(text):
        return False, "page_number"
    if is_disclosure_block(text):
        return False, "disclosure"
    if "morningstar equity research" in simplified or "morningstar equity analyst report" in simplified:
        return False, "header"
    if "contents" == simplified:
        return False, "contents"
    if x0 < 160 and y0 < 100 and " (" in text and " | " in text:
        return False, "ticker_header"
    if x0 < 200 and y0 < 145 and has_stop_marker(text, ["Last Price", "Fair Value", "Uncertainty", "Economic Moat", "Moat Trend", "Capital Allocation", "Industry Group"]):
        return False, "top_kpi"
    if x0 >= 380 and y0 < 145 and "ESG Risk Rating" in text:
        return False, "top_badge"

    if family == "sp_capiq":
        if page_num == 1:
            if y0 < 145 and ("last price" in simplified or "fair value" in simplified):
                return False, "top_kpi"
            if x0 >= 380:
                return False, "right_panel"
            if x0 < 160 and 180 <= y0 < 260 and is_analyst_contact_block(text):
                return False, "analyst_meta"
            if x0 < 160 and y0 > 380:
                return False, "sidebar"
            if any(p in text for p in ["Research as of", "Estimates as of", "Pricing data", "Rating updated as of", "Updated Forecasts and Estimates"]):
                return False, "meta_strip"
            if x0 < 160 and y0 >= 260:
                return False, "left_meta"
        else:
            if x0 < 160 and y0 < 220:
                return False, "sidebar_top"
            if x0 < 160 and any(p in text for p in ["Morningstar Analyst Forecasts", "Comparable Company Analysis", "Methodology for Valuing Companies"]):
                return False, "sidebar_nav"
            if x0 >= 380 and is_numeric_heavy(text):
                return False, "table_panel"
            if x0 >= 300 and is_financial_statement_heading(text):
                return False, "forecast_table"
        if any(p in text for p in ["Vital Statistics", "Historical/forecast data sources", "Currency amounts expressed with", "The primary analyst covering this company"]):
            return False, "noise"

    elif family == "equity_research":
        if page_num == 1:
            if "morningstar equity research" in simplified or "morningstar equity analyst note" in simplified:
                return False, "header"
            if x0 < 380 and y0 < 130 and (
                has_stop_marker(
                    text,
                    [
                        "Morningstar Rating",
                        "Last Price",
                        "Fair Value",
                        "Uncertainty",
                        "Economic Moat",
                        "Moat Trend",
                        "Capital Allocation",
                        "Industry Group",
                        "ESG Risk Rating",
                    ],
                )
                or is_numeric_heavy(text)
            ):
                return False, "top_panel"
            # On these first-page Morningstar layouts the real narrative column
            # often starts around x0 ~= 169, so only treat the far-left gutter
            # as sidebar/meta content.
            if x0 < 160 and 130 <= y0 < 460:
                return False, "sidebar"
            if x0 >= 380:
                return False, "right_panel"
            if x0 < 160 and y0 > 430:
                return False, "sidebar"
            if x0 < 160 and y0 >= 260:
                return False, "left_meta"
            if x0 >= 190 and y0 >= 500 and (
                "Financial Summary and Key Statistics" in text
                or is_numeric_heavy(text)
                or has_stop_marker(
                    text,
                    [
                        "Price/Earnings",
                        "Price/Book",
                        "Adjusted EBITDA",
                        "Revenue Growth %",
                        "Earnings Per Share",
                    ],
                )
            ):
                return False, "summary_table"
            if "important disclosure" in simplified:
                return False, "important_disclosure"
            if any(p in text for p in ["Research as of", "Estimates as of", "Pricing data through", "Pricing data as of", "Rating updated as of", "Updated Forecasts and Estimates"]):
                return False, "meta_strip"
        else:
            if is_pull_quote_block(x0, x1, text):
                return False, "pull_quote"
            if x0 < 160:
                return False, "sidebar"
            if x0 < 160 and any(p in text for p in ["Morningstar Analyst Forecasts", "Comparable Company Analysis", "Methodology for Valuing Companies"]):
                return False, "sidebar_nav"
            if x0 <= 360 and y0 <= 260 and is_equity_research_panel_block(text):
                return False, "valuation_panel"
            if x0 >= 380 and is_numeric_heavy(text) and not is_prose_like_block(text):
                return False, "table_panel"
            if x0 >= 169 and (
                text.startswith("Forecast")
                or text.startswith("Fiscal Year Ends in")
                or text.startswith("Income Statement")
                or text.startswith("Balance Sheet")
                or text.startswith("Cash Flow Statement")
            ):
                return False, "forecast_heading"
            if x0 >= 169 and is_numeric_heavy(text) and not is_prose_like_block(text) and (
                y0 > 180
                or has_stop_marker(
                    text,
                    [
                        "Revenue",
                        "EBIT",
                        "EBITDA",
                        "Net Income",
                        "Operating Margin",
                        "Debt/Capital",
                        "Price/Earnings",
                        "Fair Value per Share",
                    ],
                )
            ):
                return False, "forecast_table"
        if any(p in text for p in ["Vital Statistics", "Historical/forecast data sources", "Currency amounts expressed with", "The primary analyst covering this company", "Important Disclosure", "http://global.morningstar", "equitydisclosures"]):
            return False, "noise"

    elif family == "analyst_report":
        if page_num == 1:
            if y0 < 40:
                return False, "report_header"
            if y0 > 720:
                return False, "footer_symbol"
            if is_analyst_report_top_panel_block(x0, x1, y0, text):
                return False, "top_panel"
            if is_analyst_report_metric_panel_block(x0, text):
                return False, "metric_panel"
            if is_analyst_report_table_block(x0, text):
                return False, "table_block"
            if is_analyst_report_bullet_sidebar_block(x0, x1, text):
                return False, "sidebar_bullet"
            if x0 < 205 and y0 >= 120:
                return False, "sidebar"
            if "important disclosure" in simplified:
                return False, "important_disclosure"
        else:
            if y0 > 720:
                return False, "footer_symbol"
            if is_analyst_report_top_panel_block(x0, x1, y0, text):
                return False, "top_panel"
            if is_analyst_report_table_block(x0, text):
                return False, "table_block"
            if is_analyst_report_metric_panel_block(x0, text):
                return False, "metric_panel"
            if is_analyst_report_bullet_sidebar_block(x0, x1, text):
                return False, "sidebar_bullet"
            if is_analyst_report_sidebar_block(x0, x1, y0, text):
                return False, "sidebar"
            if x0 < 200 and any(p in text for p in ["Financials", "Research Methodology for Valuing Companies", "Analyst Notes Archive"]):
                return False, "sidebar_nav"
            if x0 < 200 and is_numeric_heavy(text):
                return False, "chart_axis"
        if any(p in text for p in ["The primary analyst covering this company", "Important Disclosure", "Price vs. Fair Value", "Total Return % as of", "Last Close:", "Over Valued", "Under Valued"]):
            return False, "noise"
        if is_numeric_heavy(text) and x0 < 220:
            return False, "chart_axis"

    return True, ""


def order_page_blocks(blocks: list[dict], family: str) -> list[dict]:
    if family == "analyst_report":
        text_blocks = [b for b in blocks if len(b["block_text"]) >= 20]
        left_hits = sum(1 for b in text_blocks if 200 <= b["x0"] < 360)
        right_hits = sum(1 for b in text_blocks if b["x0"] >= 360)
        has_two_columns = left_hits >= 2 and right_hits >= 2

        if not has_two_columns:
            return sorted(blocks, key=lambda item: (round(item["y0"], 1), item["x0"]))

        top_band = [b for b in blocks if b["y0"] < 160]
        left_col = [b for b in blocks if b not in top_band and b["x0"] < 360]
        right_col = [b for b in blocks if b not in top_band and b["x0"] >= 360]
        top_left = [b for b in top_band if b["x0"] < 360]
        top_right = [b for b in top_band if b["x0"] >= 360]

        ordered = []
        ordered.extend(sorted(top_left, key=lambda item: (round(item["y0"], 1), item["x0"])))
        ordered.extend(sorted(top_right, key=lambda item: (round(item["y0"], 1), item["x0"])))
        ordered.extend(sorted(left_col, key=lambda item: (round(item["y0"], 1), item["x0"])))
        ordered.extend(sorted(right_col, key=lambda item: (round(item["y0"], 1), item["x0"])))
        return ordered

    if family not in {"equity_research", "sp_capiq"}:
        return sorted(blocks, key=lambda item: (round(item["y0"], 1), item["x0"]))

    text_blocks = [b for b in blocks if len(b["block_text"]) >= 20]
    left_hits = sum(1 for b in text_blocks if 150 <= b["x0"] < 330)
    right_hits = sum(1 for b in text_blocks if b["x0"] >= 330)
    has_two_columns = (left_hits >= 2 and right_hits >= 2) or (left_hits >= 3 and right_hits >= 1)

    if not has_two_columns:
        return sorted(blocks, key=lambda item: (round(item["y0"], 1), item["x0"]))

    top_band = [b for b in blocks if b["y0"] < 175]
    top_left = [b for b in top_band if b["x0"] < 330]
    top_right = [b for b in top_band if b["x0"] >= 330]
    left_col = [b for b in blocks if b not in top_band and b["x0"] < 330]
    right_col = [b for b in blocks if b not in top_band and b["x0"] >= 330]

    ordered = []
    ordered.extend(sorted(top_left, key=lambda item: (round(item["y0"], 1), item["x0"])))
    ordered.extend(sorted(top_right, key=lambda item: (round(item["y0"], 1), item["x0"])))
    ordered.extend(sorted(left_col, key=lambda item: (round(item["y0"], 1), item["x0"])))
    ordered.extend(sorted(right_col, key=lambda item: (round(item["y0"], 1), item["x0"])))
    return ordered


def is_equity_research_stop_page(blocks: list[dict], page_num: int) -> bool:
    if page_num <= 1:
        return False

    prose_blocks = page_prose_block_count(blocks)
    if is_methodology_or_appendix_page(blocks):
        return True
    if page_has_marker(blocks, ["General Disclosure", "Risk Warning"]) and prose_blocks <= 1:
        return True
    return False


def is_sp_capiq_stop_page(blocks: list[dict], page_num: int) -> bool:
    if page_num <= 1:
        return False

    prose_blocks = page_prose_block_count(blocks)
    if is_methodology_or_appendix_page(blocks):
        return True
    if page_has_marker(blocks, ["General Disclosure", "Risk Warning"]) and prose_blocks <= 1:
        return True
    return False


def is_analyst_report_stop_page(blocks: list[dict], page_num: int) -> bool:
    if page_num <= 1:
        return False

    if is_analyst_report_peer_page(blocks, page_num):
        return True

    for block in blocks:
        text = block["block_text"]
        x0 = block["x0"]
        y0 = block["y0"]
        in_appendix_zone = 320 <= y0 <= 720
        if x0 <= 260 and in_appendix_zone and (
            has_stop_marker(
                text,
                [
                    "Last Price: Price of the stock as of the close",
                    "Other Definitions:",
                    "Analyst-Driven %",
                    "Quantitative Fair Value Estimate",
                    "Economic Moat Rating",
                    "Please see the Analyst Profile",
                    "The Morningstar Rating for stocks",
                ],
            )
            or text.startswith("Last Price:")
        ):
            return True
        if x0 <= 220 and 500 <= y0 <= 720 and has_stop_marker(
            text,
            [
                "Important Disclosure",
                "Unless otherwise provided in a separate agreement",
            ],
        ):
            return True
        if has_stop_marker(
            text,
            [
                "Four key components drive the Morningstar Rating",
                "The concept of an economic moat plays a vital role",
                "Our model is divided into three distinct stages",
                "Stage I: Explicit Forecast",
                "Qualitative Analysis Uncertainty Ratings",
                "The Morningstar Star Ratings for Stocks are defined below",
                "Quantitative Equity Research Overview",
                "Risk Warning",
                "Morningstar Historical Summary",
                "Morningstar Analyst Historical/Forecast Summary",
                "Operating Performance / Profitability as of",
                "Forward Valuation Estimates",
                "Quarterly Revenue & EPS",
                "Revenue Growth Year On Year %",
            ],
        ):
            return True
        if x0 <= 260 and y0 >= 60 and has_stop_marker(
            text,
            [
                "Other Definitions",
                "Morningstar Star Rating for Stocks",
                "The Morningstar Rating for stocks",
            ],
        ):
            return True
        if 380 <= x0 <= 620 and 450 <= y0 <= 720 and has_stop_marker(
            text,
            [
                "Last Price:",
                "Capital Allocation Rating:",
                "Our star ratings are guideposts",
                "Market Price",
                "Please note, there is no predefined distribution of stars",
            ],
        ):
            return True
    return False


def extract_report(pdf_path: Path) -> dict | None:
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        print(f"Could not open {pdf_path.name}: {exc}")
        return None

    if doc.page_count == 0:
        doc.close()
        return None

    first_page_text = normalize_text(doc.load_page(0).get_text("text") or "")
    family = detect_family(first_page_text)
    metadata = extract_metadata(first_page_text, pdf_path.name)
    repeated_margin_text = collect_repeated_margin_text(doc)
    num_pages = doc.page_count

    raw_pages = []
    narrative_pages = []
    block_rows = []
    raw_text_parts = []
    narrative_text_parts = []
    narrative_stopped = False

    for page_idx in range(doc.page_count):
        page = doc.load_page(page_idx)
        page_num = page_idx + 1
        page_height = float(page.rect.height)
        raw_kept = []
        narrative_kept = []
        page_blocks = []

        for block_idx, block in enumerate(page.get_text("blocks") or []):
            if len(block) < 7:
                continue
            x0, y0, x1, y1, text, _, block_type = block
            if block_type != 0:
                continue

            block_text = normalize_block_text(text)
            if not block_text:
                continue

            simplified = simplify_for_matching(block_text)
            keep_raw = 1
            raw_drop_reason = ""
            if simplified in repeated_margin_text and (y0 < 70 or y1 > page_height - 45):
                keep_raw = 0
                raw_drop_reason = "repeated_margin_text"
            elif is_page_number(block_text):
                keep_raw = 0
                raw_drop_reason = "page_number"

            keep_narrative, narrative_drop_reason = should_keep_narrative_block(
                {
                    "x0": float(x0),
                    "x1": float(x1),
                    "y0": float(y0),
                    "y1": float(y1),
                    "block_text": block_text,
                },
                family,
                page_num,
            )

            row = {
                "source_file": pdf_path.name,
                "family": family,
                "page_idx": page_idx,
                "page_num": page_num,
                "block_idx": block_idx,
                "x0": float(x0),
                "y0": float(y0),
                "x1": float(x1),
                "y1": float(y1),
                "width": float(x1 - x0),
                "height": float(y1 - y0),
                "block_text": block_text,
                "block_text_len": len(block_text),
                "keep_raw": keep_raw,
                "raw_drop_reason": raw_drop_reason,
                "keep_narrative": 1 if keep_narrative else 0,
                "narrative_drop_reason": narrative_drop_reason,
            }
            if narrative_stopped and row["keep_narrative"] == 1:
                row["keep_narrative"] = 0
                row["narrative_drop_reason"] = "after_narrative_stop"
            block_rows.append(row)
            page_blocks.append(row)

            if keep_raw:
                raw_kept.append(row)
            if row["keep_narrative"] == 1:
                narrative_kept.append(row)

        page_drop_narrative = False
        page_has_narrative_stop = False
        if family == "equity_research":
            page_drop_narrative = should_drop_equity_research_page(page_blocks, page_num)
        elif family == "sp_capiq":
            page_drop_narrative = should_drop_sp_capiq_page(page_blocks, page_num)
        if family == "equity_research":
            page_has_narrative_stop = is_equity_research_stop_page(page_blocks, page_num)
        elif family == "sp_capiq":
            page_has_narrative_stop = is_sp_capiq_stop_page(page_blocks, page_num)
        elif family == "analyst_report":
            page_has_narrative_stop = is_analyst_report_stop_page(page_blocks, page_num)
        if page_drop_narrative or page_has_narrative_stop:
            for row in page_blocks:
                if row["keep_narrative"] == 1:
                    row["keep_narrative"] = 0
                    row["narrative_drop_reason"] = "stop_page" if page_has_narrative_stop else "drop_page"
            narrative_kept = []

        raw_kept = order_page_blocks(raw_kept, family)
        narrative_kept = order_page_blocks(narrative_kept, family)

        raw_page_text = normalize_text("\n\n".join(item["block_text"] for item in raw_kept).strip())
        full_page_text = clean_full_report_text(raw_page_text)
        narrative_page_text = normalize_text("\n\n".join(item["block_text"] for item in narrative_kept).strip())
        narrative_page_text = clean_narrative_text(narrative_page_text)

        raw_pages.append(
            {
                "source_file": pdf_path.name,
                "family": family,
                "page_idx": page_idx,
                "page_num": page_num,
                "page_text": raw_page_text,
                "page_text_len": len(raw_page_text),
                "text_type": "raw",
            }
        )
        raw_pages.append(
            {
                "source_file": pdf_path.name,
                "family": family,
                "page_idx": page_idx,
                "page_num": page_num,
                "page_text": full_page_text,
                "page_text_len": len(full_page_text),
                "text_type": "full_report",
            }
        )
        if narrative_page_text:
            narrative_pages.append(
                {
                    "source_file": pdf_path.name,
                    "family": family,
                    "page_idx": page_idx,
                    "page_num": page_num,
                    "page_text": narrative_page_text,
                    "page_text_len": len(narrative_page_text),
                    "text_type": "narrative",
                }
            )

        raw_text_parts.append(raw_page_text)
        narrative_text_parts.append(narrative_page_text)
        if page_has_narrative_stop:
            narrative_stopped = True

    doc.close()

    return {
        "source_file": pdf_path.name,
        "full_path": str(pdf_path),
        "family": family,
        "metadata": metadata,
        "num_pages": num_pages,
        "raw_text": "\n\n".join(text for text in raw_text_parts if text).strip(),
        "full_report_text": clean_full_report_text("\n\n".join(text for text in raw_text_parts if text).strip()),
        "narrative_text": clean_narrative_text("\n\n".join(text for text in narrative_text_parts if text).strip()),
        "pages": raw_pages + narrative_pages,
        "blocks": block_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def append_csv_rows(csv_path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    if not rows:
        return
    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def safe_preview_name(pdf_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", pdf_path.stem).strip("._")
    stem = stem[:80] or "report"
    digest = hashlib.md5(str(pdf_path).encode("utf-8")).hexdigest()[:10]
    return f"{stem}_{digest}_narrative_preview.txt"


def write_qa_summary(narrative_csv: Path, block_csv: Path, qa_csv: Path) -> None:
    narrative_df = pd.read_csv(narrative_csv, usecols=["family", "narrative_text_len"])
    narrative_stats = (
        narrative_df.groupby("family")["narrative_text_len"]
        .agg(["count", "mean", "min", "max"])
        .reset_index()
    )

    drop_counts: dict[str, dict[str, int]] = {}
    for chunk in pd.read_csv(
        block_csv,
        usecols=["family", "keep_narrative", "narrative_drop_reason"],
        chunksize=200000,
    ):
        dropped = chunk[chunk["keep_narrative"] == 0]
        grouped = dropped.groupby(["family", "narrative_drop_reason"]).size()
        for (family, reason), count in grouped.items():
            family_counts = drop_counts.setdefault(family, {})
            family_counts[reason] = family_counts.get(reason, 0) + int(count)

    qa_rows = []
    for row in narrative_stats.itertuples(index=False):
        family = row.family
        reason_counts = drop_counts.get(family, {})
        qa_rows.append(
            {
                "family": family,
                "report_count": int(row.count),
                "mean_narrative_len": round(float(row.mean), 1),
                "min_narrative_len": int(row.min),
                "max_narrative_len": int(row.max),
                "top_drop_reasons": "; ".join(
                    f"{reason}:{count}"
                    for reason, count in sorted(
                        reason_counts.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )[:6]
                ),
            }
        )

    pd.DataFrame(qa_rows).to_csv(qa_csv, index=False, encoding="utf-8")


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir
    output_dir = args.output_dir
    raw_csv = output_dir / "reports_raw.csv"
    narrative_csv = output_dir / "reports_narrative.csv"
    page_csv = output_dir / "pages.csv"
    block_csv = output_dir / "blocks.csv"
    jsonl_path = output_dir / "reports.jsonl"
    qa_csv = output_dir / "qa_summary.csv"
    preview_dir = output_dir / "previews"

    pdf_files = sorted(input_dir.rglob("*.pdf") if args.recursive else input_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"No PDFs found in {input_dir}")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    raw_fields = [
        "source_file",
        "full_path",
        "family",
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
        "family",
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
        "family",
        "page_idx",
        "page_num",
        "page_text",
        "page_text_len",
        "text_type",
    ]
    block_fields = [
        "source_file",
        "family",
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
    else:
        for path in [raw_csv, narrative_csv, page_csv, block_csv, jsonl_path]:
            if path.exists():
                path.unlink()

    qa_state: dict[str, dict] = {}
    if completed_paths:
        print(f"Resuming with {len(completed_paths)} completed PDFs already on disk")

    print(f"Found {len(pdf_files)} PDFs")
    for idx, pdf_path in enumerate(pdf_files, start=1):
        if str(pdf_path) in completed_paths:
            continue
        print(f"[{idx}/{len(pdf_files)}] {pdf_path.name}")
        result = extract_report(pdf_path)
        if result is None:
            continue

        meta = result["metadata"]
        raw_row = {
            "source_file": result["source_file"],
            "full_path": result["full_path"],
            "family": result["family"],
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
            "family": result["family"],
            "company": meta.get("company"),
            "ticker": meta.get("ticker"),
            "analyst": meta.get("analyst"),
            "report_date": meta.get("report_date"),
            "num_pages": result["num_pages"],
            "narrative_text_len": len(result["narrative_text"]),
            "narrative_text": result["narrative_text"],
        }
        append_csv_rows(raw_csv, [raw_row], raw_fields)
        append_csv_rows(narrative_csv, [narrative_row], narrative_fields)
        append_csv_rows(page_csv, result["pages"], page_fields)
        append_csv_rows(block_csv, result["blocks"], block_fields)
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "source_file": result["source_file"],
                        "full_path": result["full_path"],
                        "family": result["family"],
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

        family_state = qa_state.setdefault(
            result["family"],
            {
                "report_count": 0,
                "narrative_lengths": [],
                "drop_counts": {},
            },
        )
        family_state["report_count"] += 1
        family_state["narrative_lengths"].append(narrative_row["narrative_text_len"])
        for block in result["blocks"]:
            if block["keep_narrative"] == 0:
                reason = block["narrative_drop_reason"]
                family_state["drop_counts"][reason] = family_state["drop_counts"].get(reason, 0) + 1

        preview_text = []
        for page in [p for p in result["pages"] if p["text_type"] == "narrative"][:PREVIEW_PAGES]:
            preview_text.append(f"===== PAGE {page['page_num']} =====\n{page['page_text']}")
        try:
            (preview_dir / safe_preview_name(pdf_path)).write_text(
                "\n\n".join(preview_text).strip(),
                encoding="utf-8",
            )
        except OSError:
            pass

    write_qa_summary(narrative_csv, block_csv, qa_csv)

    print("\nSaved files:")
    print(f"- {raw_csv}")
    print(f"- {narrative_csv}")
    print(f"- {page_csv}")
    print(f"- {block_csv}")
    print(f"- {jsonl_path}")
    print(f"- {qa_csv}")
    print(f"- previews in {preview_dir}")

    if narrative_csv.exists():
        narrative_df = pd.read_csv(narrative_csv)
        print("\nNarrative preview:")
        print(
            narrative_df[
                ["source_file", "family", "narrative_text_len"]
            ].head(10).to_string(index=False)
        )


if __name__ == "__main__":
    main()
