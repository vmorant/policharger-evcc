# policharger-evcc

Control a **Ulartec Policharger 8.1** EV charger from [evcc](https://evcc.io) over Modbus: solar surplus charging, cheap-tariff plans and load management, with the charger's real charging current fed back to evcc.

evcc has no built-in support for the Policharger. This project provides:

- **`policharger.py`**: a script evcc calls through its `custom` charger and meter types. Works today with any evcc release.
- **`evcc-native/`**: a native Go driver for evcc, work in progress towards an upstream contribution.
- **Documentation** of how the charger behaves over Modbus and why each register is used, based on measurements.

> Unofficial project, not affiliated with or endorsed by Ulartec. It controls high-power electrical equipment: use it at your own risk and check your installation's limits.

## How it works

The Policharger has two quirks that make naive control fail:

1. A charging session **only starts with a setpoint of 16 A or more**, and only **about 3.5 minutes** after the setpoint is set.
2. Stopping by setting the current to 0 takes about **55 seconds**.

The script works around both by using the charger's **"normally locked" mode**. While idle, the charger is kept *armed* (setpoint 16 A) with a separate current cap, so it starts at the current evcc asks for, without a 16 A peak. evcc then starts and stops charging through the lock register, which takes effect **within seconds**.

Details: [docs/how-it-works.md](docs/how-it-works.md) · registers and example frames: [docs/registers.md](docs/registers.md).

## Requirements

- Policharger 8.1 series charger with Modbus RS485 (slave mode).
- An **RS485 to Modbus TCP gateway**, configured as a Modbus TCP server that converts to RTU. Tested with a Waveshare RS232/485 TO WIFI ETH (B). Some gateways (for example the Elfin EW11) produce an RS485 signal too weak for the charger.
- evcc on Linux, with Python 3.8 or later on the same machine. No extra Python packages are needed.

## Quick start

1. Set up the charger menu and the gateway: [docs/setup.md](docs/setup.md).
2. Install the script and its configuration:

   ```bash
   sudo install -m 755 policharger.py /usr/local/bin/policharger.py
   sudo install -m 644 policharger.conf.example /etc/policharger.conf
   sudo nano /etc/policharger.conf        # gateway address, port, slave ID, maximum current
   ```

3. Check that it talks to the charger. Run it as the user evcc runs as (usually `evcc`):

   ```bash
   sudo -u evcc python3 /usr/local/bin/policharger.py dump
   ```

4. Add the charger and meter from [evcc/evcc.yaml.example](evcc/evcc.yaml.example) to your `evcc.yaml` and restart evcc.

## Commands

| Command | Output | Used by |
| --- | --- | --- |
| `status` | `A`, `B` or `C` | evcc charger |
| `enabled` | `true` or `false` | evcc charger |
| `enable true\|false` | — | evcc charger |
| `maxcurrent <A>` | — | evcc charger |
| `power` | watts (measured current × voltage × phases) | evcc meter |
| `current` | amps (measured) | evcc meter |
| `dump` | key registers as JSON | diagnostics |
| `manual <A>` | — | rescue: charge without evcc |
| `version` | script version | — |

Set `POLI_DEBUG=1` to print retries and register writes to stderr.

## Expected timings (evcc interval 30 s)

- Start, with the charger armed: charging begins at the next evcc cycle (about 20 s).
- Start after a session regulated below 16 A: about 3.5 minutes, because the charger has to re-arm.
- Stop: within the next evcc cycle (12–18 s).

## Tested hardware

| Charger | Phases | Software | Gateway | Status |
| --- | --- | --- | --- | --- |
| Policharger NW-T2 | 1 | 9.30 | Waveshare RS232/485 TO WIFI ETH (B), Modbus TCP mode | Working |

Three-phase and dual-connector models have not been tested. If you try one, please open a [hardware report](../../issues/new?template=hardware-report.md) with the output of `policharger.py dump`.

## Known limitations

- Power is estimated from the measured current (1 A resolution) and a configured voltage. Charged energy is not passed to evcc.
- In fast mode evcc requests the maximum current and only reduces it after measuring an overload, so the circuit limit can be exceeded for one cycle.
- If evcc is down, the charger stays locked and does not charge until evcc returns or `manual` is used.
- Not yet verified: whether the charger keeps its lock mode after a power cut. The script checks the mode before arming, so a reset should not cause unwanted charging.

## License

[MIT](LICENSE)
