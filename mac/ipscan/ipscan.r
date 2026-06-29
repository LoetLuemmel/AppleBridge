/* IPScan resources: SIZE (Apple-Event aware), menu bar, About alert, Options dialog. */
#include "SysTypes.r"
#include "Types.r"

resource 'SIZE' (-1) {
    reserved,
    acceptSuspendResumeEvents,
    reserved,
    canBackground,
    doesActivateOnFGSwitch,
    backgroundAndForeground,
    dontGetFrontClicks,
    ignoreChildDiedEvents,
    is32BitCompatible,
    isHighLevelEventAware,
    localAndRemoteHLEvents,
    notStationeryAware,
    dontUseTextEditServices,
    reserved, reserved, reserved,
    2048 * 1024,
    1024 * 1024
};

/* ---- menu bar ---- */
resource 'MBAR' (128) { { 128, 129, 130, 131, 132, 133 } };

resource 'MENU' (128) {
    128, textMenuProc, allEnabled, enabled, "\0x14",
    {
        "About MacNetScan...", noIcon, noKey, noMark, plain,
        "-",                noIcon, noKey, noMark, plain
    }
};

resource 'MENU' (129, "File") {
    129, textMenuProc, allEnabled, enabled, "File",
    {
        "Save As...", noIcon, "S",   noMark, plain,
        "-",          noIcon, noKey, noMark, plain,
        "Quit",       noIcon, "Q",   noMark, plain
    }
};

resource 'MENU' (130, "Edit") {
    130, textMenuProc, allEnabled, enabled, "Edit",
    {
        "Undo",       noIcon, "Z",   noMark, plain,
        "-",          noIcon, noKey, noMark, plain,
        "Cut",        noIcon, "X",   noMark, plain,
        "Copy",       noIcon, "C",   noMark, plain,
        "Paste",      noIcon, "V",   noMark, plain,
        "Clear",      noIcon, noKey, noMark, plain,
        "Select All", noIcon, "A",   noMark, plain
    }
};

resource 'MENU' (131, "Scan") {
    131, textMenuProc, allEnabled, enabled, "Scan",
    {
        "Rescan",     noIcon, "R",   noMark, plain,
        "Stop",       noIcon, ".",   noMark, plain,
        "-",          noIcon, noKey, noMark, plain,
        "Options...", noIcon, noKey, noMark, plain
    }
};

resource 'MENU' (132, "View") {
    132, textMenuProc, allEnabled, enabled, "View",
    {
        "IP Devices",        noIcon, noKey, noMark, plain,
        "AppleTalk Devices", noIcon, noKey, noMark, plain,
        "Zones",             noIcon, noKey, noMark, plain
    }
};

resource 'MENU' (133, "Net") {
    133, textMenuProc, allEnabled, enabled, "Net",
    {
        "AppleTalk Identity...", noIcon, noKey, noMark, plain,
        "-",                     noIcon, noKey, noMark, plain,
        "Ping...",               noIcon, noKey, noMark, plain,
        "DNS Lookup...",         noIcon, noKey, noMark, plain,
        "Traceroute...",         noIcon, noKey, noMark, plain
    }
};

/* ---- info alert (uses ParamText ^0) ---- */
resource 'DITL' (130) {
    {
        {96, 210, 116, 280}, Button { enabled, "OK" },
        {10, 20, 86, 290},   StaticText { disabled, "^0" }
    }
};
resource 'ALRT' (129) {
    {70, 60, 196, 360}, 130,
    { OK, visible, sound1, OK, visible, sound1, OK, visible, sound1, OK, visible, sound1 },
    noAutoCenter
};

/* ---- About box ---- */
resource 'DITL' (128) {
    {
        {156, 300, 176, 370}, Button { enabled, "OK" },
        {16, 120, 150, 372},  StaticText { disabled,
            /* MPW Rez: '\n' -> CR (0x0D), the char StaticText breaks lines on;
               '\r' -> LF (0x0A), which renders as a box glyph. Use '\n'. */
            "MacNetScan - a System 7 LAN scanner.\n"
            "AppleBridge sibling project.\n"
            "NetBIOS + mDNS + DNS name resolution.\n"
            "Made by Pit with love and Claude's great support.\n"
            "Free software - no warranty (MIT)." },
        {12, 12, 108, 108},   UserItem { disabled }
    }
};

