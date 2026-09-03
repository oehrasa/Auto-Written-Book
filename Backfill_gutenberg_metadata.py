#!/usr/bin/env python3
"""
One-off backfill for books downloaded by gutenberg_scrapper.py BEFORE it
started writing "{stem}.meta.json" sidecars next to each .txt [real title +
author (see pdfcon.write_book_metadata / pdfcon.build_manifest)].

Safe to rerun: any Gutenberg ID that already has a sidecar somewhere in the
repo (found by scanning existing "*.meta.json" files for "gutenberg_id") is
skipped up front, so a partial run or a later rerun won't re-ask about IDs
you already resolved.
"""
import csv
import difflib
import json
from pathlib import Path

import pdfcon as conv
import gutenberg_scraper as gs
from rich.panel import Panel

console = conv.console
rainbow = conv.rainbow

# A match at or above this score, with a clear enough lead over the next-best
# candidate, gets applied automatically instead of asked about.
AUTO_ACCEPT_SCORE = 0.92
AUTO_ACCEPT_MARGIN = 0.15


def find_unannotated_txt_files(repo_root: Path) -> list[Path]:
    """Every .txt in the repo with no metadata sidecar yet, oldest-saved first (by mtime)."""
    candidates = []
    for group_dir in sorted(p for p in repo_root.iterdir() if p.is_dir() and not p.name.startswith('.')):
        for txt_file in group_dir.glob("*.txt"):
            if not conv.metadata_path_for(txt_file).exists():
                candidates.append(txt_file)
    candidates.sort(key=lambda p: p.stat().st_mtime)
    return candidates


def already_annotated_gutenberg_ids(repo_root: Path) -> set[str]:
    """Gutenberg IDs that already have a sidecar somewhere -- these don't need backfilling."""
    ids = set()
    for group_dir in sorted(p for p in repo_root.iterdir() if p.is_dir() and not p.name.startswith('.')):
        for txt_file in group_dir.glob("*.txt"):
            meta = conv.read_book_metadata(txt_file)
            gid = meta.get("gutenberg_id")
            if gid:
                ids.add(str(gid))
    return ids


def fetch_by_gutendex_id(gid: str) -> dict | None:
    """Direct ID lookup via Gutendex's `ids=` filter, routed through curl (see gutenberg_scrapper.py's
    _get_via_curl -- Gutendex is fronted by Cloudflare and tarpits plain urllib requests)."""
    url = f"{gs.GUTENDEX_BASE}?ids={gid}"
    try:
        raw = gs._get_via_curl(url, timeout=20)
    except FileNotFoundError:
        raw = gs._get_with_retries(url, timeout=20, retries=2)
    data = json.loads(raw.decode("utf-8"))
    results = data.get("results", [])
    return results[0] if results else None


def fetch_by_catalog_id(gid: str) -> dict | None:
    """Fallback lookup: scan the cached official catalog CSV for a matching Text# (Gutenberg ID)."""
    path = gs.ensure_catalog_cached()
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("Text#") or "").strip() == gid:
                title = (row.get("Title") or "").strip()
                authors = (row.get("Authors") or "").strip()
                return {
                    "id": gid,
                    "title": title or "?",
                    "authors": [{"name": authors}] if authors else [],
                }
    return None


def fetch_book_info(gid: str) -> dict | None:
    try:
        info = fetch_by_gutendex_id(gid)
        if info:
            return info
    except Exception as e:
        console.log(f"[dim yellow]Gutendex lookup failed for #{gid}: {e}[/dim yellow]")
    try:
        return fetch_by_catalog_id(gid)
    except Exception as e:
        console.log(f"[dim yellow]Catalog lookup failed for #{gid}: {e}[/dim yellow]")
        return None


def _similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_candidate(title: str, author: str, candidate: Path) -> float:
    """How well a fetched title/author matches a candidate file's group name + filename."""
    haystack = f"{candidate.parent.name} {candidate.stem}"
    score = _similarity(title, haystack)
    if author:
        surname = author.split(",")[0].split()[-1] if author.split(",")[0].split() else ""
        if surname and surname.lower() in haystack.lower():
            score = min(1.0, score + 0.1)
    return score


