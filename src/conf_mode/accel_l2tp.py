#!/usr/bin/env python3
#
# Copyright (C) 2019 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
#

import sys
import os
import re
import subprocess
import jinja2
import socket
import time
import syslog as sl

from vyos.config import Config
from vyos import ConfigError

pidfile = r'/var/run/accel_l2tp.pid'
l2tp_cnf_dir = r'/etc/accel-ppp/l2tp'
chap_secrets = l2tp_cnf_dir + '/chap-secrets'
l2tp_conf = l2tp_cnf_dir + '/l2tp.config'
# accel-pppd -d -c /etc/accel-ppp/l2tp/l2tp.config -p /var/run/accel_l2tp.pid

### config path creation
if not os.path.exists(l2tp_cnf_dir):
  os.makedirs(l2tp_cnf_dir)
  sl.syslog(sl.LOG_NOTICE, l2tp_cnf_dir  + " created")

l2tp_config = '''
### generated by accel_l2tp.py ###
[modules]
log_syslog
l2tp
chap-secrets
{% for proto in authentication['auth_proto']: %}
{{proto}}
{% endfor%}
{% if authentication['mode'] == 'radius' %}
radius
{% endif -%}
ippool
shaper
ipv6pool
ipv6_nd
ipv6_dhcp

[core]
thread-count={{thread_cnt}}

[log]
syslog=accel-l2tp,daemon
copy=1
level=5

{% if dns %}
[dns]
{% if dns[0] %}
dns1={{dns[0]}}
{% endif %}
{% if dns[1] %}
dns2={{dns[1]}}
{% endif %}
{% endif -%}

{% if dnsv6 %}
[ipv6-dns]
{% for srv in dnsv6: %}
{{srv}}
{% endfor %}
{% endif %}

{% if wins %}
[wins]
{% if wins[0] %}
wins1={{wins[0]}}
{% endif %}
{% if wins[1] %}
wins2={{wins[1]}}
{% endif %}
{% endif -%}

[l2tp]
verbose=1
ifname=l2tp%d
ppp-max-mtu={{mtu}}
mppe={{authentication['mppe']}}
{% if outside_addr %}
bind={{outside_addr}}
{% endif %}
{% if lns_shared_secret %}
secret={{lns_shared_secret}}
{% endif %}

[client-ip-range]
0.0.0.0/0

{% if (client_ip_pool) or (client_ip_subnets) %}
[ip-pool]
{% if client_ip_pool %}
{{client_ip_pool}}
{% endif -%}
{% if client_ip_subnets %}
{% for sn in client_ip_subnets %}
{{sn}}
{% endfor -%}
{% endif %}
{% endif %}
{% if outside_nexthop %}
gw-ip-address={{outside_nexthop}}
{% endif %}

{% if authentication['mode'] == 'local' %}
[chap-secrets]
chap-secrets=/etc/accel-ppp/l2tp/chap-secrets
{% endif %}

[ppp]
verbose=1
check-ip=1
single-session=replace
{% if idle_timeout %}
lcp-echo-timeout={{idle_timeout}}
{% endif %}
{% if ppp_options['lcp-echo-interval'] %}
lcp-echo-interval={{ppp_options['lcp-echo-interval']}}
{% else %}
lcp-echo-interval=30
{% endif %}
{% if ppp_options['lcp-echo-failure'] %}
lcp-echo-failure={{ppp_options['lcp-echo-failure']}}
{% else %}
lcp-echo-failure=3
{% endif %}
{% if ccp_disable %}
ccp=0
{% endif %}
{% if client_ipv6_pool %}
ipv6=allow
{% endif %}

{% if authentication['mode'] == 'radius' %}
[radius]
{% for rsrv in authentication['radiussrv']: %}
server={{rsrv}},{{authentication['radiussrv'][rsrv]['secret']}},\
req-limit={{authentication['radiussrv'][rsrv]['req-limit']}},\
fail-time={{authentication['radiussrv'][rsrv]['fail-time']}}
{% endfor %}
{% if authentication['radiusopt']['timeout'] %}
timeout={{authentication['radiusopt']['timeout']}}
{% endif %}
{% if authentication['radiusopt']['acct-timeout'] %}
acct-timeout={{authentication['radiusopt']['acct-timeout']}}
{% endif %}
{% if authentication['radiusopt']['max-try'] %}
max-try={{authentication['radiusopt']['max-try']}}
{% endif %}
{% if authentication['radiusopt']['nas-id'] %}
nas-identifier={{authentication['radiusopt']['nas-id']}}
{% endif %}
{% if authentication['radius_source_address'] %}
nas-ip-address={{authentication['radius_source_address']}}
{% endif -%}
{% if authentication['radiusopt']['dae-srv'] %}
dae-server={{authentication['radiusopt']['dae-srv']['ip-addr']}}:\
{{authentication['radiusopt']['dae-srv']['port']}},\
{{authentication['radiusopt']['dae-srv']['secret']}}
{% endif -%}
gw-ip-address={{outside_nexthop}}
verbose=1
{% endif -%}

{% if client_ipv6_pool %}
[ipv6-pool]
{% for prfx in client_ipv6_pool.prefix: %}
{{prfx}}
{% endfor %}
{% for prfx in client_ipv6_pool.delegate_prefix: %}
delegate={{prfx}}
{% endfor %}
{% endif %}

{% if client_ipv6_pool['delegate_prefix'] %}
[ipv6-dhcp]
verbose=1
{% endif %}

{% if authentication['radiusopt']['shaper'] %}
[shaper]
verbose=1
attr={{authentication['radiusopt']['shaper']['attr']}}
{% if authentication['radiusopt']['shaper']['vendor'] %}
vendor={{authentication['radiusopt']['shaper']['vendor']}}
{% endif -%}
{% endif %}

[cli]
tcp=127.0.0.1:2004
sessions-columns=ifname,username,calling-sid,ip,{{ip6_column}}{{ip6_dp_column}}rate-limit,type,comp,state,rx-bytes,tx-bytes,uptime

'''

