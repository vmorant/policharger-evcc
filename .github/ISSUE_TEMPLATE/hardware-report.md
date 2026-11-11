---
name: Hardware report
about: Tell us whether it works with your charger model, firmware or gateway
title: "Hardware report: <model> / <firmware>"
labels: hardware
---

**Charger**
- Model and connector (e.g. NW-T2, IN-SC3F):
- Single- or three-phase:
- Output of `policharger.py dump` (remove nothing; it contains no personal data):

```json

```

**Gateway**
- Make and model:
- Mode (Modbus TCP ↔ RTU, RTU over TCP, serial):

**evcc**
- Version:
- Script or native driver:

**Result**
- [ ] Status A/B/C correct
- [ ] Start and stop from evcc work
- [ ] Current regulation works
- [ ] Measured current and power look right

Anything that behaved differently (start delay, minimum start current, errors in the evcc log):
