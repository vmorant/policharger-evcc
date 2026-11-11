# Modbus registers

This page explains every Policharger register `policharger.py` uses: what the manufacturer's register table says, what was measured, what the script uses it for, and an example. It also lists the registers the script leaves alone and why.

- **Source:** Ulartec's Modbus register table for the Policharger 8.1 series (available from Ulartec support), checked against a real charger.
- **Unit measured:** Policharger NW (reg 1 = 4), T2 connector (reg 2 = 2), software 9.30, single-phase (reg 25 = 0), with the PV Program licence (reg 85 = 1).
- The charger behaviour behind these choices is summarised in [how-it-works.md](how-it-works.md).

## Conventions

| Aspect | Value | Why it matters |
| --- | --- | --- |
| Slave ID | 1 by default | Set in the charger menu |
| Read | FC03 (holding registers) | The sheet also lists FC01/02/04, but everything answers FC03 |
| Write | FC06 (single register) | FC16 is not supported |
| Addresses | **Decimal** | The vendor sheet's hexadecimal column has typos between 68 and 84 (for example it gives `0x4C` for 73, which is `0x49`) |
| Booleans | **1 = yes, 0 = no**, for reads and writes | The sheet says `0xFF00`. Writing `0xFF00` returns exception 03 (illegal value) |
| Block reads | About 14 registers at most | Larger reads fail often through typical RS485 gateways |
| Serial line | 9600 8N1 by default (reg 12 = 2) | Never change it over Modbus: you lose the connection |

### Reading a frame

Frames to the gateway carry a Modbus TCP header: `TID(2) PID(2) LENGTH(2) UNIT(1)` followed by the PDU (function code and data).

```text
Request    00 00  00 00  00 06  01  03  00 24  00 09      read 9 registers from 36 (0x24)
Response   00 00  00 00  00 15  01  03  12  00 00 00 0B 00 00 00 01 00 00 00 01 00 00 00 0B 00 10
                                        │   reg36 reg37 reg38 reg39 reg40 reg41 reg42 reg43 reg44
                                        └── 18 data bytes
```

This is a real read during a session: reg 37 = 11 A charging current, reg 39 = 1 connected, reg 41 = 1 charging, reg 43 = 11 A reference, reg 44 = 16 A cap.

A successful FC06 write returns an echo identical to the request. An invalid value returns an exception:

```text
Request    00 00 00 00 00 06 01 06 00 58 FF 00            reg 88 = 0xFF00, as the sheet says
Response   00 00 00 00 00 03 01 86 03                     exception 03: illegal value
Request    00 00 00 00 00 06 01 06 00 58 00 01            reg 88 = 1
Response   00 00 00 00 00 06 01 06 00 58 00 01            echo: accepted
```

## Registers the script writes

### reg 30 · TipoDeBloqueo (lock type)

| | |
| --- | --- |
| Per the sheet | 0 = normal, 1 = normally locked. Read/write |
| Measured | Writing 1 takes effect immediately. With 1, a plugged-in car does not charge until reg 88 = 0 |
| Use | Kept at **1**. Checked every 10 minutes and always before arming (writing reg 80 = 16) if it was not checked in the last minute |
| Why | It allows starting and stopping within seconds through reg 88. In normal mode, arming with reg 80 = 16 would make the charger start on its own after ~3.5 minutes, even with evcc off |

```text
00 00 00 00 00 06 01 06 00 1E 00 01      reg 30 = 1
```

To go back to normal mode, write reg 30 = 0 and then reg 19 = 0.

### reg 88 · BloqueoCarga1 (charge lock, connector 1)

| | |
| --- | --- |
| Per the sheet | Only with reg 30 = 1. TRUE locks charging on connector 1, FALSE allows it |
| Measured | 0 enables: with the charger armed, charging is detected within 3–11 s. 1 locks: it stops within 4 s and keeps reg 80. Writing `0xFF00` returns exception 03 |
| Use | evcc's on/off switch. `enable true` writes 0, `enable false` writes 1. `enabled` returns `true` if reg 88 = 0 |
| Why | It stops charging without touching reg 80, so the charger stays armed and the next start is immediate |

```text
$ policharger.py enable true     → reg 88 = 0   (00 00 00 00 00 06 01 06 00 58 00 00)
$ policharger.py enable false    → reg 88 = 1   (00 00 00 00 00 06 01 06 00 58 00 01)
```

`enable false` performs a single write, with no reads first, so a communication problem cannot delay the stop.

### reg 80 · IntRepartida (allotted current)

