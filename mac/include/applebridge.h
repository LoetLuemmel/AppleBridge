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
#define MAX_COMMAND_LENGTH 8192
#define MAX_RESPONSE_LENGTH 65536
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

/* Command execution result */
typedef struct {
    short exitCode;
    char stdout[MAX_RESPONSE_LENGTH];
    char stderr[MAX_RESPONSE_LENGTH];
} CommandResult;

/* Screenshot data */
typedef struct {
    short width;
    short height;
    long dataSize;
    Ptr data;
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
void FormatResponse(const CommandResult *result, char *response, long *responseLength);
void FormatScreenshotResponse(const ScreenshotData *screenshot, char *response, long *responseLength);

/* Command execution */
BridgeResult ExecuteCommand(const char *command, CommandResult *result);
void CleanupCommandResult(CommandResult *result);

/* Screenshot capture */
BridgeResult CaptureScreenshot(ScreenshotData *screenshot);
void CleanupScreenshot(ScreenshotData *screenshot);

/* Utility functions */
void LogMessage(const char *message);
void LogError(const char *message, OSStatus err);

#endif /* APPLEBRIDGE_H */
