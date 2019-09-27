#!/bin/sh
#$ -cwd
#$ -j y
#$ -V
#$ -S /bin/bash
#$ -o fast_ep.sh.out
##$ -e fast_ep.sh.err


echo job started at `date "+%Y-%m-%d %H:%M:%S"`
echo "host: `hostname -s` (`uname`) user: `whoami`"
echo
echo

cd .
export PX=/mnt/software/px
source /mnt/software/px/CCP4/ccp4/bin/ccp4.setup-sh
export DRMAA_LIBRARY_PATH=/usr/lib64/libdrmaa.so
export FAST_EP_ROOT=/mnt/beegfs/qxu/fast_ep
$FAST_EP_ROOT/bin/fast_ep data=135699_1_w0.9788-aimless-TRIMDATA.mtz  mode=basic  machines=8 cpu=4

echo
echo
echo job finished at `date "+%Y-%m-%d %H:%M:%S"`
