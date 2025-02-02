#!/usr/bin/env python
#
# fast_ep ->
#
# Fast experimental phasing in the spirit of fast_dp, starting from nothing
# and using brute force (and educated guesses) to get everything going.
#
# run_job - a tool for running a job

import subprocess
import os
import random
import string

def random_string():
    return ''.join(random.sample(string.lowercase, 6))

def run_job(executable, arguments = [], stdin = [], working_directory = None):
    '''Run a program with some command-line arguments and some input,
    then return the standard output when it is finished.'''

    if working_directory is None:
        working_directory = os.getcwd()

    command_line = '%s' % executable
    for arg in arguments:
        command_line += ' "%s"' % arg

    popen = subprocess.Popen(command_line,
                             bufsize = 1,
                             stdin = subprocess.PIPE,
                             stdout = subprocess.PIPE,
                             stderr = subprocess.STDOUT,
                             cwd = working_directory,
                             universal_newlines = True,
                             shell = True,
                             env = os.environ)

    for record in stdin:
        popen.stdin.write('%s\n' % record)

    popen.stdin.close()

    output = []

    while True:
        record = popen.stdout.readline()
        if not record:
            break

        output.append(record)

    return output

def run_job_cluster(executable, arguments = [], stdin = [],
                    working_directory = None, ncpu = 1, timeout = None, sge_project=None):
    '''Run a program with some command-line arguments and some input,
    then return the standard output when it is finished.'''


    if working_directory is None:
        working_directory = os.getcwd()

    rs = random_string()

    script = open(os.path.join(working_directory, 'FEP_%s.sh' % rs), 'w')

    script.write('#!/bin/bash\n')

    command_line = '%s' % executable
    for arg in arguments:
        command_line += ' "%s"' % arg

    if stdin:
        script.write('%s << eof\n' % command_line)
        for record in stdin:
            script.write('%s\n' % record)
        script.write('eof\n')
    else:
        script.write('%s\n' % command_line)

    script.close()

    if timeout:
        timeout_tokens = ['--time=%d' % timeout]
    else:
        timeout_tokens = []

    if sge_project:
        project_tokens = ['-J %s' % sge_project]
    else:
        project_tokens = []

    queue = 'main'

    if ncpu > 1:
        qsub_output = run_job(
            'sbatch', timeout_tokens + project_tokens + ['--export=ALL', '--cpus-per-task=%d' % ncpu,
                                      '--chdir=%s' % working_directory, '-p', queue,
                                      'FEP_%s.sh' % rs], [], working_directory)
    else:
        qsub_output = run_job(
            'sbatch', timeout_tokens + project_tokens + ['--export=ALL', '--chdir=%s' % working_directory, '-p', queue,
                                      'FEP_%s.sh' % rs], [], working_directory)

    if 'Submitted batch job' not in qsub_output[0]:
        raise RuntimeError, 'error submitting job to queue'


    job_id = None
    for record in qsub_output:
        if 'Submitted batch job' in record:
            job_id = int(record.split()[0])

    return job_id

def is_cluster_job_finished(job_id):

    qstat_output = run_job('squeue', arguments=['-j', job_id])

    for record in qstat_output:
        if str(job_id) in record:
            return False

    return True

def setup_job_drmaa(job, executable, arguments = [], stdin = [],
                    working_directory = None, ncpu = 1, timeout = None):
    '''Generate a script to run a program with some command-line arguments and
    setup cluster job for submission using DRMAA API.'''

    if working_directory is None:
        working_directory = os.getcwd()

    rs = random_string()
    script_path = os.path.join(working_directory, 'FEP_%s.sh' % rs)
    with open(script_path, 'w') as script:

        script.write('#!/bin/bash\n')

        command_line = '%s' % executable
        for arg in arguments:
            command_line += ' "%s"' % arg

        if stdin:
            script.write('%s << eof\n' % command_line)
            for record in stdin:
                script.write('%s\n' % record)
            script.write('eof\n')
        else:
            script.write('%s\n' % command_line)

    job.jobName = 'FEP_%s.sh' % rs
    job.remoteCommand = 'sh'
    job.workingDirectory = working_directory
    job.args = [script_path]

    qsub_args = ['--export=ALL',]
    if timeout:
        qsub_args += ['--time=%s' % timeout]
    if ncpu > 1:
        qsub_args += ['--cpus-per-task=%s' % str(ncpu)]

    job.nativeSpecification = ' '.join(qsub_args)
    #job.jobCategory = 'medium'
