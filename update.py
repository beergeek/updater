try:
  import requests
  #from requests import HTTPSession
  import configparser
  from requests.auth import HTTPDigestAuth
  import argparse
  import json
  import time
except ImportError as e:
  print(e)
  exit(1)

parser = argparse.ArgumentParser(description='Script to perform a database zero-downtime upgrade for the operating system')
parser.add_argument('--context','-c', dest='context', required=True, help="The context/project to update")
parser.add_argument('--oh-dear-god', action='store_true', dest='force', help="Option to ignore missing/shutdown nodes in deployment - please do not use!")
parser.add_argument('--timeout', '-t', dest='timeout', default=10, help="Number of minutes to wait for the configuration to be correct")
args = parser.parse_args()

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
  host_found = False
  for i in status_data['processes']:
    if i['hostname'] == hostname:
      host_found = True
    if status_data['goalVersion'] != i['lastGoalVersionAchieved']:
      return False
  if host_found == False:
    print('Cannot find host in processes list')
    raise IndexError

# main
def main():
  try:

    # Get our Group ID for the Project/Context
    id = get('/groups/byName/' + args['context'])['id']

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
        # How many times should we check for all hosts to be in the desired state
        count_range = range(10)
        print("Reconfiguring automation on %s" % host)
        tmp_config = disable_node_aa(original_state, host)
        put('/groups/' + id + '/automationConfig', tmp_config)
        print("Performing upgrade tasks")
        for i in count_range:
          time.sleep(60)
          aa_status = get('/groups/' + id + '/automationStatus')
          if aa_status == True:
            # this is where we actually trigger the upgrade to OS
            break
          else:
            print(aa_status)
      # check the time for the node to be down and that is back up and working before next host
      finally:
        print("Reconfiguring automation on %s back to normal" % host)
        reconfig_aa(original_state, id)
  except requests.exceptions.RequestException as e:
    print(e)
    exit(1)

if __name__ == "__main__": main()