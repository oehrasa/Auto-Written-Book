#!/usr/bin/env python3
"""
such silly program to convert .pdf literature into .txt format which then can be used for Book-Import module in AutoBookshelf addon,
why pdf you might ask? well this project is originally made for overlord lightnovel that i got from some random drive sharing.
also thanks to A-kun for recomending rich library to me, so sorry if the code a bit too much, A-kun will be proud!!!!!!!
"""
from random import randint

from rich import print
from rich.highlighter import Highlighter
from rich.panel import Panel
from rich.console import Console
from rich.traceback import install
install()
from rich.progress import track, Progress
from pathlib import Path
import re
import pdfplumber

console = Console(record=True)
SCRIPT_DIR = Path(__file__).resolve().parent
input_folder = SCRIPT_DIR / "input_folder"
list_of_item = []
ENABLE_DECODING = False  # set True to enable auto-decoding or False & will skips decoding

class RainbowHighlighter(Highlighter):
    def highlight(self, text):
        for index in range(len(text)):
            text.stylize(f"color({randint(16, 255)})", index, index + 1)
rainbow = RainbowHighlighter()

def decode_with_shift(text, shift):
    """Decode with a specific Caesar cipher shift"""
    result = []
    for char in text:
        if 'a' <= char <= 'z':
            new_shift = shift % 26
            result.append(chr((ord(char) - ord('a') + new_shift) % 26 + ord('a')))
        elif 'A' <= char <= 'Z':
            new_shift = shift % 26
            result.append(chr((ord(char) - ord('A') + new_shift) % 26 + ord('A')))
        else:
            result.append(char)
    return ''.join(result)

def auto_detect_shift(text, sample_size=200):
    """Auto-detect the cipher shift by trying all possibilities"""
    if not text or len(text) < 50:
        return 0
    
    sample = text[:sample_size].lower()
    common_words = ['the', 'and', 'for', 'you', 'are', 'this', 'that', 'have', 'chapter', 'table', 'contents']
    
    best_shift = 0
    best_score = 0
    
    for shift in range(26):
        for direction in [1, -1]:
            test_shift = shift * direction
            decoded_sample = decode_with_shift(sample, test_shift)
            score = sum(1 for word in common_words if word in decoded_sample)
            if score > best_score:
                best_score = score
                best_shift = test_shift
    
    return best_shift

def read_to_file():
    saved_list = Path("Saved_list.txt")
    try:
        with saved_list.open("r", encoding="utf-8") as file:
            console.log("[#f7f5ba]List is successfully loaded from[/#f7f5ba] [underline #524997]Saved_list[/underline #524997].[#f5e0dc]txt[/#f5e0dc]")
            for x in file:
                name = x.strip()
                if name and name not in list_of_item:
                    list_of_item.append(name)

        if not list_of_item:
            console.log("[dim bright_black]Saved_list.txt is empty, starting with an empty list[/dim bright_black]")
        

    except FileNotFoundError:
        console.log("[dim grey]Saved_list.txt not found, starting with an empty list[/dim grey]")
    except Exception as e:
       console.log(f"[dim dark_red]An error occurred while loading the file: [italic]{e}[/italic][/dim dark_red]")

def get_base_name():
    while True:
        base_name = console.input(f"[bold][#C0392B]En[/#C0392B][#b87c5a]ter[/#b87c5a] [bright_black]the[/bright_black] [#a5479b]book[/#a5479b] [#d6d6d6]title for {group_name}[/#d6d6d6][/bold] ([#74c7ecD]22[/#74c7ecD] [italic][#FF006E]characters[/#FF006E][/italic] [bold][#f38ba8]max[/#f38ba8][/bold]): ").strip()
        if not base_name:
            console.log("[italic red]Error: name cannot be[/italic red] [dim bright_black]empty[/dim bright_black]")
            continue
        if len(base_name) > 22:
            console.log("[bold bright_red]Error: name should not be more than[/bold bright_red] [#74c7ecD]22[/#74c7ecD] [underline #eba0ac]characters[/underline #eba0ac]")
            continue
        return base_name

