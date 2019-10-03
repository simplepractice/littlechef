#Copyright 2010-2015 Miquel Torres <tobami@gmail.com>
#
#Licensed under the Apache License, Version 2.0 (the "License");
#you may not use this file except in compliance with the License.
#You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#Unless required by applicable law or agreed to in writing, software
#distributed under the License is distributed on an "AS IS" BASIS,
#WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#See the License for the specific language governing permissions and
#limitations under the License.
#
"""Chef Solo deployment"""
import os
import subprocess

from fabric.api import *
from fabric.operations import get
from fabric.contrib.files import exists, upload_template
from fabric.utils import abort

from StringIO import StringIO

from littlechef import cookbook_paths, colors
from littlechef import LOGFILE

# Path to local patch
BASEDIR = os.path.abspath(os.path.dirname(__file__).replace('\\', '/'))


def install(version):
    """Install Chef using the omnibus installer"""
    url = "https://www.chef.io/chef/install.sh"
    with hide('stdout', 'running'):
        local("""python -c "import urllib; print urllib.urlopen('{0}').read()"'
              ' > /tmp/install.sh""".format(url))
        put('/tmp/install.sh', '/tmp/install.sh')
        print("Downloading and installing Chef {0}...".format(version))
        with hide('stdout'):
            sudo("""bash /tmp/install.sh -v {0}""".format(version))
            sudo('rm /tmp/install.sh')


def configure(current_node=None):
    """Deploy chef-solo specific files"""
    current_node = current_node or {}
    # Ensure that the /tmp/chef-solo/cache directory exist
    cache_dir = "{0}/cache".format(env.node_work_path)
    # First remote call, could go wrong
    try:
        cache_exists = exists(cache_dir)
    except EOFError as e:
        abort("Could not login to node, got: {0}".format(e))
    if not cache_exists:
        with settings(hide('running', 'stdout'), warn_only=True):
            output = sudo('mkdir -p {0}'.format(cache_dir))
        if output.failed:
            error = "Could not create {0} dir. ".format(env.node_work_path)
            error += "Do you have sudo rights?"
            abort(error)
    # Change ownership of /tmp/chef-solo/ so that we can rsync
    with hide('running', 'stdout'):
        with settings(warn_only=True):
            output = sudo(
                'chown -R {0} {1}'.format(env.user, env.node_work_path))
        if output.failed:
            error = "Could not modify {0} dir. ".format(env.node_work_path)
            error += "Do you have sudo rights?"
            abort(error)
    # Set up chef solo configuration
    logging_path = os.path.dirname(LOGFILE)
    if not exists(logging_path):
        sudo('mkdir -p {0}').format(logging_path)
    if not exists('/etc/chef'):
        sudo('mkdir -p /etc/chef && chmod -R 774 /etc/chef')
    # Set parameters and upload solo.rb template
    reversed_cookbook_paths = cookbook_paths[:]
    reversed_cookbook_paths.reverse()
    cookbook_paths_list = '[{0}]'.format(', '.join(
        ['"{0}/{1}"'.format(env.node_work_path, x)
            for x in reversed_cookbook_paths]))
    data = {
        'node_work_path': env.node_work_path,
        'cookbook_paths_list': cookbook_paths_list,
        'environment': current_node.get('chef_environment', '_default'),
        'verbose': "true" if env.verbose else "false",
        'http_proxy': env.http_proxy,
        'https_proxy': env.https_proxy,
        'no_proxy': env.no_proxy,
        'formatter': env.formatter
    }
    with settings(hide('everything')):
        try:
            upload_template('client.rb.j2', '/etc/chef/client.rb',
                            context=data, use_sudo=True, backup=False,
                            template_dir=BASEDIR, use_jinja=True, mode=0644)
        except SystemExit:
            error = ("Failed to upload '/etc/chef/client.rb'\nThis "
                     "can happen when the deployment user does not have a "
                     "home directory, which is needed as a temporary location")
            abort(error)
    with hide('stdout'):
        sudo('chown root:$(id -g -n root) {0}'.format('/etc/chef/client.rb'))

# Lock node
def lock(current_node, reason):
    current_node = current_node
    proc = subprocess.Popen(['/usr/bin/whoami'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    user, error = proc.communicate()
    data = {
        'reason': reason,
        'author': user.rstrip()
    }
    with settings(hide('everything')):
        try:
            upload_template('lockfile.j2', '/etc/chef/lockfile',
                            context=data, use_sudo=True, backup=False,
                            template_dir=BASEDIR, use_jinja=True, mode=0764)
        except SystemExit:
            error = ("Failed to upload '/etc/chef/.lockfile'\nThis "
                     "can happen when the deployment user does not have a "
                     "home directory, which is needed as a temporary location")
            abort(error)
    with hide('stdout'):
        sudo('chown root:$(id -g -n root) {0}'.format('/etc/chef/lockfile'))

def unlock(current_node):
    current_node = current_node
    with cd('/etc/chef/'):
        try:
            sudo('rm -f lockfile')
            print(colors.green("{} has been unlocked.")).format(current_node['host_name'])
        except SystemExit:
            error = ("Failed to delete '/etc/chef/lockfile'\n "
                     "This can happen when the deployment user does not have a"
                     "home directory, which is needed as a temporary location")
            abort(error)

def node_locked(current_node):
    return True if exists('/etc/chef/lockfile', use_sudo=True) else False

def get_lock_info(current_node):
    with settings(hide('everything')):
        try:
            lock_info = StringIO()
            get('/etc/chef/lockfile', lock_info)
            return lock_info.getvalue()
        except SystemExit:
            abort("Failed to get lock info")
