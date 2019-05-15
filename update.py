try:
  import requests
  import pprint
  import configparser
  from requests.auth import HTTPDigestAuth
  import argparse
  import json
  import time
  import subprocess
  import copy
  import urllib3
  urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
  urllib3.disable_warnings(urllib3.exceptions.SubjectAltNameWarning)
except ImportError as e:
  print(e)
  exit(1)


config = configparser.ConfigParser()
config.read('config.conf')
baseurl = config.get('Ops Manager','baseurl')
username = config.get('Ops Manager', 'username')
token = config.get('Ops Manager','token')

def get(endpoint, ca_cert_path='', key=''):
  resp = requests.get(baseurl + endpoint, auth=HTTPDigestAuth(username, token), verify=ca_cert_path, cert=key, timeout=10)
  if resp.status_code == 200:
    group_data = json.loads(resp.text)
    return group_data
  else:
    print("GET response was %s, not `200`" % resp.status_code)
    print(resp.text)
    raise requests.exceptions.RequestException

def put(endpoint, config, ca_cert_path='', key=''):
  header = {'Content-Type': 'application/json'}
  resp = requests.put(baseurl + endpoint, auth=HTTPDigestAuth(username, token), verify=ca_cert_path, cert=key, timeout=10, data=json.dumps(config), headers=header)
  if resp.status_code == 200:
    return resp
  else:
    print("PUT response was %s, not `200`" % resp.status_code)
    print(resp.text)
    raise requests.exceptions.RequestException

def initial_check(data):
  for instance in data['processes']:
    if instance['disabled'] == False:
      print("Automation configuration indicates nodes are already down. If you REALLY want to run this, please start again with the `--oh-dear-god` option")
      raise KeyError

def get_list_of_nodes(aa_config):
  hosts = []
  for entry in aa_config['processes']:
    hosts.append(entry['hostname'])
  return hosts

def disable_node_aa(aa_config, node):
  temp_config = copy.deepcopy(aa_config)
  process_tmp = []
  for instance in temp_config['processes']:
    if instance['hostname'] == node:
      instance['disabled'] = True
    else:
      instance['disabled'] = False
    process_tmp.append(instance)
  temp_config['processes'] = process_tmp
  return temp_config

# We need to determine if all nodes are in the desired state.
# Return `False` for any node found not in the desired state.
def get_status(status_data, hostname, previous_goal_version):
  host_found = True
  if previous_goal_version != status_data['goalVersion']:
    for host in status_data['processes']:
      if host['hostname'] == hostname:
        host_found = True
      if status_data['goalVersion'] != host['lastGoalVersionAchieved']:
        return False
    if host_found == False:
      print('Cannot find host in processes list')
      raise IndexError
    return True
  return False

