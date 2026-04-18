Here's a consolidated reference of real-world parameters you can feed into your synthetic data generator, organized by the categories that matter for your model.

Hardware Specifications (current fleet models)
Your synthetic fleet already uses S21-HYD, M66S, S19XP, and S19jPro. Here are updated real-world specs to calibrate against:
ModelHashratePowerEfficiency (J/TH)GenerationAntminer S21 XP270 TH/s3,645W13.5Current flagshipAntminer S21 Pro234 TH/s3,510W15.0CurrentAntminer S21200 TH/s3,500W17.5CurrentWhatsminer M60S186 TH/s3,441W18.5CurrentAntminer S19 XP140 TH/s3,010W21.5Previous genAntminer S19k Pro120 TH/s2,760W23.0Previous genAntminer S19 Pro110 TH/s3,245W29.5Previous genAntminer S1995 TH/s3,250W34.2Previous gen
The S21 XP sits at 270 TH/s with 13.5 J/TH efficiency, making it the current best-in-class air-cooled unit Mineshop. The standard S21 delivers 200 TH/s at 17.5 J/TH D-Central Technologies. Your current synthetic fleet specs are close but could be tightened — the S21-HYD at 335 TH/s / 15 J/TH in your generator is plausible for hydro-cooled overclocked variants, and the S19XP at 141 TH/s / 21.3 J/TH is right on the money.

Component Lifespans & Failure Modes
This is where the real gold is for your predictive model training data:
ASIC chips themselves rarely fail outright — the supporting components (fans, capacitors, solder joints, PSUs) degrade first. A well-maintained miner can operate productively for 4 to 7 years. D-Central Technologies
Fans: Most mining fans require replacement every 12-18 months under continuous 24/7 operation — degrading bearings produce audible warning signs before complete failure. Miners1688 Predictive signals: RPM decline, audible noise change, temperature rise at constant ambient.
PSU: ASIC power supplies are one of the most failure-prone components. Loose connections or oxidation can cause intermittent power drops, which are hard to diagnose and damaging over time. Endless Mining Predictive signals: voltage drift (capacitor degradation), intermittent hash drops, voltage ripple.
Hashboards: Hash boards contain the ASIC chips that perform mining calculations and typically last 5-7 years with proper thermal management. Miners1688 Predictive signals: chip count dropping, individual chain errors, hashrate below rated spec at same frequency.
Capacitor degradation: Electrolytic capacitors on hashboards and PSUs lose capacitance faster at higher temperatures, following the Arrhenius equation — every 10°C above rated temperature roughly halves capacitor lifespan. D-Central Technologies This is directly modelable: your existing thermal model can drive a degradation accumulator using Arrhenius.
Thermal degradation cascade: The temperature difference between 65°C and 85°C may not seem dramatic, but it can mean the difference between a miner lasting 4 years or 14 months. EZ Smartbox And: hardware error rates above 1% and frequent unexpected reboots D-Central Technologies are key indicators of advanced thermal damage.
For your scenario files, here are realistic failure progression patterns:
Failure ModeEarly Signal (weeks before)Mid Signal (days before)Late Signal (hours before)Fan bearing wearRPM variance ±5%, slight noiseRPM drops 10-15%, temp rises 3-5°CRPM collapse, temp spike >10°CPSU capacitor agingVoltage ripple +10mVIntermittent hash drops 2-5%, voltage driftHash board detection failures, reboot loopsThermal paste degradationChip-ambient delta grows 2-3°CDelta grows 5-8°C at same loadThermal throttling, hashrate cliffSolder joint fatigue (thermal cycling)Intermittent single-chip errorsChain-level dropouts, error rate >1%Full hashboard offlineDust/fouling accumulationCooling power rises 5-10%, inlet pressure dropTemperature baseline drift upwardThermal limit reached at normal load

