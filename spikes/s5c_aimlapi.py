"""S5c: real end-to-end latency of aimlapi's async STT (submit + poll)."""
import os, sys, time, json, base64, subprocess, tempfile, statistics
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

KEY = os.environ['AIML_KEY']
FLAC = r'C:\BillyTalk\spikes\sample.flac'
UA = 'BillyTalk/0.1'

def curl(args, timeout=180):
    out = tempfile.mktemp(suffix='.json')
    p = subprocess.run(['curl.exe', '-s', '-o', out, '-w', '%{http_code}'] + args,
                       capture_output=True, text=True, timeout=timeout)
    code = p.stdout.strip()
    try:
        body = open(out, encoding='utf-8').read(); os.unlink(out)
    except Exception:
        body = ''
    return code, body

def run_once(model, poll_interval=0.15):
    t0 = time.perf_counter()
    code, body = curl([
        '-H', f'Authorization: Bearer {KEY}', '-H', f'User-Agent: {UA}',
        '-F', f'model={model}', '-F', f'audio=@{FLAC};type=audio/flac',
        'https://api.aimlapi.com/v1/stt/create'])
    t_submit = (time.perf_counter() - t0) * 1000
    if code not in ('200', '201'):
        return None, f'submit http {code}: {body[:200]}', t_submit, 0, 0
    try:
        gid = json.loads(body).get('generation_id') or json.loads(body).get('id')
    except Exception:
        return None, f'no generation_id in {body[:200]}', t_submit, 0, 0
    if not gid:
        return None, f'no generation_id in {body[:200]}', t_submit, 0, 0

    polls = 0
    while time.perf_counter() - t0 < 120:
        time.sleep(poll_interval)
        polls += 1
        c2, b2 = curl(['-H', f'Authorization: Bearer {KEY}', '-H', f'User-Agent: {UA}',
                       f'https://api.aimlapi.com/v1/stt/{gid}'])
        if c2 != '200':
            continue
        try:
            j = json.loads(b2)
        except Exception:
            continue
        st = j.get('status')
        if st in ('completed', 'succeeded', 'success'):
            total = (time.perf_counter() - t0) * 1000
            txt = ''
            r = j.get('result') or j
            if isinstance(r, dict):
                txt = r.get('text') or ''
                if not txt:
                    try:
                        txt = r['results']['channels'][0]['alternatives'][0]['transcript']
                    except Exception:
                        txt = json.dumps(r)[:120]
            return total, txt, t_submit, polls, total - t_submit
        if st in ('failed', 'error'):
            return None, f'job failed: {b2[:200]}', t_submit, polls, 0
    return None, 'timeout after 120s', t_submit, polls, 0

for model in ['nova-3', 'whisper-large', 'gpt-4o-transcribe']:
    print(f"\n{'='*70}\n{model}\n{'='*70}")
    times = []
    for i in range(3):
        total, txt, t_sub, polls, t_poll = run_once(model)
        if total is None:
            print(f"  run {i+1}: FAILED - {txt}")
            break
        print(f"  run {i+1}: total {total:7.0f} ms  (submit {t_sub:5.0f} + wait {t_poll:6.0f}, {polls} polls)")
        if i == 0:
            print(f'         "{txt[:120]}"')
        times.append(total)
    if times:
        print(f"  --> median {statistics.median(times):.0f} ms for 19.4 s of speech")
