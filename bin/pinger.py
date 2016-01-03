import sys
import json
import logging
import os
import textwrap
import time

import gevent
from gevent.wsgi import WSGIServer
from gevent.queue import Queue
from gevent.monkey import patch_all
from gevent.subprocess import Popen, PIPE, STDOUT
patch_all()

from flask import Flask, request, render_template, send_from_directory
app = Flask(__name__)

from threading import Thread

STATE_FILE = '/var/lib/rpi-homerouter/state.json'

LOG_FILE = '/var/log/rpi-homerouter.log'
if not os.path.exists(os.path.dirname( LOG_FILE )):
    os.makedirs( os.path.dirname( LOG_FILE ) )
logging.basicConfig(filename=LOG_FILE, level=logging.DEBUG, format='%(asctime)s %(message)s')

VPN_SERVER = '5.9.243.189'
GW = {
    'eth0': '192.168.2.1',
    'eth1': '192.168.0.1'
}
MAIN_INTERFACE = 'eth1'
BACKUP_INTERFACE = 'eth0'
SLEEP_TIME = 5

def error( text ):
    logging.info(text)

last_logged_text = [""]
def log( text, skip_repetitions=True ):
    if not skip_repetitions or last_logged_text[0] != text:
        logging.info(text)
        last_logged_text[0] = text

def ip_of_interface( iface ):

    cmd = [ '/sbin/ip', 'addr', 'show', 'dev', iface ]

    p = Popen(cmd, shell=False, stdout=PIPE, stderr=STDOUT)
    stdout = p.communicate()[0]

    if p.returncode:
        return None

    for line in stdout.splitlines():
        if ' inet ' in line:
            try:
              return line.strip().split()[1].split('/')[0]
            except:
                return None

def link_alive_icmp( link, remote_ip ):
    """
    Returns if the link is alive, and if it is, returns its ttl.
    If the link is not alive, returns None.
    """
    if link[0] in 'el':
        source_ip = ip_of_interface( link )
        if source_ip is None:
            log("Can't detect IP address of %s" % link)
            return

    cmd = ['/bin/ping', '-I', source_ip, '-c', '1', '-W', '1', remote_ip ]
    p = Popen(cmd, stdout=PIPE, stderr=STDOUT)
    stdout = p.communicate()[0]

    if p.returncode:
        return None

    for line in stdout.splitlines():
        if 'bytes from' in line:
            try:
              return line.strip().split()[6][5:]
            except:
                return None

def link_alive_tcp( link, remote_ip ):
    """
    Returns status of the link.
    If the link is alive, returns its rtt to the remote_ip.
    
    Use this method to check if the link is alive:

        $ nc -v -s 192.168.0.101 -w 1 1.1.1.1 35501 
        nc: connect to 1.1.1.1 port 35501 (tcp) timed out: Operation now in progress
        $ nc -v -s 192.168.0.101 -w 1 5.9.243.189 35501 
        nc: connect to 5.9.243.189 port 35501 (tcp) failed: Connection refused

    """

    if link[0] in 'el':
        source_ip = ip_of_interface( link )
        if source_ip is None:
            log("Can't detect IP address of %s" % link)
            return

    cmd = ['nc', '-v', '-s', source_ip, '-w', '1', remote_ip, '35501' ]
    p = Popen(cmd, stdout=PIPE, stderr=STDOUT)
    stdout = p.communicate()[0]

    if 'Connection refused' in stdout:
        return '1'
    return None

link_alive=link_alive_tcp

def change_default_gw( new_gw ):
    cmds = """
    set -x
    /etc/init.d/openvpn stop
    OLD_GW=`ip route show | awk '/default/{print \$3}'`
    echo OLD_GW=,$OLD_GW,
    [ -z "$OLD_GW" ] || ip route delete default via $OLD_GW
    ip route add default via %(new_gw)s
    /etc/init.d/openvpn start
    """ % locals()

    p = Popen( ["sh", "-s"], shell=False, stdin=PIPE, stdout=PIPE, stderr=STDOUT )
    output = p.communicate( cmds )[0]
    if p.returncode:
        log( 'Non-exit return code. Output:\n' + output )
        return False
    return True

def save_state( state ):
    dirname = os.path.dirname( STATE_FILE )
    if not os.path.exists( dirname ):
        os.makedirs( dirname )
    open(STATE_FILE, 'w').write(
      json.dumps( state, indent=4 )
    )

def load_state():
    if os.path.exists( STATE_FILE ):
        return json.loads( open(STATE_FILE, 'r').read() )
    else:
        return {}

def get_uptime():
    return Popen("uptime", stdout=PIPE, stderr=STDOUT).communicate()[0].split(",")[0].strip()

@app.route("/")
def web_info():
    try:
            uptime = get_uptime()
            data = load_state()
            if data.get('gateway').startswith('192.168.0.'):
                gateway_name = 'radio'
            elif data.get('gateway').startswith('192.168.2.'):
                gateway_name = 'ukrtelekom'

            data.update( locals() )

            return textwrap.dedent("""
            <pre>
            uptime:         %(uptime)s
            gateway:        %(gateway)s (%(gateway_name)s)
            used since:     %(update_time)s
            </pre>
            """ % data)
    except Exception, e:
        print e

def pinger():
    main_if = MAIN_INTERFACE
    backup_if = BACKUP_INTERFACE
    main_if_status = link_alive( main_if, VPN_SERVER )
    backup_if_status = link_alive( backup_if, VPN_SERVER )

    old_gw = GW[MAIN_INTERFACE]
    update_state = True

    # new_gw = GW that must be used now
    # old_gw = GW that has been used before
    while True:
        time.sleep( SLEEP_TIME )
        main_if_status = link_alive( main_if, VPN_SERVER )
        backup_if_status = link_alive( backup_if, VPN_SERVER )

        if update_state:
            save_state({
                main_if:        main_if_status,
                backup_if:      backup_if_status,
                'update_time':  time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                'gateway':      old_gw,
            })
            update_state = False

        if main_if_status is None:
            # main uplink is not functional
            # will switch to the backup if it is ok
            if backup_if_status is None:
                # No, it's also not ok
                # Just waiting
                log( "Both uplinks are not functional. Waiting", skip_repetitions=True )
                continue
            else:
                # if we haven't switched yet,
                # switch now
                new_gw = GW[backup_if]
                if new_gw != old_gw:
                    log( "Trying to switch to the backup uplink: %s [%s,%s]" % (new_gw,backup_if,backup_if_status) )
                    if change_default_gw( new_gw ):
                        log( "Switched to %s [backup]" % new_gw )
                        old_gw = new_gw
                        update_state = True
                else:
                    pass
                    #log( "Using the backup uplink: %s [%s,%s]" % (new_gw, backup_if, backup_if_status) )
        else:
            # main uplink is alive
            # if we have been used the backup switch to the main
            new_gw = GW[main_if]
            if new_gw != old_gw:
                log( "Trying to switch to the main uplink: %s [%s,%s]" % (new_gw, main_if, main_if_status) )
                if change_default_gw( new_gw ):
                    log( "Switched to %s [main]" % new_gw )
                    old_gw = new_gw
                    update_state = True
            else:
                pass
                #log( "Using the main uplink: %s [%s,%s]" % (new_gw, main_if, main_if_status) )

t = Thread( target=pinger)
t.setDaemon( True )
t.start()

#server = WSGIServer(("", 80), app)
#server.serve_forever()

