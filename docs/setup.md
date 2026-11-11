# Setup

## 1. Charger menu

Settings that should not be changed over Modbus are set in the charger's menu (hold PROG for 5 s and enter the installer password; see the charger manual).

| Setting | Value | Why |
| --- | --- | --- |
| Modbus | Enabled, **Es Maestro: NO**, slave number 1 | evcc is the master |
| Contador (meter) | NO | Not used |
| Sensor general | NO | When on, the charger regulates from household consumption and competes with evcc |
| Scheduled charges (4) | 00:00–00:00 (disabled) | An active schedule blocks charging |
| Conf. valle (off-peak) | NO | Same reason |
| Programa FV | Disabled, if your unit allows it | The script compensates if the licence is active |
| Intensidad máxima de carga | Your installation's limit | Upper bound for the charger itself |

The serial line defaults to 9600 8N1. Leave it as it is.

## 2. RS485 wiring

- Connect the gateway's A and B terminals to A and B on the charger's Modbus terminal block, with a twisted pair (shielded if the run is long).
- Add a 120 Ω termination resistor at each end of the bus if the cable is long or responses are unreliable.
- Use a gateway with a strong RS485 driver. Some low-power modules (for example the Elfin EW11) produced a signal too weak (0–2 V) for the charger.

## 3. Gateway

Configure the gateway as a **Modbus TCP server that converts to Modbus RTU** (often called "Modbus TCP ↔ RTU" or "Modbus gateway" mode), not as a transparent serial bridge.

Settings that worked with a Waveshare RS232/485 TO WIFI ETH (B):

| Setting | Value | Why |
| --- | --- | --- |
| Work mode | Modbus (TCP ↔ RTU) | The script speaks Modbus TCP |
| Socket | TCP server, any port (8899 is the Waveshare default) | The script connects as a client |
| Serial | 9600, 8, 1, None | What the charger expects |
| TCP timeout | 0 (never) | A short idle timeout dropped connections every 30 s |
| Modbus polling | Off | The gateway's own polling mixed up responses |
| Max TCP connections | 8 | Avoids refused connections |
| RS485 switch interval | 350 µs | More reliable turnaround |
| Restart on no data | On, 240 s | Recovers the module if it hangs with Wi-Fi still associated |

Give the gateway a fixed IP address (DHCP reservation) and put it, with the port, in `/etc/policharger.conf`.

## 4. Install the script

On the machine running evcc:

```bash
sudo install -m 755 policharger.py /usr/local/bin/policharger.py
sudo install -m 644 policharger.conf.example /etc/policharger.conf
sudo nano /etc/policharger.conf
```

| Key | Meaning |
| --- | --- |
| `host`, `port` | Gateway address |
| `slave` | Modbus slave ID from the charger menu |
| `max_current` | Highest current accepted from evcc (6–32 A). Set it to your installation's limit |
| `voltage`, `phases` | Used to estimate power from the measured current |
| `state_file`, `lock_file` | Must be writable by the user evcc runs as |

Always run the script as the user evcc runs as (usually `evcc`). Files it creates as root in `/tmp` cannot be written by evcc afterwards.

## 5. Check the connection

```bash
sudo -u evcc python3 /usr/local/bin/policharger.py dump
```

You should get JSON with the model, connector and software version. With a car plugged in, `reg86_pin_connected` should be 1 once the charger is in locked mode.

```bash
sudo -u evcc env POLI_DEBUG=1 python3 /usr/local/bin/policharger.py status
```

The first call puts the charger in normally locked mode and arms it: expect writes to registers 30, 44 and 80 in the debug output, and `B` with a car plugged in.

## 6. Configure evcc

Add the meter and charger from [../evcc/evcc.yaml.example](../evcc/evcc.yaml.example), assign them to your loadpoint and restart evcc. In the evcc UI, set the loadpoint's minimum current to 6 A, the maximum current to at most `max_current`, and the phases.

Recommended: put the loadpoint in a `circuit` with your grid meter and contracted power, so evcc limits charging when the house consumes a lot.

## Charging without evcc

If evcc is down and you need to charge:

```bash
sudo systemctl stop evcc
sudo -u evcc python3 /usr/local/bin/policharger.py manual 10     # 6 A to max_current
```

The charger returns to normal mode and starts charging after about 3.5 minutes. When evcc starts again, the script puts the charger back in locked mode in its first cycle.

## Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `[policharger] ERROR no reply from charger` in the evcc log, now and then | Lost requests on the RS485 link | Normal in small numbers; the script retries. Many in a row point to wiring or the gateway |
| Every call fails | Gateway down or wrong host/port | Ping the gateway, check its mode (Modbus TCP ↔ RTU) and `/etc/policharger.conf` |
| `Modbus exception 0x02` | Wrong slave ID or register not available on your firmware | Check the slave ID; open an issue with `dump` output |
| Status stays `B` for ~3.5 min after enabling | The charger was not armed (previous session ran below 16 A, or first start) | Expected; the next starts are immediate |
| Charger display shows "insufficient current" | PV licence active with reg 73 = 0, or an active schedule | The script raises reg 73; disable schedules in the menu |
| `charger out of sync` warnings | Another tool writes to the charger | Only evcc (through the script) should control it |
| `PermissionError` on the state file | The script was run as root before | Delete the state and lock files and run it as the evcc user |

## Removing it

To give the charger back to its own logic:

```bash
sudo systemctl stop evcc
sudo -u evcc python3 /usr/local/bin/policharger.py manual 16
```

This leaves it in normal mode: it charges at up to 16 A whenever a car is plugged in. Then remove the charger and meter from `evcc.yaml`.
