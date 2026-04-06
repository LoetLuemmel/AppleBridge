#!/usr/bin/env python3
"""
AppleBridge Encoding Converter
Converts between UTF-8 (host) and MacRoman (classic Mac)

Usage:
    # To Mac (UTF-8 → MacRoman):
    python encoding_convert.py to-mac input.txt output.txt
    python encoding_convert.py to-mac src_dir/ dest_dir/

    # From Mac (MacRoman → UTF-8):
    python encoding_convert.py from-mac input.txt output.txt
    python encoding_convert.py from-mac src_dir/ dest_dir/

    # Quick copy to Share folder:
    python encoding_convert.py to-share file.txt
    python encoding_convert.py to-share src_dir/

    # Quick copy from Share folder:
    python encoding_convert.py from-share file.txt dest.txt
"""

import sys
import os
import shutil
from pathlib import Path

# Default share folder path
SHARE_FOLDER = "/Users/pitforster/Desktop/Share"

# File extensions to treat as text (convert encoding)
TEXT_EXTENSIONS = {
    '.c', '.h', '.r', '.a', '.p', '.pas',  # Source code
    '.txt', '.md', '.doc',                  # Text files
    '.make', '.makefile',                   # Makefiles
    '.script', '.sh',                       # Scripts
    '.cfg', '.conf', '.ini',                # Config files
    '.html', '.htm', '.xml',                # Markup
    '.csv', '.tsv',                         # Data files
}

# Files without extension that are likely text
TEXT_FILENAMES = {
    'makefile', 'readme', 'license', 'changelog',
    'makefile.68k', 'makefile.ppc',
}

# MPW special characters mapping (for reference)
# ∂ (line continuation): UTF-8 e2 88 82, MacRoman b6
# ƒ (folder):            UTF-8 c6 92,    MacRoman c4
# ≈ (wildcard):          UTF-8 e2 89 88, MacRoman c7
# Ω (omega):             UTF-8 ce a9,    MacRoman bd
# π (pi):                UTF-8 cf 80,    MacRoman b9
# • (bullet):            UTF-8 e2 80 a2, MacRoman a5
# † (dagger):            UTF-8 e2 80 a0, MacRoman a0
# © (copyright):         UTF-8 c2 a9,    MacRoman a9
# ® (registered):        UTF-8 c2 ae,    MacRoman a8
# ™ (trademark):         UTF-8 e2 84 a2, MacRoman aa


def is_text_file(path: Path) -> bool:
    """Determine if a file should be treated as text."""
    # Check by extension
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    # Check by filename
    if path.name.lower() in TEXT_FILENAMES:
        return True
    # Files without extension - try to detect
    if not path.suffix:
        try:
            with open(path, 'rb') as f:
                chunk = f.read(1024)
                # If mostly printable ASCII, treat as text
                if chunk:
                    printable = sum(1 for b in chunk if 32 <= b < 127 or b in (9, 10, 13))
                    return printable / len(chunk) > 0.8
        except:
            pass
    return False


def convert_line_endings_to_mac(data: bytes) -> bytes:
    """Convert Unix LF to Mac CR line endings."""
    # First normalize CRLF to LF, then convert LF to CR
    data = data.replace(b'\r\n', b'\n')
    data = data.replace(b'\n', b'\r')
    return data


def convert_line_endings_from_mac(data: bytes) -> bytes:
    """Convert Mac CR to Unix LF line endings."""
    # Convert CR to LF (but not CRLF)
    # First protect CRLF, then convert CR, then restore
    data = data.replace(b'\r\n', b'\x00\x01')  # Protect CRLF
    data = data.replace(b'\r', b'\n')           # Convert CR to LF
    data = data.replace(b'\x00\x01', b'\n')    # CRLF becomes LF too
    return data


def utf8_to_macroman(text: str) -> bytes:
    """Convert UTF-8 string to MacRoman bytes."""
    try:
        return text.encode('mac_roman')
    except UnicodeEncodeError as e:
        # Handle characters not in MacRoman
        result = []
        for char in text:
            try:
                result.append(char.encode('mac_roman'))
            except UnicodeEncodeError:
                # Replace with ? or closest equivalent
                result.append(b'?')
                print(f"  Warning: Cannot encode '{char}' (U+{ord(char):04X}) to MacRoman", file=sys.stderr)
        return b''.join(result)


