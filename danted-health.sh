#!/bin/bash
CLOSE_WAIT=$(ss -tn | grep ':1080' | grep 'CLOSE-WAIT' | wc -l)
if [ "$CLOSE_WAIT" -gt 2000 ]; then
    echo "$(date) CLOSE-WAIT=$CLOSE_WAIT — restarting danted-dongle0" >> /var/log/danted-health.log
    systemctl restart danted-dongle0
fi
