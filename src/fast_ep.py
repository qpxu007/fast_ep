#!/usr/bin/env python
#
# fast_ep ->
#
# Fast experimental phasing in the spirit of fast_dp, starting from nothing
# and using brute force (and educated guesses) to get everything going.
#
# fast_ep - main program.

import os
import sys
import time
import shutil

from iotbx import mtz
from cctbx.sgtbx import space_group, space_group_symbols

if not 'FAST_EP_ROOT' in os.environ:
    raise RuntimeError, 'FAST_EP_ROOT not set'

fast_ep_lib = os.path.join(os.environ['FAST_EP_ROOT'], 'lib')

if not fast_ep_lib in sys.path:
    sys.path.append(fast_ep_lib)

from generate_possible_spacegroups import generate_chiral_spacegroups_unique, \
     spacegroup_enantiomorph, spacegroup_full
from number_sites_estimate import number_sites_estimate, \
     number_residues_estimate
from guess_the_atom import guess_the_atom
from run_job import run_job, run_job_cluster, is_cluster_job_finished

from fast_ep_helpers import autosharp

def useful_number_sites(cell, pointgroup):
    nha = number_sites_estimate(cell, pointgroup)

    result = []

    for f in [0.25, 0.5, 1.0, 2.0, 4.0]:
        nha_test = int(round(f * nha))
        if nha_test and not nha_test in result:
            result.append(nha_test)

    return result

def modify_ins_text(ins_text, spacegroup, nsites):
    '''Update the text in a SHELXD .ins file to handle the correct number
    of sites and spacegroup symmetry operations.'''

    new_text = []

    symm = [op.as_xyz().upper() for op in 
            space_group(space_group_symbols(spacegroup).hall()).smx()]

    for record in ins_text:
        if 'SYMM' in record:
            if not symm:
                continue
            for op in symm:
                if op == 'X,Y,Z':
                    continue
                new_text.append(('SYMM %s' % op))
            symm = None
        elif 'FIND' in record:
            new_text.append(('FIND %d' % nsites))
        else:
            new_text.append(record.strip())

    return new_text

class logger:
    def __init__(self):
        self._fout = open('fast_ep.log', 'w')
        return
        
    def __del__(self):
        self._fout.close()
        self._cout = None
        return

    def __call__(self, line):
        print line
        self._fout.write('%s\n' % line)
        return
    