Economic Parameters (current as of April 2026)
Bitcoin is currently trading at approximately $66,650 Fortune. Bitcoin mining difficulty is at 133.79 T, with an adjustment to approximately 138.41 T expected imminently. CoinWarz Block reward is 3.125 BTC (post April 2024 halving, next halving around 2028).
Electricity costs — the critical variable:
Electricity typically represents 50-70% of mining operational costs, making energy prices the primary determinant of profitability. Sazmining
TierRate ($/kWh)WhereCheapest industrial$0.03-0.04Paraguay, behind-the-meter Texas, stranded gasCompetitive industrial$0.04-0.06Iceland, Quebec, North Dakota, KazakhstanAverage commercial US$0.136National average (EIA Dec 2025)Unprofitable>$0.08-0.10Most of Europe, residential US
With power prices of $0.04–$0.06/kWh, the effective cost to mine 1 BTC for optimized operations is between $34,176 and $51,264. CompareForexBrokers At today's ~$67K price, that's a 30-50% margin for well-run operations and razor-thin or negative for anyone above $0.08/kWh.
For your cost_model.json, realistic parameters:

Pool fee: 1-2% (standard)
Maintenance cost per inspection: $150 (your current value is right)
Repair costs: ASIC miner repair costs typically range from $30 to $500, depending on the issue — simple fixes like fan or PSU issues cost less, while hashboard or chip-level repairs cost more. Asic Marketplace
An ASIC miner operating at 100 TH/s typically generates about $10 per day in revenue under normal conditions Sazmining — you can use this as a sanity check against your revenue model.
Downtime cost: roughly revenue-per-hour × hours offline. Predictive maintenance can reduce downtime by up to 30% Sazmining, which gives you a benchmark for your model's value proposition.


Environmental Parameters
For realistic ambient temperature modeling beyond your current single-site 64.5°N model:
Site ArchetypeAmbient RangeCooling NotesNorthern (Iceland, Scandinavia, Canada)-20°C to +20°CFree air cooling most of year, minimal HVACTemperate (Texas, Georgia)0°C to +40°CHeavy HVAC in summer, some free cooling in winterTropical/desert+20°C to +50°CYear-round HVAC, high cooling cost
Maintain consistent ambient temperatures between 5°C-35°C for optimal hash board longevity. Sustained operation above 75°C accelerates chip degradation. Miners1688 Your target chip temperature range for normal ops is 60-75°C; above 75°C degradation accelerates, above 85°C it becomes exponential.

Predictive Signal Summary for Training Data
The most actionable signals to encode in your scenarios for the predictive model:

Temperature trajectory — not the absolute value, but the rate of change relative to ambient and load. A 3°C rise over a week at constant ambient is a stronger signal than absolute temperature.
Hashrate-to-power ratio drift — your TE metric captures this well. The real-world pattern is that efficiency degrades before hashrate drops, because the device draws more power to maintain the same hash output as components wear.
Voltage ripple / instability — PSU degradation shows as increasing variance in voltage readings before the mean shifts. Your synthetic data can model this as noise amplitude growing over time.
Fan RPM correlation breakdown — normally fan speed and temperature are tightly correlated. When that correlation weakens (temperature rises but fan RPM doesn't proportionally increase, or fan RPM is high but temperature isn't dropping), it signals mechanical cooling degradation.
Error rate accumulation — chip errors, hashboard reboot counts, chain detection failures. These are count-based signals that tend to follow an exponential curve once degradation begins.
Energy price × efficiency crossover — the economic signal. A device becomes unprofitable when its cost-per-TH exceeds the network's revenue-per-TH (hashprice). This crossover point moves with BTC price and difficulty, making it a time-varying threshold your model should learn.

Your current generator has three anomaly types (thermal degradation, PSU instability, hashrate decay). Based on the research, I'd prioritize adding these for the training corpus: fan bearing wear (12-18 month onset, gradual then cliff), capacitor aging driven by Arrhenius thermal accumulation, dust/fouling as a slow baseline drift in cooling efficiency, and the solder fatigue pattern which is uniquely hard to predict because it's intermittent before it becomes permanent.can you list the sources with links?12:03 AMIdentified request for sourced research linksIdentified request for sourced research linksHere are the sources I pulled from:
Hardware & Failure Modes