def preview_structure(groups, base_names):
    print("\n[#fbf2c2]========== PREVIEW ==========[/#fbf2c2]\n")
    for group_name, pdfs in groups.items():
        group_base = base_names[group_name]
        print(f"[bold #eda90c]{group_name}/[/bold #eda90c] [dim](base: {group_base})[/dim]")
        for i, pdf in enumerate(pdfs, start=1):
            txt_name = f"[#aae965]{group_base} V{i}P.txt[/#aae965]"
            print(f" L [#5da817]{txt_name}[/#5da817] [#e0160c]is from[/#e0160c] [#FF8C42]{pdf.name}[/#FF8C42]")
        print()
    print("[#ffec9d]======= BOTTOM PREVIEW ======[/#ffec9d]\n")

def is_text_broken(text):
    """Check if extracted text has spacing issues."""
    if not text or len(text) < 100:
        return False
    
    sample = text[:500]  # check first 500 chars
    
    # Look for lowercase followed by uppercase in middle of text
    if re.search(r'[a-z][A-Z][a-z]', sample):
        return True
    
    # Check space to word ratio
    words = re.findall(r'\b[a-zA-Z]{3,}\b', sample)
    if len(words) < 5:
        return False
    
    space_count = sample.count(' ')
    word_count = len(words)
    
    # If there are many words but few spaces, text is likely broken
    return space_count < word_count * 0.4

def reconstruct_from_words(words):
    """Rebuild text from word list with proper spacing."""
    if not words:
        return ""
    
    # Sort by vertical then horizontal position
    words.sort(key=lambda w: (round(w['top'], 1), w['x0']))
    
    lines = []
    current_line_words = []
    current_y = None
    
    for word in words:
        word_text = word['text'].strip()
        if not word_text:
            continue
        
        word_top = word['top']
        
        # New line if vertical difference > 5 pixels
        if current_y is None or abs(word_top - current_y) > 5:
            if current_line_words:
                line_text = ' '.join([w['text'] for w in current_line_words])
                lines.append(line_text)
                current_line_words = []
            current_y = word_top
        
        current_line_words.append(word)
    
    # Add the last line
    if current_line_words:
        line_text = ' '.join([w['text'] for w in current_line_words])
        lines.append(line_text)
    
    return '\n'.join(lines)

def fix_glued_words(text):
    """Intelligently fix words that are glued together."""
    if not text:
        return ""
    
    # Function to fix individual glued words
    def fix_single_word(match):
        word = match.group(0)
        
        # Don't fix: short words, proper nouns, acronyms
        if len(word) < 6 or word.isupper() or word.istitle():
            return word
        
        # Don't fix this
        common_compounds = {
            'something', 'anything', 'everything', 'nothing',
            'someone', 'anyone', 'everyone', 'somebody',
            'anybody', 'everybody', 'nobody', 'somewhere',
            'anywhere', 'everywhere', 'nowhere', 'whenever',
            'wherever', 'whatever', 'whichever', 'whoever',
            'whomever', 'however', 'meanwhile', 'otherwise',
            'somewhat', 'anyhow', 'anyway', 'forever'
        }
        
        if word.lower() in common_compounds:
            return word
        
        # Find lowercase to uppercase transitions
        for i in range(1, len(word) - 2):
            if word[i-1].islower() and word[i].isupper():
                # Check if second part looks like a word
                second_part = word[i:]
                if len(second_part) >= 3 and re.search(r'[aeiouyAEIOUY]', second_part):
                    # Check if this might be intentional
                    if second_part[0].isupper() and second_part[1:].islower():
                        return word[:i] + ' ' + word[i:]
        
        return word
    
    # Apply to potential glued words
    text = re.sub(r'\b[a-z]+[A-Z][a-zA-Z]{2,}\b', fix_single_word, text)
    
    # Fix punctuation spacing
    text = re.sub(r'\s+([.,!?;:])', r'\1', text)
    text = re.sub(r'([.,!?;:])([A-Za-z])', r'\1 \2', text)
    
    return text