| | |
| --- | --- |
| Per the sheet | Charging capacity the master gives the charger. 0–32 A (0–52 A under PV control) |
| Measured | It is the setpoint. Starting a session needs ≥ 16 A and a ~205 s wait. Once charging, it accepts 6–16 A without stopping, and changes apply within 20 s. No watchdog |
| Use | **Idle:** kept at 16 to keep the charger armed. **Charging:** the current evcc requests. The script never writes 0 |
| Why | With a permanent 16, enabling through reg 88 is immediate. Regulating with reg 80 rather than reg 44 keeps reg 44 writes rare |

```text
$ policharger.py maxcurrent 9    while charging → reg 80 = 9    (… 01 06 00 50 00 09)
                                 while idle     → reg 44 = 9, reg 80 stays at 16
```

### reg 44 · Intensidad límite carga manual (manual current limit)

| | |
| --- | --- |
| Per the sheet | 6–32 A. Manual current limit |
| Measured | It caps the actual current: with reg 44 = 6 and reg 80 = 8 the charger delivers 6 A; raising reg 44 to 16 brings it to 8 A within 14 s. It does not change the start threshold |
| Use | **Idle:** reg 44 = requested current, as the cap. **Charging:** only raised when evcc asks for more than the cap |
| Why | reg 80 can stay at 16 (armed) while the session still starts at the current evcc wants |

```text
$ policharger.py maxcurrent 12   idle → reg 44 = 12 (… 01 06 00 2C 00 0C), reg 80 = 16
$ policharger.py enable true     → charges at 12 A
$ policharger.py maxcurrent 14   charging → reg 80 = 14, then reg 44 = 14
```

It is unknown whether reg 44 is stored in non-volatile memory, so the script writes it as rarely as possible.

### reg 73 · Intensidad permitida red en carga FV (grid current allowed for PV charging)

| | |
| --- | --- |
| Per the sheet | 0–960 A. Grid current the internal PV logic may use |
| Measured | With the PV Program licence (reg 85 = 1) and reg 73 = 0, the charger refuses to charge from the grid ("insufficient current" on the display). Lowering it to 6 neither limits a running session nor allows starting at 6 A |
| Use | Raised to **16** if lower, only when reg 85 = 1 |
| Why | reg 85 is read-only, so this is the way to neutralise the internal PV logic |

## Registers the script reads

### reg 41 · Cargando (charging)

1 while the charger delivers current. It drives the `C` state and decides whether `maxcurrent` regulates with reg 80 (charging) or sets the cap in reg 44 (idle).

### reg 86 · ConectadoPin1 (connected, connector 1)

Per the sheet, with reg 30 = 1 it reports a connected vehicle. Measured: 1 with the car plugged in, 0 without it, including after unplugging and plugging back in. Used for the `B` state.

```text
car plugged in, charging locked:   reg 86–89 = [1, 0, 1, 1]
```

### reg 39 · Conectado (connected)

Reports a connected car in normal mode, but reads 0 in locked mode while charging is locked. The script treats the car as connected if reg 86 or reg 39 is 1, which also covers the moment right after the charger dropped back to normal mode.

### reg 37 · Intensidad sensor carga (charging current)

| | |
| --- | --- |
| Per the sheet | 0–32 A. Charging consumption |
| Measured | The actual current, not the setpoint. At 6, 8 and 9 A it matched the setpoint and the grid consumption increase (+1.35, +1.78 and +2.0 kW). With 16 A offered and the car limited to 10 A, it read 10 |
| Use | `current` (reg 37) and `power` (reg 37 × voltage × phases). Both return 0 if reg 41 = 0 |
| Limitation | 1 A resolution and a configured voltage |

```text
reg 37 = 13, reg 41 = 1, 230 V, 1 phase   →   power = 2990   ·   current = 13
```

### reg 43 · Intensidad referencia carga (reference current)

Maximum current the car may draw at each moment. It has always equalled min(reg 80, reg 44). Shown in `dump` for diagnostics only.

### reg 19 · Bloqueo de carga (general charge lock)

Not written during normal operation. The charger sets it to 1 when entering locked mode and leaves it at 1 in normal mode, so `manual` writes reg 19 = 0 after reg 30 = 0.

### reg 71–72, 76–79 · Time and energy (`dump` only)

| Registers | Meaning | Measured format |
| --- | --- | --- |
| 71–72 | Session time | Milliseconds, low word first. Reset when charging stops |
| 76–77 | Total energy | Hundredths of a Wh, low word first |
| 78–79 | Session energy | Hundredths of a Wh, low word first. Reset every session |

Example: reg 76 = 5845 and reg 77 = 1285 → (1285 × 65536 + 5845) / 100 = 842,196 Wh ≈ 842.2 kWh. Over 623 s at 8 A, session energy rose by 316.7 Wh against 315.7 Wh expected.

They are not passed to evcc: one session showed an unexplained jump of exactly 65,536 units. evcc integrates power on its own.

## Registers the script leaves alone

