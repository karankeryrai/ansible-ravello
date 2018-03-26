#!/bin/python

import sys
import yaml
import random, string

try:
    from ravello_sdk import *
    HAS_RAVELLO_SDK = True
except ImportError:
    HAS_RAVELLO_SDK = False

except ImportError:
    print "failed=True msg='ravello sdk required for this module'"
    sys.exit(1)

from ravello_cli import get_diskimage

import os
import base64
import getpass
import logging
import logging.handlers
import ansible
import os
import functools
import logging
import io
import datetime
import sys
import yaml
import json
import re
        

from ansible.module_utils.ravello_utils import *
from ansible.module_utils.basic import *
#from ansible.module_utils.facts import *

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
log_capture_string = io.BytesIO()

## Classes to organize VM defaults
## and handle yaml formatting
class HardDrive:
    def __init__(self, **kwargs):
        self.index  = from_kwargs(kwargs, 'index', Exception("index required for hdds"))
        self.name  = from_kwargs(kwargs, 'name', 'vol' + str(self.index))
        self.memory_size = from_kwargs(kwargs, 'size', 40)
        self.memory_unit = from_kwargs(kwargs, 'memory_unit', "GB")
        self.bootable = from_kwargs (kwargs, 'bootable', False)
        self.controller = "virtio"
        self.image =  from_kwargs(kwargs, 'image', '')
        self.device_type = from_kwargs(kwargs, 'device_type', "DISK")
    def to_yaml_dict(self, index):
        hd_yaml = {
            'index': self.index,
            'boot': self.bootable,
            'controller': self.controller,
            'name': self.name,
            'size' : {
                'unit': self.memory_unit,
                'value': self.memory_size
              },
            'type': self.device_type
          }
        if self.image != '':
            hd_yaml['imageName'] = self.image
        return hd_yaml

class Service:
    def __init__(self, **kwargs):
        self.external = from_kwargs(kwargs, 'external', True)
        self.port_range = \
          from_kwargs(kwargs, 
                     'port', 
                      Exception('Missing required field: port'))
        self.protocol = \
            from_kwargs(
                kwargs, 
                'protocol', 
                 Exception('Missing required field: protocol'))
        self.nic = \
            from_kwargs(
                kwargs, 
                'nic', 
                Exception('Missing required field: nic'))
        self.name = \
            from_kwargs(
                kwargs, 
                'name', 
                self.protocol.lower())
    def to_yaml_dict(self):
        return {
           'external': self.external,
           'device': self.nic,
           'name': self.name,
           'portRange': self.port_range,
           'protocol': self.protocol.upper(),
         } 

class NetworkDevice:
    def __init__(self, **kwargs):
        self.name = from_kwargs(kwargs, 
                       'name', 
                       Exception('Missing required field: name'))
        self.controller = from_kwargs(kwargs, 'controller', 'virtio')
        self.ip = from_kwargs(kwargs, 'ip', None)
        # Default public_ip to false.  Ravello will change 
        # this automatically based on configured services.
        self.public_ip = from_kwargs(kwargs, 'public_ip', True)
        self.mac = from_kwargs(kwargs, 'mac', None)
    def to_yaml_dict(self, index):
        yaml = {
          'name': self.name,
          'device' : {
              'index' : index,
              'deviceType' : 'virtio'
            },
          'ipConfig' : {
             'hasPublicIp' : self.public_ip
            }
         }
        reserved_ip = { 'reservedIp' : self.ip }
        if self.ip != None:
          yaml['ipConfig']['autoIpConfig'] = reserved_ip
        else:
          yaml['ipConfig']['autoIpConfig'] = None
        if self.mac != None:
          yaml['device']['useAutomaticMac'] = False
          yaml['device']['mac'] = self.mac
        else:
          yaml['device']['useAutomaticMac'] = True
        return yaml