### l2tp chap secrets
chap_secrets_conf = '''
# username  server  password  acceptable local IP addresses shaper
{% for user in authentication['local-users'] %}
{% if authentication['local-users'][user]['state'] == 'enabled' %}
{% if (authentication['local-users'][user]['upload']) and (authentication['local-users'][user]['download']) %}
{{user}}\t*\t{{authentication['local-users'][user]['passwd']}}\t{{authentication['local-users'][user]['ip']}}\t\
{{authentication['local-users'][user]['download']}}/{{authentication['local-users'][user]['upload']}}
{% else %}
{{user}}\t*\t{{authentication['local-users'][user]['passwd']}}\t{{authentication['local-users'][user]['ip']}}
{% endif %}
{% endif %}
{% endfor %}
'''

###
# inline helper functions
###
# depending on hw and threads, daemon needs a little to start
# if it takes longer than 100 * 0.5 secs, exception is being raised
# not sure if that's the best way to check it, but it worked so far quite well 
###
def chk_con():
  cnt = 0
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  while True:
    try:
      s.connect(("127.0.0.1", 2004))
      break
    except ConnectionRefusedError:
      time.sleep(0.5)
      cnt +=1
      if cnt == 100:
        raise("failed to start l2tp server")
        break

### chap_secrets file if auth mode local
def write_chap_secrets(c):
  tmpl = jinja2.Template(chap_secrets_conf, trim_blocks=True)
  chap_secrets_txt = tmpl.render(c)
  old_umask = os.umask(0o077)
  open(chap_secrets,'w').write(chap_secrets_txt)
  os.umask(old_umask)
  sl.syslog(sl.LOG_NOTICE, chap_secrets + ' written')