D-Central — "What Factors Contribute to Deterioration of ASIC Miners?" — https://d-central.tech/what-factors-contribute-to-deterioration-of-asic-miners/
D-Central — "Understanding the Impact of Temperature on ASIC Miners Performance" — https://d-central.tech/understanding-the-impact-of-temperature-on-asic-miners-performance/
D-Central — "The Lifespan of an ASIC Miner" — https://d-central.tech/the-lifespan-of-an-asic-miner-how-long-do-asic-miners-last/
D-Central — "ASIC Hashboard Repair Deep Dive" — https://d-central.tech/asic-hashboard-repair-deep-dive-chip-level-diagnostics-failure-analysis-rework-techniques/
EZ Blockchain — "ASIC Miner Lifespan: How Long Can You Mine?" — https://ezblockchain.net/article/asic-lifespan-how-long-will-it-last/
Miners1688 — "Bitcoin Mining Hardware Maintenance & SLA" — https://www.miners1688.com/bitcoin-mining-hardware-maintenance-sla/
Endless Mining — "Extending the Lifespan of Your ASIC Miners" — https://endlessmining.com/extending-the-lifespan-of-your-asic-miners-proven-maintenance-practices/
ASIC Marketplace — "ASIC Miner Repair Services" — https://asicmarketplace.com/global-asic-miner-repair-service/
Bitmern Mining — "Mining Operations Failure" — https://bitmernmining.com/mining-operations-failure/

Predictive Maintenance

Sazmining — "Predictive Maintenance for Bitcoin Mining" — https://www.sazmining.com/blog/predictive-maintenance-bitcoin-mining
Sazmining — "Solving Downtime From Mining Hardware Failures" — https://www.sazmining.com/blog/solving-downtime-mining-hardware-failures
Volta Insite — "Cryptocurrency Mining Predictive Maintenance" — https://voltainsite.com/cryptocurrency-mining.html

ASIC Specs (2026 models)

D-Central — "Antminer S21 XP Specs" — https://d-central.tech/antminer-s21-xp-specs/
D-Central — "Antminer S21 Specs" — https://d-central.tech/antminer-s21-specs/
D-Central — "Antminer S21 Pro Review" — https://d-central.tech/antminer-s21-pro-review/
D-Central — "Antminer S19 Specifications" — https://d-central.tech/antminer-s19-specifications/
Mineshop — "Antminer S21 XP Review 2026" — https://mineshop.eu/blog/asic-miner/bitmain-antminer-s21-xp-review-2026
Hashrate Index — "Antminer S19k Pro and S19 Profitability" — https://hashrateindex.com/blog/antminers19-profitability-price/
Mining Now — "Bitmain Antminer S21 (200TH/s)" — https://miningnow.com/asic-miner/bitmain-antminer-s21-200th-s/

Economics & Profitability

BestBrokers — "Mining Madness: The Staggering Energy Cost of a Single Bitcoin in 2026" — https://www.bestbrokers.com/2025/12/19/bitcoin-energy-cost/
CompareForexBrokers — "The Real Cost of Bitcoin Mining in 2026" — https://www.compareforexbrokers.com/us/bitcoin-mining/
Sazmining — "The Link Between Electricity Cost & BTC Mining Profits" — https://www.sazmining.com/blog/electricity-cost-btc-mining-profitability
Bitbo — "Is Bitcoin Mining Profitable in 2026?" — https://bitbo.io/tools/mining-profitable/
CoinWarz — "Bitcoin Mining Calculator" — https://www.coinwarz.com/mining/bitcoin/calculator

Network & Price Data (current)

CoinWarz — "Bitcoin Difficulty Chart" — https://www.coinwarz.com/mining/bitcoin/difficulty-chart
Fortune — "Current Price of Bitcoin, April 3, 2026" — https://fortune.com/article/price-of-bitcoin-04-03-2026/
Hashrate Index — "Network Data" — https://data.hashrateindex.com/network-data/network