#!/usr/bin/env python
#
# fast_ep_shelxd ->
#
# code to run shelxd and manage the jobs - this will be called from within
# a multiprocess task so life is easier if the input is provided in the form
# of a dictionary with the name of the "problem" we're working on and the
# number of reflections to make room for.

from itertools import product, combinations
import logging
import os
import time
from pprint import pformat
import shutil

import numpy as np
import scipy.stats

from cctbx import euclidean_model_matching as emma
from cctbx.sgtbx import space_group_symbols
import iotbx.pdb
from iotbx.shelx import hklf, crystal_symmetry_from_ins,\
    cctbx_xray_structure_from
from iotbx.shelx.writer import generator

from lib.run_job import run_job, run_job_cluster, is_cluster_job_finished, setup_job_drmaa
from src.fast_ep_helpers import modify_ins_text


def setup_shelxd_job(root_wd, job_key, ins_text):

    spacegroup, nsite, rlimit = job_key
    wd = os.path.join(root_wd, spacegroup.replace(':', '-'), str(nsite), "%.2f" % rlimit)
    if not os.path.exists(wd):
        os.makedirs(wd)

    new_text = modify_ins_text(ins_text, spacegroup, nsite, rlimit)

    shutil.copyfile(os.path.join(root_wd, 'sad_fa.hkl'),
                    os.path.join(wd, 'sad_fa.hkl'))

    open(os.path.join(wd, 'sad_fa.ins'), 'w').write(
        '\n'.join(new_text))
    return wd

def run_shelxd_cluster(_settings):
    '''Run shelxd on cluster with settings given in dictionary, containing:

    nrefl = 1 + floor(nref / 100000) - space to allocate
    ncpu - number of cpus to use
    wd - working directory'''

    nrefl = _settings['nrefl']
    ncpu = _settings['ncpu']
    wd = _settings['wd']

    job_id = run_job_cluster(
        'shelxd', ['-L%d' % nrefl, 'sad_fa', '-t%d' % ncpu],
        [], wd, ncpu, timeout = 600)

    while not is_cluster_job_finished(job_id):
        time.sleep(1)

    return


def run_shelxd_drmaa(njobs, job_settings):
    '''Run shelxd on cluster with settings given in dictionary, containing:

    nrefl = 1 + floor(nref / 100000) - space to allocate
    ncpu - number of cpus to use
    wd - working directory'''

    import drmaa
    with drmaa.Session() as session:

        job = session.createJobTemplate()

        batches = range(0, len(job_settings), njobs)
        for idx in batches:
            jobs = []
            for _settings in job_settings[idx:idx+njobs]:

                nrefl = _settings['nrefl']
                ncpu = _settings['ncpu']
                wd = _settings['wd']

                setup_job_drmaa(job,
                                'shelxd', ['-L%d' % nrefl, 'sad_fa', '-t%d' % ncpu],
                                [], wd, ncpu, timeout = 600)
                jobs.append(session.runJob(job))
            session.synchronize(jobs, drmaa.Session.TIMEOUT_WAIT_FOREVER, True)
        session.deleteJobTemplate(job)
    return