/* About box is a color modal DLOG (not an ALRT); item 3 shows PICT 128, the
   MacNetScan LAN-scan logo (color QuickDraw picture, drawn by the Dialog Mgr). */
resource 'DLOG' (132) {
    {64, 50, 252, 438}, dBoxProc, invisible, noGoAway, 0, 128, "About MacNetScan",
    noAutoCenter
};

/* ---- Scan Options dialog ---- */
resource 'DITL' (129) {
    {
        {168, 238, 188, 308}, Button { enabled, "OK" },
        {168, 150, 188, 220}, Button { enabled, "Cancel" },
        {14, 16, 30, 130},    StaticText { disabled, "Start octet:" },
        {12, 136, 28, 200},   EditText  { enabled, "" },
        {40, 16, 56, 130},    StaticText { disabled, "End octet:" },
        {38, 136, 54, 200},   EditText  { enabled, "" },
        {66, 16, 82, 140},    StaticText { disabled, "Timeout (ms):" },
        {64, 146, 80, 210},   EditText  { enabled, "" },
        {92, 16, 108, 140},   StaticText { disabled, "Slots (max 24):" },
        {90, 146, 106, 210},  EditText  { enabled, "" },
        {118, 16, 134, 140},  StaticText { disabled, "Ports (comma):" },
        {138, 16, 156, 304},  EditText  { enabled, "" }
    }
};

resource 'DLOG' (128) {
    {80, 80, 280, 400}, dBoxProc, invisible, noGoAway, 0, 129, "Scan Options",
    noAutoCenter
};

/* ---- host prompt dialog (Ping / DNS / Traceroute) ---- */
resource 'DITL' (131) {
    {
        {68, 230, 88, 300}, Button { enabled, "OK" },
        {68, 150, 88, 220}, Button { enabled, "Cancel" },
        {12, 16, 28, 300},  StaticText { disabled, "Host:" },
        {36, 16, 52, 300},  EditText  { enabled, "" }
    }
};
resource 'DLOG' (130) {
    {90, 90, 200, 410}, dBoxProc, invisible, noGoAway, 0, 131, "Network Tool",
    noAutoCenter
};

