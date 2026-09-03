#!/usr/bin/env python3
"""
Fetches public-domain novels/literature from Project Gutenberg and feeds
them through the exact same cleaning + manifest + publish pipeline as
pdf_to_txt_converter.py.

Search has two layers, tried in order:
1. Gutendex (https://gutendex.com) | fast, hands back exact download URLs,
   but is a free community-run service with no uptime guarantee (observed
   error rates are high; treat it as best-effort).
2. Project Gutenberg's own official offline catalog
   (cache/epub/feeds/pg_catalog.csv.gz) | Gutenberg's own docs recommend
   this for automation instead of hitting their site/search directly.
   Downloaded once, cached locally, refreshed weekly. Used automatically
   if Gutendex fails.

Either way, everything returned is genuinely public domain | both sources
only ever surface Project Gutenberg's own catalog, so there's no per-book
copyright judgment call to make here, unlike a general-purpose scraper.

Two compliance details this script handles automatically:
1. Gutenberg's Terms of Use ask that automated tools use a MIRROR, not
   www.gutenberg.org directly (that site is "for human users only" and
   can auto-block scripted access). All downloads route through
   MIRROR_HOST, falling back to the main site (with a warning) only if
   the mirror request fails.
2. Their license only frees a book's text from all PG restrictions once
   BOTH the header/footer boilerplate AND every reference to the name
   "Project Gutenberg" are gone. clean_text() strips the standard banner;
   find_leftover_gutenberg_mentions() (shared with pdf_to_txt_converter.py)
   double-checks nothing else survived before this writes/publishes.

Every outbound request (the offline-catalog download and every book
download attempt, mirror or fallback) goes through _get() /
_get_with_retries(), which always attaches REQUEST_HEADERS there's a
single choke point for the User-Agent rather than a header set ad hoc per
call, so nothing here can accidentally go out with the bare urllib default.

Gutendex specifically is the one exception: it's fronted by Cloudflare, and
testing showed Python's urllib reliably gets its response body silently
withheld until the socket read times out, on the exact same URL where curl
(same machine, same network, any User-Agent) gets a clean 200 in well
under a second. That's the signature of Cloudflare fingerprinting the TLS
handshake itself rather than any header - Python's default TLS client
looks distinct from curl's/a browser's, and gets tarpitted instead of
outright rejected. Since curl demonstrably gets through, search_gutendex()
shells out to curl instead of trying to out-fingerprint Cloudflare in pure
Python. Everything else (mirror downloads, the offline catalog) isn't
behind that same wall and keeps using urllib via _get()/_get_with_retries().

This script must live in the SAME folder as pdfcon.py (imported below as
`conv`) as it reuses clean_text(), limit_blank_lines(),
find_leftover_gutenberg_mentions(), build_manifest(), write_manifest(),
publish_to_github(), derive_group_name(), and the shared DATA_REPO_DIR /
console / rainbow setup, so the two scripts never drift out of sync on
cleaning rules, manifest format, or group-naming logic.
"""
import csv
import gzip
import json
import re
import shutil
import subprocess
import time
import urllib.request
import urllib.parse
from pathlib import Path

import pdfcon as conv
from rich.table import Table

console = conv.console
rainbow = conv.rainbow

GUTENDEX_BASE = "https://gutendex.com/books"

# See https://www.gutenberg.org/policy/terms_of_use.html | automated
# tools are asked to use a mirror rather than www.gutenberg.org directly.
# https://www.gutenberg.org/MIRRORS.ALL has the full current list if this
# one is ever slow/down; swap it here rather than editing calls below.
MIRROR_HOST = "gutenberg.pglaf.org"

# Official offline catalog, per https://www.gutenberg.org/ebooks/offline_catalogs.html
# alternative to search/scraping. Used as a fallback if Gutendex is down.
CATALOG_URL = f"https://{MIRROR_HOST}/cache/epub/feeds/pg_catalog.csv.gz"
CATALOG_CACHE = conv.SCRIPT_DIR / "pg_catalog_cache.csv"
CATALOG_MAX_AGE_DAYS = 7
CATALOG_MAX_RESULTS = 25

REQUEST_HEADERS = {
    # Gutendex (and some mirrors) reject the bare default urllib User-Agent.
    "User-Agent": "Mozilla/5.0 (compatible; personal-library-script/1.0; +local use)",
}

