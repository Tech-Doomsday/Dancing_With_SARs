# 3.02 land_subsidence — core algorithm
# model: physics (SBAS WLS) + statistics
# method: Tikhonov-regularized WLS SBAS -> velocity + cumulative displacement; PELT change points; seasonal harmonic fit only if history >= 1 year, else linear; bowl test: connected component of |v| > k*sigma_v, min area, smooth gradient; veto on elevation-correlated pattern and long-wavelength ramp