| Registers | Meaning | Why they are not used |
| --- | --- | --- |
| 45, 46 | Peak and off-peak maximum consumption (contracted power, in A) | With the general sensor off they do not cap anything (reg 45 = 6 did not reduce an 8 A session) and do not change the start threshold |
| 23 | General sensor | Must be off. When on, the charger regulates from household consumption and competes with evcc |
| 21, 47–70 | Off-peak discrimination and scheduled charging | Must be disabled; an active schedule blocks charging |
| 28, 85 | PV Program configuration and licence | reg 85 is read-only (exception 02 on write); its effect is neutralised with reg 73 |
| 12–17 | Serial settings, slave ID, master/slave mode | Changing them breaks communication |
| 83, 84 | Reset and factory defaults | Never written |
| 13 | Meter type | Not needed for evcc |
| 40, 42, 87, 89 | Connector 2 | Dual-connector models are not supported yet |
| 1–10, 25 | Identification and three-phase flag | Informational (`dump`) |
| 90–95 | Undocumented | They respond; above 95 the charger returns exception 02 |

## Complete register map

From the vendor sheet, with what was observed. *Measured* = verified in tests · *Read* = value observed, effect not tested · *Sheet* = vendor description only.

| Reg | Name | Access | Notes | Confidence |
| --- | --- | --- | --- | --- |
| 1 | Model | R | 1 IN, 2 PRO, 3 OUT, 4 NW, 5 ÖN | Read |
| 2 | Connector | R | 1 T1, 2 T2, 3 T23F, 4–7 and 10–11 dual, 8 SC, 9 SC3F | Read |
| 3–4 | Software version | R | major, minor | Read |
| 5–10 | Hardware version, serial number | R | | Read |
| 12 | Serial configuration | R/W | 0–5: 9600/19200 with even, none or odd parity | Read |
| 13 | Meter type | R/W | 0 none … 9 | Read |
| 14–17 | Slave ID, master, Modbus enabled, number of slaves | R/W | | Read |
| 18 | Keypad locked | R/W | | Read |
| 19 | General charge lock | R/W | Set to 1 by the charger in locked mode | Measured |
| 20 | Emergency charge | R/W | | Sheet |
| 21 | Off-peak discrimination | R/W | Keep off | Measured |
| 22 | Weekend consumption setting | R/W | Only 1 or 2 accepted | Measured |
| 23, 24 | General sensor, separate general sensor | R/W | Keep off | Read |
| 25 | Three-phase | R/W | | Read |
| 26, 27 | Three sensors, average of sensors | R/W | | Read |
| 28, 29 | PV generation, grid injection | R/W | | Read |
| 30 | Lock type | R/W | 0 normal, 1 normally locked | Measured |
| 31 | Restart after stop during scheduled charge | R/W | | Sheet |
| 32–35 | Clamp factors | R/W | | Sheet |
| 36 | Total sensor current | R | | Read |
| 37 | Charging current | R | Actual current, A | Measured |
| 38 | PV sensor current | R | | Read |
| 39 | Connected | R | 0 while locked | Measured |
| 40 | Connected 2 | R | | Sheet |
| 41 | Charging | R | | Measured |
| 42 | Charging 2 | R | | Sheet |
| 43 | Reference current | R | min(reg 80, reg 44) | Measured |
| 44 | Manual current limit | R/W | Caps the actual current | Measured |
| 45, 46 | Peak, off-peak maximum consumption | R/W | Contracted power in A | Measured |
| 47–62 | Scheduled charging 1–4 (start, end) | R/W | | Sheet |
| 63–66 | Days of scheduled charging 1–4 | R/W | | Sheet |
| 67–70 | Off-peak window | R/W | | Sheet |
| 71–72 | Charging time | R | ms, low word first | Measured |
| 73 | Grid current allowed for PV charging | R/W | 0 blocks grid charging with the PV licence | Measured |
| 74–75 | Total kWh | R | 32-bit counter, format not verified | Read |
| 76–77 | Total kWh 2 | R | Hundredths of a Wh, low word first | Measured |
| 78–79 | Charged energy | R | Session, hundredths of a Wh, low word first | Measured |
| 80 | Allotted current (setpoint) | R/W | ≥ 16 to start a session | Measured |
| 81 | Restart after stop during scheduled charge | R/W | | Sheet |
| 82 | DC leakage setting | R/W | | Read |
| 83, 84 | Reset, factory defaults | W | Never write | Sheet |
| 85 | PV Program licence | R | Read-only | Measured |
| 86, 87 | Connected pin 1, 2 | R | Valid in locked mode | Measured (86) |
| 88, 89 | Charge lock 1, 2 | R/W | Valid in locked mode | Measured (88) |
| 90–95 | Undocumented | R | Exception 02 above 95 | Read |