def pdf_to_text(pdf_path):
    """Universal PDF text extraction that works with all formats"""
    text = ""
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            pdf_name = pdf_path.name
            print(f"[#fee048]Extracting[/#fee048]: [#FF8C42]{pdf_name}[/#FF8C42]")
            
            total_pages = len(pdf.pages)
            with Progress() as progress:
                task = progress.add_task(f"[#9B59B6]Pages[/#9B59B6] [#e0160c]From[/#e0160c] [#39FF14]{pdf_name}[/#39FF14]", total=total_pages)
                
                for page_num in range(1, total_pages + 1):
                    page = pdf.pages[page_num - 1]
                    # Try standard extraction first
                    page_text = page.extract_text(layout=False)
                    
                    # Check if text is broken
                    if is_text_broken(page_text):
                        # Extract words and reconstruct
                        words = page.extract_words(
                            x_tolerance=2,
                            y_tolerance=3,
                            keep_blank_chars=False,
                            use_text_flow=True,
                            split_at_punctuation=True
                        )
                        
                        if words:
                            page_text = reconstruct_from_words(words)
                        else:
                            # Fallback: try with layout
                            page_text = page.extract_text(layout=True)
                    
                    # If there is still no text, try different parameters
                    if not page_text or len(page_text.strip()) < 20:
                        page_text = page.extract_text(
                            x_tolerance=1,
                            y_tolerance=2,
                            layout=False,
                            use_text_flow=False
                        )
                    
                    if page_text:
                        # Fix any glued words
                        page_text = fix_glued_words(page_text)
                        
                        # Auto-detect and decode cipher (optional)
                        if ENABLE_DECODING:
                            shift = auto_detect_shift(page_text)
                            if shift != 0:
                                page_text = decode_with_shift(page_text, shift)
                        
                        text += page_text + "\n\n"
                    else:
                        print(f"  [dim dark_magenta]Page[/dim dark_magenta] [underline #d5679d]{page_num}[/underline #d5679d]: [yellow]No text extracted[/yellow]")
                    
                    progress.update(task, advance=1)
    
    except Exception as e:
        console.log(f"[dim yellow]Error extracting[/dim yellow] {pdf_path}: {e}")
        
        # last resort, try PyPDF2 as fallback
        try:
            import PyPDF2
            with pdf_path.open('rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    page_text = page.extract_text()
                    if page_text:
                        shift = auto_detect_shift(page_text)
                        if shift != 0:
                            page_text = decode_with_shift(page_text, shift)
                        text += page_text + "\n\n"
            console.log(f"[#FFD93D]Used PyPDF2 fallback for[/#FFD93D] [olive]{pdf_path.name}[/olive]")
        except Exception as e2:
            console.print_exception(f"All extraction methods failed: {e2}")
    
    return text

def remove_gutenberg_boilerplate(text):
    """
    Strip Project Gutenberg's injected header/footer boilerplate.

    Cuts everything up through the "*** START OF THE PROJECT
    GUTENBERG EBOOK ... ***" banner (including a Transcriber's Note
    block immediately after it, if present), and everything from the
    "*** END OF THE PROJECT GUTENBERG EBOOK ... ***" banner onward
    (the license text at the end of every Gutenberg file).

    Different Gutenberg files wrap this banner differently ("* * *"
    vs "***", spread across 1-3 lines), so this matches on the
    "start/end of ... project gutenberg" phrase rather than exact
    punctuation. Safe to call on non-Gutenberg text -- if no banner
    is found, the text is returned unchanged.
    """
    if not text:
        return text

    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = normalized.split('\n')

    start_pattern = re.compile(r"start of th(?:e|is) project gutenberg", re.IGNORECASE)
    end_pattern = re.compile(r"end of th(?:e|is) project gutenberg", re.IGNORECASE)
    closing_asterisks = re.compile(r"(\*\s*){3,}$")

    start_index = next((i for i, line in enumerate(lines) if start_pattern.search(line)), None)

    if start_index is not None:
        # The banner text itself can spill onto the next couple of
        # lines before its closing "* * *" / "***" -- walk forward
        # to find where it actually ends.
        banner_end = start_index
        for j in range(start_index, min(start_index + 6, len(lines))):
            banner_end = j
            stripped = lines[j].strip()
            if closing_asterisks.search(stripped) or not stripped:
                break

        lines = lines[banner_end + 1:]

        # Drop a leading "Transcriber's Note" block, if present.
        # It runs until the next blank line.
        for k, line in enumerate(lines[:5]):
            if re.match(r"transcriber\W?s note", line.strip(), re.IGNORECASE):
                note_end = k
                for m in range(k, len(lines)):
                    note_end = m
                    if not lines[m].strip():
                        break
                lines = lines[note_end + 1:]
                break

    end_index = next((i for i, line in enumerate(lines) if end_pattern.search(line)), None)
    if end_index is not None:
        lines = lines[:end_index]

    return '\n'.join(lines).strip()

def clean_text(text):
    """Clean and normalize extracted text."""
    if not text:
        return ""
    
    # remove unwanted characters
    unwanted_chars = [
        "\x00", "\x0c", "\ufeff", "\u200b", "\u200e", "\u200f",
        "�", "﻿", "￼", "\u2028", "\u2029", "\uFEFF"
    ]
    
    for char in unwanted_chars:
        text = text.replace(char, "")
    
    symbols = ["●", "■", "•", "◦", "○", "□", "▪", "▫"]
    for symbol in symbols:
        text = text.replace(symbol, "")

    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # Cut Project Gutenberg's injected header/footer boilerplate,
    # if this text came from a Gutenberg-sourced book.
    text = remove_gutenberg_boilerplate(text)

    # Fix multiple spaces (but let intentional indent)
    lines = text.splitlines()
    cleaned_lines = []
    
    for line in lines:
        line = line.rstrip()
        # Collapse multiple spaces into single space
        line = re.sub(r'[ \t]+', ' ', line)
        
        # Fix common PDF artifacts
        line = re.sub(r'\s+-\s+', '-', line)  # Hyphens
        line = re.sub(r'\s+/\s+', '/', line)  # Slashes
        line = re.sub(r'\s+&\s+', '&', line)  # Ampersands 
        cleaned_lines.append(line)

    result_lines = []
    blank_count = 0
    
    for line in cleaned_lines:
        if not line.strip():  #blank line
            blank_count += 1
            if blank_count <= 1:
                result_lines.append("")
        else:
            blank_count = 0
            result_lines.append(line)
    
    return '\n'.join(result_lines)

def limit_blank_lines(text, max_blank=1):
    lines = text.splitlines()
    result_lines = []
    blank_count = 0
    
    for line in lines:
        if line.strip() == "":
            blank_count += 1
            if blank_count <= max_blank:
                result_lines.append("")
        else:
            blank_count = 0
            result_lines.append(line)
    
    return "\n".join(result_lines)

def get_pdf_group_name(pdf_name):
    """
    Group by series name while avoiding per-volume numeric subfolders.
    """
    name = pdf_name.stem

    normalized = re.sub(r"[-_]+", " ", name).strip()
    normalized = re.sub(r"\s+", " ", normalized)

    # normalize fused forms like '1prologue' etc.
    normalized = re.sub(r"(?i)(\d)(?=(?:prologue|epilogue)\b)", r"\1 ", normalized)

    # remove trailing bracketed metadata
    normalized = re.sub(r"\s*\([^)]*\)\s*$", "", normalized).strip()

    # keep a marker for prologue/epilogue so we can keep it in group name if present
    suffix_marker = ""
    marker_match = re.search(r"\b(prologue|epilogue)\b", normalized, flags=re.IGNORECASE)
    if marker_match:
        suffix_marker = marker_match.group(1).capitalize()

    # drop volume/chapter/part labels and numeric tokens
    normalized = re.sub(r"\b(?:volume|vol(?:ume)?|v|chapter|ch|part|episode|ep)\b\.?", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b\d+\b", "", normalized)
    normalized = re.sub(r"\b(?:prologue|epilogue)\b", "", normalized, flags=re.IGNORECASE)

    normalized = re.sub(r"\s+", " ", normalized).strip()
    if suffix_marker:
        normalized = f"{normalized} {suffix_marker}".strip()

    if not normalized:
        return name

    return normalized

def convert_folder(input_dir, base_name=None):  # Make base_name optional
    pdf_files = sorted(
        f for f in input_dir.iterdir()
        if f.suffix.lower() == ".pdf"
    )

    todo_files = [f for f in pdf_files if f.name not in list_of_item]

    if not todo_files:
        console.log("[dark_magenta]All [italic #FF8C42]PDF files[/italic #FF8C42] are already converted, Nothing to do![/dark_magenta]")
        print(rainbow("its joever"))
        return

    groups = {}
    custom_group = console.input("[#51CF66]Enter[/#51CF66] [#e85850]custom[/#e85850] [#cf2c2f]group name[/#cf2c2f] [bright_black]for all[/bright_black] [#FF8C42]PDFs[/#FF8C42] ([bright_yellow]X = exit, Enter = auto-detect[/bright_yellow]): ").strip()
    if custom_group.lower() == 'x':
        console.log("[#ee243e]Conversion is [strike]cancelled[/strike] by user[/#ee243e]")
        return

    for pdf_file in todo_files:
        if custom_group:
            group_name = custom_group
        else:
            # auto detect use intelligent grouping
            group_name = get_pdf_group_name(pdf_file)

        groups.setdefault(group_name, []).append(pdf_file)

    # Ask for base_name per group if no global base_name provided
    group_base_names = {}
    if base_name is None:
        console.print("\n[bold yellow] Name each group individually:[/bold yellow]\n")
        for group_name in groups.keys():
            while True:
                custom_base = console.input(f"[bold cyan]Base name for group[/bold cyan] [bold #eda90c]'{group_name}'[/bold #eda90c] ([#74c7ec]max 22 chars[/#74c7ec]): ").strip()
                if not custom_base:
                    console.log("[italic red]Error: name cannot be empty[/italic red]")
                    continue
                if len(custom_base) > 22:
                    console.log("[bold bright_red]Error: name should not be more than 22 characters[/bold bright_red]")
                    continue
                group_base_names[group_name] = custom_base
                break
    else:
        # Use the same base_name for all groups
        for group_name in groups.keys():
            group_base_names[group_name] = base_name

    preview_structure(groups, group_base_names)  # Pass the base names dict

    confirm = console.input("[#b5e3fb]Proceed[/#b5e3fb] [#13c1dc]with the[/#13c1dc] [#2f54bc]convert?[/#2f54bc] [bold #e0160c]or delete[/bold #e0160c] ([bold #71bc18]Y[/bold #71bc18]/[#ffdb4f]N[/#ffdb4f]/[#ae0010]D[/#ae0010]): ").strip().lower()
    if confirm == "y":
        pass  # continue

    elif confirm == "d":
        while True:
            delete_item = console.input("\n[dim #71bc18]Enter the [#eceb4b]name[/#eceb4b] of the[/dim #71bc18] [#FF8C42]PDF[/#FF8C42] [bold #e0160c]to delete[/bold #e0160c] ([italic bright_yellow]X = exit, enter to stop[/italic bright_yellow]): ").strip()
            if delete_item.lower() == 'x':
                console.log("[#ee243e]Conversion is [strike]cancelled[/strike] by user[/#ee243e]")
                return
            if delete_item.lower() == '':
                break
            matches = []
            for group_name, group_pdfs in groups.items():
                for pdf in group_pdfs:
                    if delete_item.lower() in pdf.name.lower():
                        matches.append((group_name, pdf))
            
            if not matches:
                print(f"[bright_black]No [#FF8C42]PDF[/#FF8C42] matching '[bold #e0160c]{delete_item}[/bold #e0160c]' found in structure[/bright_black]")
                continue
            
            if len(matches) == 1:
                group_name, pdf = matches[0]
                groups[group_name].remove(pdf)
                print(f"[bold #e0160c]Deleted[/bold #e0160c] '[#FF8C42]{pdf.name}[/#FF8C42]' [#e0160c]from[/#e0160c] [bold #ae0010]group[/bold #ae0010] '[bold #eda90c]{group_name}[/bold #eda90c]'")
                if not groups[group_name]:
                    del groups[group_name]
                    del group_base_names[group_name]  # Also remove base name
            else:
                print(f"[underline dark_magenta]Multiple PDFs match[/underline dark_magenta]: '[bold #e0160c]{delete_item}[/bold #e0160c]'")
                # show all the matches
                for i, (group_name, pdf) in enumerate(matches, start=1):
                    print(f"{i}. [#FF8C42]{pdf.name}[/#FF8C42] ([#feeb4e]in[/#feeb4e] [bold #eda90c]{group_name}[/bold #eda90c])")
                choice_input = console.input("\n[dim #71bc18]Enter the [dark_magenta]numbers[/dark_magenta] in list[/dim #71bc18] [bold #e0160c]to delete[/bold #e0160c] [#FF8C42]PDF[/#FF8C42] ([bright_yellow]comma-separated[/bright_yellow]): ").strip()
                try:
                    choices = [int(x.strip()) for x in choice_input.split(',') if x.strip()]
                    
                    if all(1 <= c <= len(matches) for c in choices):
                        selected = [matches[c-1] for c in choices]
                        for group_name, pdf in selected:
                            groups[group_name].remove(pdf)
                            print(f"[bold #e0160c]Deleted[/bold #e0160c] '[#FF8C42]{pdf.name}[/#FF8C42]' [#e0160c]from[/#e0160c] [#ae0010]group[/#ae0010] '[bold #eda90c]{group_name}[/bold #eda90c]'")
                            if not groups[group_name]:
                                del groups[group_name]
                                del group_base_names[group_name]  # Also remove base name
                    else:
                        print("[#b43cb8]Invalid numbers[/#b43cb8]")
                except ValueError:
                    console.log("[#b43cb8]Please enter valid numbers separated by commas[/#b43cb8]")
            
            total_pdfs = sum(len(pdfs) for pdfs in groups.values())
            if total_pdfs == 0:
                print("[italic #FF8C42]All PDFs[/italic #FF8C42] were [bold #e0160c]Deleted[/bold #e0160c]")
                quit()
    else:
        console.log("[#ee243e]Conversion is [strike]cancelled[/strike] by user[/#ee243e]")
        return
        
    for group_name, group_pdfs in groups.items():
        output_path = Path(group_name)
        output_path.mkdir(exist_ok=True)
        print(f"\n[italic bright_black]Processing group[italic bright_black]: '[#eda90c]{group_name}[/#eda90c]' [#eceb4b]to Folder[#eceb4b]: [#df8ec1]{output_path}[/#df8ec1]")
        
        # Use the specific base_name for this group
        group_base = group_base_names[group_name]
        
        for i, pdf_file in enumerate(group_pdfs, start=1):
            if pdf_file.name in list_of_item:
                print(f"[yellow][SKIP] [#FF8C42]{pdf_file.name}[/#FF8C42] already converted[/yellow]")
                continue

            txt_filename = f"{group_base} V{i}P.txt"
            txt_path = output_path / txt_filename

            print(f"[italic #314fdd]Converting[/italic #314fdd] '[#FF8C42]{pdf_file.name}[/#FF8C42]' [#2042a3]to[/#2042a3] '[#00D9FF]{txt_filename}[/#00D9FF]'")

            extracted_text = pdf_to_text(pdf_file)
            cleaned_text = clean_text(extracted_text)
            final_text = limit_blank_lines(cleaned_text, max_blank=1)

            with txt_path.open("w", encoding="utf-8") as f:
                f.write(final_text)

            # update the list
            with Path("Saved_list.txt").open("a", encoding="utf-8") as file:
                file.write(pdf_file.name + "\n")
                list_of_item.append(pdf_file.name)
    console.log("[italic #ffa6c4]Conversion complete![/italic #ffa6c4]")
    print(rainbow("we are so barrack"))

console.save_html("fuckywucky.html")

if __name__ == "__main__":
    input_folder = input_folder.resolve()
    print(Panel.fit(rainbow("Oehrasa")))
    read_to_file()
    pdf_files = sorted(f for f in input_folder.iterdir() if f.suffix.lower() == ".pdf")
    todo_files = [f for f in pdf_files if f.name not in list_of_item]
    
    if not todo_files:
        print("[dark_magenta]All PDF files are already converted, Nothing to do![/dark_magenta]")
        print(rainbow("its joever"))
        quit()

    # Ask if individual naming or global naming
    naming_choice = console.input("[bold yellow]Name each group individually?[/bold yellow] ([bold green]Y[/bold green]/[bold red]n[/bold red]): ").strip().lower()
    
    if naming_choice == 'y':
        convert_folder(input_folder, base_name=None)  # Individual naming
    else:
        base_name = get_base_name()  # Global naming
        convert_folder(input_folder, base_name)