/*
 * AppleBridge - Network Layer
 * TCP/IP communication using Open Transport
 * CLIENT MODE - connects OUT to host server
 */

#include <applebridge.h>
#include <mystring.h>

static Boolean gNetworkInitialized = false;

/* External status function from main.c */
extern void StatusMessage(const char *msg);

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
        return err;
    }

    gNetworkInitialized = true;

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
    }
}

/*
 * Connect to host server
 */
OSStatus ConnectToHost(EndpointRef *endpoint, unsigned long hostIP, InetPort port)
{
    OSStatus err;
    InetAddress addr;
    TCall sndCall;
    TBind bindReq;
    InetAddress localAddr;

    StatusMessage("Opening TCP endpoint...");

    /* Create TCP endpoint */
    *endpoint = OTOpenEndpoint(OTCreateConfiguration(kTCPName), 0, NULL, &err);
    if (err != noErr) {
        StatusMessage("OTOpenEndpoint failed!");
        return err;
    }
    StatusMessage("Endpoint opened OK");

    /* Bind to any local port */
    StatusMessage("Binding local port...");
    OTMemzero(&localAddr, sizeof(localAddr));
    OTInitInetAddress(&localAddr, 0, kOTAnyInetAddress);

    bindReq.addr.buf = (UInt8 *)&localAddr;
    bindReq.addr.len = sizeof(localAddr);
    bindReq.qlen = 0;

    err = OTBind(*endpoint, &bindReq, NULL);
    if (err != noErr) {
        StatusMessage("OTBind failed!");
        OTCloseProvider(*endpoint);
        return err;
    }
    StatusMessage("Bound OK");

    /* Set up destination address */
    StatusMessage("Connecting to host...");
    OTMemzero(&addr, sizeof(addr));
    OTInitInetAddress(&addr, port, hostIP);

    OTMemzero(&sndCall, sizeof(sndCall));
    sndCall.addr.buf = (UInt8 *)&addr;
    sndCall.addr.len = sizeof(addr);

    /* Connect to host */
    err = OTConnect(*endpoint, &sndCall, NULL);
    if (err != noErr) {
        StatusMessage("OTConnect failed!");
        OTCloseProvider(*endpoint);
        return err;
    }

    StatusMessage("Connected to host!");

    return noErr;
}

/*
 * Receive data from host
 */
OSStatus ReceiveData(EndpointRef endpoint, char *buffer, long bufferSize, long *bytesReceived)
{
    OTResult result;
    OTFlags flags;

    *bytesReceived = 0;

    /* Receive data */
    result = OTRcv(endpoint, buffer, bufferSize, &flags);

    if (result < 0) {
        return result;
    }

    *bytesReceived = result;
    return noErr;
}

/*
 * Send data to host
 */
OSStatus SendData(EndpointRef endpoint, const char *data, long dataSize)
{
    OTResult result;
    long totalSent = 0;

    while (totalSent < dataSize) {
        result = OTSnd(endpoint, (void *)(data + totalSent), dataSize - totalSent, 0);

        if (result < 0) {
            return result;
        }

        totalSent += result;
    }

    return noErr;
}

/*
 * Parse IP string to unsigned long (e.g., "192.168.1.100")
 */
unsigned long ParseIPAddress(const char *ipStr)
{
    unsigned long ip = 0;
    unsigned long octet = 0;
    int i;

    for (i = 0; ipStr[i]; i++) {
        if (ipStr[i] >= '0' && ipStr[i] <= '9') {
            octet = octet * 10 + (ipStr[i] - '0');
        } else if (ipStr[i] == '.') {
            ip = (ip << 8) | (octet & 0xFF);
            octet = 0;
        }
    }
    ip = (ip << 8) | (octet & 0xFF);

    return ip;
}