def accel_cmd(cmd=''):
  if not cmd:
    return None
  try:
    ret = subprocess.check_output(['/usr/bin/accel-cmd','-p','2004',cmd]).decode().strip()
    return ret
  except:
    return 1

### 
# inline helper functions end
###

def get_config():
  c = Config()
  if not c.exists('vpn l2tp remote-access '):
    return None

  c.set_level('vpn l2tp remote-access')
  config_data = {
    'authentication'    : {
        'mode'            : 'local',
        'local-users'     : {
        },
    'radiussrv'           : {},
    'radiusopt'           : {},
    'auth_proto'          : [],
    'mppe'                : 'prefer'
    },
    'outside_addr'        : '',
    'outside_nexthop'     : '',
    'dns'                 : [],
    'dnsv6'               : [],
    'wins'                : [],
    'client_ip_pool'      : None,
    'client_ip_subnets'   : [],
    'client_ipv6_pool'    : {},
    'mtu'                 : '1436',
    'ip6_column'          : '',
    'ip6_dp_column'       : '',
    'ppp_options'         : {},
  }

  ### general options ###

  if c.exists('dns-servers server-1'):
    config_data['dns'].append( c.return_value('dns-servers server-1'))
  if c.exists('dns-servers server-2'):
    config_data['dns'].append( c.return_value('dns-servers server-2'))
  if c.exists('dnsv6-servers'):
    for dns6_server in c.return_values('dnsv6-servers'):
      config_data['dnsv6'].append(dns6_server)
  if c.exists('wins-servers server-1'):
    config_data['wins'].append( c.return_value('wins-servers server-1'))
  if c.exists('wins-servers server-2'):
    config_data['wins'].append( c.return_value('wins-servers server-2'))
  if c.exists('outside-address'):
    config_data['outside_addr'] = c.return_value('outside-address')

  ### auth local 
  if c.exists('authentication mode local'):
    if c.exists('authentication local-users username'):
      for usr in c.list_nodes('authentication local-users username'):
        config_data['authentication']['local-users'].update(
          {
            usr : {
              'passwd' : '',
              'state'  : 'enabled',
              'ip'     : '*',
              'upload'    : None,
              'download'  : None
            }
          }
        )

        if c.exists('authentication local-users username ' + usr + ' password'):
          config_data['authentication']['local-users'][usr]['passwd'] = c.return_value('authentication local-users username ' + usr + ' password')
        if c.exists('authentication local-users username ' + usr + ' disable'):
          config_data['authentication']['local-users'][usr]['state'] = 'disable'
        if c.exists('authentication local-users username ' + usr + ' static-ip'):
          config_data['authentication']['local-users'][usr]['ip'] = c.return_value('authentication local-users username ' + usr + ' static-ip')
        if c.exists('authentication local-users username ' + usr + ' rate-limit download'):
          config_data['authentication']['local-users'][usr]['download'] = c.return_value('authentication local-users username ' + usr + ' rate-limit download')
        if c.exists('authentication local-users username ' + usr + ' rate-limit upload'):
          config_data['authentication']['local-users'][usr]['upload'] = c.return_value('authentication local-users username ' + usr + ' rate-limit upload')

  ### authentication mode radius servers and settings

  if c.exists('authentication mode radius'):
    config_data['authentication']['mode'] = 'radius'
    rsrvs = c.list_nodes('authentication radius server')
    for rsrv in rsrvs:
      if c.return_value('authentication radius server ' + rsrv + ' fail-time') == None:
        ftime = '0'
      else:
        ftime = str(c.return_value('authentication radius server ' + rsrv + ' fail-time'))
      if c.return_value('authentication radius-server ' + rsrv + ' req-limit') == None:
        reql = '0'
      else:
        reql = str(c.return_value('authentication radius server ' + rsrv + ' req-limit'))

      config_data['authentication']['radiussrv'].update(
        {
          rsrv  : {
            'secret'  : c.return_value('authentication radius server ' + rsrv + ' key'),
            'fail-time' : ftime,
            'req-limit' : reql
            }
        }
      )
    ### Source ip address feature
    if c.exists('authentication radius source-address'):
      config_data['authentication']['radius_source_address'] = c.return_value('authentication radius source-address')

    #### advanced radius-setting
    if c.exists('authentication radius acct-timeout'):
      config_data['authentication']['radiusopt']['acct-timeout'] = c.return_value('authentication radius acct-timeout')
    if c.exists('authentication radius max-try'):
      config_data['authentication']['radiusopt']['max-try'] = c.return_value('authentication radius max-try')
    if c.exists('authentication radius timeout'):
      config_data['authentication']['radiusopt']['timeout'] = c.return_value('authentication radius timeout')
    if c.exists('authentication radius nas-identifier'):
      config_data['authentication']['radiusopt']['nas-id'] = c.return_value('authentication radius nas-identifier')
    if c.exists('authentication radius dae-server'):
      # Set default dae-server port if not defined
      if c.exists('authentication radius dae-server port'):
        dae_server_port = c.return_value('authentication radius dae-server port')
      else:
        dae_server_port = "3799"
      config_data['authentication']['radiusopt'].update(
        {
          'dae-srv' : {
            'ip-addr' : c.return_value('authentication radius dae-server ip-address'),
            'port'    : dae_server_port,
            'secret'  : str(c.return_value('authentication radius dae-server secret'))
          }
        }
      )
    #### filter-id is the internal accel default if attribute is empty
    #### set here as default for visibility which may change in the future
    if c.exists('authentication radius rate-limit enable'):
      if not c.exists('authentication radius rate-limit attribute'):
        config_data['authentication']['radiusopt']['shaper'] = {
          'attr'  : 'Filter-Id'
        }
      else:
        config_data['authentication']['radiusopt']['shaper'] = {
        'attr'  : c.return_value('authentication radius rate-limit attribute')
        }
      if c.exists('authentication radius rate-limit vendor'):
        config_data['authentication']['radiusopt']['shaper']['vendor'] = c.return_value('authentication radius rate-limit vendor')

  if c.exists('client-ip-pool'):
    if c.exists('client-ip-pool start') and c.exists('client-ip-pool stop'):
      config_data['client_ip_pool'] = c.return_value('client-ip-pool start') + '-' + re.search('[0-9]+$', c.return_value('client-ip-pool stop')).group(0)

  if c.exists('client-ip-pool subnet'):
    config_data['client_ip_subnets'] = c.return_values('client-ip-pool subnet')

  if c.exists('client-ipv6-pool prefix'):
    config_data['client_ipv6_pool']['prefix'] = c.return_values('client-ipv6-pool prefix')
    config_data['ip6_column'] = 'ip6,'
  if c.exists('client-ipv6-pool delegate-prefix'):
    config_data['client_ipv6_pool']['delegate_prefix'] = c.return_values('client-ipv6-pool delegate-prefix')
    config_data['ip6_dp_column'] = 'ip6-dp,'

  if c.exists('mtu'):
    config_data['mtu'] = c.return_value('mtu')

  ### gateway address 
  if c.exists('outside-nexthop'):
    config_data['outside_nexthop'] = c.return_value('outside-nexthop') 
  
  if c.exists('authentication require'):
    auth_mods = {'pap' : 'pap','chap' : 'auth_chap_md5', 'mschap' : 'auth_mschap_v1', 'mschap-v2' : 'auth_mschap_v2'}
    for proto in c.return_values('authentication require'):
      config_data['authentication']['auth_proto'].append(auth_mods[proto])
  else:
    config_data['authentication']['auth_proto'] = ['auth_mschap_v2']

  if c.exists('authentication mppe'):
    config_data['authentication']['mppe'] = c.return_value('authentication mppe')

  if c.exists('idle'):
    config_data['idle_timeout'] = c.return_value('idle')

  ### LNS secret
  if c.exists('lns shared-secret'):
    config_data['lns_shared_secret'] = c.return_value('lns shared-secret')

  if c.exists('ccp-disable'):
    config_data['ccp_disable'] = True

  ### ppp_options
  ppp_options = {}
  if c.exists('ppp-options'):
    if c.exists('ppp-options lcp-echo-failure'):
      ppp_options['lcp-echo-failure'] = c.return_value('ppp-options lcp-echo-failure')
    if c.exists('ppp-options lcp-echo-interval'):
      ppp_options['lcp-echo-interval'] = c.return_value('ppp-options lcp-echo-interval')

  if len(ppp_options) !=0:
    config_data['ppp_options'] = ppp_options

  return config_data

