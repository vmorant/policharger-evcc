#!/usr/bin/env python3
"""
policharger.py - drive a Ulartec Policharger 8.1 charger from evcc over Modbus.

evcc calls this script through `source: script` (see evcc/evcc.yaml.example):
  status          -> A | B | C
  enabled         -> true | false
  enable <bool>   -> allow or lock charging
  maxcurrent <A>  -> set the charging current
  power           -> charging power in W (measured current x voltage x phases)
  current         -> charging current in A
Extra commands:
  dump            -> key registers as JSON
  manual <A>      -> rescue: back to normal mode and charge at <A> without evcc

Charger behaviour this script relies on (measured on an NW-T2, software 9.30):
  - A session only starts with IntRepartida (reg 80) >= 16 A, about 205 s after it
    was set. While reg 80 stays at 16 the charger stays "armed".
  - The actual current is min(reg 80, reg 44).
  - In "normally locked" mode (reg 30 = 1) an armed charger starts (reg 88 = 0) and
    stops (reg 88 = 1) within seconds.
  - Booleans are 1/0, not 0xFF00 as the vendor sheet says.
  - Typical RS485 gateways are only reliable with reads of up to ~14 registers and
    lose single requests: every operation is idempotent and retried.

Strategy:
  - Idle: reg 80 = 16 (armed) and reg 44 = requested current (cap), so a session
    starts at the requested current without a 16 A peak. Before arming, reg 30 = 1
    is verified: in normal mode reg 80 = 16 would start charging on its own.
  - Charging: the current is regulated with reg 80; reg 44 is only raised.

Any failure exits with code 1 and prints nothing on stdout.

Configuration: /etc/policharger.conf (see policharger.conf.example), or the file
named in the POLICHARGER_CONF environment variable.

Not affiliated with Ulartec. Use at your own risk.
"""
import configparser
import fcntl
import json
import os
import socket
import struct
import sys
import time

VERSION = "1.0.0"

# -- Configuration -------------------------------------------------------------
_cfg = configparser.ConfigParser()
_cfg.read(os.environ.get("POLICHARGER_CONF", "/etc/policharger.conf"))
_c = _cfg["policharger"] if _cfg.has_section("policharger") else {}

HOST = _c.get("host", "192.168.1.100")            # Modbus TCP gateway
PORT = int(_c.get("port", "502"))
SLAVE = int(_c.get("slave", "1"))
MAX_A = int(_c.get("max_current", "16"))          # upper limit accepted from evcc
VOLTS = float(_c.get("voltage", "230"))           # the charger reports current only
PHASES = int(_c.get("phases", "1"))
STATE_FILE = _c.get("state_file", "/tmp/policharger.json")
LOCK_FILE = _c.get("lock_file", "/tmp/policharger.lock")

DEADLINE_S = 12.0            # reads: evcc kills the script after the 15 s default timeout
DEADLINE_WRITE_S = 25.0      # enable/maxcurrent: use timeout: 30s in evcc.yaml
ARM_A = 16                   # minimum reg 80 value that lets the charger start a session
MIN_A = 6
CFG_CHECK_S = 600            # periodic verification of the lock mode
CFG_ARM_S = 60               # the lock mode must have been verified this recently before arming
CACHE_S = 10                 # reads are shared between the calls of one evcc cycle
STATE = {}                   # local state and read cache (saved to STATE_FILE)
DEBUG = bool(os.environ.get("POLI_DEBUG"))

# Registers (decimal addresses)
REG_LOCK_TYPE = 30           # TipoDeBloqueo: 0 normal, 1 normally locked
REG_CURRENT = 37             # Intensidad sensor carga [A]
REG_CONNECTED = 39           # Conectado (reads 0 while locked)
REG_CHARGING = 41            # Cargando
REG_MANUAL_LIMIT = 44        # Intensidad limite carga manual [A]
REG_PV_GRID = 73             # Intensidad permitida red en carga FV [A]
REG_SETPOINT = 80            # IntRepartida [A]
REG_PV_PROGRAM = 85          # Programa FV licence (read-only)
REG_PIN_CONNECTED = 86       # ConectadoPin1 (valid in normally locked mode)
REG_LOCK = 88                # BloqueoCarga1: 0 charging allowed, 1 locked

T0 = time.monotonic()


class ChargerError(Exception):
    pass


def left():
    remaining = DEADLINE_S - (time.monotonic() - T0)
    if remaining <= 0:
        raise ChargerError("deadline exceeded")
    return remaining


def dbg(msg):
    if DEBUG:
        print(f"[policharger] {msg}", file=sys.stderr)


