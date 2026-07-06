# AppleBridge Serial Transport — Design (Phase 5, Reach)

Status: **draft** (2026-07-02). Design-first; no code yet.

Phase 5 of the roadmap is *reach* — running AppleBridge on machines the current
stack can't touch. The current bridge is TCP over Open Transport or MacTCP; both
need a **network stack and an Ethernet path**. A large part of the classic-Mac
population has neither: a Mac Plus, SE, or Classic has a serial (modem/printer)
port and nothing else. A **serial transport backend** is the narrowest possible
physical link — two wires and a UART — and is therefore the item that most
widens AppleBridge's reach. This document designs it: how it fits the existing
transport seam, what the host side needs, how framing and reliability change, and
how to test it without real hardware.

## What we already have to build on

Selectable networking shipped in v0.7.0 as a **transport seam**. Everything above
the seam (`main.c`, `protocol.c`, `fileio.c`) sees only the opaque `ABConn` handle
and a small backend-neutral API:

```c
OSStatus ABNetInit(short want);                                  /* bring up a stack, with fallback */
OSStatus ABConnect(ABConn **conn, unsigned long hostIP, unsigned short port);
OSStatus ABRecv(ABConn *conn, char *buf, long bufSize, long *got);   /* noErr+got>0 | kABNoData | <0 */
OSStatus ABSend(ABConn *conn, const char *data, long size);
void     ABClose(ABConn *conn);
```

Two backends implement it behind private `ot_*` / `mt_*` entry points selected by
the `NET=` pref (`kTransportOT` = 0, `kTransportMacTCP` = 1). Adding a third is,
in principle, one more file plus a dispatch arm. The interesting part is that
serial breaks two assumptions the seam was built around.

## The two design tensions

**1. The seam is IP-shaped.** `ABConnect` takes `(hostIP, port)`, and `main.c`
computes `hostIP = ParseIPAddress(gPrefs.ip)` before calling it. A serial link has
**no address** — it is point-to-point; whatever is on the other end of the wire is
"the host." The serial backend must therefore *ignore* `hostIP`/`port` and treat
"connect" as "open and configure the serial port." No seam signature change is
required — the parameters simply go unused for `kTransportSerial`, exactly as a
Unix domain socket would ignore a port.

**2. There is no host TCP server to dial.** The whole architecture is
NAT-reversed: the guest daemon **dials out** to `host_server.py` on `:9000`.
Serial has no dialling and no NAT — it is a symmetric byte pipe. So the host side
needs a **serial reader** that owns the host end of the wire and speaks the same
length-framed protocol. This is the larger piece of new work; see *Host side*.

The wire protocol itself is transport-agnostic — `COMMAND:<len>\n…` and the
CR-framed `STATUS:` response are a byte stream, and the length-framed readers on
both ends already reassemble across arbitrary chunk boundaries. Serial is just
another byte stream, so **the protocol layer does not change**, including the v0.2
`HELLO:` handshake and heartbeat.

## Guest side — `transport_serial.c`

A new backend mirroring the `mt_*` shape, selected as `kTransportSerial` (= 2).

### Connection model

Classic serial I/O is the **Serial Manager**: two drivers per port — input and
output — opened with `OpenDriver("\p.AIn", …)` / `"\p.AOut"` for the modem port
(`.BIn` / `.BOut` for the printer port). "Connect" becomes:

1. `OpenDriver` the in/out drivers for the configured port.
2. `SerReset` the configuration word — baud, data bits, parity, stop bits (e.g.
   57 600 baud, 8-N-1). Optionally set handshaking (`SerHShake` with CTS/RTS).
3. Mark the `ABConn` as open.

