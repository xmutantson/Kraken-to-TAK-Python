# Upstream-first reliability implementation — 2026-10-02

## Provenance

Fresh isolated checkout of `xmutantson/Kraken-to-TAK-Python`, main
`78f42c3cd7b0a92d5097c4c174692cb1a5916ce8`. The `tcpcot` remote branch has the
same head; despite its name the reviewed implementation uses UDP.

Fetched `canaryradio/Kraken-to-TAK-Python` main and verified 0 ahead / 4 behind.
Fast-forwarded locally to `7e3be13a33499c5f63809bdb8f7a35f9f37a0206` to retain
the existing upstream timeout, persistence, locking, dependency manifest,
multi-server support, and dashboard work with their original history.

## Additional corrections

- Validate an entire candidate config before saving or publishing it. Atomic
  sibling-file replacement, flush, and fsync preserve the last saved file on
  write/serialization failures. HTTP 400 signals bad input and HTTP 500 signals
  disk failure. Corrupt, ambiguous, or unsupported config stops startup unchanged.
- Retain legacy single-server migration and explicit multicast/persistence
  choices. Reject duplicate station IDs that would collide in CoT UIDs, invalid
  ports, non-finite numbers, partial exclusions, and wrong boolean types.
- Keep fractional bearings; handle exclusion wraparound and normalize output
  longitude at the antimeridian. Reject missing, non-finite, and out-of-range
  coordinates. Require at least a 2D GPS fix before accepting GPSD coordinates.
- Check HTTP status before consuming a response. Keep upstream's five-second
  requests timeout. Expose one polling cycle for safe isolated integration tests.
- Escape XML attribute text and display dashboard station labels as literal
  text. Use UUID4 suffixes for persistent lines to avoid the old 90,000-value
  random suffix space overwriting prior lines during longer sessions.
- Associate live status by station/IP identity instead of array position after
  removal or reordering. Count events only when a local unicast or multicast send
  succeeds. Reload saved dashboard settings after rejected or failed saves.

## Offline checks

Python 3.11.7 in task-local `.venv`; installed declared dependencies from PyPI:
Flask 3.1.3, requests 2.34.2, gpsd-py3 0.3.0. Node 24.18.0; no npm packages.
No global packages or environment settings were changed.

```
.venv/Scripts/python.exe -m unittest discover -s tests -v
node --test tests/dashboard.test.cjs
.venv/Scripts/python.exe -m pip check
git diff --check
```

Results: 42 Python tests pass, 6 JavaScript tests pass, pip reports no broken
requirements, and the diff whitespace check passes. Logged errors in the Python
suite are deliberate synthetic failure cases; there were no test failures.

Python tests cover geometry, fractional and wraparound exclusions, missing data,
GPS fallback/no-fix, HTTP errors, XML text/times, config migration, failed writes,
strict types, station identity, counters, and a whole polling cycle with multiple
stations. The real socket/HTTP/GPS guards raise BaseException so application
fallback handlers cannot swallow an accidental live-I/O attempt. JavaScript tests
parse the whole inline script and exercise the changed rendering/save helpers
with an in-memory DOM stub; they are not a full browser test.

## Remaining decisions and validation limits

- Upstream prefers the same local GPSD fix for every station point, while each
  DOA line starts at that station's Kraken coordinates. For physically separated
  stations this may not be the intended location-source policy. It remains
  unchanged pending a product decision.
- gpsd-py3 0.3.0 uses blocking connect/read operations and exposes no timeout
  parameter. HTTP timeout handling does not bound a stalled GPSD server. Replacing
  or isolating that client needs a separate compatibility decision; no global
  socket timeout or unbounded helper threads were introduced.
- Polling is still sequential; many unreachable HTTP servers can delay a cycle.
  The dashboard remains the upstream unauthenticated non-production Flask server.
  Authentication, exposure policy, parallel polling, and per-station GPS source
  configuration were not invented in this fix set.
- Dependency versions remain upstream's unpinned requirements. Tested versions
  above provide the validation baseline, not a new support matrix.
- The existing CoT line structure (including its two point elements) is retained.
  XML well-formedness is tested; interoperability with TAK and hardware accuracy
  are not claimed. UDP success only means the local socket accepted a datagram.
- Atomic replacement protects against partial writes; it is not a guarantee
  against every filesystem/power-loss scenario. Config is still relative to the
  process working directory. Multiple bridge processes sharing one config file
  are not coordinated by the process-local lock.

No application execution, web listener, RF/GPS hardware access, TAK transmission,
live data service, paid GitHub CI, deployment, push, or publication was performed.
