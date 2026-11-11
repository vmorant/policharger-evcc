# Native evcc driver (work in progress)

A Go implementation of the same control strategy as `policharger.py`, written as an evcc charger (`type: policharger`) so it can eventually be contributed upstream. **It is not part of evcc yet and has not been tested against a real charger.** For production use, keep using the script.

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
| Test against a real charger | Pending |
| Discussion with the evcc maintainers | Pending |
| Pull request to evcc-io/evcc | Pending |

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
