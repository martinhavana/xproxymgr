#!/bin/bash
# Automatyczna aktualizacja whitelist proxy gdy zmieni się IP DuckDNS
# Zainstaluj: cp update-proxy-whitelist.sh /usr/local/bin/
# Cron (co minutę): * * * * * /usr/local/bin/update-proxy-whitelist.sh

DUCKDNS_HOST="havanawin.duckdns.org"
NEW_IP=$(dig +short "$DUCKDNS_HOST" | head -1)

if [ -z "$NEW_IP" ]; then
    echo "$(date): Nie udało się pobrać IP z DuckDNS" >> /var/log/proxy-whitelist.log
    exit 1
fi

# Sprawdź czy to IP już jest w iptables
CURRENT=$(iptables -L INPUT -n | grep 'dpt:1080' | grep ACCEPT | grep -v '192.168.1.0' | grep -v '127.0.0.1' | awk '{print $4}' | head -1)

if [ "$CURRENT" = "$NEW_IP" ]; then
    # IP się nie zmieniło - nic nie rób
    exit 0
fi

echo "$(date): IP zmieniło się z $CURRENT na $NEW_IP - aktualizuję whitelist" >> /var/log/proxy-whitelist.log

# Usuń stare reguły dla poprzedniego publicznego IP
if [ -n "$CURRENT" ]; then
    iptables -D INPUT -p tcp --dport 1080 -s "$CURRENT" -j ACCEPT 2>/dev/null
    iptables -D INPUT -p tcp --dport 1081 -s "$CURRENT" -j ACCEPT 2>/dev/null
fi

# Dodaj nowe reguły PRZED regułami DROP (pozycja 3 i 4)
iptables -I INPUT 3 -p tcp --dport 1080 -s "$NEW_IP" -j ACCEPT
iptables -I INPUT 4 -p tcp --dport 1081 -s "$NEW_IP" -j ACCEPT

# Zapisz na stałe
iptables-save > /etc/iptables/rules.v4

echo "$(date): Whitelist zaktualizowany -> $NEW_IP" >> /var/log/proxy-whitelist.log
