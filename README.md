## Routing setup

You can add it to /etc/network/interfaces or in oher network initialization files:

    ip rule add from 192.168.0.3 table t0
    ip rule add from 192.168.2.3 table t1
    ip route add default via 192.168.0.1 table t0
    ip route add default via 192.168.2.1 table t1

## Installation

    apt-get install python-dev
    pip install -r requirements.txt

