# 3.06 permafrost_thaw — core algorithm
# model: physics-informed regression
# method: seasonal amplitude + secular trend regression; abnormal thaw needs >= 2 seasons, otherwise amplitude is reported with probability capped; abrupt thaw (thermokarst) = localized rapid subsidence + sigma0 change cluster
