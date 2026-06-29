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
        {16, 100, 150, 372},  StaticText { disabled,
            /* MPW Rez: '\n' -> CR (0x0D), the char StaticText breaks lines on;
               '\r' -> LF (0x0A), which renders as a box glyph. Use '\n'. */
            "MacNetScan - a System 7 LAN scanner.\n"
            "AppleBridge sibling project.\n"
            "NetBIOS + mDNS + DNS name resolution.\n"
            "Made by Pit with love and Claude's great support.\n"
            "Free software - no warranty (MIT)." },
        {16, 16, 88, 88},     UserItem { disabled }
    }
};

/* About box is a color modal DLOG (not an ALRT) so the logo user-item (item 3)
   can draw with Color QuickDraw; DrawAboutLogo renders it. */
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
