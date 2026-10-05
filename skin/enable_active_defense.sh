#!/bin/bash
# One-time setup: let the skin firewall act without asking for a password.
# The app calls this with pkexec (a password prompt appears once, then never again).
set -e
echo 'a ALL=(ALL) NOPASSWD: /usr/sbin/iptables, /usr/bin/kill, /bin/kill' > /etc/sudoers.d/blackhorse-skin
chmod 440 /etc/sudoers.d/blackhorse-skin
visudo -c
sed -i 's/Environment=BLACKHORSE_DRY_RUN=true/Environment=BLACKHORSE_DRY_RUN=false/' /home/a/.config/systemd/user/blackhorse-defense.service || true
systemctl --user daemon-reload || true
echo "Active defense enabled."
