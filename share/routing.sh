#!/bin/sh

if [ "$1" = 192.168.0.3 ]
then
  ip rule add from 192.168.0.3 table t0
  ip route add default via 192.168.0.1 table t0
fi

if [ "$1" = 192.168.2.3 ]
then
  ip rule add from 192.168.2.3 table t1
  ip route add default via 192.168.2.1 table t1
fi


