# 3.15 oil_spill — core algorithm
# model: statistics + learned slot
# method: adaptive local-threshold dark-spot detection vs surrounding sea; features: area, perimeter, shape complexity, contrast, boundary gradient, wind, distance to platform / ship; oil vs look-alike classifier (gradient boosting, weights loaded from file if present, otherwise rule-based); probability capped for L-band thin-film insensitivity