/* About-box logo: MacNetScan LAN-scan icon (96x96, 8-bit color PICT). */
data 'PICT' (128) {
    $"02 00 00 00 00 00 00 60 00 60 00 11 02 FF 0C 00"
    $"FF FE 00 00 00 48 00 00 00 48 00 00 00 00 00 00"
    $"00 60 00 60 00 00 00 00 00 01 00 0A 00 00 00 00"
    $"00 60 00 60 00 98 80 60 00 00 00 00 00 60 00 60"
    $"00 00 00 00 00 00 00 00 00 48 00 00 00 48 00 00"
    $"00 00 00 08 00 01 00 08 00 00 00 00 00 00 00 00"
    $"00 00 00 00 00 00 00 00 00 00 00 A0 00 00 00 00"
    $"00 00 00 00 00 01 1A 1A 1A 1A 1A 1A 00 02 1D 1D"
    $"5E 5E 3D 3D 00 03 1A 1A 68 68 3F 3F 00 04 28 28"
    $"66 66 47 47 00 05 25 25 6C 6C 47 47 00 06 26 26"
    $"6E 6E 48 48 00 07 27 27 6F 6F 49 49 00 08 31 31"
    $"6D 6D 4F 4F 00 09 28 28 70 70 4A 4A 00 0A 2A 2A"
    $"73 73 4C 4C 00 0B 2D 2D 75 75 4F 4F 00 0C 2D 2D"
    $"76 76 4F 4F 00 0D 32 32 6E 6E 50 50 00 0E 33 33"
    $"6F 6F 52 52 00 0F 2F 2F 78 78 51 51 00 10 37 37"
    $"73 73 56 56 00 11 39 39 74 74 57 57 00 12 32 32"
    $"7B 7B 54 54 00 13 34 34 7E 7E 56 56 00 14 3B 3B"
    $"76 76 59 59 00 15 3E 3E 78 78 5C 5C 00 16 55 55"
    $"55 55 55 55 00 17 5D 5D 5D 5D 5D 5D 00 18 40 40"
    $"7A 7A 5E 5E 00 19 46 46 7F 7F 63 63 00 1A 64 64"
    $"64 64 64 64 00 1B 73 73 73 73 73 73 00 1C 7D 7D"
    $"7D 7D 7D 7D 00 1D 1E 1E 92 92 50 50 00 1E 2C 2C"
    $"90 90 58 58 00 1F 1D 1D A8 A8 58 58 00 20 18 18"
    $"BC BC 5C 5C 00 21 47 47 80 80 65 65 00 22 48 48"
    $"81 81 65 65 00 23 49 49 82 82 66 66 00 24 4B 4B"
    $"84 84 68 68 00 25 4F 4F 87 87 6B 6B 00 26 51 51"
    $"89 89 6E 6E 00 27 53 53 8A 8A 6F 6F 00 28 54 54"
    $"8B 8B 71 71 00 29 56 56 8C 8C 72 72 00 2A 5C 5C"
    $"92 92 78 78 00 2B 5F 5F 94 94 7A 7A 00 2C 5F 5F"
    $"95 95 7B 7B 00 2D 60 60 95 95 7B 7B 00 2E 60 60"
    $"96 96 7C 7C 00 2F 55 55 A1 A1 77 77 00 30 55 55"
    $"A2 A2 77 77 00 31 00 00 CC CC 44 44 00 32 0D 0D"
    $"E7 E7 65 65 00 33 00 00 FF FF 66 66 00 34 08 08"
    $"F7 F7 68 68 00 35 04 04 FD FD 68 68 00 36 15 15"
    $"F7 F7 6F 6F 00 37 26 26 F9 F9 7B 7B 00 38 49 49"
    $"CF CF 7D 7D 00 39 6A 6A B8 B8 8C 8C 00 3A 6B 6B"
    $"BA BA 8D 8D 00 3B 6D 6D BC BC 8F 8F 00 3C 7C 7C"
    $"AD AD 96 96 00 3D 7D 7D AE AE 96 96 00 3E 7D 7D"
    $"AE AE 97 97 00 3F 7E 7E AE AE 97 97 00 40 7E 7E"
    $"AF AF 98 98 00 41 78 78 C8 C8 9A 9A 00 42 79 79"
    $"C9 C9 9B 9B 00 43 7A 7A CA CA 9C 9C 00 44 7B 7B"
    $"CB CB 9D 9D 00 45 7D 7D CE CE 9F 9F 00 46 5F 5F"
    $"E3 E3 93 93 00 47 4C 4C FE FE 93 93 00 48 7E 7E"
    $"CF CF A0 A0 00 49 7F 7F D0 D0 A1 A1 00 4A 7C 7C"
    $"DD DD A5 A5 00 4B 5D 5D FF FF A0 A0 00 4C 7D 7D"
    $"FF FF B4 B4 00 4D 83 83 83 83 83 83 00 4E 88 88"
    $"88 88 88 88 00 4F 9B 9B 9B 9B 9B 9B 00 50 9D 9D"
    $"9D 9D 9D 9D 00 51 9F 9F 9F 9F 9F 9F 00 52 84 84"
    $"B3 B3 9D 9D 00 53 86 86 B5 B5 9F 9F 00 54 8A 8A"
    $"B8 B8 A2 A2 00 55 8B 8B BA BA A4 A4 00 56 90 90"
    $"BE BE A9 A9 00 57 A0 A0 A0 A0 A0 A0 00 58 AA AA"
    $"AA AA AA AA 00 59 AC AC AC AC AC AC 00 5A AE AE"
    $"AE AE AE AE 00 5B AF AF AF AF AF AF 00 5C BB BB"
    $"BB BB BB BB 00 5D BD BD BD BD BD BD 00 5E BE BE"
    $"BE BE BE BE 00 5F BF BF BF BF BF BF 00 60 92 92"
    $"C0 C0 AA AA 00 61 94 94 C1 C1 AC AC 00 62 80 80"
    $"D1 D1 A2 A2 00 63 83 83 D4 D4 A5 A5 00 64 8B 8B"
    $"DD DD AD AD 00 65 9C 9C C8 C8 B3 B3 00 66 9E 9E"
    $"C9 C9 B5 B5 00 67 9E 9E CA CA B6 B6 00 68 A0 A0"
    $"CC CC B7 B7 00 69 A2 A2 CD CD B9 B9 00 6A A4 A4"
    $"CF CF BB BB 00 6B A5 A5 CF CF BB BB 00 6C 8F 8F"
    $"E6 E6 B3 B3 00 6D 93 93 E6 E6 B5 B5 00 6E 95 95"
    $"E8 E8 B7 B7 00 6F 98 98 EC EC BA BA 00 70 9A 9A"
    $"EE EE BC BC 00 71 9B 9B EF EF BD BD 00 72 97 97"
    $"F5 F5 BC BC 00 73 AA AA D3 D3 C0 C0 00 74 AE AE"
    $"D7 D7 C4 C4 00 75 B2 B2 DA DA C7 C7 00 76 B4 B4"
    $"DC DC CA CA 00 77 B4 B4 DD DD CA CA 00 78 B8 B8"
    $"DF DF CD CD 00 79 9E 9E F2 F2 C0 C0 00 7A 97 97"
    $"FF FF C0 C0 00 7B 9C 9C FF FF C8 C8 00 7C BA BA"
    $"E1 E1 CF CF 00 7D A1 A1 F5 F5 C3 C3 00 7E A2 A2"
    $"F6 F6 C4 C4 00 7F A4 A4 F8 F8 C6 C6 00 80 AA AA"
    $"FF FF CC CC 00 81 B8 B8 EC EC D1 D1 00 82 C2 C2"
    $"C2 C2 C2 C2 00 83 C5 C5 C5 C5 C5 C5 00 84 C7 C7"
    $"C7 C7 C7 C7 00 85 CC CC CC CC CC CC 00 86 CE CE"
    $"CE CE CE CE 00 87 D3 D3 D3 D3 D3 D3 00 88 D8 D8"
    $"D8 D8 D8 D8 00 89 DD DD DD DD DD DD 00 8A C7 C7"
    $"EC EC DB DB 00 8B C9 C9 EE EE DD DD 00 8C CD CD"
    $"F2 F2 E1 E1 00 8D D7 D7 FA FA EA EA 00 8E D8 D8"
    $"FE FE EC EC 00 8F DB DB FD FD EE EE 00 90 DD DD"
    $"FF FF F0 F0 00 91 E1 E1 E1 E1 E1 E1 00 92 E2 E2"
    $"E2 E2 E2 E2 00 93 E4 E4 E4 E4 E4 E4 00 94 E5 E5"
    $"E5 E5 E5 E5 00 95 E8 E8 E8 E8 E8 E8 00 96 E9 E9"
    $"E9 E9 E9 E9 00 97 EA EA EA EA EA EA 00 98 EE EE"
    $"EE EE EE EE 00 99 F0 F0 F0 F0 F0 F0 00 9A F3 F3"
    $"F3 F3 F3 F3 00 9B F5 F5 F5 F5 F5 F5 00 9C F9 F9"
    $"F9 F9 F9 F9 00 9D FA FA FA FA FA FA 00 9E FD FD"
    $"FD FD FD FD 00 9F FE FE FE FE FE FE 00 A0 FF FF"
    $"FF FF FF FF 00 00 00 00 00 60 00 60 00 00 00 00"
    $"00 60 00 60 00 00 02 A1 A0 0B 00 A0 F2 00 C2 A0"
    $"F2 00 01 A0 A0 0B 00 A0 F2 00 C2 A0 F2 00 01 A0"
    $"A0 1B 03 A0 00 00 89 F9 01 FE 89 02 00 4E 4E C4"
    $"A0 02 00 00 89 F9 01 FE 89 02 00 4E 4E 25 05 A0"
    $"00 00 89 01 01 FD 80 01 01 01 FE 89 02 00 4E 4E"
    $"C4 A0 04 00 00 89 01 01 FD 80 01 01 01 FE 89 02"
    $"00 4E 4E 25 05 A0 00 00 89 01 01 FD 80 01 01 01"
    $"FE 89 02 00 4E 4E C4 A0 04 00 00 89 01 01 FD 80"
    $"01 01 01 FE 89 02 00 4E 4E 25 05 A0 00 00 89 01"
    $"01 FD 80 01 01 01 FE 89 02 00 4E 4E C4 A0 04 00"
    $"00 89 01 01 FD 80 01 01 01 FE 89 02 00 4E 4E 1B"
    $"03 A0 00 00 89 F9 01 FE 89 02 00 4E 4E C4 A0 02"
    $"00 00 89 F9 01 FE 89 02 00 4E 4E 1B 03 A0 00 00"
    $"89 F9 01 FE 89 02 00 4E 4E C4 A0 02 00 00 89 F9"
    $"01 FE 89 02 00 4E 4E 15 02 A0 00 00 F5 89 02 00"
    $"4E 4E C4 A0 01 00 00 F5 89 02 00 4E 4E 15 02 A0"
    $"00 00 F5 89 02 00 4E 4E C4 A0 01 00 00 F5 89 02"
    $"00 4E 4E 15 02 A0 00 00 F5 89 02 00 4E 4E C4 A0"
    $"01 00 00 F5 89 02 00 4E 4E 1B 03 A0 00 00 89 F9"
    $"00 FE 89 02 00 4E 4E C4 A0 02 00 00 89 F9 00 FE"
    $"89 02 00 4E 4E 15 02 A0 00 00 F5 89 02 00 4E 4E"
    $"C4 A0 01 00 00 F5 89 02 00 4E 4E 15 02 A0 00 00"
    $"F5 89 02 00 4E 4E C4 A0 01 00 00 F5 89 02 00 4E"
    $"4E 12 00 A0 F2 00 01 4E 4E C7 A0 02 92 92 A0 F2"
    $"00 01 4E 4E 11 FE A0 F2 4E 00 5B C9 A0 05 5E 17"
    $"17 97 A0 A0 F2 4E 11 FE A0 F2 4E 00 5B C9 A0 05"
    $"5E 17 17 97 A0 A0 F2 4E 12 F1 A0 04 97 97 86 87"
    $"87 CD A0 04 92 92 83 89 89 EF A0 10 EE A0 04 57"
    $"57 1B 99 99 D1 A0 02 4F 1C 1C EC A0 10 EE A0 04"
    $"57 57 1B 99 99 D1 A0 02 4F 1C 1C EC A0 0E EC A0"
    $"FE 89 00 5E D4 A0 02 97 97 5E EA A0 12 EB A0 04"
    $"92 92 1B 83 83 D9 A0 04 99 99 1C 5B 5B E9 A0 12"
    $"EB A0 04 92 92 1B 83 83 D9 A0 04 99 99 1C 5B 5B"
    $"E9 A0 0F E6 A0 02 4F 97 97 DD A0 03 92 A0 A0 83"
    $"E7 A0 10 E6 A0 02 5B 4D 4D DF A0 04 97 97 17 87"
    $"87 E6 A0 10 E6 A0 02 5B 4D 4D DF A0 04 97 97 17"
    $"87 87 E6 A0 0F E5 A0 01 99 99 F4 00 FB 5B F0 00"
    $"00 92 E4 A0 10 E3 A0 00 00 F5 97 FB 89 F3 97 02"
    $"5B 00 00 E3 A0 10 E3 A0 00 00 F5 97 FB 89 F3 97"
    $"02 5B 00 00 E3 A0 10 E3 A0 02 00 97 97 E3 86 02"
    $"5B 00 00 FE 4E E6 A0 13 E3 A0 03 00 97 97 86 E6"
    $"01 04 86 86 5B 00 00 FE 4E E6 A0 13 E3 A0 03 00"
    $"97 97 86 E6 01 04 86 86 5B 00 00 FE 4E E6 A0 2B"
    $"E3 A0 23 00 97 97 86 01 01 0E 69 68 90 61 61 0E"
    $"2A 2A 69 81 81 4A A0 A0 33 1D 1D 35 7B 7B 67 11"
    $"11 01 86 86 5B 00 00 FE 4E E6 A0 2B E3 A0 0C 00"
    $"97 97 86 01 01 2F 80 80 44 0B 0B 3A FE 80 02 72"
    $"72 46 FE 33 0D 34 34 1F 44 44 80 2F 2F 01 86 86"
    $"5B 00 00 FE 4E E6 A0 2B E3 A0 0C 00 97 97 86 01"
    $"01 2F 80 80 44 0B 0B 3A FE 80 02 72 72 46 FE 33"
    $"0D 34 34 1F 44 44 80 2F 2F 01 86 86 5B 00 00 FE"
    $"4E E6 A0 2B E3 A0 23 00 97 97 86 01 01 7C 90 90"
    $"19 40 40 90 77 77 27 02 02 03 1E 1E 36 4C 4C 3D"
    $"23 21 8F 77 77 01 86 86 5B 00 00 FE 4E E6 A0 2B"
    $"E3 A0 23 00 97 97 86 01 01 80 48 48 06 7E 7E 6F"
    $"0B 0B 2F 6C 6C 38 32 32 20 6E 6E 79 0B 0B 44 80"
    $"80 01 86 86 5B 00 00 FE 4E E6 A0 2B E3 A0 23 00"
    $"97 97 86 01 01 80 48 48 06 7E 7E 6F 0B 0B 2F 6C"
    $"6C 38 32 32 20 6E 6E 79 0B 0B 44 80 80 01 86 86"
    $"5B 00 00 FE 4E E6 A0 2B E3 A0 23 00 97 97 86 01"
    $"01 90 40 40 2A 90 90 55 21 21 90 8F 8F 37 4B 4B"
    $"23 55 55 90 2A 2A 40 8F 8F 01 86 86 5B 00 00 FE"
    $"4E E6 A0 2B E3 A0 06 00 97 97 86 01 01 80 FE 2F"
    $"01 80 80 FE 2F 05 80 7A 7A 47 80 80 FE 2F 00 80"
    $"FE 2F 07 80 80 01 86 86 5B 00 00 FE 4E E6 A0 2B"
    $"E3 A0 06 00 97 97 86 01 01 80 FE 2F 01 80 80 FE"
    $"2F 05 80 7A 7A 47 80 80 FE 2F 00 80 FE 2F 07 80"
    $"80 01 86 86 5B 00 00 FE 4E E6 A0 2A E3 A0 0E 00"
    $"97 97 86 01 01 90 3D 3D 2A 90 90 53 25 25 FD 90"
    $"10 8C 8C 27 53 53 90 2A 2A 3D 90 90 01 86 86 5B"
    $"00 00 FE 4E E6 A0 2B E3 A0 23 00 97 97 86 01 01"
    $"80 62 62 05 7F 7F 6F 0F 0F 2F 64 64 62 2F 2F 12"
    $"6D 6D 7E 06 06 49 80 80 01 86 86 5B 00 00 FE 4E"
    $"E6 A0 2B E3 A0 23 00 97 97 86 01 01 80 62 62 05"
    $"7F 7F 6F 0F 0F 2F 64 64 62 2F 2F 12 6D 6D 7E 06"
    $"06 48 80 80 01 86 86 5B 00 00 FE 4E E6 A0 2B E3"
    $"A0 23 00 97 97 86 01 01 77 8F 8F 23 3D 3D 90 74"
    $"74 28 04 04 08 28 28 74 90 90 3D 23 23 8D 75 75"
    $"01 86 86 5B 00 00 FE 4E E6 A0 2B E3 A0 0C 00 97"
    $"97 86 01 01 2F 80 80 44 0B 0B 3A FE 80 02 79 79"
    $"6F FE 80 0D 3A 3A 12 44 41 80 2F 2F 01 86 86 5B"
    $"00 00 FE 4E E6 A0 2B E3 A0 0C 00 97 97 86 01 01"
    $"2F 80 80 44 0B 0B 3A FE 80 02 79 79 6F FE 80 0D"
    $"3A 3A 12 44 44 80 2F 2F 01 86 86 5B 00 00 FE 4E"
    $"E6 A0 2B E3 A0 23 00 97 97 86 01 01 15 67 67 8F"
    $"61 61 10 2E 2A 69 8B 8B 8A 69 69 2E 15 15 56 8F"
    $"90 67 18 18 01 86 86 5B 00 00 FE 4E E6 A0 13 E3"
    $"A0 03 00 97 97 86 E6 01 04 86 86 5B 00 00 FE 4E"
    $"E6 A0 13 E3 A0 03 00 97 97 86 E6 01 04 86 86 5B"
    $"00 00 FE 4E E6 A0 10 E3 A0 02 00 97 97 E3 86 02"
    $"5B 00 00 FE 4E E6 A0 13 E3 A0 03 00 97 97 86 E6"
    $"5E 04 86 86 5B 00 00 FE 4E E6 A0 13 E3 A0 03 00"
    $"97 97 86 E6 5E 04 86 86 5B 00 00 FE 4E E6 A0 13"
    $"E3 A0 03 00 97 97 86 E6 00 04 86 86 5B 00 00 FE"
    $"4E E6 A0 17 E3 A0 03 00 97 97 86 EA 00 08 4E 00"
    $"00 4D 86 86 5B 00 00 FE 4E E6 A0 17 E3 A0 03 00"
    $"97 97 86 EA 00 08 4D 00 00 4E 86 86 5B 00 00 FE"
    $"4E E6 A0 13 E3 A0 03 00 97 97 86 E6 16 04 86 86"
    $"5B 00 00 FE 4E E6 A0 19 E3 A0 05 00 97 97 80 31"
    $"31 EC 86 08 4D 86 86 4E 86 86 5B 00 00 FE 4E E6"
    $"A0 19 E3 A0 05 00 97 97 80 31 31 EC 86 08 4D 86"
    $"86 4D 86 86 5B 00 00 FE 4E E6 A0 12 E3 A0 02 00"
    $"97 97 FE 31 E6 86 02 5B 00 00 FE 4E E6 A0 16 E3"
    $"A0 02 00 97 97 E9 86 08 4D 86 86 4D 86 86 5B 00"
    $"00 FE 4E E6 A0 16 E3 A0 02 00 97 97 E9 86 08 4D"
    $"86 86 4E 86 86 5B 00 00 FE 4E E6 A0 10 E3 A0 02"
    $"00 97 97 E3 86 02 5B 00 00 FE 4E E6 A0 10 E3 A0"
    $"02 00 97 97 E3 86 02 5B 00 00 FE 4E E6 A0 11 E3"
    $"A0 02 00 97 97 E3 86 05 5B 00 00 4E 4D 4E E6 A0"
    $"10 E5 A0 02 99 99 00 E0 5B 04 00 00 4D 4D 4E E6"
    $"A0 0E E6 A0 02 5B 4D 4D DD 00 02 4D 4D 4E E6 A0"
    $"0C E6 A0 02 5B 4D 4D DD 00 FE 4E E6 A0 1B E6 A0"
    $"02 4F 97 97 FE A0 FA 4E 01 4D 4D F7 4E FD 4D F8"
    $"4E 04 4D 4D 4E 4E 83 E7 A0 1B EB A0 04 92 92 1B"
    $"83 83 FB A0 FA 4E FE 4D F6 4E 01 4D 4D F4 4E 02"
    $"1C 5B 5B E9 A0 17 EB A0 04 92 92 1B 83 83 FB A0"
    $"FA 4E 01 4D 4D E6 4E 02 1C 5B 5B E9 A0 0E EC A0"
    $"FE 89 00 5E D4 A0 02 97 97 5E EA A0 10 EE A0 04"
    $"4F 4F 1B 99 99 D1 A0 02 4F 1C 1C EC A0 10 EE A0"
    $"04 4F 4F 1B 99 99 D1 A0 02 4F 1C 1C EC A0 12 F1"
    $"A0 04 97 97 86 87 87 CD A0 04 92 92 83 89 89 EF"
    $"A0 10 F2 A0 03 97 1A 1A 5B C9 A0 03 5E 17 17 97"
    $"F0 A0 10 F2 A0 03 97 1A 1A 5B C9 A0 03 5E 17 17"
    $"97 F0 A0 0C F1 A0 01 92 92 C7 A0 01 92 92 EF A0"
    $"0B 00 A0 F2 00 C2 A0 F2 00 01 A0 A0 0B 00 A0 F2"
    $"00 C2 A0 F2 00 01 A0 A0 1B 03 A0 00 00 89 F9 01"
    $"FE 89 02 00 4E 4E C4 A0 02 00 00 89 F9 01 FE 89"
    $"02 00 4E 4E 25 05 A0 00 00 89 01 01 FD 80 01 01"
    $"01 FE 89 02 00 4E 4E C4 A0 04 00 00 89 01 01 FD"
    $"80 01 01 01 FE 89 02 00 4E 4E 25 05 A0 00 00 89"
    $"01 01 FD 80 01 01 01 FE 89 02 00 4E 4E C4 A0 04"
    $"00 00 89 01 01 FD 80 01 01 01 FE 89 02 00 4E 4E"
    $"25 05 A0 00 00 89 01 01 FD 80 01 01 01 FE 89 02"
    $"00 4E 4E C4 A0 04 00 00 89 01 01 FD 80 01 01 01"
    $"FE 89 02 00 4E 4E 1B 03 A0 00 00 89 F9 01 FE 89"
    $"02 00 4E 4E C4 A0 02 00 00 89 F9 01 FE 89 02 00"
    $"4E 4E 1B 03 A0 00 00 89 F9 01 FE 89 02 00 4E 4E"
    $"C4 A0 02 00 00 89 F9 01 FE 89 02 00 4E 4E 15 02"
    $"A0 00 00 F5 89 02 00 4E 4E C4 A0 01 00 00 F5 89"
    $"02 00 4E 4E 15 02 A0 00 00 F5 89 02 00 4E 4E C4"
    $"A0 01 00 00 F5 89 02 00 4E 4E 15 02 A0 00 00 F5"
    $"89 02 00 4E 4E C4 A0 01 00 00 F5 89 02 00 4E 4E"
    $"1B 03 A0 00 00 89 F9 00 FE 89 02 00 4E 4E C4 A0"
    $"02 00 00 89 F9 00 FE 89 02 00 4E 4E 15 02 A0 00"
    $"00 F5 89 02 00 4E 4E C4 A0 01 00 00 F5 89 02 00"
    $"4E 4E 15 02 A0 00 00 F5 89 02 00 4E 4E C4 A0 01"
    $"00 00 F5 89 02 00 4E 4E 0E 00 A0 F2 00 01 4E 4E"
    $"C4 A0 F2 00 01 4E 4E 08 FE A0 F2 4E C2 A0 F2 4E"
    $"08 FE A0 F2 4E C2 A0 F2 4E 00 00 FF"
};
