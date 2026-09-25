#!/usr/bin/env python3
"""
Download SDS/label PDFs referenced in unique_products.json and pre-parse them with
unstructured (https://unstructured.io), so the sds-research.js Workflow gets clean
extracted text instead of relying on each agent's own live web-fetch of a PDF
(which is unreliable for multi-column Section 9/11 tables).

Usage:
    python3 collect_pdfs.py <unique_products.json> [options]

    # then feed the enriched file straight into the Workflow instead of the plain one:
    Workflow({ scriptPath: ".claude/workflows/sds-research.js",
               args: { products: <unique_products_with_pdfs.json content>, fields: [...] } })

Options:
    --out-dir DIR          Where to store downloaded PDFs/text/manifest (default:
                            <dir of input>/pdfs)
    --url-fields LIST      Comma-separated product-record keys to treat as candidate
                            PDF URLs (default: tox_source_url,ingredient_source,
                            supplemental_source_url,doc_source,source_url)
    --max-per-product N    Max PDFs fetched per product (default: 3)
    --excerpt-chars N      Max characters of extracted text embedded into
                            unique_products_with_pdfs.json per PDF (default: 4000;
                            the full text is always kept in the .txt file)
    --strategy STR         unstructured partition_pdf strategy: fast|hi_res|ocr_only
                            (default: fast - no extra ML deps required)
    --timeout N            Per-request HTTP timeout in seconds (default: 30)
    --max-bytes N          Skip download if Content-Length exceeds this (default:
                            26214400 = 25MB)

Only fetches URLs that look like a PDF (path ends in .pdf, ignoring query string) or
whose Content-Type header is application/pdf - this is a PDF collector, not a general
scraper. Every URL is downloaded at most once even if multiple products/fields cite it
(dedup by URL). Failures (404, timeout, not-actually-a-PDF, parse error) are recorded in
manifest.json per URL and never raise - one bad source must not stop the whole batch.

Requires: pip install "unstructured[pdf]" requests
"""
import argparse
import hashlib
import json
import os
import re
import sys

import requests

DEFAULT_URL_FIELDS = [
    "tox_source_url", "ingredient_source", "supplemental_source_url",
    "doc_source", "source_url",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; formulation-harness-pdf-collector/1.0; "
        "+internal SDS research tool)"
    )
}


def is_pdf_url(url):
    if not url or not isinstance(url, str):
        return False
    path = url.split("?", 1)[0].split("#", 1)[0]
    return path.lower().endswith(".pdf")


def slugify(product_name, url):
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(product_name or "unknown"))[:60].strip("_")
    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
    return f"{base or 'unknown'}_{url_hash}"


def download_pdf(url, dest_path, timeout, max_bytes):
    with requests.get(url, headers=HEADERS, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "")
        content_length = resp.headers.get("Content-Length")
        if content_length and int(content_length) > max_bytes:
            raise ValueError(f"Content-Length {content_length} exceeds --max-bytes {max_bytes}")
        total = 0
        chunks = []
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"download exceeded --max-bytes {max_bytes}")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data.startswith(b"%PDF") and "pdf" not in content_type.lower():
            raise ValueError(f"response is not a PDF (Content-Type={content_type!r})")
        with open(dest_path, "wb") as f:
            f.write(data)


def parse_pdf(pdf_path, strategy):
    from unstructured.partition.pdf import partition_pdf

    elements = partition_pdf(filename=pdf_path, strategy=strategy)
    texts = [str(el).strip() for el in elements if str(el).strip()]
    n_tables = sum(1 for el in elements if el.category == "Table")
    return "\n".join(texts), len(elements), n_tables


