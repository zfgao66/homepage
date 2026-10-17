#!/usr/bin/env python3
"""Run one authorized SSH maintenance command; credentials come from a pipe."""
import base64
import json
import os
import subprocess
import sys

DRIVER = r'''
log_user 0
set timeout 50
set transcript ""
set auth 0
set sudoauth 0
spawn -noecho /usr/bin/ssh $env(ADMIN_TTY) -p $env(ADMIN_PORT) -o ConnectTimeout=10 -o ConnectionAttempts=1 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=accept-new -o NumberOfPasswordPrompts=1 -o PreferredAuthentications=password,keyboard-interactive -o PubkeyAuthentication=no -o LogLevel=ERROR -l $env(ADMIN_USER) $env(ADMIN_HOST) $env(ADMIN_COMMAND)
match_max 262144
stty raw -echo < $spawn_out(slave,name)
expect {
  -re {(?i)password: ?$} {
    append transcript $expect_out(buffer)
    incr auth
    if {$auth > 1} {puts "Authentication rejected"; exit 71}
    send -- "$env(ADMIN_PASSWORD)\n"
    exp_continue
  }
  -exact {__MONITOR_SUDO__} {
    append transcript $expect_out(buffer)
    incr sudoauth
    if {$sudoauth > 1} {puts "Sudo rejected"; exit 72}
    send -- "$env(ADMIN_PASSWORD)\n"
    exp_continue
  }
  -exact {__MONITOR_DEPLOY_READY__} {
    append transcript $expect_out(buffer)
    send -- "$env(ADMIN_PAYLOAD)\n"
    exp_continue
  }
  timeout {append transcript $expect_out(buffer); set code 74}
  eof {append transcript $expect_out(buffer); set result [wait]; set code [lindex $result 3]}
}
set transcript [string map [list $env(ADMIN_PASSWORD) {<redacted>} $env(ADMIN_PAYLOAD) {<redacted>}] $transcript]
puts $transcript
exit $code
'''

def run(config):
    payload = base64.b64encode(json.dumps(config.get('payload', {})).encode()).decode()
    env = dict(os.environ, ADMIN_HOST=config['host'], ADMIN_PORT=str(config.get('port', 22)),
               ADMIN_USER=config['user'], ADMIN_PASSWORD=config['password'],
               ADMIN_COMMAND=config['command'], ADMIN_PAYLOAD=payload,
               ADMIN_TTY='-tt' if config.get('sudo') else '-T')
    result = subprocess.run(['/usr/bin/expect', '-c', DRIVER], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=config.get('timeout', 75))
    output = result.stdout.replace(config['password'], '<redacted>').replace(payload, '<redacted>')
    return {'host':config['host'], 'exit_code':result.returncode, 'output':output.strip()}

if __name__ == '__main__':
    if sys.stdin.isatty():
        raise SystemExit('Configuration must come from a pipe')
    print(json.dumps(run(json.loads(sys.stdin.readline())), ensure_ascii=False), flush=True)
