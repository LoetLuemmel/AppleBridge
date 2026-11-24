#!/usr/bin/env python3
"""Build the guest kit for upload sites that forbid ZIP: a folder of MacBinary files and one MacBinary disk image.

    host/tools/build_macbinary_kit.py <version> <readme.txt> <out-dir>

Sites that archive classic Mac software (Macintosh Garden and the like) reject ZIP and SIT for Mac OS
applications - a ZIP of raw Mac files loses the resource forks - and ask for MacBinary (.bin) or BinHex.
This makes both forms from the same inputs as build_release_kit.py:

  <out-dir>/AppleBridge-<version>-MacBinary/   one .bin per item (the five programs, the prefs, the Read Me),
                                               plus README.txt and an "Installation Guide" link (.webloc, .url)
  <out-dir>/AppleBridge-<version>.img          a 1440 KB HFS volume holding the same items, already decoded:
                                               fits a high-density floppy, mounts as a disk in an emulator
  <out-dir>/AppleBridge-<version>.img.bin      that image as MacBinary (type 'rohd', creator 'ddsk'), the
                                               single-file upload

Source binaries: $APPLEBRIDGE_KIT_SOURCE (default ~/.applebridge/s7floor/kit) with <leaf>.bin for each of
ABJournalDRVR, AppleBridge, AppleBridgeConfig, AppleBridgeInstaller, AppleBridgeWatchdog - put the builds to
ship there first. Every MacBinary gets the shipping name and today's date. Needs hfsutils.
Used for 0.8d52 (2026-11-22): decoded with StuffIt Expander on System 7.0, 7.5.0 (PowerBook Duo 230) and 7.6.1.
"""
import calendar, os, shutil, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import install_bridge as ib

LEAVES = ("ABJournalDRVR", "AppleBridge", "AppleBridgeConfig", "AppleBridgeInstaller", "AppleBridgeWatchdog")
GUIDE = "https://github.com/LoetLuemmel/AppleBridge#quick-start"
NOW = calendar.timegm(time.localtime()) + 2082844800          # Mac epoch, local time as the Finder shows it


def crc(data):
    c = 0
    for byte in data:
        c ^= byte << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if c & 0x8000 else (c << 1) & 0xFFFF
    return c


def finish(h):
    """Dates, MacBinary II version bytes and the header CRC."""
    h[91:95] = NOW.to_bytes(4, "big"); h[95:99] = NOW.to_bytes(4, "big")
    h[122] = 129; h[123] = 129
    h[124:126] = crc(bytes(h[:124])).to_bytes(2, "big")


def rename(src, name, dst):
    """Same forks and Finder info; the shipping NAME and today's dates."""
    b = bytearray(open(src, "rb").read())
    nb = name.encode("mac_roman")
    b[1] = len(nb); b[2:65] = nb + bytes(63 - len(nb))
    h = b[:128]; finish(h); b[:128] = h
    open(dst, "wb").write(b)


def data_file(name, data, ftype, creator, dst):
    """A MacBinary holding a data fork only."""
    h = bytearray(128); nb = name.encode("mac_roman")
    h[1] = len(nb); h[2:2 + len(nb)] = nb; h[65:69] = ftype; h[69:73] = creator
    h[83:87] = len(data).to_bytes(4, "big")
    finish(h)
    open(dst, "wb").write(bytes(h) + data + bytes((-len(data)) % 128))


def run(*a):
    return subprocess.run(list(a), capture_output=True, text=True, check=True).stdout


def main():
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    version, readme_path, out = sys.argv[1], sys.argv[2], os.path.abspath(sys.argv[3])
    kit = os.path.expanduser(os.environ.get("APPLEBRIDGE_KIT_SOURCE", "~/.applebridge/s7floor/kit"))
    readme = open(readme_path, encoding="utf-8").read()
    folder = os.path.join(out, f"AppleBridge-{version}-MacBinary")
    shutil.rmtree(folder, ignore_errors=True); os.makedirs(folder)

    for leaf in LEAVES:
        rename(os.path.join(kit, leaf + ".bin"), leaf, os.path.join(folder, leaf + ".bin"))
    prefs = ib.guest_prefs_text(ib.GUEST_ROUTER, net="OT").encode("mac_roman")   # LF on purpose (R20)
    data_file("AppleBridge Prefs", prefs, b"TEXT", b"ttxt", os.path.join(folder, "AppleBridge Prefs.bin"))
    mac_readme = readme.replace("\n", "\r").encode("mac_roman")
    data_file("Read Me - Installation", mac_readme, b"TEXT", b"ttxt",
              os.path.join(folder, "Read Me - Installation.bin"))
    open(os.path.join(folder, "README.txt"), "w", encoding="utf-8").write(readme)
    open(os.path.join(folder, "AppleBridge Installation Guide.webloc"), "w").write(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0">\n<dict>\n\t<key>URL</key>\n\t<string>{GUIDE}</string>\n</dict>\n</plist>\n')
    open(os.path.join(folder, "AppleBridge Installation Guide.url"), "w").write(
        f"[InternetShortcut]\r\nURL={GUIDE}\r\n")

    # The disk image: the same items, decoded, on a floppy-sized HFS volume.
    img = os.path.join(out, f"AppleBridge-{version}.img")
    open(img, "wb").write(b"\0" * (1440 * 1024))
    run("hformat", "-l", f"AppleBridge {version}", img)
    run("hmount", img)
    try:
        for f in sorted(os.listdir(folder)):
            if f.endswith(".bin"):
                run("hcopy", "-m", os.path.join(folder, f), ":")
        listing = run("hls", "-l", ":")
    finally:
        run("humount")
    data_file(f"AppleBridge-{version}.img", open(img, "rb").read(), b"rohd", b"ddsk", img + ".bin")

    print(listing)
    for f in sorted(os.listdir(out)):
        p = os.path.join(out, f)
        if os.path.isfile(p):
            print(f"{os.path.getsize(p):9d}  {f}")


if __name__ == "__main__":
    main()
