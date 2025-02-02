#!/usr/bin/env python3
# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
This script can be used to prepare a bundle of files describing the state of a
GKE cluster.

Usage examples:
 * Using default kubeconfig:
   $ python3 create_snapshot.py
 * Using selected config from a file:
   $ python3 create_snapshot.py --kubeconfig /tmp/kubeconfig
 * Specifying timeout for the kubectl calls (default is 15 seconds):
   $ python3 create_snapshot.py --timeout 10

Output:
snapshot-{timestamp}.tar.gz file containing outputs of various kubectl commands
that were executed. The file is created in the current working directory.

Requirements:
 * Python 3.8 (but probably work with lower versions of Python 3 too)
 * kubectl available through $PATH

"""

import argparse
import os
import pathlib
import subprocess
import tarfile
import tempfile
import time

CMD_TIMEOUT_SEC = 15
BACKOFF_LIMIT = 4

KUBECTL_GLOBAL_CMDS = [
    'kubectl version {kubeconfig_arg} --request-timeout {timeout}',
    'kubectl cluster-info {kubeconfig_arg} --request-timeout {timeout}',
    'kubectl get clusterroles -o wide {kubeconfig_arg} --request-timeout {timeout}',          # noqa: E501
    'kubectl get clusterrolebindings -o wide {kubeconfig_arg} --request-timeout {timeout}',   # noqa: E501
    'kubectl get crd -o wide {kubeconfig_arg} --request-timeout {timeout}',
    'kubectl get nodes -o wide {kubeconfig_arg} --request-timeout {timeout}',
    'kubectl get clusterroles -o yaml {kubeconfig_arg} --request-timeout {timeout}',          # noqa: E501
    'kubectl get clusterrolebindings -o yaml {kubeconfig_arg} --request-timeout {timeout}',   # noqa: E501
    'kubectl get crd -o yaml {kubeconfig_arg} --request-timeout {timeout}',
    'kubectl get nodes -o yaml {kubeconfig_arg} --request-timeout {timeout}',
    'kubectl describe clusterroles {kubeconfig_arg} --request-timeout {timeout}',             # noqa: E501
    'kubectl describe clusterrolebindings {kubeconfig_arg} --request-timeout {timeout}',      # noqa: E501
    'kubectl describe crd {kubeconfig_arg} --request-timeout {timeout}',
    'kubectl describe nodes {kubeconfig_arg} --request-timeout {timeout}',
]

KUBECTL_PER_NS_CMDS = [
    'kubectl get all -o wide {kubeconfig_arg} --request-timeout {timeout} --namespace {namespace}',  # noqa: E501
    'kubectl get all -o yaml {kubeconfig_arg} --request-timeout {timeout} --namespace {namespace}',  # noqa: E501
    'kubectl describe all {kubeconfig_arg} --request-timeout {timeout} --namespace {namespace}',     # noqa: E501
]

KUBECTL_PER_POD_CMDS = [
    'kubectl logs {kubeconfig_arg} {pod} --container {container} --request-timeout {timeout} --namespace {namespace}',  # noqa: E501
]


def parse_args():
    parser = argparse.ArgumentParser(
      description='Create a snapshot of important information about Anthos K8'
                  ' cluster to be used by GCP support.')
    parser.add_argument('--kubeconfig',
                        dest='kubeconfig',
                        action='store',
                        default=os.getenv('KUBECONFIG', ''),
                        help='Path to kubeconfig file to be used to gather the'
                        'snapshot')
    parser.add_argument('--timeout', dest='timeout', action='store',
                        default=CMD_TIMEOUT_SEC, type=int,
                        help='Timeout for kubectl commands.')
    args = parser.parse_args()
    return args.kubeconfig, args.timeout


def run_cmd(cmd: str, subfolder: str, output_dir: pathlib.Path):  # noqa: E999
    output_path = output_dir / subfolder / cmd.replace(' ', '_')
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print("Executing: {}... ".format(cmd), end='')
    backoff_timer = 1
    backoff_count = 0
    with open(output_path, mode='w') as output_file:
        while True:
            if backoff_count > BACKOFF_LIMIT:
                print('[ FAIL ]')
                return
            process = subprocess.run(cmd, stdout=output_file,
                                     stderr=output_file,
                                     timeout=60,
                                     shell=True)
            if not process.returncode:
                print("[ DONE ]")
                return
            print("\nCommand failed, trying again in {}s. ' \
                  'Error output: {}".format(backoff_timer, process.stderr))
            time.sleep(backoff_timer)
            backoff_timer *= 2
            backoff_count += 1


def get_kubectl_list(object_type, kubeconfig, timeout, namespace=None,
                     object_name='', jsonpath="{.items[*].metadata.name}"):
    cmd = 'kubectl get {obj_type} {kubeconfig_arg} ' \
          '--request-timeout {timeout} ' \
          '-o jsonpath="{jsonpath}" {obj_name}'. \
          format(kubeconfig_arg=kubeconfig,
                 jsonpath=jsonpath,
                 timeout=timeout,
                 obj_type=object_type, obj_name=object_name)
    if namespace:
        cmd = "{} -n {}".format(cmd, namespace)
    backoff_timer = 1
    backoff_count = 0
    print("Executing: {}... ".format(cmd), end='')
    while True:
        if backoff_count > BACKOFF_LIMIT:
            print('[ FAIL ]')
            return
        process = subprocess.run(cmd, shell=True,
                                 capture_output=True)
        if not process.returncode:
            print("[ DONE ]")
            obj_list = process.stdout.decode().strip().split(' ')
            if '' in obj_list:
                obj_list.remove('')
            return obj_list
        print("\nCommand failed, trying again in {}s. ' \
              'Error output: {}".format(backoff_timer, process.stderr))
        time.sleep(backoff_timer)
        backoff_timer *= 2
        backoff_count += 1


def main():
    kubeconfig, timeout = parse_args()
    timeout = "{}s".format(timeout)
    if kubeconfig:
        kubeconfig = \
          '--kubeconfig {}'.format(pathlib.Path(kubeconfig).absolute())

    namespaces_list = get_kubectl_list('namespaces', kubeconfig, timeout)

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_dir = pathlib.PosixPath(tmp_dir)
        for cmd in KUBECTL_GLOBAL_CMDS:
            run_cmd(
                cmd.format(kubeconfig_arg=kubeconfig, timeout=timeout),
                'global', output_dir
            )
        for namespace in namespaces_list:
            for cmd in KUBECTL_PER_NS_CMDS:
                run_cmd(
                    cmd.format(
                      kubeconfig_arg=kubeconfig,
                      timeout=timeout,
                      namespace=namespace
                    ),
                    'namespaces/{}'.format(namespace),
                    output_dir
                )
            for pod in get_kubectl_list('pods',
                                        kubeconfig, timeout, namespace):
                containers = get_kubectl_list('pod',
                                              kubeconfig, timeout,
                                              namespace=namespace,
                                              jsonpath="{.spec.containers[*].name}",  # noqa: E501
                                              object_name=pod)
                for container in containers:
                    for cmd in KUBECTL_PER_POD_CMDS:
                        run_cmd(
                            cmd.format(kubeconfig_arg=kubeconfig,
                                       timeout=timeout,
                                       namespace=namespace,
                                       pod=pod,
                                       container=container),
                            'namespaces/{}/{}'.format(namespace, pod),
                            output_dir
                        )
        # Commands done
        snapshot_name = 'snapshot-{}'.format(int(time.time()))
        snap_file = tarfile.open('{}.tar.gz'.format(snapshot_name), 'w:gz')
        snap_file.add(output_dir, snapshot_name)
        snap_file.close()
        print("Created snapshot: {}".format(snap_file.name))


if __name__ == '__main__':
    main()
