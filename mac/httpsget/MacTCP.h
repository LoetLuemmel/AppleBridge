/* Minimal MacTCP TCP interface for Retro68/Multiversal — layouts copied from
 * Apple's Universal Interfaces MacTCP.h (TCP only; UDP/ICMP/DNR omitted). */
#ifndef __MACTCP_MIN__
#define __MACTCP_MIN__
#include <MacTypes.h>
#pragma pack(push, 2)
typedef unsigned long ip_addr;
typedef unsigned short tcp_port;
typedef unsigned long StreamPtr;
struct wdsEntry { unsigned short length; Ptr ptr; };
typedef struct wdsEntry wdsEntry;
enum { TCPCreate = 30, TCPPassiveOpen = 31, TCPActiveOpen = 32, TCPSend = 34, TCPNoCopyRcv = 35,
       TCPRcvBfrReturn = 36, TCPRcv = 37, TCPClose = 38, TCPAbort = 39, TCPStatus = 40,
       TCPExtendedStat = 41, TCPRelease = 42, TCPGlobalInfo = 43 };
enum { timeoutValue = 0x80, timeoutAction = 0x40, typeOfService = 0x20, precedence = 0x10 };
typedef struct TCPCreatePB { Ptr rcvBuff; unsigned long rcvBuffLen; void *notifyProc; Ptr userDataPtr; } TCPCreatePB;
typedef struct TCPOpenPB { SInt8 ulpTimeoutValue, ulpTimeoutAction, validityFlags, commandTimeoutValue;
  ip_addr remoteHost; tcp_port remotePort; ip_addr localHost; tcp_port localPort;
  SInt8 tosFlags, precedence; Boolean dontFrag; SInt8 timeToLive, security, optionCnt, options[40]; Ptr userDataPtr; } TCPOpenPB;
typedef struct TCPSendPB { SInt8 ulpTimeoutValue, ulpTimeoutAction, validityFlags; Boolean pushFlag, urgentFlag; SInt8 filler;
  Ptr wdsPtr; unsigned long sendFree; unsigned short sendLength; Ptr userDataPtr; } TCPSendPB;
typedef struct TCPReceivePB { SInt8 commandTimeoutValue; Boolean markFlag, urgentFlag; SInt8 filler;
  Ptr rcvBuff; unsigned short rcvBuffLen; Ptr rdsPtr; unsigned short rdsLength, secondTimeStamp; Ptr userDataPtr; } TCPReceivePB;
typedef struct TCPClosePB { SInt8 ulpTimeoutValue, ulpTimeoutAction, validityFlags, filler; Ptr userDataPtr; } TCPClosePB;
typedef struct TCPStatusPB { SInt8 ulpTimeoutValue, ulpTimeoutAction; long unused;
  ip_addr remoteHost; tcp_port remotePort; ip_addr localHost; tcp_port localPort;
  SInt8 tosFlags, precedence, connectionState, filler;
  unsigned short sendWindow, rcvWindow, amtUnackedData, amtUnreadData; Ptr securityLevelPtr;
  unsigned long sendUnacked, sendNext, congestionWindow, rcvNext, srtt, lastRTT, sendMaxSegSize;
  void *connStatPtr; Ptr userDataPtr; } TCPStatusPB;
typedef struct TCPAbortPB { Ptr userDataPtr; } TCPAbortPB;
typedef struct TCPiopb {
  SInt8 fill12[12]; void *ioCompletion; short ioResult; Ptr ioNamePtr; short ioVRefNum, ioCRefNum, csCode;
  StreamPtr tcpStream;
  union { TCPCreatePB create; TCPOpenPB open; TCPSendPB send; TCPReceivePB receive; TCPClosePB close;
          TCPAbortPB abort; TCPStatusPB status; } csParam;
} TCPiopb;
#pragma pack(pop)
#endif
