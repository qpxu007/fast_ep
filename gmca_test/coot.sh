#module load ccp4
#coot --python -c 'make_and_draw_map("sad.mtz", "F", "PHI", "/HKL_base/HKL_base/FOM", 1, 0)' --no-guano
coot --python -c 'make_and_draw_map("sad.mtz", "F", "PHI", "/HKL_base/HKL_base/FOM", 1, 0);skeletonize_map(0,1)' --no-guano