def run():
    conv.ensure_data_repo_exists()

    if not gs.SAVED_GUTENBERG_LIST.exists():
        console.log("[dim bright_black]No Saved_gutenberg_list.txt found -- nothing to backfill.[/dim bright_black]")
        return

    gutenberg_ids = [
        line.strip() for line in gs.SAVED_GUTENBERG_LIST.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    if not gutenberg_ids:
        console.log("[dim bright_black]Saved_gutenberg_list.txt is empty -- nothing to backfill.[/dim bright_black]")
        return

    already_done = already_annotated_gutenberg_ids(conv.DATA_REPO_DIR)
    todo_ids = [gid for gid in gutenberg_ids if gid not in already_done]
    if not todo_ids:
        console.log("[#aae965]Every ID in Saved_gutenberg_list.txt already has a metadata sidecar. Nothing to do.[/#aae965]")
        return

    remaining = find_unannotated_txt_files(conv.DATA_REPO_DIR)
    if not remaining:
        console.log("[dim bright_black]No .txt files without a metadata sidecar were found -- nothing to match against.[/dim bright_black]")
        return

    console.log(f"[dim]{len(todo_ids)} ID(s) to backfill against {len(remaining)} un-annotated file(s).[/dim]")

    annotated_count = 0

    for position, gid in enumerate(todo_ids):
        if not remaining:
            console.log(f"[dim yellow]No un-annotated files left; {len(todo_ids) - position} ID(s) unmatched.[/dim yellow]")
            break

        info = fetch_book_info(gid)
        if not info:
            console.log(f"[dim yellow]Couldn't look up Gutenberg #{gid} (Gutendex and the offline catalog both failed/had no match) -- skipping.[/dim yellow]")
            continue

        title = info.get("title") or "?"
        author = ", ".join(a.get("name", "?") for a in info.get("authors", [])) or None

        scored = sorted(
            ((score_candidate(title, author or "", c), c) for c in remaining),
            key=lambda pair: pair[0],
            reverse=True,
        )
        top = scored[:3]
        best_score, best_candidate = top[0]

        console.print(
            f"\n[bold #eda90c]Gutenberg #{gid}[/bold #eda90c] -- [#FF8C42]{title}[/#FF8C42]"
            + (f" [dim]by {author}[/dim]" if author else "")
        )

        if best_score >= AUTO_ACCEPT_SCORE and (len(top) == 1 or best_score - top[1][0] >= AUTO_ACCEPT_MARGIN):
            conv.write_book_metadata(best_candidate, {
                "source": "gutenberg", "gutenberg_id": gid, "title": title, "author": author,
            })
            remaining.remove(best_candidate)
            annotated_count += 1
            console.log(f"[#aae965]Matched automatically[/#aae965] -> {best_candidate.relative_to(conv.DATA_REPO_DIR)} [dim](confidence {best_score:.2f})[/dim]")
            continue

        for i, (score, cand) in enumerate(top, start=1):
            console.print(f"  {i}. [#74c7ec]{cand.relative_to(conv.DATA_REPO_DIR)}[/#74c7ec] [dim](match {score:.2f})[/dim]")
        choice = console.input(
            "  [#b5e3fb]Pick a match[/#b5e3fb] ([bright_yellow]number, S = skip, P = enter a path, blank = skip[/bright_yellow]): "
        ).strip().lower()

        if choice in ("", "s"):
            console.log(f"[dim bright_black]Skipped #{gid}.[/dim bright_black]")
            continue

        if choice == "p":
            manual = console.input("  Path to the .txt (relative to the repo root): ").strip()
            manual_path = (conv.DATA_REPO_DIR / manual).resolve()
            if not manual_path.is_file() or manual_path.suffix.lower() != ".txt":
                console.log("[bold bright_red]Not a valid .txt path, skipping.[/bold bright_red]")
                continue
            chosen = manual_path
        else:
            try:
                idx = int(choice) - 1
                if not (0 <= idx < len(top)):
                    raise ValueError
                chosen = top[idx][1]
            except ValueError:
                console.log("[#b43cb8]Invalid choice, skipping.[/#b43cb8]")
                continue

        conv.write_book_metadata(chosen, {
            "source": "gutenberg", "gutenberg_id": gid, "title": title, "author": author,
        })
        if chosen in remaining:
            remaining.remove(chosen)
        annotated_count += 1
        console.log(f"[#aae965]Saved metadata[/#aae965] -> {chosen.relative_to(conv.DATA_REPO_DIR)}")

    if annotated_count == 0:
        console.log("[dim bright_black]Nothing was matched, skipping manifest publish.[/dim bright_black]")
        return

    console.log(f"\n[italic #ffa6c4]Backfilled metadata for {annotated_count} book(s).[/italic #ffa6c4]")
    conv.publish_to_github(conv.DATA_REPO_DIR)


if __name__ == "__main__":
    console.print(Panel.fit(rainbow("Backfilling Gutenberg metadata")))
    run()