#!/bin/bash
WORKING_DIR_1=/mnt/beegfs/qxu/fast_ep/gmca_test/P321/3/2.14
WORKING_DIR_2=/mnt/beegfs/qxu/fast_ep/gmca_test/P321/5/2.14
WORKING_DIR_3=/mnt/beegfs/qxu/fast_ep/gmca_test/P321/10/2.14
WORKING_DIR_4=/mnt/beegfs/qxu/fast_ep/gmca_test/P321/20/2.14
WORKING_DIR_5=/mnt/beegfs/qxu/fast_ep/gmca_test/P321/40/2.14
WORKING_DIR_6=/mnt/beegfs/qxu/fast_ep/gmca_test/P3121/3/2.14
WORKING_DIR_7=/mnt/beegfs/qxu/fast_ep/gmca_test/P3121/5/2.14
WORKING_DIR_8=/mnt/beegfs/qxu/fast_ep/gmca_test/P3121/10/2.14
WORKING_DIR_9=/mnt/beegfs/qxu/fast_ep/gmca_test/P3121/20/2.14
WORKING_DIR_10=/mnt/beegfs/qxu/fast_ep/gmca_test/P3121/40/2.14
TASK_WORKING_DIR=WORKING_DIR_${SLURM_ARRAY_TASK_ID}
cd ${!TASK_WORKING_DIR}
shelxd -L10 sad_fa -t4 > ${!TASK_WORKING_DIR}/FEP_shelxd.out  2> ${!TASK_WORKING_DIR}/FEP_shelxd.err