# main
def main():
  try:
    parser = argparse.ArgumentParser(description='Script to perform a database zero-downtime upgrade for the operating system')
    parser.add_argument('--project','-p', dest='context', required=True, help="The context/project to update")
    parser.add_argument('--oh-dear-god', action='store_true', dest='force', help="Option to ignore missing/shutdown nodes in deployment - please do not use!")
    parser.add_argument('--timeout', '-t', dest='timeout', default=10, help="Number of minutes to wait for the configuration to be correct. Default is 10 minutes")
    parser.add_argument('--ssh-user', '-s', dest='ssh_user', default='root', help="SSH user to trigger OS command. Default os `root`")
    parser.add_argument('--ssh-key', '-k', dest='ssh_key', required=True, help="SSH user to trigger OS command")
    parser.add_argument('--ca-cert', dest='ca_cert', default='', required=False, help="Absolute path to CA cert, if required")
    parser.add_argument('--key', dest='ssl_key', default='', required=False, help="Absolute path to SSL key (pem file), if required")
    parser.add_argument('--command', '-c', dest='command_string', default="date;hostname;subscription-manager refresh;rm -rf /var/cache/yum/;yum -y update;echo $?;tail -10 /var/log/yum.log;reboot &>/dev/null & exit", help="The command to trigger for the OS. Default is \"date;hostname;subscription-manager refresh;rm -rf /var/cache/yum/;yum -y update;echo $?;tail -10 /var/log/yum.log;reboot &>/dev/null & exit\"")
    args = parser.parse_args()

    timeout_range = range(args.timeout)
    ca_cert = args.ca_cert
    ssl_key = args.ssl_key

    # Get our Group ID for the Project/Context
    print('Getting current state...')
    id = get('/groups/byName/' + args.context, ca_cert, ssl_key)['id']

    # Retrieve the original/current state of the standalone/replica set/sharded cluster
    ORIGINAL_STATE = get('/groups/' + id + '/automationConfig', ca_cert, ssl_key)

    # If any node is in the `disabled` state throw an error,
    # unless you are using the evil `force` option
    if 'force' not in args:
      initial_check(ORIGINAL_STATE)

    # Get list of hosts for the Project
    hosts = get_list_of_nodes(ORIGINAL_STATE)

    # Cycle through each host to:
    # * disable
    # * check state of all hosts
    # * fire off OS tasking
    # * enable host
    # * check state of all hosts
    # * rinse and repeat for next host
    for host in hosts:
      try:
        print("Reconfiguring automation on %s" % host)

        # Get current GoalState and disable the host of interest
        original_goal_version = get('/groups/' + id + '/automationStatus', ca_cert, ssl_key)['goalVersion']
        tmp_config = disable_node_aa(ORIGINAL_STATE, host)
        put('/groups/' + id + '/automationConfig', tmp_config, ca_cert, ssl_key)

        # trigger flag to determine when tasking has been triggered
        status = False

        # Check config is correct
        for i in timeout_range:
          time.sleep(10)
          aa_status = get('/groups/' + id + '/automationStatus', ca_cert, ssl_key)
          get_status_value = get_status(aa_status, host, original_goal_version)
          print("Automation status up to date: %s" % get_status_value)
          if get_status_value == True:
            status = True
            break
          else:
            print("Waiting for goalVersion to be correct on all hosts...")
        
        # Did we exceed our time limits?
        if status == False:
          print("Time exceeded for %s, exiting" % host)
          exit(1)
        
        print("Performing upgrade tasks for %s" % host)
        # execute the command
        output = subprocess.Popen(['ssh','-o StrictHostKeyChecking=no', '-i', args.ssh_key, args.ssh_user + "@" + host, args.command_string], 
          stdout=subprocess.PIPE, 
          stderr=subprocess.STDOUT)
        stdout,stderr = output.communicate()
        print("STDERR %s" % stderr)
        print("STDOUT %s" % stdout)
        # Wait for reboot to occur
        time.sleep(30)
        print("Reconfiguring automation on %s back to normal" % host)
        task_goal_version = get('/groups/' + id + '/automationStatus', ca_cert, ssl_key)['goalVersion']
        put('/groups/' + id + '/automationConfig', ORIGINAL_STATE, ca_cert, ssl_key)
        # wait for automation to trigger
        time.sleep(15)

        # Check we are back and working
        for j in timeout_range:
          # wait a bit.....
          time.sleep(10)
          # get latest status of hosts
          finish_status = get('/groups/' + id + '/automationStatus', ca_cert, ssl_key)
          finish_status_value = get_status(finish_status, host, task_goal_version)
          # If the config is back to normal jump out the loop, or try again
          if finish_status_value == True:
            print("Host %s should be back online." % host)
            break
          else:
            print("Waiting for %s and service to be back online and goalVersion correct..." % host)
      except requests.exceptions.RequestException as e:
        print("Error: %s. Reconfiguring automation on %s back to normal" % (e, host))
        put('/groups/' + id + '/automationConfig', ORIGINAL_STATE, ca_cert, ssl_key)
        exit(1)
  except requests.exceptions.RequestException as e:
    print("Error %s" % e)
    exit(1)

if __name__ == "__main__": main()