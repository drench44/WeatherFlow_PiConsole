"""The board must run for a LAN viewer on plain http, not only the localhost kiosk.

crypto.randomUUID exists only in a secure context (https or localhost). Called
bare at top level it throws for http://<pi-ip>:<port>/, the board script stops,
and the page keeps the artboard's sample numbers with no visible error.
"""
import json
import re
import subprocess

from tests.test_radar_v46 import HTML, function

SESSION = r'[A-Za-z0-9-]{16,64}'  # serve.py _camera_transaction's radarSession check
UUID4 = r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'


def test_every_random_uuid_call_is_guarded():
    calls = list(re.finditer(r'(\w+)\.randomUUID\(', HTML))
    assert calls, 'the session id must still prefer crypto.randomUUID'
    for call in calls:
        guard = HTML[max(0, call.start()-200):call.start()]
        assert f"typeof {call[1]}.randomUUID==='function'" in guard, HTML[call.start()-80:call.end()+40]
    assert 'getRandomValues(' in function('radarSessionId')
    assert 'session:radarSessionId()' in HTML


def run(crypto):
    script = function('radarSessionId') + '''
const window={crypto:CRYPTO};
if(window.crypto&&window.crypto.getRandomValues===true)
  window.crypto.getRandomValues=a=>require('crypto').webcrypto.getRandomValues(a);
const ids=Array.from({length:64},radarSessionId);
console.log(JSON.stringify(ids));
'''.replace('CRYPTO', crypto)
    return json.loads(subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True).stdout)


def check(ids, pattern=UUID4):
    assert len(set(ids)) == len(ids)
    for value in ids:
        assert re.fullmatch(SESSION, value), value  # accepted as a radar owner
        assert re.fullmatch(pattern, value), value


def test_secure_context_uses_random_uuid():
    ids = run("{randomUUID:()=>'0123456789abcdef-'+Math.random().toString(16).slice(2,10)}")
    check(ids, r'0123456789abcdef-[0-9a-f]+')


def test_plain_http_falls_back_to_get_random_values():
    # Browsers keep crypto.getRandomValues outside secure contexts.
    check(run('{getRandomValues:true}'))


def test_no_crypto_still_yields_a_session():
    check(run('undefined'))