# Be polite between successive downloads rather than hammering the mirror.
DOWNLOAD_DELAY_SECONDS = 1.0

SAVED_GUTENBERG_LIST = conv.SCRIPT_DIR / "Saved_gutenberg_list.txt"


def load_saved_ids() -> set[str]:
    if not SAVED_GUTENBERG_LIST.exists():
        return set()
    return {line.strip() for line in SAVED_GUTENBERG_LIST.read_text(encoding="utf-8").splitlines() if line.strip()}


def mark_saved(gutenberg_id: str):
    with SAVED_GUTENBERG_LIST.open("a", encoding="utf-8") as f:
        f.write(f"{gutenberg_id}\n")


def _get(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read()


def _get_with_retries(url: str, timeout: int, retries: int = 3, backoff: float = 2.0) -> bytes:
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            return _get(url, timeout)
        except Exception as e:
            last_err = e
            if attempt < retries:
                console.log(f"[dim yellow]Request failed (attempt {attempt}/{retries}): {e} | retrying...[/dim yellow]")
                time.sleep(backoff * attempt)
    if last_err is not None:
        raise last_err
    raise RuntimeError("Request failed without an exception")


# Gutendex path
def _get_via_curl(url: str, timeout: int) -> bytes:
    """
    Fetches a URL by shelling out to curl instead of urllib.
    """
    if shutil.which("curl") is None:
        raise FileNotFoundError("curl is not installed or not on PATH")
    result = subprocess.run(
        ["curl", "-sS", "-L", "-m", str(timeout), "-A", REQUEST_HEADERS["User-Agent"], url],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"curl exited {result.returncode}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def search_gutendex(query: str) -> list[dict]:
    url = f"{GUTENDEX_BASE}?search={urllib.parse.quote(query)}"
    try:
        raw = _get_via_curl(url, timeout=20)
    except FileNotFoundError:
        # No curl on this machine then fall back to urllib, even though
        # it's the one known to get tarpitted by Cloudflare here, so a
        # missing curl install doesn't crash the whole search.
        console.log("[dim yellow]curl not found, using urllib for Gutendex (may hang/timeout behind Cloudflare)...[/dim yellow]")
        raw = _get_with_retries(url, timeout=20, retries=2)
    data = json.loads(raw.decode("utf-8"))
    return data.get("results", [])


def pick_plain_text_url(formats: dict) -> str | None:
    """Gutendex lists several encodings per book; prefer UTF-8 plain text, fall back sensibly."""
    for key in ("text/plain; charset=utf-8", "text/plain; charset=us-ascii", "text/plain"):
        if key in formats:
            return formats[key]
    for key, url in formats.items():
        if key.startswith("text/plain"):
            return url
    return None


# Official catalog path (fallback, no external API dependency)
def ensure_catalog_cached() -> Path:
    if CATALOG_CACHE.exists():
        age_days = (time.time() - CATALOG_CACHE.stat().st_mtime) / 86400
        if age_days < CATALOG_MAX_AGE_DAYS:
            return CATALOG_CACHE

    console.log("[dim]Downloading official Project Gutenberg catalog (one-time/weekly, ~14MB)...[/dim]")
    raw_gz = _get_with_retries(CATALOG_URL, timeout=60, retries=2)
    csv_bytes = gzip.decompress(raw_gz)
    CATALOG_CACHE.write_bytes(csv_bytes)
    return CATALOG_CACHE


def search_catalog_offline(query: str) -> list[dict]:
    path = ensure_catalog_cached()
    query_lower = query.lower()
    results = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("Title") or "").strip()
            authors = (row.get("Authors") or "").strip()
            if query_lower in title.lower() or query_lower in authors.lower():
                results.append({
                    "id": row.get("Text#"),
                    "title": title or "?",
                    "authors": [{"name": authors}] if authors else [],
                    "formats": {},  # not known offline | resolved via candidate URLs at download time
                })
            if len(results) >= CATALOG_MAX_RESULTS:
                break
    return results


def catalog_url_candidates(gid: str) -> list[str]:
    """Standard PG file-layout guesses, tried in order, since the offline catalog doesn't list exact URLs."""
    return [
        f"https://{MIRROR_HOST}/cache/epub/{gid}/pg{gid}.txt",
        f"https://{MIRROR_HOST}/files/{gid}/{gid}-0.txt",
        f"https://{MIRROR_HOST}/files/{gid}/{gid}.txt",
    ]


# Downloading + mirror routing
def to_mirror_url(url: str) -> str:
    """Rewrites a www.gutenberg.org download URL to use the mirror instead, per their ToS."""
    parsed = urllib.parse.urlparse(url)
    if parsed.netloc in ("www.gutenberg.org", "gutenberg.org"):
        return parsed._replace(netloc=MIRROR_HOST).geturl()
    return url


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def download_text(url: str) -> str:
    mirror_url = to_mirror_url(url)
    try:
        raw = _get_with_retries(mirror_url, timeout=30, retries=2)
    except Exception as e:
        if mirror_url != url:
            console.log(f"[dim yellow]Mirror fetch failed ({e}), falling back to gutenberg.org directly | this is against their automated-access ToS, use sparingly.[/dim yellow]")
            raw = _get_with_retries(url, timeout=30, retries=2)
        else:
            raise
    return _decode(raw)


def download_text_trying_candidates(urls: list[str]) -> str:
    last_err = None
    for url in urls:
        try:
            return download_text(url)
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("No candidate URLs given")

def show_results(results: list[dict], saved_ids: set[str]) -> None:
    table = Table(title="Search results")
    table.add_column("#", style="bold")
    table.add_column("Title")
    table.add_column("Author")
    table.add_column("Gutenberg ID")
    table.add_column("Status")

    for i, book in enumerate(results, start=1):
        authors = ", ".join(a.get("name", "?") for a in book.get("authors", [])) or "Unknown"
        gid = str(book["id"])
        status = "[dim]already saved[/dim]" if gid in saved_ids else ""
        table.add_row(str(i), book.get("title", "?"), authors, gid, status)

    console.print(table)


def run():
    conv.ensure_data_repo_exists()
    saved_ids = load_saved_ids()

    query = console.input("[#51CF66]Search Project Gutenberg for[/#51CF66] (title/author, or blank to quit): ").strip()
    if not query:
        return

    console.log(f"[dim]Searching Gutendex for[/dim] '{query}'...")
    results = None
    source = None
    try:
        results = search_gutendex(query)
        source = "gutendex"
    except Exception as e:
        console.log(f"[bold dark_red]Gutendex search failed:[/bold dark_red] {e}")
        console.log("[dim]Falling back to the official offline catalog...[/dim]")
        try:
            results = search_catalog_offline(query)
            source = "catalog"
        except Exception as e2:
            console.log(f"[bold dark_red]Catalog fallback also failed:[/bold dark_red] {e2}")
            return

    if not results:
        console.log("[dim bright_black]No results.[/dim bright_black]")
        return

    show_results(results, saved_ids)

    choice_input = console.input(
        "\n[#b5e3fb]Enter numbers to download[/#b5e3fb] ([bright_yellow]comma-separated, blank = cancel[/bright_yellow]): "
    ).strip()
    if not choice_input:
        return

    try:
        choices = [int(x.strip()) for x in choice_input.split(",") if x.strip()]
    except ValueError:
        console.log("[#b43cb8]Please enter valid numbers.[/#b43cb8]")
        return

    selected = [results[c - 1] for c in choices if 1 <= c <= len(results)]
    if not selected:
        console.log("[#b43cb8]No valid selections.[/#b43cb8]")
        return

    # Show exactly what's about to be named/grouped, instead of the group
    # and base-name prompts below floating with no context.
    console.print("\n[bold #74c7ec]Selected for download:[/bold #74c7ec]")
    for book in selected:
        authors = ", ".join(a.get("name", "?") for a in book.get("authors", [])) or "Unknown"
        console.print(f"  [#FF8C42]-{book.get('title', '?')}[/#FF8C42] [dim]by {authors}[/dim] (Gutenberg #{book['id']})")

    custom_group = console.input(
        "\n[#cf2c2f]Group/series name[/#cf2c2f] for the books listed above "
        "([bright_yellow]blank = auto-detect per title, X = exit[/bright_yellow]): "
    ).strip()
    if custom_group.lower() == "x":
        console.log("[#ee243e]Conversion is [strike]cancelled[/strike] by user[/#ee243e]")
        return

    # Group either by the single name the user gave, or per-title
    # auto-detection.
    groups: dict[str, list[dict]] = {}
    for book in selected:
        title = book.get("title", "?")
        group_name = custom_group if custom_group else conv.derive_group_name(title)
        groups.setdefault(group_name, []).append(book)

    group_base_names = {}
    for group_name, books_in_group in groups.items():
        console.print(f"\n[bold #eda90c]Group '{group_name}'[/bold #eda90c] contains:")
        for book in books_in_group:
            console.print(f"  [#FF8C42]-{book.get('title', '?')}[/#FF8C42] (Gutenberg #{book['id']})")
        while True:
            base_name = console.input(
                f"[bold cyan]Base name[/bold cyan] for group [bold #eda90c]'{group_name}'[/bold #eda90c] ([#74c7ec]max 22 chars[/#74c7ec]): "
            ).strip()
            if not base_name:
                console.log("[italic red]Base name cannot be empty[/italic red]")
                continue
            if len(base_name) > 22:
                console.log("[bold bright_red]Base name should not be more than 22 characters[/bold bright_red]")
                continue
            group_base_names[group_name] = base_name
            break

    downloaded_any = False
    for group_name, books_in_group in groups.items():
        output_path = conv.DATA_REPO_DIR / group_name
        output_path.mkdir(parents=True, exist_ok=True)
        base_name = group_base_names[group_name]

        existing_volumes = sorted(output_path.glob(f"{re.escape(base_name)} V*P.txt"))
        next_volume = len(existing_volumes) + 1

        for i, book in enumerate(books_in_group):
            gid = str(book["id"])
            title = book.get("title", "?")

            if gid in saved_ids:
                console.log(f"[yellow][SKIP][/yellow] '{title}' (Gutenberg #{gid}) already downloaded before")
                continue

            if source == "gutendex":
                text_url = pick_plain_text_url(book.get("formats", {}))
                candidates = [text_url] if text_url else []
            else:
                candidates = catalog_url_candidates(gid)

            if not candidates:
                console.log(f"[dim yellow]No plain-text format available for[/dim yellow] '{title}', skipping")
                continue

            console.log(f"[#fee048]Downloading[/#fee048]: [#FF8C42]{title}[/#FF8C42] (Gutenberg #{gid}) [dim]-> group '{group_name}'[/dim]")
            try:
                raw_text = download_text_trying_candidates(candidates)
            except Exception as e:
                console.log(f"[dim dark_red]Download failed for '{title}':[/dim dark_red] {e}")
                continue

            cleaned = conv.clean_text(raw_text)
            final_text = conv.limit_blank_lines(cleaned, max_blank=1)

            leftover = conv.find_leftover_gutenberg_mentions(final_text)
            if leftover:
                console.log(f"[bold yellow]Warning:[/bold yellow] '{title}' still mentions 'Gutenberg' {len(leftover)} time after cleaning | review before publishing! :")
                for line in leftover[:5]:
                    console.log(f"  [dim]{line[:100]}[/dim]")
                proceed = console.input("  [#b5e3fb]Save anyway?[/#b5e3fb] ([bold #71bc18]Y[/bold #71bc18]/[#ffdb4f]N[/#ffdb4f]): ").strip().lower()
                if proceed != "y":
                    console.log(f"[dim bright_black]Skipped '{title}'.[/dim bright_black]")
                    continue

            volume_num = next_volume + i
            txt_filename = f"{base_name} V{volume_num}P.txt"
            txt_path = output_path / txt_filename
            txt_path.write_text(final_text, encoding="utf-8")

            author = ", ".join(a.get("name", "?") for a in book.get("authors", [])) or None
            conv.write_book_metadata(txt_path, {
                "source": "gutenberg",
                "gutenberg_id": gid,
                "title": title,
                "author": author,
            })

            mark_saved(gid)
            saved_ids.add(gid)
            downloaded_any = True
            console.log(f"[#aae965]Saved[/#aae965] -> {txt_path}")

            time.sleep(DOWNLOAD_DELAY_SECONDS)

    if not downloaded_any:
        console.log("[dim bright_black]Nothing new downloaded, skipping manifest publish.[/dim bright_black]")
        return

    console.log("[italic #ffa6c4]Gutenberg import complete![/italic #ffa6c4]")
    conv.publish_to_github(conv.DATA_REPO_DIR)


if __name__ == "__main__":
    from rich.panel import Panel
    console.print(Panel.fit(rainbow("Ea-nasir's Public Domain Bookshelf")))
    run()