def run_shelxd_drmaa_array(wd, nrefl, ncpu, njobs, job_settings, timeout, sge_project):
    '''Run shelxd on cluster with settings given in dictionary, containing:

    nrefl = 1 + floor(nref / 100000) - space to allocate
    ncpu - number of cpus to use
    wd - working directory'''

    script_path = os.path.join(wd, 'shelxd_batch.sh')
    with open(script_path, 'w') as script:

        script.write('#!/bin/bash\n')

        for idx, _settings in enumerate(job_settings, start=1):
            script.write('WORKING_DIR_{idx}={wd}\n'.format(idx=idx, wd= _settings['wd']))

        script.write('TASK_WORKING_DIR=WORKING_DIR_${SLURM_ARRAY_TASK_ID}\n')
        script.write('cd ${!TASK_WORKING_DIR}\n')
        script.write('shelxd -L{nrefl} sad_fa -t{ncpu} > ${{!TASK_WORKING_DIR}}/FEP_shelxd.out  2> ${{!TASK_WORKING_DIR}}/FEP_shelxd.err\n'.format(nrefl=nrefl,
                                                                 ncpu=ncpu))



    import drmaa
    with drmaa.Session() as session:
        job = session.createJobTemplate()
        job.jobName = 'FEP_shelxd'
        job.workingDirectory = wd
        job.remoteCommand = 'sh'
        args = [script_path,]
        job.args = args
        #job.jobCategory = 'medium'
        if sge_project:
            proj = '--wckey={}'.format(sge_project)
        else:
            proj = ''

        job.nativeSpecification = '{proj} --cpus-per-task={ncpu} -o /dev/null'.format(proj=proj, ncpu=ncpu)

        job_ids = session.runBulkJobs(job, 1, len(job_settings), 1)
        session.synchronize(job_ids, drmaa.Session.TIMEOUT_WAIT_FOREVER, True)
        session.deleteJobTemplate(job)


def run_shelxd_local(_settings):
    '''Run shelxd locally settings given in dictionary, containing:

    nrefl = 1 + floor(nref / 100000) - space to allocate
    ncpu - number of cpus to use
    wd - working directory'''

    nrefl = _settings['nrefl']
    ncpu = _settings['ncpu']
    wd = _settings['wd']

    job_output = run_job(
        'shelxd', ['-L%d' % nrefl, 'sad_fa', '-t%d' % ncpu], [], wd)

    open(os.path.join(wd, 'shelxd.log'), 'w').write(''.join(job_output))

    return


def analyse_res(wd):

    _res = open(os.path.join(wd, 'sad_fa.res')).readlines()

    try:
        cc = float(_res[0].split()[5])
        if cc > 100.:
            raise ValueError
    except (ValueError, IndexError):
        cc = float('nan')

    try:
        cc_weak = float(_res[0].split()[7])
        if cc_weak > 100.:
            raise ValueError
    except (ValueError, IndexError):
        cc_weak = float('nan')

    try:
        cfom = float(_res[0].split()[9])
        if cfom > 200.:
            raise ValueError
    except (ValueError, IndexError):
        cfom = float('nan')

    # estimate real # sites - as drop below 30% relative occupancy

    nsites_real = 0

    try:
        for record in _res:
            if not 'SE' in record[:2]:
                continue
            if float(record.split()[5]) > 0.3:
                nsites_real += 1
    except (ValueError, IndexError):
        nsites_real = int('nan')

    return {'CCall' : cc,
            'CCweak': cc_weak,
            'CFOM'  : cfom,
            'nsites': nsites_real}


def get_advanced_stats(wd):

    norm_cc = shelxd_cc_all(os.path.join(wd, 'sad_fa.pdb'),
                            os.path.join(wd, 'sad_fa.hkl'),
                            os.path.join(wd, 'sad_fa.ins'),
                            3.0, 20)
    return {'CCres' : norm_cc}


def happy_shelxd_log(_shelxd_lst_file):
    for record in open(_shelxd_lst_file):
        if '** NO SUITABLE PATTERSON VECTORS FOUND **' in record:
            return False
        if '** CANNOT ALLOCATE ENOUGH MEMORY **' in record:
            return False

    best_cfom = 0.0

    # columns can get merged in output, so watch for that

    for record in open(_shelxd_lst_file):
        if '*****' in record:
            continue
        if record.startswith(' Try'):
            best_cfom_token = record.replace(',', ' ').split()[-3]
            best_cfom = float(best_cfom_token.replace('best', ''))

    if best_cfom == 0.0:
        return False

    return True


def read_shelxd_substructure(_shelxd_res_file):
    '''Read SHELXD substructure model ignoring atoms at special positions
    and with low occupancy'''
    res_model = cctbx_xray_structure_from(None, filename=_shelxd_res_file)
    spos_idx = [idx for idx,_ in enumerate(res_model.scatterers())
                    if idx not in res_model.special_position_indices()]
    spos_sel = res_model.by_index_selection(spos_idx)
    nonsp_model = res_model.select(spos_sel)
    occ = nonsp_model.scatterers().extract_occupancies()
    return nonsp_model.select(occ>0.3)