def fast_ep(hklin):
    '''Quickly (i.e. with the aid of lots of CPUs) try to experimentally
    phase the data in here.'''

    start_time = time.time()

    log = logger()

    working_directory = os.getcwd()

    m = mtz.object(hklin)
    
    pointgroup = m.space_group().type().number()

    for crystal in m.crystals():
        if crystal.name() != 'HKL_base':
            unit_cell = crystal.unit_cell().parameters()

    # first run mtz2sca on this input => sad.sca

    log('Unit cell: %.3f %.3f %.3f %.3f %.3f %.3f' % unit_cell)

    run_job('mtz2sca', [hklin, 'sad.sca'])

    # in here run shelxc to generate the ins file (which will need to be
    # modified) and the hkl files, which will need to be copied.

    spacegroup_0 = generate_chiral_spacegroups_unique(pointgroup)[0]
    nsites_0 = useful_number_sites(unit_cell, pointgroup)[0]

    run_job('shelxc', ['sad'],
            ['sad sad.sca',
             'cell %.3f %.3f %.3f %.3f %.3f %.3f' % unit_cell,
             'spag %s' % spacegroup_0,
             'find %d' % nsites_0,
             'mind -3.5',
             'ntry 200'])

    # then for all possible spacegroups and all possible numbers of
    # sites modify the ins file and run shelxd on the cluster.

    ins_text = open('sad_fa.ins', 'r').readlines()

    job_ids = []

    for spacegroup in generate_chiral_spacegroups_unique(pointgroup):
        for nsites in useful_number_sites(unit_cell, pointgroup):
            
            wd = os.path.join(working_directory, spacegroup, str(nsites))
            if not os.path.exists(wd):
                os.makedirs(wd)

            new_text = modify_ins_text(ins_text, spacegroup, nsites)

            shutil.copyfile(os.path.join(working_directory, 'sad_fa.hkl'),
                            os.path.join(wd, 'sad_fa.hkl'))

            open(os.path.join(wd, 'sad_fa.ins'), 'w').write(
                '\n'.join(new_text))

            job_id = run_job_cluster('shelxd_mp', ['-L4', 'sad_fa'], [], wd, 8)
            job_ids.append(job_id)

    for job_id in job_ids:
        while not is_cluster_job_finished(job_id):
            time.sleep(1)

    results = { }

    best_cfom = 0.0
    best_spacegroup = None
    best_nsites = None

    for spacegroup in generate_chiral_spacegroups_unique(pointgroup):
        for nsites in useful_number_sites(unit_cell, pointgroup):
            res = open(os.path.join(working_directory, spacegroup, str(nsites),
                                    'sad_fa.res'), 'r').readlines()

            cc = float(res[0].split()[5])
            cc_weak = float(res[0].split()[7])
            cfom = float(res[0].split()[9])

            # estimate real # sites - as drop below 30% relative occupancy

            nsites_real = 0

            for record in res:
                if not 'SE' in record[:2]:
                    continue
                if float(record.split()[5]) > 0.3:
                    nsites_real += 1

            results[(spacegroup, nsites)] = (cc, cc_weak, cfom, nsites_real)

            if cfom > best_cfom:
                best_cfom = cfom
                best_spacegroup = spacegroup
                best_nsites = nsites

    for spacegroup in generate_chiral_spacegroups_unique(pointgroup):
        for nsites in useful_number_sites(unit_cell, pointgroup):

            (cc, cc_weak, cfom, nsites_real) = results[(spacegroup, nsites)]

            if (spacegroup, nsites) == (best_spacegroup, best_nsites):
                log('%10s %3d %6.2f %6.2f %6.2f %3d ***' % \
                    (spacegroup, nsites, cc, cc_weak, cfom, nsites_real))
            else:
                log('%10s %3d %6.2f %6.2f %6.2f %3d' % \
                    (spacegroup, nsites, cc, cc_weak, cfom, nsites_real))

    atom, wavelength = guess_the_atom(hklin, best_nsites)
    nres = number_residues_estimate(unit_cell, pointgroup)
    user = os.environ['USER']

    log('Probable atom: %s' % atom)

    # copy back result files
    
    best = os.path.join(working_directory, best_spacegroup, str(best_nsites))

    for ending in 'lst', 'pdb', 'res':
        shutil.copyfile(os.path.join(best, 'sad_fa.%s' % ending),
                        os.path.join(working_directory, 'sad_fa.%s' % ending))

    # now see if we can phase with shelxe - to get the enantiomorph and
    # solvent fraction, run these on the cluster too... loop over 25 to 75%
    # in 5% steps

    job_ids = []
    
    for solvent_fraction in range(25, 76, 5):
        solvent = '%.2f' % (0.01 * solvent_fraction)
        wd = os.path.join(working_directory, solvent)
        if not os.path.exists(wd):
            os.makedirs(wd)
        shutil.copyfile(os.path.join(working_directory, 'sad.hkl'),
                        os.path.join(wd, 'sad.hkl'))
        for ending in 'lst', 'pdb', 'res', 'hkl':
            shutil.copyfile(
                os.path.join(working_directory, 'sad_fa.%s' % ending),
                os.path.join(wd, 'sad_fa.%s' % ending))
        job_id = run_job_cluster(
            'shelxe', ['sad', 'sad_fa', '-h', '-s%s' % solvent,
                       '-m20'], [], wd)
        job_ids.append(job_id)
        job_id = run_job_cluster(
            'shelxe', ['sad', 'sad_fa', '-h', '-s%s' % solvent,
                       '-m20', '-i'], [], wd)
        job_ids.append(job_id)

    for job_id in job_ids:
        while not is_cluster_job_finished(job_id):
            time.sleep(1)

    results = { }

    best_fom = 0.0
    best_solvent = None
    best_enantiomorph = None

    for solvent_fraction in range(25, 76, 5):
        solvent = '%.2f' % (0.01 * solvent_fraction)
        wd = os.path.join(working_directory, solvent)
        for record in open(os.path.join(wd, 'sad.lst')):
            if 'Estimated mean FOM =' in record:
                fom_orig = float(record.split()[4])
        for record in open(os.path.join(wd, 'sad_i.lst')):
            if 'Estimated mean FOM =' in record:
                fom_oh = float(record.split()[4])
        results[solvent] = (fom_orig, fom_oh)
        if fom_orig > best_fom:
            best_fom = fom_orig
            best_sovent = solvent
            best_enantiomorph = 'original'
        if fom_oh > best_fom:
            best_fom = fom_oh
            best_solvent = solvent
            best_enantiomorph = 'inverted'

    for solvent_fraction in range(25, 76, 5):
        solvent = '%.2f' % (0.01 * solvent_fraction)
        fom_orig, fom_oh = results[solvent]
        if solvent == best_solvent:
            log('%.2f %.3f %.3f ***' % \
                (0.01 * solvent_fraction, fom_orig, fom_oh))
        else:
            log('%.2f %.3f %.3f' % \
                (0.01 * solvent_fraction, fom_orig, fom_oh))

    # copy best results back, convert to an mtz file

    best = os.path.join(working_directory, best_solvent)

    if best_enantiomorph == 'original':
        for ending in 'phs', 'pha', 'lst', 'hat':
            shutil.copyfile(os.path.join(best, 'sad.%s' % ending),
                            os.path.join(working_directory, 'sad.%s' % ending))
    else:
        for ending in 'phs', 'pha', 'lst', 'hat':
            shutil.copyfile(os.path.join(best, 'sad_i.%s' % ending),
                            os.path.join(working_directory, 'sad.%s' % ending))
        best_spacegroup = spacegroup_enantiomorph(best_spacegroup)

    log('Best spacegroup: %s' % best_spacegroup)

    run_job('convert2mtz', ['-hklin', 'sad.phs', '-mtzout', 'sad.mtz',
                            '-colin', 'F FOM PHI SIGF',
                            '-cell', '%f %f %f %f %f %f' % unit_cell,
                            '-spacegroup', spacegroup_full(best_spacegroup)],
            [], working_directory)
            
    # below follows hacky code to write an autoSHARP script...

    fout = open('sharp.sh', 'w')

    fout.write('module load sharp/beta-2011-07-20\n')
    fout.write('reindex hklin %s hklout reindex.mtz << eof\n' % hklin)
    fout.write('symm %s\n' % best_spacegroup)
    fout.write('eof\n')
    fout.write('truncate hklin reindex.mtz hklout fast_ep.mtz << eof\n')
    fout.write('nres %d\n' % nres)
    fout.write('eof\n')
    fout.write('cat > .autoSHARP << eof\n')
    fout.write(
        autosharp(nres, user, wavelength, atom, best_nsites, 'fast_ep.mtz'))
    fout.write('\neof\n')
    fout.write('$BDG_home/bin/sharp/detect.sh | tee detect.html 2>&1\n')
    fout.close()

    log('Time: %.2fs' % (time.time() - start_time))
                
if __name__ == '__main__':
    fast_ep(sys.argv[1])
    