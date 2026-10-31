# 3.08 deforestation — core algorithm
# model: statistics (sequential change)
# method: per-pixel CUSUM on HV dB against robust baseline; persistence rule (>= 2-3 consecutive epochs below baseline by >= k dB); patch filter >= 0.5 ha; cause attribution: fire (FIRMS in patch) | clearing (geometric patch, roads) | windthrow (post-storm cluster)
