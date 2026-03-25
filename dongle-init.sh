#!/bin/bash
# Wymusza reinicjalizację dongle interfejsów przy starcie
# Uruchamiane przez dongle-init.service

sleep 10  # poczekaj aż system wstanie

for iface in eth1 eth2 eth3; do
    [ -d /sys/class/net/$iface ] || continue
    ip=$(ip -4 addr show $iface | grep 'inet ' | awk '{print $2}' | cut -d/ -f1)
    case "$ip" in
        192.168.10[1-9].*|192.168.1[0-9][0-9].*)
            echo "$(date) Reinit $iface ($ip)"
            ip link set $iface down
            sleep 3
            ip link set $iface up
            sleep 5
            # Ustaw MTU dla True dongla
            case "$ip" in
                192.168.103.*) ip link set $iface mtu 1280 ;;
            esac
            ;;
    esac
done

# Uruchom routing skrypt po reinit
bash /etc/networkd-dispatcher/routable.d/50-eth1-policy-routing
echo "$(date) dongle-init done"
