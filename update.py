try:
  import requests
  #from requests import HTTPSession
  import configparser
  from requests.auth import HTTPDigestAuth
  import argparse
  import json
  import time
  import subprocess
except ImportError as e:
  print(e)
  exit(1)


config = configparser.ConfigParser()
config.read('config.conf')
baseurl = config.get('Ops Manager','baseurl')
username = config.get('Ops Manager', 'username')
token = config.get('Ops Manager','token')

#session = requests.Session()
#session.auth = HTTPDigestAuth(username, token)

def get(endpoint):
  resp = requests.get(baseurl + endpoint, auth=HTTPDigestAuth(username, token), timeout=10)
  if resp.status_code == 200:
    group_id = json.loads(resp.text)
    return group_id
  else:
    print("Response was %s, not `200`" % resp.status_code)
    raise requests.exceptions.RequestException

def put(endpoint, config):
  header = {'Content-Type': 'application/json'}
  resp = requests.put(baseurl + endpoint, auth=HTTPDigestAuth(username, token), timeout=10, data=json.dumps(config), headers=header)
  if resp.status_code == 200:
    return resp
  else:
    print("Response was %s, not `200`" % resp.status_code)
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
  process_tmp = []
  for instance in aa_config['processes']:
    if instance['hostname'] == node:
      instance['disabled'] = True
    else:
      instance['disabled'] = False
    process_tmp.append(instance)
  aa_config['processes'] = process_tmp
  return aa_config

def reconfig_aa(config, project_id):
  header = {'Content-Type': 'application/json'}
  resp = requests.put(baseurl + '/groups/' + project_id + '/automationConfig', auth=HTTPDigestAuth(username, token), timeout=10, data=json.dumps(config), headers=header)
  if resp.status_code == 200:
    return resp
  else:
    print("Response was %s, not `200`" % resp.status_code)
    raise requests.exceptions.RequestException

# We need to determine if all nodes are in the desired state.
# Return `False` for any node found not in the desired state.
def get_status(status_data, hostname):
  host_found = True
  for host in status_data['processes']:
    if host['hostname'] == hostname:
      host_found = True
    if status_data['goalVersion'] != host['lastGoalVersionAchieved']:
      return False
  if host_found == False:
    print('Cannot find host in processes list')
    raise IndexError
  return True

# main
def main():
  try:
    parser = argparse.ArgumentParser(description='Script to perform a database zero-downtime upgrade for the operating system')
    parser.add_argument('--project','-p', dest='context', required=True, help="The context/project to update")
    parser.add_argument('--oh-dear-god', action='store_true', dest='force', help="Option to ignore missing/shutdown nodes in deployment - please do not use!")
    parser.add_argument('--timeout', '-t', dest='timeout', default=10, help="Number of minutes to wait for the configuration to be correct. Default is 10 minutes")
    parser.add_argument('--ssh-user', '-s', dest='ssh_user', default='root', help="SSH user to trigger OS command. Default os `root`")
    parser.add_argument('--ssh-key', '-k', dest='ssh_key', required=True, help="SSH user to trigger OS command")
    parser.add_argument('--command', '-c', dest='command_string', default="date;hostname;subscription-manager refresh;rm -rf /var/cache/yum/;yum -y update;echo $?;tail -10 /var/log/yum.log;reboot &>/dev/null & exit", help="The command to trigger for the OS. Default is \"date;hostname;subscription-manager refresh;rm -rf /var/cache/yum/;yum -y update;echo $?;tail -10 /var/log/yum.log;reboot &>/dev/null & exit\"")
    args = parser.parse_args()

    timeout_range = range(args.timeout)

    # Get our Group ID for the Project/Context
    id = get('/groups/byName/' + args.context)['id']

    # Retrieve the original/current state of the standalone/replica set/sharded cluster
    original_state = get('/groups/' + id + '/automationConfig')

    # If any node is in the `disabled` state throw an error,
    # unless you are using the evil `force` option
    if 'force' not in args:
      initial_check(original_state)

    # Get list of hosts for the Project
    hosts = get_list_of_nodes(original_state)

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
        original_goal_version = get('/groups/' + id + '/automationStatus')['goalVersion']
        print("Original Goal Version: %s" % original_goal_version)
        tmp_config = disable_node_aa(original_state, host)
        put('/groups/' + id + '/automationConfig', tmp_config)

        # trigger flag to determine when tasking has been triggered
        tasking_triggered = False

        # How many times should we check for all hosts to be in the desired state
        for i in timeout_range:
          time.sleep(10)
          aa_status = get('/groups/' + id + '/automationStatus')
          print("Current Goal Version: %s" % aa_status['goalVersion'])
          get_status_value = get_status(aa_status, host)
          print("Automation status up to date: %s" % get_status_value)
          if get_status_value == True:
            if tasking_triggered:
              finish_status = get('/groups/' + id + '/automationStatus')
              finish_status_value = get_status(finish_status, host)
              if finish_status_value == True:
              # check up and running before break
                print("Host %s should be back online." % host)
                break
              else:
                print("Waiting for %s and service to be back online..." % host)
            else:
              print("Performing upgrade tasks for %s" % host)
              # this is where we actually trigger the upgrade to OS
              tasking_triggered = True

              # execute the command
              output = subprocess.Popen(['ssh','-o StrictHostKeyChecking=no', '-i', args.ssh_key, args.ssh_user + "@" + host, args.command_string], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT)
              stdout,stderr = output.communicate()
              print("STDOUT %s" % stdout)
              print("STDERR %s" % stderr)
              # Wait for reboot to occur
              time.sleep(15)
              print("Reconfiguring automation on %s back to normal" % host)
              put('/groups/' + id + '/automationConfig', original_state)
              # wait for automation to trigger
              time.sleep(15)
          else:
            print("Waiting for goalVersion to be correct on all hosts...")
      # check the time for the node to be down and that is back up and working before next host
      except requests.exceptions.RequestException as e:
        print("Error: %s. Reconfiguring automation on %s back to normal" % (e, host))
        put('/groups/' + id + '/automationConfig', original_state)
        exit(1)
  except requests.exceptions.RequestException as e:
    print("Error %s" % e)
    exit(1)

if __name__ == "__main__": main()