def verify(c):
  if c == None:
    return None

  if c['authentication']['mode'] == 'local':
    if not c['authentication']['local-users']:
      raise ConfigError('l2tp-server authentication local-users required')
    for usr in c['authentication']['local-users']:
      if not c['authentication']['local-users'][usr]['passwd']:
        raise ConfigError('user ' + usr + ' requires a password')

  if c['authentication']['mode'] == 'radius':
    if len(c['authentication']['radiussrv']) == 0:
      raise ConfigError('radius server required')
    for rsrv in c['authentication']['radiussrv']:
      if c['authentication']['radiussrv'][rsrv]['secret'] == None:
        raise ConfigError('radius server ' + rsrv + ' needs a secret configured')

  ### check for the existence of a client ip pool
  if not c['client_ip_pool'] and not c['client_ip_subnets']:
    raise ConfigError("set vpn l2tp remote-access client-ip-pool requires subnet or start/stop IP pool")

  if not c['outside_nexthop']:
    #raise ConfigError('set vpn l2tp remote-access outside-nexthop required')
    print ("WARMING: set vpn l2tp remote-access outside-nexthop required")

  ## check ipv6
  if 'delegate_prefix' in c['client_ipv6_pool'] and not 'prefix' in c['client_ipv6_pool']:
    raise ConfigError("\"set vpn l2tp remote-access client-ipv6-pool prefix\" required for delegate-prefix ")

  if len(c['dnsv6']) > 3:
    raise ConfigError("Maximum allowed dnsv6-servers addresses is 3")

