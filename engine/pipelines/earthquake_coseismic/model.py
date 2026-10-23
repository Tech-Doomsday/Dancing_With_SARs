# 3.01 earthquake_coseismic — core algorithm
# model: physics (Okada85) + optimizer
# method: Mw -> M0 (Hanks-Kanamori), area (Wells-Coppersmith, 2:1), uniform slip; DC3D on strided subgrid (<=400k points); BOTH nodal planes evaluated, better fit kept
# method: consistency screen: amplitude ratio within x3 and Pearson r > 0.25
# method: UNCATALOGUED branch: if a coherent high-gradient LOS cluster exists with no catalog event, fit one rectangular source on a quadtree-subsampled field (bounded global optimizer)
