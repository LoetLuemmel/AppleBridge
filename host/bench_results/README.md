# AppleBridge transport benchmarks

Backend performance numbers for the slirp migration, so the **etherhelper/en8**
path and the **slirp** path can be compared apples-to-apples *before* the default
is switched. Captured with [`../bench_transport.py`](../bench_transport.py).

See the design rationale: the migration plan flags throughput as the one thing to
confirm live — slirp is user-mode NAT (more CPU per packet) but stays
host-internal (removes a wire hop), so the net effect is unknown until measured.

## How to reproduce

The bridge must be up (host server + daemon + ToolServer connected). Then, on the
host:

```bash
cd host
/usr/bin/python3 bench_transport.py --backend etherhelper-en8   # current default
# … switch ~/.basilisk_ii_prefs to "ether slirp", reboot the guest, reconnect …
/usr/bin/python3 bench_transport.py --backend slirp
/usr/bin/python3 bench_transport.py --compare \
    bench_results/etherhelper-en8-*.json bench_results/slirp-*.json
```

All traffic goes through the local control port (`:9001`), so MCP overhead is
excluded. Each test does one warmup transfer (discarded) before timing.

## Metrics

| Metric | What it exercises | Backend-sensitive part |
|---|---|---|
| `latency` (Echo HELLO) | command round trip incl. AE to ToolServer | tail latency |
| `catenate` (raw file) | ~330 KB streamed back over the bridge | clean MiB/s (bytes on :9001 == bytes over the bridge) |
| `dumpfile` (hex text) | ~1.3 MB transfer — bandwidth dominates fixed cost | clean MiB/s |
| `screenshot` | daemon captures ~768 KB pixmap → bridge → host PNG decode | wall-time (PNG byte count is *not* the bridge payload) |

## Baseline — `etherhelper/en8` (2026-06-28)

Guest: System 7.6.1 in Basilisk II, `ether etherhelper/en8`, host `.154` on the
default-route NIC. Transfer file `MeinMac:MPW:AppleBridge:bin:AppleBridge.NJ`.

| Metric | median | mean | p90 | notes |
|---|---|---|---|---|
| latency (Echo) | 154.5 ms | 175.1 ms | 262.7 ms | min 100 ms, sd 49 ms |
| catenate | 1.2 MiB/s | 1.3 MiB/s | 1.5 MiB/s | 322 KiB/transfer |
| dumpfile | 0.8 MiB/s | 0.8 MiB/s | 0.8 MiB/s | 1306 KiB/transfer, very stable |
| screenshot | 1683 ms | 1668 ms | 1760 ms | 36 KiB PNG (uniform screen compresses well) |

Raw samples in the JSON file alongside this README.

### Reading the baseline

Throughput sits around **1 MiB/s** — far below what either host backend can carry,
which supports the plan's core claim: the **emulated 68K Open Transport is the
ceiling**, not the host transport. That is the central reason slirp is not expected
to regress throughput (and may even help by dropping the physical wire hop). The
`dumpfile` figure (larger payload, sd ≈ 0) is the most reliable bandwidth number;
`latency` has a wide spread typical of the cooperative scheduler under AE load.
