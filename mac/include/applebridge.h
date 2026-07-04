/*
 * AppleBridge - Mac Side Daemon
 * Header file with type definitions and function declarations
 */

#ifndef APPLEBRIDGE_H
#define APPLEBRIDGE_H

#include <Types.h>
#include <transport.h>     /* the transport seam: opaque ABConn + kABNoData.
                            * No <OpenTransport.h> here any more — OT is now just
                            * one backend (transport_ot.c); nothing above the seam
                            * sees EndpointRef / kOTNoDataErr. */

/* Configuration */
#define BRIDGE_PORT 9000
/* Custom connect-poll result code (kOTTimeOutErr isn't in these OT headers).
 * Returned by ConnectToHost on timeout so main.c can show the .154/NIC hint. */
#define kABConnectTimeout (-30001)
/* Host answered but REFUSED the connection (TCP RST), or the connect attempt
 * failed outright on the local .154 path. On a reachable host this almost
 * always means the host-side AppleBridge server simply isn't running yet, so
 * main.c shows a "start the host bridge" hint instead of a cryptic OTConnect
 * error. (A genuinely unreachable host / wrong NIC gives kABConnectTimeout.) */
#define kABConnectRefused (-30002)
#define MAX_COMMAND_LENGTH 8192
#define MAX_RESPONSE_LENGTH 65536          /* (legacy) — no longer stack-allocated */
#define MAX_DYNAMIC_RESPONSE (4L*1024L*1024L) /* 4 MB cap for command stdout (heap) */
#define MAX_FILE_BYTES (8L*1024L*1024L)    /* per-fork cap for WRITEFILE/READFILE (streamed, not buffered) */
#define SOCKET_BACKLOG 5
#define RESP_SCRATCH 128                   /* small fixed verb/error replies; replaces the 64 KB stack frame */
#define AE_SCRIPT_TIMEOUT (18000L)         /* ticks (~5 min) for the 'dosc' AESend; kAEDefaultTimeout (~60 s) gave spurious -1712 on long Link/SC */

/* Protocol constants */
#define PROTO_COMMAND "COMMAND:"
#define PROTO_STATUS "STATUS:"
#define PROTO_STDOUT "STDOUT:"
#define PROTO_STDERR "STDERR:"
#define PROTO_SCREENSHOT "SCREENSHOT"
#define PROTO_IMAGE "IMAGE:"
#define PROTO_WRITEFILE "WRITEFILE:"   /* host->daemon: write both forks to disk */
#define PROTO_READFILE "READFILE:"     /* daemon->host: stream both forks back */
#define PROTO_FILE "FILE:"             /* READFILE response header */
#define PROTO_KEY "KEY:"               /* host->daemon: inject one keystroke */
#define PROTO_TYPE "TYPE:"             /* host->daemon: inject a run of characters */
#define PROTO_CLICK "CLICK:"           /* host->daemon: inject a mouse click */
#define PROTO_STAT "STAT"              /* host->daemon: report liveness counters */
#define PROTO_AESEND "AESEND:"         /* host->daemon: send an arbitrary Apple Event */
#define PROTO_CLIPGET "CLIPGET"        /* host->daemon: read the guest TEXT scrap */
#define PROTO_CLIPSET "CLIPSET:"       /* host->daemon: set the guest TEXT scrap */
#define PROTO_LISTDIR "LISTDIR:"       /* host->daemon: native directory listing (no ToolServer) */
#define PROTO_HELLO "HELLO:"           /* host->daemon: version negotiation + auth challenge (v0.2) */
#define PROTO_AUTH2 "AUTH2:"           /* host->daemon: host's proof of the shared token (v0.2) */
#define PROTO_SWAPSELF "SWAPSELF"      /* host->daemon: rename the staged '<name> new' binary over the running daemon (self-update) */
#define PROTO_SHUTDOWN "SHUTDOWN"      /* host->daemon: clean System 7 power-off via the Shutdown Manager (ShutDwnPower) */

/* Wire protocol version advertised in the HELLO reply (see docs/PROTOCOL_v0.2.md). */
#define AB_PROTOCOL_VERSION 2

