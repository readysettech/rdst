#!/bin/sh
# Generate the bastion's client key once into the shared /keys directory and
# authorize it. The key is test-only and the port binds to loopback; it is
# left readable so the driver, running as another user, can copy it.
set -eu

# Bastions that start together share /keys; the lock lets one create the key
# and the rest authorize that one.
(
  flock 9
  if [ ! -f /keys/id_ed25519 ]; then
    ssh-keygen -q -t ed25519 -N '' -C relentless-bastion -f /keys/id_ed25519
  fi
  chmod 644 /keys/id_ed25519 /keys/id_ed25519.pub
) 9>/keys/.lock

mkdir -p /home/relentless/.ssh
cp /keys/id_ed25519.pub /home/relentless/.ssh/authorized_keys
chown -R relentless:relentless /home/relentless/.ssh
chmod 700 /home/relentless/.ssh
chmod 600 /home/relentless/.ssh/authorized_keys

exec /usr/sbin/sshd -D -e -p 2222 \
  -o AllowTcpForwarding=yes \
  -o PasswordAuthentication=no \
  -o PermitRootLogin=no
