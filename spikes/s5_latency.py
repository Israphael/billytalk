"""S5: measure real STT round-trip latency from this machine.

Key is read from env (AIML_KEY / GROQ_API_KEY) - never stored in this file.
"""
import os, sys, time, json, urllib.request, urllib.error, ssl, statistics
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

FLAC = r'C:\BillyTalk\spikes\sample.flac'
audio = open(FLAC, 'rb').read()
print(f"clip: {len(audio)/1024:.0f} KB FLAC, 19.4 s of speech\n")

def post_multipart(url, key, model, data, extra=None):
    boundary = '----BillyTalkBoundary7MA4YWxkTrZu0gW'
    parts = []
    fields = {'model': model}
    if extra:
        fields.update(extra)
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="a.flac"\r\n'
        f'Content-Type: audio/flac\r\n\r\n'.encode() + data + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    body = b''.join(parts)
    req = urllib.request.Request(url, data=body, method='POST')
    req.add_header('Authorization', f'Bearer {key}')
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    ctx = ssl.create_default_context()
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            payload = r.read()
        return (time.perf_counter() - t0) * 1000, r.status, payload[:600].decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return (time.perf_counter() - t0) * 1000, e.code, e.read()[:600].decode('utf-8', 'replace')
    except Exception as e:
        return (time.perf_counter() - t0) * 1000, -1, f'{type(e).__name__}: {e}'

TARGETS = []
aiml = os.environ.get('AIML_KEY')
if aiml:
    for m in ['whisper-large-v3', 'whisper-large-v3-turbo', '#g1_whisper-large-v3',
              'openai/whisper-large-v3', 'distil-whisper-large-v3-en']:
        TARGETS.append(('aimlapi', 'https://api.aimlapi.com/v1/audio/transcriptions', aiml, m))
groq = os.environ.get('GROQ_API_KEY')
if groq:
    TARGETS.append(('groq-direct', 'https://api.groq.com/openai/v1/audio/transcriptions',
                    groq, 'whisper-large-v3-turbo'))

print("=" * 74)
print("PHASE 1 - which endpoint/model actually answers")
print("=" * 74)
working = []
for name, url, key, model in TARGETS:
    ms, code, body = post_multipart(url, key, model, audio)
    mark = 'OK  ' if code == 200 else 'FAIL'
    print(f"  {mark} {name:12s} {model:28s} {code}  {ms:7.0f} ms")
    if code != 200:
        print(f"        -> {body[:200].strip()}")
    else:
        try:
            txt = json.loads(body).get('text', '')[:110]
        except Exception:
            txt = body[:110]
        print(f'        -> "{txt}..."')
        working.append((name, url, key, model))

if not working:
    print("\nNo working endpoint. Cannot measure latency.")
    raise SystemExit(1)

name, url, key, model = working[0]
N = 5
print("\n" + "=" * 74)
print(f"PHASE 2 - {N} sequential runs on {name} / {model}")
print("=" * 74)
times = []
for i in range(N):
    ms, code, _ = post_multipart(url, key, model, audio)
    print(f"  run {i+1}: {ms:7.0f} ms   (http {code})")
    if code == 200:
        times.append(ms)

if times:
    times_sorted = sorted(times)
    print(f"\n  median : {statistics.median(times):.0f} ms")
    print(f"  min    : {times_sorted[0]:.0f} ms")
    print(f"  max    : {times_sorted[-1]:.0f} ms")
    print(f"\n  spec budget was 500-800 ms for a 15 s clip.")
    print(f"  clip here is 19.4 s.")
    verdict = 'WITHIN BUDGET' if statistics.median(times) <= 1200 else 'OVER BUDGET'
    print(f"  VERDICT: {verdict}")
