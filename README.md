# Updater

This script allows the user to perform a rolling operating system patching task to a MongoDB replica set.

The script interacts with Ops Manager to perform the rolling disabling and shutting down of the `mongod` instance, after which the operating system tasking is performed. At the end of the tasking Ops Manager will initiate a restart of the `mongod` instance.

# Usage

You will need to do API whitelisting within Ops Manager (the user's account) for this script.

There is a required config file `config.conf` that needs to be in the same directory as the Python script, with the following:

```shell
[Ops Manager]
baseurl=<URL OF OPS MANAGER, WITH PORT>
username=<USERNAME FOR OPS MANAGER>
token=<ACCESS TOKEN FOR USER>
```

```shell
[ user@node ~]$ python update.py -h
usage: update.py [-h] --project CONTEXT [--oh-dear-god] [--timeout TIMEOUT]
                 [--ssh-user SSH_USER] --ssh-key SSH_KEY [--ca-cert CA_CERT]
                 [--key SSL_KEY] [--command COMMAND_STRING]

Script to perform a database zero-downtime upgrade for the operating system

optional arguments:
  -h, --help            show this help message and exit
  --project CONTEXT, -p CONTEXT
                        The context/project to update
  --oh-dear-god         Option to ignore missing/shutdown nodes in deployment
                        - please do not use!
  --timeout TIMEOUT, -t TIMEOUT
                        Number of minutes to wait for the configuration to be
                        correct. Default is 10 minutes
  --ssh-user SSH_USER, -s SSH_USER
                        SSH user to trigger OS command. Default os `root`
  --ssh-key SSH_KEY, -k SSH_KEY
                        SSH user to trigger OS command
  --ca-cert CA_CERT     Absolute path to CA cert, if required
  --key SSL_KEY         Absolute path to SSL key (pem file), if required
  --command COMMAND_STRING, -c COMMAND_STRING
                        The command to trigger for the OS. Default is
                        "date;hostname;subscription-manager refresh;rm -rf
                        /var/cache/yum/;yum -y update;echo $?;tail -10
                        /var/log/yum.log;reboot &>/dev/null & exit"
```
