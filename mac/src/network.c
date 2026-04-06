/*
 * AppleBridge - Network Layer
 * TCP/IP communication using Open Transport
 */

#include "applebridge.h"
#include <string.h>
#include <stdio.h>

static Boolean gNetworkInitialized = false;

/*
 * Initialize Open Transport network stack
 */
OSStatus InitializeNetwork(void)
{
    OSStatus err;

    if (gNetworkInitialized) {
        return noErr;
    }

    err = InitOpenTransport();
    if (err != noErr) {
        LogError("Failed to initialize Open Transport", err);
        return err;
    }

    gNetworkInitialized = true;
    LogMessage("Open Transport initialized");

    return noErr;
}

/*
 * Shutdown Open Transport
 */
void ShutdownNetwork(void)
{
    if (gNetworkInitialized) {
        CloseOpenTransport();
        gNetworkInitialized = false;
        LogMessage("Open Transport shutdown");
    }
}

/*
 * Create a listening TCP socket
 */
OSStatus CreateListenSocket(EndpointRef *endpoint, InetPort port)
{
    OSStatus err;
    TBind reqAddr, retAddr;
    InetAddress addr;

    /* Create TCP endpoint */
    *endpoint = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr) {
        LogError("Failed to create endpoint", err);
        return err;
    }

    /* Bind to port */
    OTMemzero(&addr, sizeof(addr));
    OTInitInetAddress(&addr, port, kOTAnyInetAddress);

    reqAddr.addr.buf = (UInt8 *)&addr;
    reqAddr.addr.len = sizeof(addr);
    reqAddr.qlen = SOCKET_BACKLOG;

    retAddr.addr.buf = NULL;
    retAddr.addr.maxlen = 0;

    err = OTBind(*endpoint, &reqAddr, &retAddr);
    if (err != noErr) {
        LogError("Failed to bind socket", err);
        OTCloseProvider(*endpoint);
        return err;
    }

    LogMessage("Socket bound and listening");
    return noErr;
}

/*
 * Accept an incoming connection
 */
OSStatus AcceptConnection(EndpointRef listenEndpoint, EndpointRef *clientEndpoint)
{
    OSStatus err;
    TCall call;
    OTResult result;

    /* Wait for incoming connection */
    OTMemzero(&call, sizeof(call));
    call.addr.buf = NULL;
    call.addr.maxlen = 0;
    call.opt.buf = NULL;
    call.opt.maxlen = 0;
    call.udata.buf = NULL;
    call.udata.maxlen = 0;

    /* Check for connection (non-blocking) */
    err = OTListen(listenEndpoint, &call);
    if (err == kOTNoDataErr) {
        /* No connection pending - return so event loop can run */
        return kOTNoDataErr;
    }

    if (err != noErr) {
        LogError("Listen failed", err);
        return err;
    }

    /* Create endpoint for client */
    *clientEndpoint = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr) {
        LogError("Failed to create client endpoint", err);
        return err;
    }

    /* Accept the connection */
    err = OTAccept(listenEndpoint, *clientEndpoint, &call);
    if (err != noErr) {
        LogError("Accept failed", err);
        OTCloseProvider(*clientEndpoint);
        return err;
    }

    LogMessage("Connection accepted");
    return noErr;
}

/*
 * Receive data from client
 */
OSStatus ReceiveData(EndpointRef endpoint, char *buffer, long bufferSize, long *bytesReceived)
{
    OTResult result;
    OTFlags flags;

    *bytesReceived = 0;

    /* Receive data */
    result = OTRcv(endpoint, buffer, bufferSize, &flags);

    if (result < 0) {
        LogError("Receive failed", result);
        return result;
    }

    *bytesReceived = result;
    return noErr;
}

/*
 * Send data to client
 */
OSStatus SendData(EndpointRef endpoint, const char *data, long dataSize)
{
    OTResult result;
    long totalSent = 0;

    while (totalSent < dataSize) {
        result = OTSnd(endpoint, (void *)(data + totalSent), dataSize - totalSent, 0);

        if (result < 0) {
            LogError("Send failed", result);
            return result;
        }

        totalSent += result;
    }

    return noErr;
}