# -- Modbus TCP: a fresh connection per operation -------------------------------
def _recv(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def _xfer(fc, reg, val):
    """One attempt: FC03 returns the register list, FC06 expects the echo. OSError if no reply."""
    left()
    with socket.create_connection((HOST, PORT), timeout=min(2.5, left())) as s:
        s.settimeout(0.3)
        try:                               # drop stale frames left in the gateway
            while s.recv(256):
                pass
        except OSError:
            pass
        time.sleep(0.4)
        s.settimeout(min(1.0, left()))     # replies arrive within ~0.4 s or not at all
        pdu = struct.pack(">BHH", fc, reg, val)
        s.sendall(struct.pack(">HHHB", 0, 0, 1 + len(pdu), SLAVE) + pdu)
        rfc = _recv(s, 8)[7]
        if rfc & 0x80:
            code = _recv(s, 1)[0]
            raise ChargerError(f"Modbus exception 0x{code:02x} (FC 0x{fc:02x}, reg {reg})")
        if rfc != fc:
            raise ConnectionError(f"unexpected function code 0x{rfc:02x}")
        if fc == 3:
            n = _recv(s, 1)[0]
            if n != val * 2:
                raise ConnectionError(f"unexpected length {n}")
            out = list(struct.unpack(">" + "H" * val, _recv(s, n)))
        else:
            r, v = struct.unpack(">HH", _recv(s, 4))
            if r != reg or v != val:
                raise ConnectionError("wrong echo")
            out = v
    time.sleep(0.5)
    return out


def _read(reg, count):
    last = None
    for _ in range(5):
        try:
            return _xfer(3, reg, count)
        except OSError as e:               # timeouts, refused connections, crossed frames
            last = e
            dbg(f"read retry (reg {reg}): {type(e).__name__} {e}")
            time.sleep(0.3)
    raise ChargerError(f"no reply from charger (reg {reg}): {last}")


def _write(reg, val):
    """Write; if the echo is lost, read the register back before writing again."""
    last = None
    for _ in range(4):
        try:
            _xfer(6, reg, val)
            return
        except OSError as e:
            last = e
            dbg(f"no echo (reg {reg}): {type(e).__name__} {e}")
        for _ in range(2):
            time.sleep(0.3)
            try:
                if _xfer(3, reg, 1)[0] == val:
                    dbg(f"reg{reg} verified by read-back")
                    return
                break                      # replied without the value: write again
            except OSError:
                continue
    raise ChargerError(f"no reply from charger on write (reg {reg}): {last}")


def read_block(start, count, into):
    for i, v in enumerate(_read(start, count)):
        into[start + i] = v


def cached_block(state, start, count, into, max_age):
    """Read a block, or reuse a recent read from another call (max_age = 0: always read)."""
    entry = state.get(f"blk{start}")
    if entry and len(entry["v"]) == count and 0 <= time.time() - entry["t"] <= max_age:
        values = entry["v"]
    else:
        values = _read(start, count)
        state[f"blk{start}"] = {"t": time.time(), "v": values}
        save_state(state)
    for i, v in enumerate(values):
        into[start + i] = v


def read_state(state, max_age=CACHE_S):
    """Regs 36-44 (sensors, connected, charging, cap) and 80-89 (setpoint, pin, lock)."""
    r = {}
    cached_block(state, 36, 9, r, max_age)
    cached_block(state, 80, 10, r, max_age)
    return r


def write(reg, val):
    STATE.pop("blk36", None)               # any write invalidates cached reads
    STATE.pop("blk80", None)
    save_state(STATE)
    _write(reg, val)
    dbg(f"reg{reg} = {val}")


# -- Local state -----------------------------------------------------------------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def clamp(amps):
    return max(MIN_A, min(MAX_A, int(round(amps))))


def target(state):
    return clamp(state.get("target", MIN_A))


# -- Charger logic -----------------------------------------------------------------
def charging(r):
    return r[REG_CHARGING] != 0


def connected(r):
    return r[REG_PIN_CONNECTED] != 0 or r[REG_CONNECTED] != 0


def ensure_config(state, r, max_age=CFG_CHECK_S):
    """Normally locked mode (reg 30 = 1) unless verified within max_age.
    With the PV programme licence, reg 73 = 0 blocks charging from the grid."""
    if time.time() - state.get("cfg_checked", 0) < max_age:
        return
    read_block(19, 13, r)                  # regs 19-31
    if r[REG_LOCK_TYPE] != 1:
        if r.get(REG_LOCK) != 1:
            write(REG_LOCK, 1)             # lock before changing mode
            r[REG_LOCK] = 1
        write(REG_LOCK_TYPE, 1)
        r[REG_LOCK_TYPE] = 1
    if r.get(REG_PV_PROGRAM, 0) != 0:
        read_block(REG_PV_GRID, 1, r)
        if r[REG_PV_GRID] < ARM_A:
            write(REG_PV_GRID, ARM_A)
            r[REG_PV_GRID] = ARM_A
    state["cfg_checked"] = time.time()
    save_state(state)


def prearm(state, r, amps):
    """Idle: cap in reg 44 first, then reg 80 = 16, so the charger arms without a peak."""
    if r[REG_MANUAL_LIMIT] != amps:
        write(REG_MANUAL_LIMIT, amps)
        r[REG_MANUAL_LIMIT] = amps
    if r[REG_SETPOINT] != ARM_A:
        ensure_config(state, r, CFG_ARM_S)  # in normal mode reg 80 = 16 would start charging
        write(REG_SETPOINT, ARM_A)
        r[REG_SETPOINT] = ARM_A
    if not state.get("prearmed"):
        state["prearmed"] = True
        save_state(state)


def cmd_status(state):
    r = {}
    cached_block(state, 80, 10, r, 0)
    if r[REG_LOCK] != 0:                   # locked: cannot be charging
        ensure_config(state, r)
        if r[REG_SETPOINT] != ARM_A or not state.get("prearmed"):
            cached_block(state, 36, 9, r, CACHE_S)
            prearm(state, r, target(state))
        print("B" if r[REG_PIN_CONNECTED] != 0 or r.get(REG_CONNECTED, 0) != 0 else "A")
        return
    cached_block(state, 36, 9, r, CACHE_S)  # usually just read by "power"
    ensure_config(state, r)
    if not charging(r):
        prearm(state, r, target(state))
    print("C" if charging(r) else "B" if connected(r) else "A")


def cmd_enabled(state):
    r = {}
    cached_block(state, 80, 10, r, CACHE_S)
    print("true" if r[REG_LOCK] == 0 else "false")


def cmd_power(state):
    r = {}
    cached_block(state, 36, 9, r, 0)
    print(round(r[REG_CURRENT] * VOLTS * PHASES) if charging(r) else 0)


def cmd_current(state):
    r = {}
    cached_block(state, 36, 9, r, CACHE_S)
    print(r[REG_CURRENT] if charging(r) else 0)


def cmd_enable(state, on):
    if not on:
        write(REG_LOCK, 1)                 # a single write, no reads first: stop
        state["enabled"] = False
        state["prearmed"] = False          # the next status re-arms
        save_state(state)
        return
    r = read_state(state)
    ensure_config(state, r)
    if not charging(r):
        prearm(state, r, target(state))    # the cap must be in place before unlocking
    if r[REG_LOCK] != 0:
        write(REG_LOCK, 0)
    state["enabled"] = True
    save_state(state)


def cmd_maxcurrent(state, amps):
    amps = clamp(amps)
    state["target"] = amps
    save_state(state)
    r = read_state(state)
    if not charging(r):
        prearm(state, r, amps)
        return
    if r[REG_SETPOINT] != amps or r[REG_MANUAL_LIMIT] < amps:
        state["prearmed"] = False          # reg 80 no longer 16: re-arm after stopping
        save_state(state)
    if r[REG_SETPOINT] != amps:
        write(REG_SETPOINT, amps)          # setpoint first...
    if r[REG_MANUAL_LIMIT] < amps:
        write(REG_MANUAL_LIMIT, amps)      # ...then lift the cap


def cmd_dump(state):
    r = read_state(state, 0)
    read_block(1, 4, r)
    read_block(19, 13, r)
    read_block(70, 10, r)
    names = {1: "model", 2: "connector", 3: "sw_major", 4: "sw_minor", 25: "three_phase",
             30: "lock_type", 37: "current", 39: "connected", 41: "charging",
             43: "reference_current", 44: "manual_limit", 73: "pv_grid_limit",
             80: "setpoint", 85: "pv_program", 86: "pin_connected", 88: "lock"}
    out = {f"reg{k}_{n}": r[k] for k, n in names.items()}
    out["session_Wh"] = round((r[79] * 65536 + r[78]) / 100, 1)
    out["total_kWh"] = round((r[77] * 65536 + r[76]) / 100000, 1)
    out["state"] = {k: v for k, v in state.items() if not k.startswith("blk")}
    out["version"] = VERSION
    print(json.dumps(out))


def cmd_manual(state, amps):
    """Rescue without evcc: normal mode, charges at <amps> once armed (~3.5 min)."""
    amps = clamp(amps)
    write(REG_MANUAL_LIMIT, amps)
    write(REG_SETPOINT, ARM_A)
    write(REG_LOCK_TYPE, 0)
    write(19, 0)                           # general lock, left at 1 by the charger
    state["cfg_checked"] = 0
    save_state(state)
    print(f"normal mode, {amps} A")


def main():
    global DEADLINE_S
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    if mode in ("enable", "maxcurrent", "manual"):
        DEADLINE_S = DEADLINE_WRITE_S
    arg = sys.argv[2].strip().lower() if len(sys.argv) > 2 else ""
    with open(LOCK_FILE, "a") as lock:     # one call at a time against the gateway
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                left()
                time.sleep(0.2)
        STATE.update(load_state())
        state = STATE
        if mode == "status":
            cmd_status(state)
        elif mode == "enabled":
            cmd_enabled(state)
        elif mode == "enable":
            cmd_enable(state, arg in ("1", "true"))
        elif mode == "maxcurrent":
            cmd_maxcurrent(state, float(arg))
        elif mode == "power":
            cmd_power(state)
        elif mode == "current":
            cmd_current(state)
        elif mode == "dump":
            cmd_dump(state)
        elif mode == "manual":
            cmd_manual(state, float(arg))
        elif mode in ("version", "--version"):
            print(VERSION)
        else:
            raise ChargerError(f"unknown command: {mode}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                 # never exit with code 0 after a failure
        print(f"[policharger] ERROR {e}", file=sys.stderr)
        sys.exit(1)