def get_shelxd_results(pth, spacegroups, nsites, ano_rlimits, advanced=False):
    '''Parse SHELXD logs and read substructure models'''
    results = {}
    models = {}
    for spacegroup in spacegroups:
        for nsite in nsites:
            for rlimit in ano_rlimits:
                wd = os.path.join(pth, spacegroup.replace(':', '-'), str(nsite), "%.2f" % rlimit)
                shelxd_log = os.path.join(wd, 'sad_fa.lst')
                shelxd_res = os.path.join(wd, 'sad_fa.res')
                if happy_shelxd_log(shelxd_log):
                    results[(spacegroup, nsite, rlimit)] = analyse_res(wd)
                    if advanced:
                        results[(spacegroup, nsite, rlimit)].update(get_advanced_stats(wd))
                        models[(spacegroup, nsite, rlimit)] = read_shelxd_substructure(shelxd_res)
                else:
                    results[(spacegroup, nsite, rlimit)] = {'CCall' : -np.inf,
                                                            'CCweak': -np.inf,
                                                            'CFOM'  : -np.inf,
                                                            'nsites': 0}
                    if advanced:
                        results[(spacegroup, nsite, rlimit)].update({'CCres': -np.inf})
                        models[(spacegroup, nsite, rlimit)] = None
    return results, models


def get_substruct_matches(substruct_dict, spacegroups, nsites, ano_rlimits):
    '''Run EMMA on all pairs of substructure models found for different HA number/resolution
    combinations for the given space group'''
    ha_dict = {}
    for spgr in spacegroups:
        sbm = [substruct_dict[(spgr, nsite, rlimit)] for (nsite, rlimit) in product(nsites, ano_rlimits)]
        ha_dict[spgr] = [[0] * len(mod.scatterers()) if mod else [] for mod in sbm]
        for idx1, idx2 in combinations(range(len(sbm)), 2):
            em1, em2 = sbm[idx1], sbm[idx2]
            try:
                emma_matches = emma.model_matches(em1.as_emma_model(),
                                              em2.as_emma_model(),
                                              tolerance=0.5,
                                              break_if_match_with_no_singles=False)
                best_match = next(iter(emma_matches.refined_matches))
                for ha_em1, ha_em2 in best_match.pairs:
                    ha_dict[spgr][idx1][ha_em1] += 1
                    ha_dict[spgr][idx2][ha_em2] += 1
            except AttributeError:
                continue
            except StopIteration:
                continue
    return ha_dict


def analyse_substructure(ha_list, thres):
    '''Select SHELXD substructure with the highest number of scatterer matches'''
    matched_list = [sorted(mod, reverse=True) for mod in ha_list if mod]
    idx_list = [len([nd for nd in mod if nd > thres]) for mod in ha_list]
    rank_dict = [(i, n_match) for i, (n_match, n_idx) in enumerate(zip(matched_list, idx_list)) if n_idx > 0]
    idx_best_score, max_found_ha = max(rank_dict, key=lambda x:x[1])
    return idx_best_score, max_found_ha, matched_list