def macroman_to_utf8(data: bytes) -> str:
    """Convert MacRoman bytes to UTF-8 string."""
    return data.decode('mac_roman')


def convert_file_to_mac(src: Path, dest: Path, verbose: bool = True) -> bool:
    """Convert a single file from UTF-8 to MacRoman."""
    try:
        if is_text_file(src):
            # Text file - convert encoding and line endings
            with open(src, 'r', encoding='utf-8') as f:
                content = f.read()

            mac_bytes = utf8_to_macroman(content)
            mac_bytes = convert_line_endings_to_mac(mac_bytes)

            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, 'wb') as f:
                f.write(mac_bytes)

            if verbose:
                print(f"  [TEXT] {src.name} → {dest}")
        else:
            # Binary file - copy as-is
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if verbose:
                print(f"  [BIN]  {src.name} → {dest}")
        return True
    except Exception as e:
        print(f"  [ERR]  {src.name}: {e}", file=sys.stderr)
        return False


def convert_file_from_mac(src: Path, dest: Path, verbose: bool = True) -> bool:
    """Convert a single file from MacRoman to UTF-8."""
    try:
        if is_text_file(src):
            # Text file - convert encoding and line endings
            with open(src, 'rb') as f:
                mac_bytes = f.read()

            mac_bytes = convert_line_endings_from_mac(mac_bytes)
            content = macroman_to_utf8(mac_bytes)

            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, 'w', encoding='utf-8') as f:
                f.write(content)

            if verbose:
                print(f"  [TEXT] {src.name} → {dest}")
        else:
            # Binary file - copy as-is
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if verbose:
                print(f"  [BIN]  {src.name} → {dest}")
        return True
    except Exception as e:
        print(f"  [ERR]  {src.name}: {e}", file=sys.stderr)
        return False


def convert_directory(src_dir: Path, dest_dir: Path, to_mac: bool, verbose: bool = True) -> int:
    """Convert all files in a directory."""
    converted = 0
    converter = convert_file_to_mac if to_mac else convert_file_from_mac

    for src_file in src_dir.rglob('*'):
        if src_file.is_file():
            # Skip hidden files and common non-essential files
            if src_file.name.startswith('.'):
                continue
            if src_file.name in ('.DS_Store', 'Thumbs.db'):
                continue

            rel_path = src_file.relative_to(src_dir)
            dest_file = dest_dir / rel_path

            if converter(src_file, dest_file, verbose):
                converted += 1

    return converted


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()
    source = sys.argv[2]

    # Determine destination
    if len(sys.argv) >= 4:
        dest = sys.argv[3]
    elif command == 'to-share':
        dest = SHARE_FOLDER
    elif command == 'from-share':
        if len(sys.argv) < 4:
            print("Error: from-share requires a destination path")
            sys.exit(1)
        # Swap source and dest for from-share
        source = os.path.join(SHARE_FOLDER, source)
        dest = sys.argv[3] if len(sys.argv) >= 4 else '.'
    else:
        # Default: same filename in current directory with .mac or .utf8 suffix
        base = Path(source).stem
        ext = Path(source).suffix
        if command in ('to-mac', 'to-share'):
            dest = f"{base}.mac{ext}"
        else:
            dest = f"{base}.utf8{ext}"

    src_path = Path(source)
    dest_path = Path(dest)

    # Handle to-share specially - put file IN share folder
    if command == 'to-share':
        if src_path.is_file():
            dest_path = Path(SHARE_FOLDER) / src_path.name
        else:
            dest_path = Path(SHARE_FOLDER) / src_path.name

    print(f"Converting: {src_path} → {dest_path}")
    print(f"Direction: {'UTF-8 → MacRoman' if command in ('to-mac', 'to-share') else 'MacRoman → UTF-8'}")
    print()

    to_mac = command in ('to-mac', 'to-share')

    if src_path.is_file():
        if to_mac:
            success = convert_file_to_mac(src_path, dest_path)
        else:
            success = convert_file_from_mac(src_path, dest_path)

        if success:
            print("\nDone!")
        else:
            print("\nConversion failed!")
            sys.exit(1)

    elif src_path.is_dir():
        count = convert_directory(src_path, dest_path, to_mac)
        print(f"\nConverted {count} files.")
    else:
        print(f"Error: {source} not found")
        sys.exit(1)


if __name__ == '__main__':
    main()
