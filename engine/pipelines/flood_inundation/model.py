# 3.07 flood_inundation — core algorithm
# model: statistics (+ optional learned slot)
# method: open-water class (co-pol drop) + flooded-vegetation class (co-pol increase, double bounce); region growing from high-confidence seeds, HAND <= 15 m constraint, area filter; confidence = agreement of independent cues (backscatter, HAND, rain/discharge, JRC not permanent)
# method: learned slot: gradient boosting on features, weak labels from DFO / EMS masks
