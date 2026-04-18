# NordHash Mining — Safety Procedures & Emergency Protocols

## General Safety Rules

1. **Hearing protection** mandatory in all mining halls (>85 dBA). Approved models: 3M Peltor X5A or equivalent NRR 31+.
2. **ESD wristband** mandatory when handling any electronic components.
3. **No food or drink** in mining halls (spill risk to equipment).
4. **Two-person rule** for any work involving high-voltage (>400V) or heavy lifting (>25 kg).
5. **Lock-Out/Tag-Out (LOTO)** required before any electrical maintenance. Nils (electrician) manages LOTO keys.

## Emergency Contact Chain

### Escalation Matrix

| Severity | Who to Contact | Response Time | Examples |
|----------|---------------|---------------|----------|
| **P1 — Critical** | Magnus (any hour) + Karl (HSE) | Immediate | Fire, electrical shock, chip temp >80°C, transformer trip |
| **P2 — High** | Shift lead + Magnus (business hours) | 30 minutes | PSU fire smell (no flame), cooling system failure, multiple units offline |
| **P3 — Medium** | Shift lead | Next shift start | Single unit failure, fan noise, network issues |
| **P4 — Low** | Maintenance queue | Next Tuesday window | Cosmetic damage, cable management, labeling |

### Emergency Phone Numbers

| Contact | Phone | Role |
|---------|-------|------|
| Magnus Johansson | +46-70-555-0101 | Head of Operations (24/7 for P1) |
| Karl Wahlström | +46-70-555-0102 | HSE Officer (P1 + workplace injuries) |
| Aleksei Volkov | +46-70-555-0142 | Night Shift Lead |
| Nils Pettersson | +46-70-555-0103 | Electrician (electrical emergencies) |
| Vattenfall Grid Control | +46-20-820-820 | Power outages |
| Kiruna Fire Department | 112 | Fire emergency |
| Kiruna Hospital | +46-980-731-00 | Medical emergency (25 min drive) |

## Thermal Emergency Protocol

### Chip Temperature > 80°C — Immediate Shutdown Required

**This is a P1 emergency.** Sustained operation above 80°C risks:
- Solder joint degradation (BGA connections)
- Permanent chip damage
- Potential PCB delamination
- Fire risk (rare but non-zero)

**Procedure:**

1. **Immediately** issue emergency shutdown via MOS for the affected device(s).
2. **Do NOT** attempt to underclock first — at >80°C, shutdown is the only safe action.
3. Notify shift lead and Magnus.
4. Physically inspect the device after 15-minute cooldown:
   - Check fans (spinning? correct RPM?)
   - Check airflow path (obstructions? neighboring units?)
   - Check ambient temperature sensor for the hall
5. If ambient temperature is normal and issue is device-specific, likely causes:
   - Failed fan(s) → replace per SOP-002
   - Thermal paste degradation → reapply per SOP-004
   - Heatsink contact issue → reseat heatsink
6. If ambient temperature is elevated (>35°C), trigger fleet-wide underclock per facility-specs.md.
7. **Do not restart** the device until root cause is identified and fixed.
8. After repair, restart at 70% clock speed. Ramp up 10% per hour while monitoring.
9. Log incident in safety register with: device_id, peak temperature, root cause, corrective action.

### Chip Temperature 75-80°C — Underclock Required

1. Underclock to 80% via MOS immediately.
2. Monitor for 15 minutes.
3. If temperature drops below 70°C, schedule maintenance per SOP-012 Level 2.
4. If temperature remains >75°C at 80% clock, underclock to 70%.
5. If still >75°C at 70% clock, proceed with shutdown protocol above.

## Electrical Safety

### High-Voltage Work

- **Only Nils Pettersson** is authorized for work on the 20kV incoming supply or transformer.
- **Senior technicians** may work on 400V distribution with LOTO and buddy system.
- **All technicians** may work on 12V DC (ASIC level) without LOTO.

### Electrical Fire

1. **Do NOT use water.** Use CO2 extinguisher (located at each hall entrance and every 10 racks).
2. If fire is beyond a single extinguisher: evacuate, call 112, activate Novec suppression (red pull station at each hall entrance).
3. Cut power to affected hall via emergency stop button (red mushroom button, one at each hall entrance).
4. Do not re-enter until fire department clears the area.

### Arc Flash

- All distribution panels have arc flash labels with incident energy (cal/cm2) and required PPE level.
- **PPE requirement**: Category 2 arc flash suit for work inside any panel >100A.
- Arc flash suits stored in electrical room ER-1 (2 sets, sizes M and XL).

## Environmental Incidents

### Water Leak

1. Identify source (cooling system, roof, plumbing).
2. If water is near electrical equipment: cut power to affected area immediately.
3. Deploy portable water barriers (stored in each hall, behind east doors).
4. Call Magnus + Karl.
5. If cooling system leak: isolate the section, switch to passive airflow.

### Gas Detection

- **CO detectors** in all halls (carbon monoxide from potential PSU fires).
- **Alarm threshold**: 35 ppm (STEL) or 25 ppm (TWA-8h).
- If CO alarm triggers: evacuate hall, ventilate, identify source (likely smoldering PSU).

## Incident Reporting

All incidents (P1-P4) must be logged in the safety register within 24 hours.

**Required fields:**
- Date, time, location (hall/rack)
- Incident type (thermal, electrical, mechanical, environmental, personnel)
- Severity (P1-P4)
- Personnel involved
- Root cause (if known)
- Corrective action taken
- Follow-up required (yes/no, assigned to whom)

**P1 and P2 incidents** additionally require:
- Written incident report (Karl's template) within 72 hours
- Review meeting with Magnus + Karl within 1 week
- Corrective action verification within 30 days

## Safety Training

- **New employee orientation**: 2-day safety induction by Karl (mandatory before hall access).
- **Annual refresher**: 1-day training for all operations staff (next session: September 2026).
- **First aid**: Lars and Aleksei are certified first responders (Swedish Red Cross).
- **First aid kits**: Located at each hall entrance and in the breakroom.
- **AED (defibrillator)**: Located in main corridor between Halls B and C.