def fetch_and_parse(url, out_dir, strategy, timeout, max_bytes, cache):
    if url in cache:
        return cache[url]

    slug = slugify(cache.get("_hint_name", ""), url)
    pdf_path = os.path.join(out_dir, f"{slug}.pdf")
    txt_path = os.path.join(out_dir, f"{slug}.txt")
    result = {"url": url, "pdf_path": None, "text_path": None,
              "n_elements": 0, "n_tables": 0, "chars": 0, "error": None}

    try:
        if not os.path.exists(pdf_path):
            download_pdf(url, pdf_path, timeout, max_bytes)
        text, n_elements, n_tables = parse_pdf(pdf_path, strategy)
        with open(txt_path, "w") as f:
            f.write(text)
        result.update({
            "pdf_path": pdf_path, "text_path": txt_path,
            "n_elements": n_elements, "n_tables": n_tables, "chars": len(text),
        })
    except Exception as e:
        result["error"] = str(e)
        if os.path.exists(pdf_path) and result["text_path"] is None:
            # keep the downloaded file around for manual inspection even if parsing failed
            result["pdf_path"] = pdf_path

    cache[url] = result
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("unique_products_json")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--url-fields", default=",".join(DEFAULT_URL_FIELDS))
    ap.add_argument("--max-per-product", type=int, default=3)
    ap.add_argument("--excerpt-chars", type=int, default=4000)
    ap.add_argument("--strategy", default="fast", choices=["fast", "hi_res", "ocr_only"])
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--max-bytes", type=int, default=26214400)
    args = ap.parse_args()

    try:
        import unstructured  # noqa: F401
    except ImportError:
        print(
            'ERROR: the "unstructured" package is not installed.\n'
            '  pip install "unstructured[pdf]"\n'
            "See https://unstructured.io for details.",
            file=sys.stderr,
        )
        sys.exit(1)

    in_path = args.unique_products_json
    in_dir = os.path.dirname(os.path.abspath(in_path)) or "."
    out_dir = args.out_dir or os.path.join(in_dir, "pdfs")
    os.makedirs(out_dir, exist_ok=True)

    url_fields = [f.strip() for f in args.url_fields.split(",") if f.strip()]
    products = json.load(open(in_path))

    cache = {}
    manifest = {}
    enriched = []

    for product in products:
        name = product.get("product_name")
        cache["_hint_name"] = name
        seen_urls = set()
        candidates = []
        for field in url_fields:
            url = product.get(field)
            if is_pdf_url(url) and url not in seen_urls:
                seen_urls.add(url)
                candidates.append((field, url))
        candidates = candidates[: args.max_per_product]

        excerpts = []
        manifest_entries = []
        for field, url in candidates:
            r = fetch_and_parse(url, out_dir, args.strategy, args.timeout, args.max_bytes, cache)
            entry = {"source_field": field, **{k: v for k, v in r.items() if k != "url"}, "url": url}
            manifest_entries.append(entry)
            if not r["error"]:
                excerpts.append({
                    "source_field": field,
                    "url": url,
                    "n_tables": r["n_tables"],
                    "text_excerpt": (r["text_path"] and open(r["text_path"]).read()[: args.excerpt_chars]) or "",
                    "full_text_path": r["text_path"],
                })

        if manifest_entries:
            manifest[name] = manifest_entries

        enriched_product = dict(product)
        if excerpts:
            enriched_product["pdf_excerpts"] = excerpts
        enriched.append(enriched_product)

    manifest_path = os.path.join(out_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    enriched_path = os.path.join(in_dir, "unique_products_with_pdfs.json")
    with open(enriched_path, "w") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    n_urls = len({k for k in cache if k != "_hint_name"})
    n_ok = sum(1 for k, v in cache.items() if k != "_hint_name" and not v["error"])
    n_err = n_urls - n_ok
    print(f"Candidate PDF URLs fetched: {n_urls} ({n_ok} ok, {n_err} failed)")
    print(f"Products with at least one parsed PDF: {sum(1 for p in enriched if p.get('pdf_excerpts'))}/{len(enriched)}")
    print(f"Wrote: {manifest_path}")
    print(f"Wrote: {enriched_path}  <- pass this as args.products to sds-research.js")


if __name__ == "__main__":
    main()
