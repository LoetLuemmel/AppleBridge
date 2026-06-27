/*
 * AppleBridge Watchdog - keep-alive for the faceless daemon (Phase 6).
 *
 * A faceless (onlyBackground) helper. Every few seconds it asks the Process
 * Manager whether the daemon (creator 'ABrg') is running and, if not, relaunches
 * it from its known path. It is the single Startup Items entry, so at boot it
 * brings the daemon up (which in turn chain-launches ToolServer from the prefs)
 * and then keeps it alive.
 *
 * Deliberately tiny: no window, no Open Transport, no Apple Events to the daemon.
 * It only uses the Process Manager (GetNextProcess / LaunchApplication) and quits
 * cleanly on the shutdown kAEQuitApplication so a Finder restart ends it tidily.
 *
 * Note on scope: on the current Basilisk II / Sequoia host a daemon death tends to
 * take the emulator down with it (Open Transport teardown), so the *relaunch-after-
 * mid-session-death* path rarely gets to run here. The boot-time "launch the absent
 * daemon" path is the same code and is what gets exercised in practice.
 */

#include <Quickdraw.h>
#include <Fonts.h>
#include <Events.h>
#include <Processes.h>
#include <Files.h>
#include <AppleEvents.h>

QDGlobals qd;

#define kDaemonCreator  'ABrg'
#define DAEMON_PATH     "MeinMac:MPW:AppleBridge:bin:AppleBridge"
#define kCheckInterval  180L     /* ticks between checks (~3 s at 60/s) */

static Boolean gRunning = true;

static void CtoP(const char *c, Str255 p)
{
    short i = 0;
    while (c[i] && i < 255) { p[i + 1] = c[i]; i++; }
    p[0] = (unsigned char)i;
}

/* Is the daemon (creator 'ABrg') currently running? */
static Boolean DaemonRunning(void)
{
    ProcessSerialNumber psn;
    ProcessInfoRec info;

    psn.highLongOfPSN = 0;
    psn.lowLongOfPSN = kNoProcess;
    while (GetNextProcess(&psn) == noErr) {
        info.processInfoLength = sizeof(info);
        info.processName = NULL;
        info.processAppSpec = NULL;
        if (GetProcessInformation(&psn, &info) == noErr) {
            if (info.processSignature == kDaemonCreator) return true;
        }
    }
    return false;
}

/* Launch the daemon from its known path, staying in the background. */
static OSErr LaunchDaemon(void)
{
    Str255 pPath;
    FSSpec spec;
    LaunchParamBlockRec lpb;
    OSErr err;

    CtoP(DAEMON_PATH, pPath);
    err = FSMakeFSSpec(0, 0, pPath, &spec);
    if (err != noErr) return err;

    lpb.launchBlockID = extendedBlock;
    lpb.launchEPBLength = extendedBlockLen;
    lpb.launchFileFlags = 0;
    /* launchDontSwitch: bring the daemon up without stealing the foreground.
     * LaunchApplication won't start a second instance of an already-running app,
     * so the DaemonRunning() guard plus this call are race-safe. */
    lpb.launchControlFlags = launchContinue | launchNoFileFlags | launchDontSwitch;
    lpb.launchAppSpec = &spec;
    lpb.launchAppParameters = NULL;
    return LaunchApplication(&lpb);
}

static pascal OSErr HandleQuitApp(const AppleEvent *ae, AppleEvent *reply, long refcon)
{
#pragma unused(ae, reply, refcon)
    gRunning = false;
    return noErr;
}

int main(void)
{
    EventRecord ev;
    long nextCheck;

    InitGraf(&qd.thePort);
    AEInstallEventHandler(kCoreEventClass, kAEQuitApplication,
                          NewAEEventHandlerUPP(HandleQuitApp), 0, false);

    nextCheck = 0;   /* check immediately on launch */
    while (gRunning) {
        if (WaitNextEvent(everyEvent, &ev, 60L, NULL)) {
            if (ev.what == kHighLevelEvent) AEProcessAppleEvent(&ev);
        }
        if (TickCount() >= nextCheck) {
            if (!DaemonRunning()) LaunchDaemon();
            nextCheck = TickCount() + kCheckInterval;
        }
    }
    return 0;
}
