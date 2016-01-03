#!/bin/sh

WORKDIR=/home/pi/git/rpi-homerouter/

start() {
  cd "$WORKDIR"/bin/
  exec /usr/local/bin/gunicorn -b 0.0.0.0:80 pinger:app
}

stop() {
  kill $(/bin/ps aux | /usr/bin/awk '/gunicorn.*pinger:[a]pp/ {print $2}')
}

case $1 in
  start|stop) "$1" ;;
esac