There is no handshake and no "refused" — a serial port either opens or it doesn't.
Reconnect (the daemon's existing reconnect loop) becomes *close and re-open the
port*. The `kABConnectTimeout` / `kABConnectRefused` hints in `main.c` are
IP-specific and should be replaced with a serial-appropriate message ("serial
port busy / not available") under `kTransportSerial`.

### `ABConn` fields

`transport_priv.h`'s `struct ABConn` gains serial handles (kept as neutral types
alongside the OT/MacTCP fields):

```c
struct ABConn {
    short          transport;
    void          *ep;        /* OT   */
    unsigned long  stream;    /* MacTCP */
    Ptr            rcvBuf;    /* MacTCP */
    short          inRef;     /* Serial: input driver refnum  (.AIn/.BIn)  */
    short          outRef;    /* Serial: output driver refnum (.AOut/.BOut) */
};
```

### Non-blocking receive

`ABRecv` must return `kABNoData` when the port is idle — the main loop polls it
and must never block (blocking starves System 7's cooperative scheduler, the same
freeze the OT backend was careful to avoid). The Serial Manager gives us exactly
this: `SerGetBuf(inRef, &count)` reports how many bytes are buffered without
blocking. So:

```
sr_Recv:  SerGetBuf(inRef, &n)
          if n == 0            -> *got = 0, return kABNoData
          read min(n, bufSize) via a synchronous PBRead (bytes are already buffered)
          *got = bytesRead, return noErr
```

`sr_Send` is a synchronous `PBWrite` (or `FSWrite`) of all bytes to `outRef`;
long writes are chunked so a slow UART can't stall the loop indefinitely — the
same pattern `ot_Send` uses to ride out flow control. `sr_Close` closes both
drivers; `sr_Init`/`sr_Shutdown` are near-trivial (no global stack to bring up,
unlike OT).

### Port + line configuration (prefs)

Two new prefs keys, both optional with sane defaults:

- `PORT=A` (modem, default) or `PORT=B` (printer).
- `BAUD=9600` (default, safe first-contact; 19 200 / 38 400 / 57 600 selectable in the Control Panel). Higher (115 200) works on faster machines; lower is
  safer on a busy 68000.

`NET=Serial` selects the backend. The fallback ladder in `ABNetInit` should **not**
silently fall back from Serial to OT — a machine configured for serial almost
certainly has no OpenTransport to fall back to, and a silent fall-back would just
fail differently. Serial init failure should retry serial, not switch mediums.

## Host side — a serial reader

`host_server.py` today binds `:9000` and `:9001` and `accept()`s the daemon's
inbound TCP. For serial there is no `accept()`: the host instead **owns a serial
device** and runs the framing loop directly on it.

Recommended shape: add a **serial mode** to `host_server.py` rather than a
separate program, so all the hard-won framing, heartbeat, drain, and command
dispatch are reused unchanged. Selected by an env var / flag:

```
APPLEBRIDGE_SERIAL=/dev/tty.usbserial-XXXX   # or a pty from the test harness
APPLEBRIDGE_BAUD=9600
```

In serial mode the `:9000` accept loop is replaced by "open the device, treat it
as the daemon connection." The device object exposes the same `recv`/`sendall`
surface the code already uses (a thin wrapper over `os.read`/`os.write` on the fd,
with `termios` set for raw 8-N-1 at the chosen baud), so `_read_framed_response`,
`send_command`, `negotiate_version`, and the heartbeat work as-is. The `:9001`
control port and the whole command-dispatch path are unchanged — only the daemon
transport swaps from a socket to a serial fd.

`pyserial` would be the obvious dependency, but the host is deliberately
stdlib-only (firewall + `/usr/bin/python3`); a raw `os.open` + `termios` wrapper
keeps that property and is ~30 lines.

## Framing and reliability

The length-framed protocol rides serial unchanged, but serial removes a guarantee
TCP gave for free: **there is no retransmission**. Two regimes:

- **Emulator via a pseudo-terminal (the test harness):** the link is a host pty,
  which is *lossless* — no bytes are dropped or corrupted. The existing framing is
  sufficient; nothing more is needed to prove the backend end-to-end.
- **Real serial hardware:** a UART can drop or corrupt bytes under noise or
  overrun. The length-framed reader will then desync (it trusts declared lengths).
  Mitigations, in order of cost: enable **hardware flow control** (RTS/CTS) so the
  sender never overruns the receiver; and, if that proves insufficient, add a thin
  **framed-with-checksum** layer under the protocol (length + CRC per frame, with a
  resend on mismatch). The checksum layer is **out of scope for the first pass** —
  it is only needed on real hardware, and the pty harness proves everything else
  first.

## Speed reality

Serial is about *reach*, not throughput. At 57 600 baud the ceiling is ~5.6 KB/s;
115 200 doubles that. Commands and their small replies are fine. Bulk paths are
not: a 768 KB screenshot pixmap is ~2.5 minutes at 57 600 baud, and a large file
read is proportional. This is acceptable for the target machines (a Plus or SE was
never going to stream screenshots quickly), but the design should **degrade
loudly**, not hang: the host's adaptive timeouts must scale with baud, and the
docs should set expectations. No new mechanism — just larger timeouts under serial.

## The test harness (no real hardware needed)

Basilisk II maps a guest serial port to a host device via the `seriala` /
`serialb` prefs (currently empty). A **pseudo-terminal pair** bridges the guest to
the host serial reader with zero hardware:

1. Create a linked pty pair on the host (`socat -d -d pty,raw,echo=0 pty,raw,echo=0`,
   or Python `pty.openpty()` exposing two device paths).
2. Point Basilisk at one end: `seriala /dev/ttysXXX` (the guest's modem port),
   reboot the guest.
3. Start `host_server.py` in serial mode on the *other* end
   (`APPLEBRIDGE_SERIAL=/dev/ttysYYY`).
4. Set the daemon to `NET=Serial`, rebuild, deploy, reboot.
5. `Echo HELLO` over the control port should round-trip — over a wire, not a socket.

Because the pty is lossless, a green `Echo` proves the guest backend, the host
reader, and the framing together. Real-hardware validation (with the reliability
layer) is a later, separate step.

## Changes at a glance

| File | Change |
|---|---|
| `mac/include/transport.h` | `#define kTransportSerial 2` |
| `mac/include/transport_priv.h` | `sr_*` prototypes; `inRef`/`outRef` in `ABConn` |
| `mac/src/transport_serial.c` | **new** — Serial Manager backend (`sr_Init/Connect/Recv/Send/Close`) |
| `mac/src/transport.c` | dispatch arm for `kTransportSerial`; no serial→OT auto-fallback |
| `mac/src/prefs.c` / `prefs.h` | `NET=Serial`, `PORT=A|B`, `BAUD=` keys |
| `mac/src/main.c` | serial-appropriate connect messages; adaptive-timeout note |
| `mac/Makefile.68k` | add `transport_serial.c.o` |
| `host/host_server.py` | serial mode: `os.open`+`termios` fd wrapper replacing the `:9000` accept |
| `host/` (harness) | a small `serial_bridge` helper / documented `socat` recipe |
| `docs/SETUP.md` | a "Serial (unnetworked Macs)" section |

## Compatibility

Serial is fully opt-in via `NET=Serial`; OT and MacTCP are untouched, and a
machine uses exactly one transport at a time. The host must be started in the
matching mode (TCP mode for OT/MacTCP daemons, serial mode for a serial daemon) —
they are different physical media and cannot be served simultaneously by one host
process. The v0.2 `HELLO:` handshake and optional auth ride serial unchanged, so a
serial daemon still negotiates version and can authenticate.

## Decisions to confirm before implementation

1. **Host side:** add a *serial mode to `host_server.py`* (recommended — reuses all
   framing/dispatch), or a *separate serial adapter* process that speaks to the
   existing server? (Default: serial mode in `host_server.py`.)
2. **Reliability:** pty-lossless first, defer the checksum/resend layer to the
   real-hardware step? (Default: yes — prove the backend on the pty harness first.)
3. **Port/line config:** prefs keys `PORT=A|B` + `BAUD=` as above, defaulting to
   modem port / 57 600 / 8-N-1? (Default: yes.)
4. **First PR scope:** guest `transport_serial.c` + host serial mode + the pty
   harness + an `Echo`-over-serial verification — deferring real-hardware +
   reliability to a follow-up? (Default: yes.)
