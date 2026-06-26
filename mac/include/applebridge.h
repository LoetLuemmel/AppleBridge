/*
 * AppleBridge - Mac Side Daemon
 * Header file with type definitions and function declarations
 */

#ifndef APPLEBRIDGE_H
#define APPLEBRIDGE_H

#include <Types.h>
#include <OpenTransport.h>
#include <OpenTptInternet.h>

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
#define MAX_RESPONSE_LENGTH 65536          /* screenshot / verb / error scratch buffer */
#define MAX_DYNAMIC_RESPONSE (4L*1024L*1024L) /* 4 MB cap for command stdout (heap) */
#define SOCKET_BACKLOG 5

/* Protocol constants */
#define PROTO_COMMAND "COMMAND:"
#define PROTO_STATUS "STATUS:"
#define PROTO_STDOUT "STDOUT:"
#define PROTO_STDERR "STDERR:"
#define PROTO_SCREENSHOT "SCREENSHOT"
#define PROTO_IMAGE "IMAGE:"

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

/* Network functions */
OSStatus InitializeNetwork(void);
void ShutdownNetwork(void);
OSStatus ConnectToHost(EndpointRef *endpoint, unsigned long hostIP, InetPort port);
OSStatus ReceiveData(EndpointRef endpoint, char *buffer, long bufferSize, long *bytesReceived);
OSStatus SendData(EndpointRef endpoint, const char *data, long dataSize);
unsigned long ParseIPAddress(const char *ipStr);

/* Protocol functions */
BridgeResult ParseCommand(const char *request, char *command, long *commandLength);
BridgeResult SendCommandResult(EndpointRef endpoint, const CommandResult *result);
BridgeResult SendScreenshot(EndpointRef endpoint, const ScreenshotData *screenshot);

/* Command execution */
BridgeResult ExecuteCommand(const char *command, CommandResult *result);
void CleanupCommandResult(CommandResult *result);

/* Send a Quit Apple Event to a running app by creator signature (QUIT: verb) */
OSErr QuitAppBySignature(OSType signature);

/* Screenshot capture */
BridgeResult CaptureScreenshot(ScreenshotData *screenshot);
void CleanupScreenshot(ScreenshotData *screenshot);

/* Utility functions */
void LogMessage(const char *message);
void LogError(const char *message, OSStatus err);

#endif /* APPLEBRIDGE_H */
