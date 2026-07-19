"""S5d: real Groq whisper-large-v3-turbo latency from this machine.

Measures cold (new connection) vs warm (pooled session) - the spec claims
keeping an HTTP client warm is mandatory because TLS setup costs ~540 ms.
"""
import os, sys, time, statistics, io
import requests
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

KEY = os.environ['GROQ_API_KEY']
URL = 'https://api.groq.com/openai/v1/audio/transcriptions'
MODEL = 'whisper-large-v3-turbo'
HDRS = {'Authorization': f'Bearer {KEY}', 'User-Agent': 'BillyTalk/0.1'}

flac = open(r'C:\BillyTalk\spikes\sample.flac', 'rb').read()
wav = open(r'C:\BillyTalk\spikes\sample.wav', 'rb').read()
print(f"clip: 19.4 s of speech   FLAC {len(flac)/1024:.0f} KB   WAV {len(wav)/1024:.0f} KB\n")

def once(sess, blob, name, mime, extra=None):
    files = {'file': (name, io.BytesIO(blob), mime)}
    data = {'model': MODEL, 'response_format': 'json'}
    if extra:
        data.update(extra)
    t0 = time.perf_counter()
    r = sess.post(URL, headers=HDRS, files=files, data=data, timeout=120)
    ms = (time.perf_counter() - t0) * 1000
    txt = ''
    if r.status_code == 200:
        try:
            txt = r.json().get('text', '')
        except Exception:
            txt = r.text[:120]
    return ms, r.status_code, txt

print("=" * 72)
print("1. COLD - brand new connection each time (DNS + TCP + TLS every call)")
print("=" * 72)
cold = []
for i in range(3):
    with requests.Session() as s:
        ms, code, txt = once(s, flac, 'a.flac', 'audio/flac')
    print(f"  run {i+1}: {ms:7.0f} ms   http {code}")
    if i == 0 and txt:
        print(f'         "{txt.strip()[:140]}"')
    if code == 200:
        cold.append(ms)

print("\n" + "=" * 72)
print("2. WARM - one pooled session, connection reused")
print("=" * 72)
warm = []
with requests.Session() as s:
    once(s, flac, 'a.flac', 'audio/flac')          # prime the pool
    for i in range(5):
        ms, code, _ = once(s, flac, 'a.flac', 'audio/flac')
        print(f"  run {i+1}: {ms:7.0f} ms   http {code}")
        if code == 200:
            warm.append(ms)

print("\n" + "=" * 72)
print("3. FLAC vs WAV on a warm connection (is compression worth it?)")
print("=" * 72)
with requests.Session() as s:
    once(s, flac, 'a.flac', 'audio/flac')
    f = [once(s, flac, 'a.flac', 'audio/flac')[0] for _ in range(3)]
    w = [once(s, wav, 'a.wav', 'audio/wav')[0] for _ in range(3)]
print(f"  FLAC {len(flac)//1024:4d} KB : median {statistics.median(f):6.0f} ms")
print(f"  WAV  {len(wav)//1024:4d} KB : median {statistics.median(w):6.0f} ms")
print(f"  compression saves {statistics.median(w)-statistics.median(f):.0f} ms")

print("\n" + "=" * 72)
print("4. RUSSIAN + the 'prompt' vocabulary-biasing parameter")
print("=" * 72)
with requests.Session() as s:
    once(s, flac, 'a.flac', 'audio/flac')
    m1, c1, t1 = once(s, flac, 'a.flac', 'audio/flac', {'language': 'en'})
    print(f"  language=en, no prompt : {m1:.0f} ms")
    print(f'    "{t1.strip()[:150]}"')
    m2, c2, t2 = once(s, flac, 'a.flac', 'audio/flac',
                      {'language': 'en', 'prompt': 'BillyTalk, Groq, Whisper, Remnawave'})
    print(f"  language=en, WITH prompt: {m2:.0f} ms")
    print(f'    "{t2.strip()[:150]}"')

print("\n" + "=" * 72)
if cold and warm:
    print(f"  COLD median : {statistics.median(cold):6.0f} ms")
    print(f"  WARM median : {statistics.median(warm):6.0f} ms")
    print(f"  warm saves  : {statistics.median(cold)-statistics.median(warm):6.0f} ms per dictation")
    print(f"\n  spec budget was 500-800 ms (for 15 s; this clip is 19.4 s)")
    v = 'WITHIN BUDGET' if statistics.median(warm) <= 1000 else 'OVER BUDGET'
    print(f"  VERDICT: {v}")