def generate(c):
  if c == None:
    return None
  
  ### accel-cmd reload doesn't work so any change results in a restart of the daemon
  try:
    if os.cpu_count() == 1:
      c['thread_cnt'] = 1
    else:
      c['thread_cnt'] = int(os.cpu_count()/2)
  except KeyError:
    if os.cpu_count() == 1:
      c['thread_cnt'] = 1
    else:
      c['thread_cnt'] = int(os.cpu_count()/2)

  tmpl = jinja2.Template(l2tp_config, trim_blocks=True)
  config_text = tmpl.render(c)
  open(l2tp_conf,'w').write(config_text)

  if c['authentication']['local-users']:
    write_chap_secrets(c)

  return c

def apply(c):
  if c == None:
    if os.path.exists(pidfile):
      accel_cmd('shutdown hard')
      if os.path.exists(pidfile):
        os.remove(pidfile)
    return None

  if not os.path.exists(pidfile):
    ret = subprocess.call(['/usr/sbin/accel-pppd','-c',l2tp_conf,'-p',pidfile,'-d'])
    chk_con()
    if ret !=0 and os.path.exists(pidfile):
      os.remove(pidfile)
      raise ConfigError('accel-pppd failed to start')
  else:
    ### if gw ip changes, only restart doesn't work
    accel_cmd('restart')
    sl.syslog(sl.LOG_NOTICE, "reloading config via daemon restart")

if __name__ == '__main__':
  try:
    c = get_config()
    verify(c)
    generate(c)
    apply(c)
  except ConfigError as e:
    print(e)
    sys.exit(1)