class Vm:
    def __init__(self, **kwargs):
        # Parse kwargs
        self.name = str(kwargs['index']) + kwargs['name']
        self.tag = kwargs['name']
        self.description = \
           from_kwargs(
               kwargs, 
               'description', 
               "\"" + self.name + "\\nnohbac: true\\n\"")
        self.num_cpus = from_kwargs(kwargs, 'cpus', 1)
        self.memory_size = from_kwargs(kwargs, 'ram', 2)
        self.memory_unit = from_kwargs(kwargs, 'mem_unit', "GB")
        self.keypair_name= from_kwargs(kwargs, 'keypair_name', 'opentlc-admin-backdoor')
        self.keypair_id = from_kwargs(kwargs, 'id', '62226455')
        self.hostnames = \
            from_kwargs(kwargs, 'hostname',
            [self.tag + "-REPL.rhpds.opentlc.com",
            self.tag + ".example.com",
            self.tag])
        disks = from_kwargs(kwargs, 'disks', [{'size' : 40 }])
        self.stop_timeout = 300
        self.public_key = from_kwargs(kwargs, 'public_key', Exception("public key required"))
        self.users = []
        self.hard_drives = []
        self.network_devices = []
        self.services = []
        for i, d in enumerate(disks):
            d['index'] = i
            self.add_hard_drive(**d)
        nics = from_kwargs(kwargs, 'nics', [{'name' : 'eth0'}])
        for n in nics:
            if 'services' in n and isinstance(n['services'], list):
                self.add_network_device(**n)
                for s in n['services']:
                   self.add_service(**s)
        # Add boot disk
        if not filter(lambda hd: hd.bootable, self.hard_drives):
            self.hard_drives[0].image = DEFAULT_BOOT_IMAGE
            self.hard_drives[0].bootable = True
       
    def add_hard_drive(self, **kwargs):
        hd = HardDrive(**kwargs)
        self.hard_drives.append(hd)

    def add_service(self, **kwargs):
        nic = from_kwargs(kwargs, 'nic', 
             self.network_devices[0] \
                 if self.network_devices \
                 else Exception("add_service Error: no nics found"))
        kwargs['nic'] = nic.name
        s = Service(**kwargs)
        self.services.append(s)

    def add_network_device(self, **kwargs):
        nd = NetworkDevice(**kwargs)
        self.network_devices.append(nd)

    def to_yaml(self):
        vm_yaml = {
          'name' : self.name,
          'tag' : self.tag,
          'allowNested': False,
          'preferPhysicalHost' : False,
          'description' : self.description,
          'numCpus' : self.num_cpus, 
          'memorySize': {
             'unit' : self.memory_unit,
             'value' : self.memory_size,
            }, 
          'hostnames' : self.hostnames if isinstance(self.hostnames, list) \
                                       else [self.hostnames],
          'supportsCloudInit' : True,
          'keypairId' : int(self.keypair_id),
          'keypairName' : self.keypair_name,
          'hardDrives' : [hd.to_yaml_dict(i) for i, hd in enumerate(self.hard_drives)],
          'suppliedServices' : [sv.to_yaml_dict() for i, sv in enumerate(self.services)],
          'networkConnections' : [nd.to_yaml_dict(i) for i, nd in enumerate(self.network_devices)],
          'userData' : """\
  #cloud-config
  ssh_pwauth: False
  disable_root: False
  users:
    - name: "cloud-user"
      sudo: ALL=(ALL) NOPASSWD:ALL
      lock_passwd: False
      ssh-authorized-keys:
      - """ + self.public_key

          }
        return vm_yaml

class Template:
    def __init__(self):
        self.vm_list = []
    def add_vm(self, vm):
        self.vm_list.append(vm)
    def to_yaml(self):
      return { "vms" : [vm.to_yaml() for vm in self.vm_list] }

def gen_template(template):
    t = Template()
    for i, instance in enumerate(template):
        instance['index'] = i
        t.add_vm(Vm(**instance))
    return t
            
def main():
    ch = logging.StreamHandler(log_capture_string)
    ch.setLevel(logging.DEBUG)
    ### Optionally add a formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    ### Add the console handler to the logger
    logger.addHandler(ch)    
    argument_spec=dict(
      path=dict(required=True, type='str'),
      instances=dict(required=True, type='list'))

    module = AnsibleModule(
        argument_spec=argument_spec,
        mutually_exclusive=[['blueprint', 'app_template']])
    module_fail.attach_ansible_modle(module)
    filepath = module.params.get('path')
    instances= module.params.get('instances')
    t = gen_template(instances)
    with open(filepath, "w") as f:
        f.write(yaml.safe_dump(t.to_yaml(), default_flow_style=False))
    module.exit_json(msg="Created template: " + filepath)
    
main()