def select_substructure(substruct_dict, ha_dict, nsites, ano_rlimits):
    '''Select substructure model most consistently found in SHELXD results'''
    nsites_resol_list = list(product(nsites, ano_rlimits))
    n_models = len(nsites)
    thres = max(3, 2 * n_models / 3 - 1)
    solutions = {}
    for spgr, ha_list in ha_dict.iteritems():
        try:
            idx_best_score, max_found_ha, matched_list = analyse_substructure(ha_list, thres)
            found_ha = [i for i,v in enumerate(ha_list[idx_best_score]) if not v < thres]
            best_nsites, best_rlim = nsites_resol_list[idx_best_score]
            ha_selection = substruct_dict[(spgr, best_nsites, best_rlim)].by_index_selection(found_ha)
            found_model = substruct_dict[(spgr, best_nsites, best_rlim)].select(ha_selection)
            solutions[spgr] = {'nsites': best_nsites,
                               'rlim': best_rlim,
                               'found_nsites': len(found_model.scatterers()),
                               'substructure':found_model,
                               'max_found_ha': max_found_ha,
                               'matched_list': matched_list}
            logging.debug(pformat({spgr: solutions[spgr]}))
        except ValueError:
            continue
    best_sg = max(solutions, key=lambda k: (solutions[k]['max_found_ha'],
                                            solutions[k]['found_nsites']))
    best_nsites = solutions[best_sg]['nsites']
    best_rlim = solutions[best_sg]['rlim']
    best_substructure = solutions[best_sg]['substructure']

    print_substructure_results(solutions, nsites_resol_list)

    return (best_sg, solutions[best_sg]['found_nsites'], best_rlim), best_substructure


def print_substructure_results(solutions, nsites_resol_list):
    logging.info('Substructure EMMA matching summary-----------------------')
    logging.debug(pformat(dict([(spgr, zip(nsites_resol_list, sol['matched_list'])) for spgr, sol in solutions.items()])))
    logging.info('{:>8} {:>8} {:>4}   {:<12}'.format('Spgr', 'Res.', 'No.', 'HA matches'))
    for spgr, vals in solutions.items():
            logging.info('{:>8} {:>8.2f} {:>4}   {:<12}'.format(spgr,
                                                                vals['rlim'],
                                                                vals['nsites'],
                                                                vals['max_found_ha']))


def write_shelxd_substructure(wd, substruct):
    '''Write substructure model from EMMA matching results'''
    pth = os.path.join(wd, 'sad_fa.res')
    with open(pth,'w') as fp:
        for line in generator(substruct, False, 'sad_fa.ins SAD',
                              full_matrix_least_squares_cycles=0):
            fp.write(line)


def get_shelxd_result_ranks(results, spacegroups, nsites, ano_rlimits):
    result_ranks = {}
    cols = next(results.itervalues()).keys()
    for nsite in nsites:
        for rlimit in ano_rlimits:
            for col in cols:
                vals = sorted([(sg, results[(sg, nsite, rlimit)][col]) for sg in spacegroups],
                              key=lambda v: v[1], reverse=True)
                for rk, (sg, _) in enumerate(vals, 1):
                    try:
                        result_ranks[(sg, nsite, rlimit)][col] = rk
                    except:
                        result_ranks[(sg, nsite, rlimit)] = {col: rk}

    return result_ranks


def get_average_ranks(spacegroups, nsites, ano_rlims, results, result_ranks):

    av_ranks = dict([(sg, {}) for sg in spacegroups])
    for sg, rk in av_ranks.iteritems():
        for col in ['CCall', 'CCweak', 'CFOM', 'CCres']:
            sel_results = [(results[(sg, n, r)][col],
                            result_ranks[(sg, n, r)][col]) for n, r in product(nsites, ano_rlims)]
            param_results = [sl[0] for sl in sel_results]
            rank_results = [sl[1] for sl in sel_results]
            try:
                rk[col] = np.average(rank_results, weights=param_results)
            except:
                rk[col] = np.average(rank_results)
    return av_ranks


def read_shelxd_log(_shelxd_lst_file):
    '''Compute statistics values for CCall/weak and CFOM data'''
    cc = []
    cc_weak = []
    cfom = []
    for record in open(_shelxd_lst_file):
        if record.startswith(' Try'):
            try:
                fields = dict(zip(['try', 'cpu', 'cc', 'cfom', 'best', 'patfom'], record.split(',')))
                cc.append(float(fields['cc'].split()[-3]))
                cc_weak.append(float(fields['cc'].split()[-1]))
                cfom.append(float(fields['cfom'].split()[-1]))
            except:
                continue
    return cc, cc_weak, cfom


