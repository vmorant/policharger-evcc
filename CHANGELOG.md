# Changelog

## 1.0.0 — 2026-11-11

First public release.

- `policharger.py`: control through the charger's normally locked mode, with the charger armed while idle and the current capped through reg 44. Starts within seconds and stops within 4 s when armed.
- Lock-mode check before arming, so a charger back in normal mode never starts on its own.
- Measured charging current and power for evcc (`current`, `power`).
- Retries on every operation, read-back of writes whose echo was lost, read cache shared within an evcc cycle, lock file against concurrent calls, exit code 1 on any failure.
- Configuration file `/etc/policharger.conf`.
- `evcc-native/`: work-in-progress native evcc driver with unit tests.
