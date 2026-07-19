"""S5b: latency + quality across the STT models aimlapi actually offers."""
import os, sys, time, json, statistics, subprocess, tempfile
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

FLAC = r'C:\BillyTalk\spikes\sample.flac'
KEY = os.environ['AIML_KEY']
URL = 'https://api.aimlapi.com/v1/audio/transcriptions'

MODELS = ['openai/gpt-4o-transcribe', 'openai/gpt-4o-mini-transcribe',
          'deepgram/nova-3', 'deepgram/whisper-large', 'mistralai/voxtral-small-24b-2507']

def call(model, path):
    """curl avoids the Cloudflare signature block that killed urllib."""
    out = tempfile.mktemp(suffix='.json')
    t0 = time.perf_counter()
    p = subprocess.run(
        ['curl.exe', '-s', '-o', out, '-w', '%{http_code}',
         '-H', f'Authorization: Bearer {KEY}',
         '-H', 'User-Agent: BillyTalk/0.1',
         '-F', f'model={model}',
         '-F', f'file=@{path};type=audio/flac',
         URL],
        capture_output=True, text=True, timeout=180)
    ms = (time.perf_counter() - t0) * 1000
    code = p.stdout.strip()
    try:
        body = open(out, encoding='utf-8').read()
        os.unlink(out)
    except Exception:
        body = ''
    return ms, code, body

def extract(body):
    try:
        j = json.loads(body)
    except Exception:
        return body[:150]
    for k in ('text', 'transcript'):
        if isinstance(j.get(k), str):
            return j[k]
    r = j.get('results')
    if isinstance(r, dict):
        try:
            return r['channels'][0]['alternatives'][0]['transcript']
        except Exception:
            pass
    return json.dumps(j)[:150]

print("=" * 74)
print("PHASE 1 - which models answer, and what do they hear")
print("=" * 74)
ok = []
for m in MODELS:
    ms, code, body = call(m, FLAC)
    if code == '200':
        txt = extract(body)
        print(f"  OK   {m:34s} {ms:7.0f} ms")
        print(f'         "{txt[:130]}"')
        ok.append(m)
    else:
        print(f"  FAIL {m:34s} http {code}  {ms:6.0f} ms")
        print(f"         {body[:160].strip()}")

if not ok:
    raise SystemExit("nothing worked")

print("\n" + "=" * 74)
print("PHASE 2 - 4 sequential runs each (warm connection)")
print("=" * 74)
for m in ok:
    ts = []
    for _ in range(4):
        ms, code, _ = call(m, FLAC)
        if code == '200':
            ts.append(ms)
    if ts:
        print(f"  {m:34s} median {statistics.median(ts):6.0f} ms   "
              f"min {min(ts):6.0f}   max {max(ts):6.0f}")

print("\nclip = 19.4 s of speech. Spec budget was 500-800 ms for 15 s.")