def stats_shelxd_log(_shelxd_lst_file):
    '''Compute statistics values for CCall/weak and CFOM data'''
    cc, cc_weak, cfom = read_shelxd_log(_shelxd_lst_file)
    cc_set, cc_weak_set, cfom_set = map(set, [cc, cc_weak, cfom])

    res = []
    for vals, st in [(cc, cc_set),
                     (cc_weak, cc_weak_set),
                     (cfom, cfom_set)]:
        # Check if there are too many duplicate values
        if len(st) < 10:
            res.append((np.NaN, np.NaN))
            continue

        _, pval = scipy.stats.shapiro(vals)
        percel = np.percentile(vals, 75)
        quant = filter(lambda x: x < percel, vals)
        try:
            mx = np.max(vals)
            md = np.mean(quant)
            sig = np.std(quant)

            # Avoid SegFault when sigma==0
            if len(quant) > 2 and sig > 0:
                smax = np.divide(mx - md, sig)
                res.append((smax, pval))
            else:
                res.append((np.NaN, np.NaN))
        except:
            res.append((np.NaN, np.NaN))

    return res


def shelxd_substructure_ecalc(pdb_sub, ea, fa_ins, d_min, n_bins):
    '''Calculate E-values for heavy atom substructure'''

    pdb_obj =  iotbx.pdb.hierarchy.input(file_name=pdb_sub)
    c = crystal_symmetry_from_ins.extract_from(file_name=fa_ins)
    structure = pdb_obj.xray_structure_simple(crystal_symmetry=c)
    fcalc = ea.structure_factors_from_scatterers(xray_structure=structure, algorithm="direct").f_calc().amplitudes()
    fcalc.setup_binner(n_bins=n_bins)

    ecalc = fcalc.quasi_normalize_structure_factors()
    ecalc.setup_binner(n_bins=n_bins)
    return ecalc


def shelxd_read_hklf(fa_file, fa_ins, d_min, n_bins):
    '''Normalise Fa values output by SHELX'''

    fa_data = hklf.reader(file_name=fa_file)
    c = crystal_symmetry_from_ins.extract_from(file_name=fa_ins)

    fsigf_all = fa_data.as_miller_arrays(crystal_symmetry=c)[0]
    fsigf = fsigf_all.select(fsigf_all.d_spacings().data()>d_min)
    fsigf.setup_binner(n_bins=n_bins)

    ea = fsigf.quasi_normalize_structure_factors()
    ea.setup_binner(n_bins=n_bins)
    ea_weak = ea.select((abs(ea.data())<1.5))
    ea_weak.setup_binner(n_bins=n_bins)
    return ea, ea_weak


def shelxd_cc_all(pdb_sub, fa_file, fa_ins, d_min, n_bins):
    '''Calculate correlation between Ea and Ecalc values'''

    try:
        ea, ea_weak = shelxd_read_hklf(fa_file, fa_ins, d_min, n_bins)
        ecalc = shelxd_substructure_ecalc(pdb_sub, ea, fa_ins, d_min, n_bins)
        ecalc_weak = shelxd_substructure_ecalc(pdb_sub, ea_weak, fa_ins, d_min, n_bins)

        corr_all = 100.*ea.correlation(ecalc, use_binning=False).coefficient()
        if corr_all > 100.:
            corr_all = float('nan')

        corr_weak = 100.*ea_weak.correlation(ecalc_weak, use_binning=False).coefficient()
        if corr_weak > 100.:
            corr_weak = float('nan')

        corr_cfom = corr_all + corr_weak
        return corr_weak
    except:
        return float('nan')


def log_rank_table(ranks, spacegroups, best_sg):

    logging.info('SHELXD solution rank averages----------------------------')
    logging.info('    Spgr     CCres     CCall    CCweak     CFOM')
    cols = ['CCall', 'CCweak', 'CFOM', 'CCres']
    for sg in spacegroups:
        cc_rank, ccweak_rank, cfom_rank, normcc_rank = [ranks[sg][col] for col in cols]
        log_pattern = '%8s  %6.2f|%2.f %6.2f|%2.f %6.2f|%2.f %6.2f|%2.f'
        if sg == best_sg:
            log_pattern += '  (best)'
        logging.info(log_pattern, sg, normcc_rank, normcc_rank,
                           cc_rank, cc_rank,
                           ccweak_rank, ccweak_rank,
                           cfom_rank, cfom_rank)
    logging.info('---------------------------------------------------------')



