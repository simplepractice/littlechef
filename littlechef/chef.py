#Copyright 2010-2014 Miquel Torres <tobami@gmail.com>
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
"""Node configuration and syncing
See http://wiki.opscode.com/display/chef/Anatomy+of+a+Chef+Run
"""
import os
import shutil
import json
import requests
import subprocess
from copy import deepcopy

from fabric.api import settings, hide, env, sudo, put
from fabric.contrib.files import exists
from fabric.utils import abort
from fabric.contrib.project import rsync_project

from littlechef import cookbook_paths, whyrun, lib, solo, colors
from littlechef import LOGFILE, enable_logs as ENABLE_LOGS

import gspread
import datetime
from oauth2client.client import SignedJwtAssertionCredentials
import boto3

# Path to local patch
basedir = os.path.abspath(os.path.dirname(__file__).replace('\\', '/'))
chef_tracker_bucket = 'chef-tracker.practicesimple.com'

def save_config(node, force=False):
    """Saves node configuration
    if no nodes/hostname.json exists, or force=True, it creates one
    it also saves to tmp_node.json

    """
    filepath = os.path.join("nodes", env.host_string + ".json")
    tmp_filename = 'tmp_{0}.json'.format(env.host_string)
    files_to_create = [tmp_filename]
    if not os.path.exists(filepath) or force:
        # Only save to nodes/ if there is not already a file
        print "Saving node configuration to {0}...".format(filepath)
        files_to_create.append(filepath)
    for node_file in files_to_create:
        with open(node_file, 'w') as f:
            f.write(json.dumps(node, indent=4, sort_keys=True))
    return tmp_filename


def _get_ipaddress(node):
    """Adds the ipaddress attribute to the given node object if not already
    present and it is correctly given by ohai
    Returns True if ipaddress is added, False otherwise

    """
    if "ipaddress" not in node:
        with settings(hide('stdout'), warn_only=True):
            output = sudo('ohai -l warn ipaddress')
        if output.succeeded:
            try:
                node['ipaddress'] = json.loads(output)[0]
            except ValueError:
                abort("Could not parse ohai's output for ipaddress"
                      ":\n  {0}".format(output))
            return True
    return False

# Lock/Unlock function callers
def lock_node(node, reason):
    """"Calls locker with settings,current_node and action variables"""
    current_node = lib.get_node(node['name'])
    if solo.node_locked(current_node):
        content = json.loads(solo.get_lock_info(current_node))
        print colors.yellow("Node {0} already locked by {1}.\nReason: {2}".format(current_node['host_name'], content['author'], content['reason']))
        raise SystemExit
    else:
        solo.lock(current_node, reason)
        print colors.green("Node {0} locked".format(current_node['host_name']))
        record_chef_run(node, "successful", reason)

def unlock_node(node):
    """Calls unlocker from solo"""
    current_node = lib.get_node(node['name'])
    if solo.node_locked(current_node):
        solo.unlock(current_node)
        record_chef_run(node, "successful", "")
    else:
        print "Failed to unlock node. Node {0} is not locked.".format(current_node['host_name'])

def chef_test():
    """Calls chef-solo on the remote node, returns True if successful,
    False otherwise

    """
    cmd = "chef-solo --version"
    output = sudo(cmd, warn_only=True, quiet=True)
    if 'chef-solo: command not found' in output:
        return False
    return True

def slack_notifier(message):
    headers = {"Content-type":"application/json"}
    encrypted_url = subprocess.check_output("knife solo data bag show credentials chef-slack -F json", shell=True)
    url = eval(encrypted_url)['url']
    requests.post(url, data=message, headers=headers)

def aws_credentials():
    sts_client = boto3.client('sts')
    assumed_role_object=sts_client.assume_role(
            RoleArn='arn:aws:iam::526655127920:role/OrganizationAccountAccessRole',
            RoleSessionName='chef-tracker'
    )
    return assumed_role_object['Credentials']

