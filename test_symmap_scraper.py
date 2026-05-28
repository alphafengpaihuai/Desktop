"""Single-disease SymMap scraper reconnaissance.

Phase 1 only: hard-coded Asthma test. Do not expand this script into the
510-disease batch until the selectors/endpoints are confirmed by the user.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


BASE_URL = "http://www.symmap.org"
SEARCH_URL = urljoin(BASE_URL, "/search/")
RELATED_COMPONENTS_URL = urljoin(BASE_URL, "/related_components/")
QUERY = "Asthma"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
}


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)

    print("=" * 80)
    print(f"SymMap single-disease reconnaissance: {QUERY}")
    print("=" * 80)

    print("\n[1] GET search page and inspect forms")
    search_page = session.get(SEARCH_URL, timeout=30)
    search_page.raise_for_status()
    print_response_summary(search_page)
    print_search_forms(search_page.text)

    print("\n[2] POST Disease search for Asthma")
    search_payload = {"table_name": "Disease", "key": QUERY}
    search_response = session.post(
        SEARCH_URL,
        data=search_payload,
        headers={"Referer": SEARCH_URL, "X-Requested-With": "XMLHttpRequest"},
        timeout=30,
    )
    search_response.raise_for_status()
    print_response_summary(search_response)
    print_html_or_json_preview(search_response.text)

    search_rows = parse_search_rows(search_response.text)
    print(f"Search rows found: {len(search_rows)}")
    for row in search_rows[:5]:
        print(f"  - {row}")

    disease_id = pick_disease_id(search_rows, QUERY)
    if not disease_id:
        raise RuntimeError("No SMDE disease id found for Asthma")
    detail_url = urljoin(BASE_URL, f"/detail/{disease_id}")
    print(f"Selected disease_id: {disease_id}")
    print(f"Detail URL: {detail_url}")

    print("\n[3] GET disease detail page and inspect HTML structure")
    detail_response = session.get(detail_url, headers={"Referer": SEARCH_URL}, timeout=30)
    detail_response.raise_for_status()
    print_response_summary(detail_response)
    inspect_detail_html(detail_response.text)

    soup = BeautifulSoup(detail_response.text, "html.parser")
    req_name = soup.select_one("#req_name")
    rrid = req_name.get("value", "").strip() if req_name else disease_id
    print(f"Related components rrid from #req_name: {rrid}")

    print("\n[4] POST related_components for Effective Herbs")
    herbs = fetch_related_components(session, rrid=rrid, table_name="Herb", referer=detail_url)
    print_component_rows("Effective Herbs", herbs, fields=[
        "Herb_id", "Chinese_name", "Pinyin_name", "Latin_name", "English_name",
        "Value", "P_value", "FDR(BH)", "Relationship",
    ])

    print("\n[5] POST related_components for Molecular Targets")
    targets = fetch_related_components(session, rrid=rrid, table_name="Gene", referer=detail_url)
    print_component_rows("Molecular Targets", targets, fields=[
        "Gene_id", "Gene_symbol", "Gene_name", "Protein_name", "UniProt_id", "Ensembl_id",
    ])

    print("\n[6] Compact extraction result")
    result = {
        "query": QUERY,
        "disease_id": disease_id,
        "detail_url": detail_url,
        "herb_count": len(herbs),
        "target_count": len(targets),
        "top_herbs": herbs[:10],
        "top_targets": targets[:10],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2)[:5000])


def print_response_summary(response: requests.Response) -> None:
    print(f"URL: {response.url}")
    print(f"Status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('content-type')}")
    print(f"Length: {len(response.text)}")


def print_html_or_json_preview(text: str, limit: int = 1200) -> None:
    preview = text[:limit].replace("\n", " ")
    print(f"Raw preview ({min(len(text), limit)} chars):")
    print(preview)


def print_search_forms(html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    print(f"Forms found: {len(forms)}")
    for form in forms:
        form_id = form.get("id")
        hidden = {
            item.get("name"): item.get("value")
            for item in form.select("input[type='hidden']")
            if item.get("name")
        }
        text_inputs = [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "placeholder": item.get("placeholder"),
            }
            for item in form.select("input[type='text']")
        ]
        print(f"  form #{form_id}: hidden={hidden}, text_inputs={text_inputs}")


def parse_search_rows(text: str) -> list[dict[str, Any]]:
    """SymMap search currently returns JSON text with text/html content-type.

    A BeautifulSoup fallback is kept because the site may return literal HTML
    on some deployments or after anti-bot changes.
    """
    try:
        payload = json.loads(text)
        rows = payload.get("data", [])
        return rows if isinstance(rows, list) else []
    except json.JSONDecodeError:
        pass

    soup = BeautifulSoup(text, "html.parser")
    rows: list[dict[str, Any]] = []
    for link in soup.select("a[href*='/detail/SMDE']"):
        href = link.get("href", "")
        match = re.search(r"SMDE\d+", href)
        if match:
            rows.append({
                "Disease_id": match.group(0),
                "Disease_name": link.get_text(" ", strip=True),
                "href": urljoin(BASE_URL, href),
            })
    return rows


def pick_disease_id(rows: list[dict[str, Any]], query: str) -> str:
    if not rows:
        return ""
    exact = [
        row for row in rows
        if str(row.get("Disease_name", "")).strip().lower() == query.lower()
    ]
    selected = exact[0] if exact else rows[0]
    return str(selected.get("Disease_id", "")).strip()


def inspect_detail_html(html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    headings = [h.get_text(" ", strip=True) for h in soup.find_all(["h1", "h2", "h3", "h4"])]
    print("Headings:")
    for heading in headings[:12]:
        print(f"  - {heading}")

    tables = soup.find_all("table")
    print(f"Tables found: {len(tables)}")
    for idx, table in enumerate(tables):
        text = table.get_text(" ", strip=True)
        print(f"  table[{idx}] id={table.get('id')} class={table.get('class')} text={text[:180]}")

    buttons = [button.get_text(" ", strip=True) for button in soup.select("#button_select_group button")]
    print(f"Related component buttons: {buttons}")

    scripts = [script.get("src") for script in soup.find_all("script") if script.get("src")]
    useful_scripts = [src for src in scripts if "function_detail" in src or "network_summary" in src]
    print(f"Useful script references: {useful_scripts}")


def fetch_related_components(
    session: requests.Session,
    *,
    rrid: str,
    table_name: str,
    referer: str,
) -> list[dict[str, Any]]:
    response = session.post(
        RELATED_COMPONENTS_URL,
        data={"rrid": rrid, "table_name": table_name, "filter": "0"},
        headers={"Referer": referer, "X-Requested-With": "XMLHttpRequest"},
        timeout=60,
    )
    response.raise_for_status()
    print_response_summary(response)
    data = json.loads(response.text)
    rows = data.get("data", [])
    return rows if isinstance(rows, list) else []


def print_component_rows(title: str, rows: list[dict[str, Any]], fields: list[str]) -> None:
    print(f"{title} rows: {len(rows)}")
    for idx, row in enumerate(rows[:10], start=1):
        compact = {field: row.get(field) for field in fields if field in row}
        print(f"  {idx:02d}. {compact}")


if __name__ == "__main__":
    main()
