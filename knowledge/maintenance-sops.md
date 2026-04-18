# NordHash Mining — Maintenance Standard Operating Procedures

## SOP-001: Scheduled Maintenance Window

**Frequency**: Weekly (Tuesdays 06:00-10:00 CET)
**Scope**: Non-critical maintenance accumulated during the week.

1. Magnus reviews maintenance queue (Monday EOD).
2. Prioritize by: safety issues > warranty items > performance items > cosmetic.
3. Day shift executes during Tuesday window.
4. Affected racks powered down sequentially (max 2 racks offline simultaneously).
5. Update maintenance log in MOS after each unit serviced.
6. If maintenance overruns window, resume next Tuesday unless safety-critical.

## SOP-002: Fan Replacement

**Trigger**: Fan RPM < 4,000 or audible bearing noise.
**Time**: ~15 minutes per unit.
**Personnel**: Any technician.

1. Power down ASIC via MOS dashboard (graceful shutdown).
2. Wait 60 seconds for capacitors to discharge.
3. Disconnect power cables.
4. Remove faulty fan (4 Phillips screws, 1 PWM connector).
5. Install replacement fan from inventory SR-1.
6. Reconnect power, boot via MOS.
7. Verify fan RPM in MOS telemetry (expect 5,500-6,000 RPM at idle).
8. Log replacement in maintenance tracker (device_id, old fan serial, new fan serial, date).

## SOP-003: PSU Replacement

**Trigger**: PSU voltage outside 11.8-12.6V range, or intermittent power cycling.
**Time**: ~30 minutes per unit.
**Personnel**: Senior technician or Nils (electrician) for high-voltage rail issues.

1. Power down ASIC via MOS.
2. Disconnect all cables from PSU.
3. Remove PSU from chassis (4 screws + sliding rail).
4. Install replacement APW12/APW15 from inventory SR-1.
5. Reconnect cables. Verify polarity markings.
6. Boot and verify stable voltage in MOS telemetry for 10 minutes.
7. Monitor for 24 hours — if voltage drifts, escalate to Nils.

## SOP-004: Thermal Paste Reapplication

**Trigger**: Chip temperature > 75°C sustained with normal fan operation, or efficiency > 15% worse than nominal.
**Time**: ~45 minutes per unit.
**Personnel**: Senior technician only (Lars or Henrik).

1. Power down and discharge (SOP-002 steps 1-3).
2. Remove heatsink assembly (6 screws, careful with thermal interface).
3. Clean old paste with isopropyl alcohol (99%) and lint-free cloth.
4. Apply new paste (Arctic MX-6) — thin X pattern on each chip.
5. Reinstall heatsink. Torque screws to 0.5 Nm in star pattern.
6. Boot and monitor chip temps for 30 minutes.
7. Expected result: 5-10°C temperature drop.

## SOP-005: Firmware Update

**Trigger**: Bitmain security advisory, or MOS platform update.
**Time**: ~5 minutes per unit (batch-updatable via MOS).
**Personnel**: Katarina (firmware specialist) or David (network).

1. Download firmware from Bitmain support portal (verify SHA256 hash).
2. Test on 3 units in Hall D (newest hardware, fastest recovery if issues).
3. Monitor for 24 hours.
4. If stable, deploy to remaining fleet via MOS batch update (max 100 units per batch).
5. Stagger batches 30 minutes apart to avoid simultaneous reboot impact on hashrate.

## SOP-010: Rack Inspection

**Trigger**: Monthly, or after any environmental alert (temperature, humidity, power).
**Time**: ~20 minutes per rack.
**Personnel**: Any technician.

1. Visual inspection: cable management, dust accumulation, physical damage.
2. Thermal camera scan of all units (look for hot spots > 80°C).
3. Check airflow: no obstructions, all fans spinning.
4. Verify network connectivity (MOS agent responding).
5. Note any anomalies in inspection log.
6. If dust accumulation is significant, schedule compressed air cleaning.

## SOP-012: Thermal Response Protocol

**Trigger**: Any device reporting chip temperature > 70°C sustained for > 10 minutes.
**Severity levels and response**:

### Level 1: Elevated (70-75°C)
1. **First action**: Underclock to 90% via MOS.
2. Monitor for 30 minutes.
3. If temperature stabilizes below 70°C, maintain underclock until next maintenance window.
4. Schedule thermal paste check (SOP-004) for next Tuesday.

### Level 2: High (75-80°C)
1. **First action**: Underclock to 80% via MOS.
2. Check cooling system airflow in the rack (SOP-010 steps 3-4).
3. If temperature doesn't drop below 75°C within 15 minutes, underclock to 70%.
4. Schedule immediate thermal paste reapplication (SOP-004).
5. If no on-site technician qualified for thermal paste (e.g., Jean on leave, only Oscar available), maintain underclock and escalate to next shift with a senior tech.

### Level 3: Critical (>80°C)
1. **Immediate shutdown** via MOS emergency stop.
2. Notify Magnus (any hour) and Karl (HSE).
3. Do not restart until inspected by senior technician.
4. Inspect for: failed fans, blocked airflow, PSU overvoltage, ambient temperature anomaly.
5. After repair, boot at 70% clock speed, ramp up 10% per hour while monitoring.

**Key rule**: When no qualified technician is on-site for thermal paste work, **always underclock first** rather than attempting repairs beyond your certification level. Underclocking is safe and reversible; improper thermal paste application can permanently damage chips.

## SOP-015: Hash Board Replacement

**Trigger**: Hash board producing < 50% of rated hashrate, or control board reports board offline.
**Time**: ~60 minutes per unit.
**Personnel**: Senior technician only.

1. If under warranty, file RMA with Bitmain before opening unit.
2. Power down and discharge.
3. Photograph board layout before removal (for reassembly reference).
4. Remove faulty hash board (ribbon cables + power connectors).
5. Install replacement board.
6. Boot and run calibration (MOS auto-calibrate, ~20 minutes).
7. Verify hashrate within 10% of rated for 1 hour.

## SOP-020: Emergency Power Loss

**Trigger**: Grid power failure or transformer trip.
**Personnel**: Nils (electrician, primary), Aleksei (night shift lead, if after hours).

1. UPS provides 5 minutes of power to networking and control systems only (not ASICs).
2. ASICs will hard-power-off. This is expected — no graceful shutdown possible.
3. When power returns, wait 5 minutes for voltage stabilization.
4. Boot ASICs in staggered batches (200 units per batch, 2-minute intervals) to avoid inrush current spike.
5. Monitor MOS for units that fail to boot — likely PSU or control board damage from power surge.
6. Log incident in safety register. If >10 units damaged, file insurance claim.