def log_shelxd_results(results, spacegroups, best_keys, xml_results):

    _, nsites, ano_rlimits = map(sorted, map(set, zip(*results.keys())))
    for spacegroup in spacegroups:
        if spacegroup == best_keys[0]:
            logging.info('Spacegroup: %s (best)', spacegroup)
            xml_results['SPACEGROUP'] = space_group_symbols(spacegroup).number()
        else:
            logging.info('Spacegroup: %s', spacegroup)

        logging.info('No.  Res.   CCall  CCweak CFOM  No. found')
        cols = ['CCall', 'CCweak', 'CFOM', 'nsites']
        for nsite in nsites:
            write_nsite = True
            for rlimit in ano_rlimits:
                try:
                    (cc, cc_weak, cfom, nsite_real) = [results[(spacegroup, nsite, rlimit)][col] for col in cols]
                except KeyError:
                    continue
                if write_nsite:
                    log_pattern = '%3d  %.2f  %6.2f %6.2f %6.2f %3d'
                else:
                    log_pattern = '     %.2f  %6.2f %6.2f %6.2f %3d'
                if (spacegroup, nsite, rlimit) == best_keys:
                    log_pattern += ' (best)'
                if write_nsite:
                    logging.info(log_pattern, nsite, rlimit,
                                       cc, cc_weak,
                                       cfom, nsite_real)
                else:
                    logging.info(log_pattern, rlimit,
                                       cc, cc_weak,
                                       cfom, nsite_real)
                write_nsite = False


def log_shelxd_results_advanced(results, result_ranks, spacegroups, best_keys, xml_results):


    _, nsites, ano_rlimits = map(sorted, map(set, zip(*results.keys())))
    for spacegroup in spacegroups:
        if spacegroup == best_keys[0]:
            logging.info('Spacegroup: %s (best)', spacegroup)
            xml_results['SPACEGROUP'] = space_group_symbols(spacegroup).number()
        else:
            logging.info('Spacegroup: %s', spacegroup)

        logging.info('No.  Res.    CCres     CCall     CCweak    CFOM    No. found')
        cols = ['CCall', 'CCweak', 'CFOM', 'nsites', 'CCres']
        rk_cols = ['CCall', 'CCweak', 'CFOM', 'CCres']
        for nsite in nsites:
            write_nsite = True
            for rlimit in ano_rlimits:
                try:
                    (cc, cc_weak, cfom, nsite_real, norm_cc) = [results[(spacegroup, nsite, rlimit)][col] for col in cols]
                    (rk_cc, rk_cc_weak, rk_cfom, rk_norm_cc) = [result_ranks[(spacegroup, nsite, rlimit)][col] for col in rk_cols]
                except KeyError:
                    continue
                if write_nsite:
                    log_pattern = '%3d  %.2f  %6.2f|%2d %6.2f|%2d %6.2f|%2d %6.2f|%2d %3d'
                else:
                    log_pattern = '     %.2f  %6.2f|%2d %6.2f|%2d %6.2f|%2d %6.2f|%2d %3d'
                if (spacegroup, nsite, rlimit) == best_keys:
                    log_pattern += ' (best)'
                if write_nsite:
                    logging.info(log_pattern, nsite, rlimit,
                                       norm_cc, rk_norm_cc,
                                       cc, rk_cc, cc_weak, rk_cc_weak,
                                       cfom, rk_cfom, nsite_real)
                else:
                    logging.info(log_pattern, rlimit,
                                       norm_cc, rk_norm_cc,
                                       cc, rk_cc, cc_weak, rk_cc_weak,
                                       cfom, rk_cfom, nsite_real)
                write_nsite = False
