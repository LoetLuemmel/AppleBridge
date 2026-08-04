"""Find things inside a powered-off guest image instead of assuming where they are.

`:System Folder:Preferences:AppleBridge Prefs` was written down twice — once in
`install_bridge.py`, once in `bridge_doctor.py` — and it is the ENGLISH name. Two
of the three guests this project runs against are German (the SE/30 runs German
System 7.5, the 2013 MacBook's guest is German), where that folder is called
`Systemordner`. On those images every `hcopy` of the hardcoded path returns
nothing, the loop moves on, and the fall-through blames the IMAGES — sending the
reader to look for a corrupt or missing disk when the real answer is a name in
another language.

That is the same shape as the hfsutils note in `bridge_doctor.probe_guest_ip`,
one layer down: an absent tool blamed the disks, and now an absent NAME does.
The cure is the same one — find it, and when it cannot be found, say what was
looked for rather than naming one candidate as though it were the only one.

The System Folder is identified by its CONTENTS, not its name: it is the root
directory holding a file called `System`. That file is untranslated on every
localisation this project has seen, which is why the test is worth more than any
list of folder names — a list can only ever cover the languages somebody thought
of. The name list is kept anyway, as a fast path.

stdlib only; every function takes its `run` so it is testable without an image.
"""

import re

# The prefs file itself. This name IS ours, so it is the one thing here that may
# safely be a constant.
PREFS_NAME = "AppleBridge Prefs"

# A kit volume has no System Folder and keeps the file at the root.
KIT_PREFS_HFS = ":" + PREFS_NAME

# Fast paths only — never the sole answer. Reaching the end of one of these
# lists must not mean "not present"; it means "now go and look".
SYSTEM_FOLDER_NAMES = ("System Folder", "Systemordner", "Dossier Système",
                       "Cartella Sistema", "Carpeta Sistema", "Systeemmap")
PREFS_FOLDER_NAMES = ("Preferences", "Préférences", "Systemeinstellungen")

# The file every localisation of the System Folder still calls `System`.
SYSTEM_FILE = "System"

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
# `hls -l` prints:  kind [flags] [type/creator] sizes  Mon DD HH:MM  name
# Measured 2026-08-04 against an image built with hformat for the purpose,
# rather than read off the manual page:
#     d          3 items               Aug  4 17:20 Systemordner
#     f  ????/UNIX         0         8 Aug  4 17:20 System
# The name is everything after the date, so it may contain spaces — which is
# the whole reason this is a regex and not a `.split()[-1]`.
_HLS_LINE = re.compile(
    r"^(?P<kind>[dfF])\s+.*?\s(?:%s)\s+\d{1,2}\s+(?:\d{4}|\d{1,2}:\d{2})\s"
    r"(?P<name>.+)$" % _MONTHS)


def parse_hls_long(text):
    """`hls -l` output -> [{"kind": "d"|"f", "name": str}], unreadable lines dropped.

    A line that does not parse is skipped rather than guessed at: a wrong name
    here becomes a path that silently does not exist, which is exactly the
    failure this module was written to end.
    """
    out = []
    for line in (text or "").splitlines():
        m = _HLS_LINE.match(line.rstrip())
        if m:
            out.append({"kind": "d" if m.group("kind") == "d" else "f",
                        "name": m.group("name").strip()})
    return out


def directories(entries):
    return [e["name"] for e in entries if e["kind"] == "d"]


def holds_the_system_file(entries):
    """Is this the System Folder? Asked of its contents, not of its name."""
    return any(e["kind"] == "f" and e["name"] == SYSTEM_FILE for e in entries)


def contains_file(entries, name):
    return any(e["kind"] == "f" and e["name"] == name for e in entries)


def find_system_folder(run):
    """-> (":Name:", why) — the blessed folder in the CURRENTLY MOUNTED volume.

    Caller must have run `hmount` already; this never mounts or unmounts, so it
    cannot surprise a caller that has its own reasons for holding a volume.
    """
    root = parse_hls_long(run(["hls", "-l", ":"]))
    dirs = directories(root)
    if not dirs:
        return None, "the volume root has no directories at all"

    # Fast path: a name we know. Still verified by contents, so a folder merely
    # NAMED "System Folder" cannot send the search down a dead end.
    ordered = ([d for d in dirs if d in SYSTEM_FOLDER_NAMES]
               + [d for d in dirs if d not in SYSTEM_FOLDER_NAMES])
    for name in ordered:
        inner = parse_hls_long(run(["hls", "-l", f":{name}:"]))
        if holds_the_system_file(inner):
            return f":{name}:", f"found by its `{SYSTEM_FILE}` file"
    return None, (f"no directory in the volume root holds a `{SYSTEM_FILE}` "
                  f"file; looked in: {', '.join(dirs[:12])}"
                  + (" …" if len(dirs) > 12 else ""))


def find_guest_prefs(run):
    """-> (hfs_path, why) for the guest's AppleBridge Prefs, or (None, why).

    Order is deliberate. The System Folder is tried first because that is where
    an INSTALLED guest keeps it, and the kit's root copy is the special case; a
    volume that somehow had both would otherwise answer with the one the daemon
    never reads.
    """
    folder, why = find_system_folder(run)
    if folder:
        inner = parse_hls_long(run(["hls", "-l", folder]))
        subdirs = directories(inner)
        ordered = ([d for d in subdirs if d in PREFS_FOLDER_NAMES]
                   + [d for d in subdirs if d not in PREFS_FOLDER_NAMES])
        for sub in ordered:
            here = parse_hls_long(run(["hls", "-l", f"{folder}{sub}:"]))
            if contains_file(here, PREFS_NAME):
                return f"{folder}{sub}:{PREFS_NAME}", f"found in `{folder}{sub}:`"
        # The folder was found and the file was not. Say which, because the two
        # send the reader to completely different places: a missing System
        # Folder is a wrong image, a missing prefs file is an uninstalled guest.
        near = f"the System Folder `{folder}` has no `{PREFS_NAME}` in any of " \
               f"its {len(subdirs)} subfolder(s)"
    else:
        near = why

    root = parse_hls_long(run(["hls", "-l", ":"]))
    if contains_file(root, PREFS_NAME):
        return KIT_PREFS_HFS, "found at the volume root (a kit image)"
    return None, f"{near}; and no `{KIT_PREFS_HFS}` at the root either"


def describe_prefs_location(folder=None):
    """Where to TELL somebody the file lives, when nothing can be read yet.

    Distinct from find_guest_prefs on purpose: that one answers about an image
    in hand, this one goes in a printed instruction where no image is open. It
    names the English default and says so, rather than presenting one
    localisation as the truth.
    """
    if folder:
        return f"{folder}Preferences:{PREFS_NAME}"
    return (f":System Folder:Preferences:{PREFS_NAME}  (English system; on a "
            f"German one `:Systemordner:`, and the installer finds it either way)")