def git_branch():
    proc = subprocess.Popen("git branch | awk '/\*/ { print $2; }'",
                    shell=True, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    branch, error = proc.communicate()
    return branch.rstrip()

def chef_tracker_update(data, host_data):
    host = host_data.get('HOST')
    element = filter(lambda x: x.get('HOST') == host, data)
    if len(element) > 0:
        index = data.index(filter(lambda x: x.get('HOST') == host, data)[0])
        data[index] = host_data
    else:
        data.insert(0, host_data)
    return sorted(data, key = lambda i: i['HOST'])

def chef_tracker_json(name):
    filename = 'tmp/' + name + '.json'
    if os.path.isfile(filename):
        f = open(filename, 'r')
        r = f.read()
        f.close()
        return json.loads(r)
    else:
        url = 'http://' + chef_tracker_bucket + '/' + name + '.json'
        r = requests.get(url)
        f = open(filename, 'w')
        f.write(r.text)
        f.close()
        return r.json()

def chef_tracker_upload(name, data):
    json_data = json.dumps(data)
    filename = name + '.json'
    f = open('tmp/' + filename, 'w')
    f.write(json_data)
    f.close()
    credentials = aws_credentials()
    s3_client = boto3.client(
        's3',
        aws_access_key_id = credentials['AccessKeyId'],
        aws_secret_access_key = credentials['SecretAccessKey'],
        aws_session_token = credentials['SessionToken'],
    )
    response = s3_client.put_object(
        Bucket = chef_tracker_bucket,
        Key = filename,
        Body = json_data
    )


def record_chef_run(node, status, lock_note):
    user = os.environ['USER']
    branch = git_branch()
    node['littlechef'] = { 'branch': branch, 'user': user }

    hostname = node['name']
    host_data = {
            'HOST': hostname,
            'BRANCH': branch,
            'USER': user,
            'TIME': datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            'CSTATUS': status,
            'ENV': node['chef_environment'],
            'LOCK': lock_note
            }
    status_data = chef_tracker_json('status')
    updated_status_data = chef_tracker_update(status_data, host_data)
    chef_tracker_upload('status', updated_status_data)

    log_data = chef_tracker_json('log')
    log_data.insert(0, host_data)
    chef_tracker_upload('log', log_data[:500])


    # Post to Slack #engineering channel
    post_message = "{0} successfully deployed [{1}] to *{2}*.".format(user, branch, hostname) if status == "successful" else "{0} failed to deploy [{1}] to *{2}*.".format(user, branch, hostname)
    post_message = post_message.replace('\n','')
    payload = '{{"attachments": [ {{"color": "#00BD9D", "title": "Chef deploy messages", "text":"{0}", "mrkdwn": true}}]}}'.format(post_message) if status == "successful" else '{{"attachments": [ {{"color": "#E53D00", "title": "Chef deploy messages", "text":"{0}", "mrkdwn": true}}]}}'.format(post_message)
    slack_notifier(payload)

def sync_node(node):
    """Builds, synchronizes and configures a node.
    It also injects the ipaddress to the node's config file if not already
    existent.

    """
    if node.get('dummy') or 'dummy' in node.get('tags', []):
        lib.print_header("Skipping dummy: {0}".format(env.host))
        return False
    current_node = lib.get_node(node['name'])
    # Check if node locked
    if solo.node_locked(current_node):
        content = json.loads(solo.get_lock_info(current_node))
        print colors.yellow("Skipping node {0}.\nLocked by {1}.\nReason: {2}".format(current_node['host_name'], content['author'], content['reason']))
        return False
    # Always configure Chef Solo
    solo.configure(current_node)
    ipaddress = _get_ipaddress(node)
    # Everything was configured alright, so save the node configuration
    # This is done without credentials, so that we keep the node name used
    # by the user and not the hostname or IP translated by .ssh/config
    filepath = save_config(node, ipaddress)
    try:
        # Synchronize the kitchen directory
        _synchronize_node(filepath, node)
        # Execute Chef Solo
        _configure_node(node)
    finally:
        _node_cleanup()
    return True


def _synchronize_node(configfile, node):
    """Performs the Synchronize step of a Chef run:
    Uploads all cookbooks, all roles and all databags to a node and add the
    patch for data bags

    Returns the node object of the node which is about to be configured,
    or None if this node object cannot be found.

    """
    msg = "Synchronizing nodes, environments, roles, cookbooks and data bags..."
    if env.parallel:
        msg = "[{0}]: {1}".format(env.host_string, msg)
    print(msg)
    # First upload node.json
    remote_file = '/etc/chef/node.json'
    put(configfile, remote_file, use_sudo=True, mode=400)
    with hide('stdout'):
        sudo('chown root:$(id -g -n root) {0}'.format(remote_file))
    # Remove local temporary node file
    os.remove(configfile)
    # Synchronize kitchen
    extra_opts = "-q"
    if env.follow_symlinks:
        extra_opts += " --copy-links"
    ssh_opts = ""
    if env.ssh_config_path:
        ssh_opts += " -F %s" % os.path.expanduser(env.ssh_config_path)
    if env.encrypted_data_bag_secret:
        put(env.encrypted_data_bag_secret,
            "/etc/chef/encrypted_data_bag_secret",
            use_sudo=True,
            mode=0600)
        sudo('chown root:$(id -g -n root) /etc/chef/encrypted_data_bag_secret')

    paths_to_sync = ['./data_bags', './roles', './environments']
    for cookbook_path in cookbook_paths:
        paths_to_sync.append('./{0}'.format(cookbook_path))

    # Add berksfile directory to sync_list
    if env.berksfile:
        paths_to_sync.append(env.berksfile_cookbooks_directory)

    if env.loglevel is "debug":
        extra_opts = ""

    if env.gateway:
        ssh_key_file = '.ssh/' + os.path.basename(' '.join(env.ssh_config.lookup(
            env.host_string)['identityfile']))
        ssh_opts += " " + env.gateway + " ssh -o StrictHostKeyChecking=no -i "
        ssh_opts += ssh_key_file

    rsync_project(
        env.node_work_path,
        ' '.join(paths_to_sync),
        exclude=('*.svn', '.bzr*', '.git*', '.hg*'),
        delete=True,
        extra_opts=extra_opts,
        ssh_opts=ssh_opts
    )

    if env.sync_packages_dest_dir and env.sync_packages_local_dir:
        print("Uploading packages from {0} to remote server {2} directory "
              "{1}").format(env.sync_packages_local_dir,
                            env.sync_packages_dest_dir, env.host_string)
        try:
            rsync_project(
              env.sync_packages_dest_dir,
              env.sync_packages_local_dir+"/*",
              exclude=('*.svn', '.bzr*', '.git*', '.hg*'),
              delete=True,
              extra_opts=extra_opts,
              ssh_opts=ssh_opts
            )
        except:
            print("Warning: package upload failed. Continuing cooking...")

    _add_environment_lib()  # NOTE: Chef 10 only


def build_dct(dic, keys, value):
    """Builds a dictionary with arbitrary depth out of a key list"""
    key = keys.pop(0)
    if len(keys):
        dic.setdefault(key, {})
        build_dct(dic[key], keys, value)
    else:
        # Transform cookbook default attribute strings into proper booleans
        if value == "false":
            value = False
        elif value == "true":
            value = True
        # It's a leaf, assign value
        dic[key] = deepcopy(value)


def update_dct(dic1, dic2):
    """Merges two dictionaries recursively
    dic2 will have preference over dic1

    """
    for key, val in dic2.items():
        if isinstance(val, dict):
            dic1.setdefault(key, {})
            update_dct(dic1[key], val)
        else:
            dic1[key] = val


def _add_automatic_attributes(node):
    """Adds some of Chef's automatic attributes:
        http://wiki.opscode.com/display/chef/Recipes#Recipes
        -CommonAutomaticAttributes

    """
    node['fqdn'] = node['name']
    node['hostname'] = node['fqdn'].split('.')[0]
    node['domain'] = ".".join(node['fqdn'].split('.')[1:])


def _add_merged_attributes(node, all_recipes, all_roles):
    """Merges attributes from cookbooks, node and roles

    Chef Attribute precedence:
    http://docs.opscode.com/essentials_cookbook_attribute_files.html#attribute-precedence
    LittleChef implements, in precedence order:
        - Cookbook default
        - Environment default
        - Role default
        - Node normal
        - Role override
        - Environment override

    NOTE: In order for cookbook attributes to be read, they need to be
        correctly defined in its metadata.json

    """
    # Get cookbooks from extended recipes
    attributes = {}
    for recipe in node['recipes']:
        # Find this recipe
        found = False
        for r in all_recipes:
            if recipe == r['name']:
                found = True
                for attr in r['attributes']:
                    if r['attributes'][attr].get('type') == "hash":
                        value = {}
                    else:
                        value = r['attributes'][attr].get('default')
                    # Attribute dictionaries are defined as a single
                    # compound key. Split and build proper dict
                    build_dct(attributes, attr.split("/"), value)
        if not found:
            error = "Could not find recipe '{0}' while ".format(recipe)
            error += "building node data bag for '{0}'".format(node['name'])
            abort(error)

    # Get default role attributes
    for role in node['roles']:
        for r in all_roles:
            if role == r['name']:
                update_dct(attributes, r.get('default_attributes', {}))

    # Get default environment attributes
    environment = lib.get_environment(node['chef_environment'])
    update_dct(attributes, environment.get('default_attributes', {}))

    # Get normal node attributes
    non_attribute_fields = [
        'id', 'name', 'role', 'roles', 'recipes', 'run_list', 'ipaddress']
    node_attributes = {}
    for key in node:
        if key in non_attribute_fields:
            continue
        node_attributes[key] = node[key]
    update_dct(attributes, node_attributes)

    # Get override role attributes
    for role in node['roles']:
        for r in all_roles:
            if role == r['name']:
                update_dct(attributes, r.get('override_attributes', {}))

    # Get override environment attributes
    update_dct(attributes, environment.get('override_attributes', {}))

    # Merge back to the original node object
    node.update(attributes)


def build_node_data_bag():
    """Builds one 'node' data bag item per file found in the 'nodes' directory

    Automatic attributes for a node item:
        'id': It adds data bag 'id', same as filename but with underscores
        'name': same as the filename
        'fqdn': same as the filename (LittleChef filenames should be fqdns)
        'hostname': Uses the first part of the filename as the hostname
            (until it finds a period) minus the .json extension
        'domain': filename minus the first part of the filename (hostname)
            minus the .json extension
    In addition, it will contain the merged attributes from:
        All default cookbook attributes corresponding to the node
        All attributes found in nodes/<item>.json file
        Default and override attributes from all roles

    """
    nodes = lib.get_nodes()
    node_data_bag_path = os.path.join('data_bags', 'node')
    # In case there are leftovers
    remove_local_node_data_bag()
    os.makedirs(node_data_bag_path)
    all_recipes = lib.get_recipes()
    all_roles = lib.get_roles()
    for node in nodes:
        # Dots are not allowed (only alphanumeric), substitute by underscores
        node['id'] = node['name'].replace('.', '_')

        # Build extended role list
        node['role'] = lib.get_roles_in_node(node)
        node['roles'] = node['role'][:]
        for role in node['role']:
            node['roles'].extend(lib.get_roles_in_role(role))
        node['roles'] = list(set(node['roles']))

        # Build extended recipe list
        node['recipes'] = lib.get_recipes_in_node(node)
        # Add recipes found inside each roles in the extended role list
        for role in node['roles']:
            node['recipes'].extend(lib.get_recipes_in_role(role))
        node['recipes'] = list(set(node['recipes']))

        # Add node attributes
        _add_merged_attributes(node, all_recipes, all_roles)
        _add_automatic_attributes(node)

        # Save node data bag item
        with open(os.path.join(
                  'data_bags', 'node', node['id'] + '.json'), 'w') as f:
            f.write(json.dumps(node))


def remove_local_node_data_bag():
    """Removes generated 'node' data_bag locally"""
    node_data_bag_path = os.path.join('data_bags', 'node')
    if os.path.exists(node_data_bag_path):
        shutil.rmtree(node_data_bag_path)


def ensure_berksfile_cookbooks_are_installed():
    """Run 'berks vendor' to berksfile cookbooks directory"""
    msg = "Vendoring cookbooks from Berksfile {0} to directory {1}..."
    print(msg.format(env.berksfile, env.berksfile_cookbooks_directory))

    run_vendor = True
    cookbooks_dir = env.berksfile_cookbooks_directory
    berksfile_lock_path = cookbooks_dir+'/Berksfile.lock'

    berksfile_lock_exists = os.path.isfile(berksfile_lock_path)
    cookbooks_dir_exists = os.path.isdir(cookbooks_dir)

    if cookbooks_dir_exists and berksfile_lock_exists:
        berksfile_mtime = os.stat('Berksfile').st_mtime
        cookbooks_mtime = os.stat(berksfile_lock_path).st_mtime
        run_vendor = berksfile_mtime > cookbooks_mtime

    if run_vendor:
        if cookbooks_dir_exists:
            shutil.rmtree(env.berksfile_cookbooks_directory)

        p = subprocess.Popen(['berks', 'vendor', env.berksfile_cookbooks_directory],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        if env.verbose or p.returncode:
            print stdout, stderr


def _remove_remote_node_data_bag():
    """Removes generated 'node' data_bag from the remote node"""
    node_data_bag_path = os.path.join(env.node_work_path, 'data_bags', 'node')
    if exists(node_data_bag_path):
        sudo("rm -rf {0}".format(node_data_bag_path))


def _node_cleanup():
    if env.loglevel is not "debug":
        with hide('running', 'stdout'):
            _remove_remote_node_data_bag()
            with settings(warn_only=True):
                sudo("rm '/etc/chef/node.json'")
                solo_node_cnf_file = os.path.join(env.node_work_path, "nodes", env.host_string.split('.')[0] + ".json")
                sudo("rm -f {0}".format(solo_node_cnf_file))
                if env.encrypted_data_bag_secret:
                    sudo("rm '/etc/chef/encrypted_data_bag_secret'")


def _add_environment_lib():
    """Adds the chef_solo_envs cookbook, which provides a library that adds
    environment attribute compatibility for chef-solo v10
    NOTE: Chef 10 only

    """
    # Create extra cookbook dir
    lib_path = os.path.join(env.node_work_path, cookbook_paths[0],
                            'chef_solo_envs', 'libraries')
    with hide('running', 'stdout'):
        sudo('mkdir -p {0}'.format(lib_path))
    # Add environment patch to the node's cookbooks
    put(os.path.join(basedir, 'environment.rb'),
        os.path.join(lib_path, 'environment.rb'), use_sudo=True)


def _configure_node(node):
    """Exectutes chef-solo to apply roles and recipes to a node"""
    print("")
    msg = "Cooking..."
    if env.parallel:
        msg = "[{0}]: {1}".format(env.host_string, msg)
    print(msg)
    # Backup last report
    with settings(hide('stdout', 'warnings', 'running'), warn_only=True):
        sudo("mv {0} {0}.1".format(LOGFILE))
    # Build chef-solo command
    cmd = "RUBYOPT=-Ku chef-solo"
    if whyrun:
        cmd += " --why-run"
    cmd += ' -l {0} -j /etc/chef/node.json'.format(env.loglevel)
    if ENABLE_LOGS:
        cmd += ' | tee {0}'.format(LOGFILE)
    if env.loglevel == "debug":
        print("Executing Chef Solo with the following command:\n"
              "{0}".format(cmd))
    with settings(hide('warnings', 'running'), warn_only=True):
        output = sudo(cmd)
    if (output.failed or "FATAL: Stacktrace dumped" in output or
            ("Chef Run complete" not in output and
             "Report handlers complete" not in output)):
        record_chef_run(node, "failed", "")
        if 'chef-solo: command not found' in output:
            print(
                colors.red(
                    "\nFAILED: Chef Solo is not installed on this node"))
            print(
                "Type 'fix node:{0} deploy_chef' to install it".format(
                    env.host))
            abort("")
        else:
            print(colors.red(
                "\nFAILED: chef-solo could not finish configuring the node\n"))
            import sys
            sys.exit(1)
    else:
        msg = "\n"
        if env.parallel:
            msg += "[{0}]: ".format(env.host_string)
        record_chef_run(node, "successful", "")
        msg += "SUCCESS: Node correctly configured"
        print(colors.green(msg))
