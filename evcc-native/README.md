# Native evcc driver (work in progress)

A Go implementation of the same control strategy as `policharger.py`, written as an evcc charger (`type: policharger`) so it can eventually be contributed upstream. **It is not part of evcc yet.** It has been tested against one real charger through a small harness built on evcc's Modbus layer, but not yet inside a full evcc build. For production use, keep using the script.

| File | Goes to (in an evcc checkout) |
| --- | --- |
| `charger/policharger.go` | `charger/policharger.go` |
| `charger/policharger_test.go` | `charger/policharger_test.go` |
| `templates/definition/charger/policharger.yaml` | `templates/definition/charger/policharger.yaml` |

Written against evcc commit `1ca89f2` (10 November 2026).

## What it does

- Implements `api.Charger` (`Status`, `Enabled`, `Enable`, `MaxCurrent`), `api.Meter` (`CurrentPower`) and `api.PhaseCurrents` (`Currents`).
- Uses evcc's shared Modbus connection, so it works with RS485 serial, RTU over TCP and Modbus TCP gateways.
- Keeps the charger in normally locked mode, armed while idle, with the cap in reg 44 and the lock-mode check before arming (see [../docs/how-it-works.md](../docs/how-it-works.md)).
- Retries every operation, reads a written register back when the echo is lost, and caches reads for 5 s so one evcc cycle needs about two Modbus reads.
- Reads of at most 13 registers.
- Three-phase power is computed from reg 25, untested. Only the first connector of dual-connector models is controlled.

## Status

| Step | State |
| --- | --- |
| Driver and template | Written |
| Unit tests against a simulated charger (11 cases: status, arming, lock-mode recovery, enable/disable order, regulation, PV limit, measurements) | Passing |
| evcc template test | Passing |
| Gateway check: the tested gateway now echoes the Modbus TCP transaction ID, so evcc's standard client can use it | Done |
| Test against a real charger (harness on evcc's `util/modbus`, see below) | Passing, 2 of 2 runs |
| Test inside a full evcc build | Pending |
| Discussion with the evcc maintainers | Pending |
| Pull request to evcc-io/evcc | Pending |

## Hardware test

Run on 11 November 2026 against a Policharger 8.1 NW-T2 (model 4, connector 2, software 9.30, single-phase) through a WiFi RS485 gateway in Modbus TCP mode, with evcc stopped. The harness calls the driver's API methods in the order evcc would and uses evcc's own `util/modbus` package unchanged.

| Step | Run 1 | Run 2 |
| --- | --- | --- |
| Create driver (identify, lock-mode check) | 17.3 s | 11.0 s |
| Initial status | B | B |
| `Enable(true)` to status C | 11.5 s | 162 s (see note) |
| Charging at 8 A | 1840 W | 1840 W |
| `MaxCurrent(6)`, measured | 6 A, 1380 W | 6 A, 1380 W |
| `MaxCurrent(10)`, measured | 10 A, 2300 W | 10 A, 2300 W |
| `Enable(false)` to status B | 0.27 s | 0.27 s |

Note on run 2: it started about 150 s after run 1 stopped. Stopping re-arms the charger (reg 80 = 16), and the charger accepts a start only about 205 s after reg 80 is set. Run 2 started at that mark. In normal use the charger stays armed for minutes or hours between sessions, so starts take seconds, as in run 1. A start requested right after a stop waits for the rest of that window.

Driver creation and the first status are slow on a lossy WiFi gateway because of retries. A wired RS485 adapter on the evcc host should avoid most of that (not tested yet).

## Building and testing

```bash
git clone --depth 1 https://github.com/evcc-io/evcc
cp -r evcc-native/charger evcc-native/templates evcc/
cd evcc
go test ./charger/ -run 'Policharger|Templates/policharger'
```

A full evcc binary also needs the web UI built (`make`); see evcc's CONTRIBUTING.md.

## Configuration once available

```yaml
chargers:
  - name: policharger
    type: template
    template: policharger
    modbus: tcpip
    id: 1
    host: 192.168.1.100
    port: 8899
```