/* Result codes */
typedef enum {
    kBridgeNoErr = 0,
    kBridgeSocketErr = -1,
    kBridgeBindErr = -2,
    kBridgeListenErr = -3,
    kBridgeAcceptErr = -4,
    kBridgeProtocolErr = -5,
    kBridgeCommandErr = -6
} BridgeResult;

/* Command execution result.
 *
 * outData is a dynamically allocated Handle (Mac heap, up to
 * MAX_DYNAMIC_RESPONSE) so command output is no longer bounded by a fixed
 * 64 KB stack buffer. errData stays a small fixed buffer (diagnostics/errors
 * are always short). The response is streamed straight from outData, so it
 * never needs to be assembled into one contiguous send buffer. */
typedef struct {
    short  exitCode;
    Handle outData;       /* dynamically allocated; NULL if no output */
    long   outLen;        /* valid bytes in outData */
    char   errData[256];  /* diagnostics / error text — always small */
} CommandResult;

/* Screenshot data.
 *
 * Captured from the main GDevice PixMap so we know the pixel depth and (for
 * indexed depths <= 8) the colour table. The raw pixels are streamed to the
 * host, which decodes them to PNG data-driven by these fields — no fixed
 * format or size assumption. */
typedef struct {
    short width;
    short height;
    short depth;              /* pixelSize: 1/2/4/8/16/32 */
    long  rowBytes;           /* bytes per scanline (may exceed width*depth/8) */
    long  dataSize;           /* height * rowBytes */
    Ptr   data;               /* raw pixels (dynamically allocated) */
    short clutCount;          /* CLUT entries (0 for direct-colour 16/32-bit) */
    unsigned char clut[768];  /* up to 256 * RGB (8-bit per channel) */
} ScreenshotData;

/* Function declarations */

/* Network: the transport seam (ABConn, ABNetInit/ABConnect/ABRecv/ABSend/ABClose,
 * ParseIPAddress) lives in <transport.h>, pulled in above. */

/* Protocol functions */
BridgeResult ParseCommand(const char *request, char *command, long *commandLength);
BridgeResult SendCommandResult(ABConn *conn, const CommandResult *result);
BridgeResult SendScreenshot(ABConn *conn, const ScreenshotData *screenshot);

/* Command execution */
BridgeResult ExecuteCommand(const char *command, CommandResult *result);
BridgeResult ExecuteAppleEvent(OSType targetSig, OSType evtClass, OSType evtID,
							   const char *directObj, long doLen, CommandResult *result);
void CleanupCommandResult(CommandResult *result);

/* Send a Quit Apple Event to a running app by creator signature (QUIT: verb) */
OSErr QuitAppBySignature(OSType signature);
Boolean IsAppRunning(OSType signature);

/* Screenshot capture */
BridgeResult CaptureScreenshot(ScreenshotData *screenshot);
void CleanupScreenshot(ScreenshotData *screenshot);

/* Fork-aware binary file transfer (fileio.c). Each returns true if the
 * connection is still healthy, false if a transfer desynced the wire (the
 * caller must then drop + reconnect, like ProcessRequest's other verbs). */
Boolean WriteFileVerb(ABConn *conn, char *request, long requestLen);
Boolean ReadFileVerb(ABConn *conn, char *request, long requestLen);

/* Native directory listing (fileio.c): enumerate a folder with PBGetCatInfo and
 * stream a tab-separated listing — works with NO ToolServer (unlike the MPW
 * `Files` path). One line per entry: name<TAB>type<TAB>creator<TAB>size<TAB>modSecs<CR> */
Boolean ListDirVerb(ABConn *conn, char *request, long requestLen);
OSErr SwapSelf(void);   /* self-update: rename '<name> new' over the running daemon */

/* events.c -- synthetic input injection (drive the front GUI app). */
OSErr InjectKey(short charCode, short keyCode);
OSErr InjectKeyMod(short charCode, short keyCode, short modifiers);
OSErr InjectType(const char *text, long len);
OSErr InjectClick(short h, short v);
OSErr InjectClickMod(short h, short v, short count, short modifiers);

/* Utility functions */
void LogMessage(const char *message);
void LogError(const char *message, OSStatus err);

#endif /* APPLEBRIDGE_H */
