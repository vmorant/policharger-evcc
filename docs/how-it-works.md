# How it works

This page explains how the Policharger behaves over Modbus and how `policharger.py` drives it. The register-level details, with example frames, are in [registers.md](registers.md).

All behaviour below was measured on a single-phase Policharger NW-T2 with software 9.30 and the PV Program licence. Other models and firmware versions may differ.

## Charger behaviour

### A session only starts at 16 A or more

The setpoint register (IntRepartida, reg 80) accepts 0–32 A, but a new session only starts when it is **16 A or higher**. With 6 A the charger does not start, even with the manual limit (reg 44), the contracted power (reg 45) and the PV grid limit (reg 73) all lowered to 6 A, and also not in normally locked mode. The threshold is not exposed through any documented register.

Once a session is running, the setpoint can go down to 6 A without stopping it.

### There is an arming delay of about 3.5 minutes

After the setpoint reaches 16 A, the charger waits about **205 seconds** before it starts charging (measured 202, 204, 208 and 212 s). The delay does not depend on the car. As long as the setpoint stays at 16 A, the charger remains *armed*.

### The actual current is min(setpoint, manual limit)

The charger offers the lower of the setpoint (reg 80) and the manual current limit (reg 44). With the setpoint at 16 A and the limit at 6 A, it arms normally and charges at 6 A.

### Normally locked mode starts and stops within seconds

With the lock type (reg 30) set to 1 ("normally locked"), charging is allowed or blocked by the charge lock register (reg 88):

| Action | Normal mode (setpoint 0 ↔ value) | Normally locked mode (lock 1 ↔ 0), already armed |
| --- | --- | --- |
| Start | ~3.5 min | 3–11 s (the charger's own session timer reads 2.5–5 s) |
| Stop | ~55 s | ≤ 4 s |

This mode is documented by the manufacturer precisely for external control: an external system unlocks each charge. Its side effect is that nobody can charge by just plugging in while evcc keeps the charger locked.

### Other findings

- Booleans are read and written as **1/0**. The vendor sheet says `0xFF00`, but writing it returns exception 03.
- There is **no communication watchdog**: after 10 minutes without any Modbus traffic, the charger keeps charging at the last setpoint.
- In locked mode the "connected" register (reg 39) reads 0 even with a car plugged in; the pin register (reg 86) reports the car instead.
- When entering locked mode, the charger sets the general lock (reg 19) to 1 and leaves it there when going back to normal mode.
- Unplugging and plugging the car back in keeps the setpoint and the lock: the car does not start charging on its own.

### Gateway behaviour

- Reads of up to ~14 registers are reliable. With 20–23 registers about 1 in 4 fail; with 45 or more, almost all fail.
- Single requests are lost in bursts (5–30 % at times), with any connection strategy. The script retries every operation.
- Right after writing the lock register the charger does not answer for a few seconds.
- Two clients talking to the gateway at the same time get each other's responses. The script serialises its calls with a lock file.

## Control strategy

From those findings:

- The charger is kept in **normally locked mode**. evcc enables charging with reg 88 = 0 and stops it with reg 88 = 1.
- **While idle** the charger is kept armed: reg 80 = 16 and reg 44 = the current evcc requested. When evcc enables charging, it starts within seconds at that current, with no 16 A peak.
- **While charging** the current is regulated with reg 80. reg 44 is only raised when evcc asks for more than the cap, so it is written a couple of times per session at most (it is unknown whether it is stored in non-volatile memory).
- **Before arming**, the script checks that the lock type is still 1. In normal mode, setting reg 80 = 16 would make the charger start charging on its own about 3.5 minutes later. The lock type is also checked every 10 minutes.
- With the PV Program licence active (reg 85 = 1) and reg 73 = 0, the charger refuses to charge from the grid. The script raises reg 73 to 16 if it is lower.

### Typical sequence

```text
car plugged in, evcc in "off"      status: lock = 1 → cap reg 44 = 6 → reg 80 = 16 → "B"
                                   … the charger arms in ~205 s, without charging …
evcc enables at 12 A               maxcurrent 12: reg 44 = 12
                                   enable true:   reg 88 = 0 → charging within seconds at 12 A
evcc lowers to 9 A                 reg 80 = 9
evcc raises to 14 A                reg 80 = 14, then reg 44 = 14
evcc stops                         enable false:  reg 88 = 1 → stops within 4 s
                                   status:        reg 44 = target, reg 80 = 16 (re-arm)
```

If a session ran below 16 A, re-arming after it stops takes another ~3.5 minutes before the next start is immediate again.

### Reliability measures

- Every read and write is retried. If a write's echo is lost, the register is read back to check whether the value arrived before writing again.
- Reads are cached for 10 s and shared between the calls of one evcc cycle (status, enabled, power, current): about two Modbus reads every 30 s.
- Reads and writes have deadlines below evcc's script timeouts (12 s for reads, 25 s for `enable` and `maxcurrent` with `timeout: 30s` in evcc).
- Any failure exits with code 1 and no output, so evcc never mistakes a failed write for a successful one.
