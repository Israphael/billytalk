"""S5e: verify the two surprising results, and test Russian.

1. Is WAV really faster than FLAC? (larger sample, interleaved to cancel drift)
2. Russian transcription quality + the prompt parameter on RU tech terms
"""
import os, sys, time, statistics, io
import requests, soundfile as sf
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

KEY = os.environ['GROQ_API_KEY']
URL = 'https://api.groq.com/openai/v1/audio/transcriptions'
MODEL = 'whisper-large-v3-turbo'
HDRS = {'Authorization': f'Bearer {KEY}', 'User-Agent': 'BillyTalk/0.1'}
SP = r'C:\BillyTalk\spikes'

# build the RU variants
d, r = sf.read(rf'{SP}\sample_ru.wav')
sf.write(rf'{SP}\sample_ru.flac', d, r, format='FLAC', subtype='PCM_16')
ru_wav = open(rf'{SP}\sample_ru.wav', 'rb').read()
ru_flac = open(rf'{SP}\sample_ru.flac', 'rb').read()
en_wav = open(rf'{SP}\sample.wav', 'rb').read()
en_flac = open(rf'{SP}\sample.flac', 'rb').read()
print(f"RU clip: {len(d)/r:.1f}s   WAV {len(ru_wav)//1024} KB   FLAC {len(ru_flac)//1024} KB\n")

def call(sess, blob, name, mime, extra=None):
    files = {'file': (name, io.BytesIO(blob), mime)}
    data = {'model': MODEL, 'response_format': 'json'}
    if extra:
        data.update(extra)
    t0 = time.perf_counter()
    resp = sess.post(URL, headers=HDRS, files=files, data=data, timeout=120)
    ms = (time.perf_counter() - t0) * 1000
    txt = resp.json().get('text', '') if resp.status_code == 200 else f'HTTP {resp.status_code}'
    return ms, txt

s = requests.Session()
call(s, en_flac, 'a.flac', 'audio/flac')   # prime

print("=" * 74)
print("1. FLAC vs WAV - 8 runs each, INTERLEAVED (cancels network drift)")
print("=" * 74)
f_times, w_times = [], []
for i in range(8):
    mf, _ = call(s, en_flac, 'a.flac', 'audio/flac')
    mw, _ = call(s, en_wav, 'a.wav', 'audio/wav')
    f_times.append(mf); w_times.append(mw)
    print(f"  pair {i+1}: FLAC {mf:6.0f} ms   WAV {mw:6.0f} ms")
mf, mw = statistics.median(f_times), statistics.median(w_times)
print(f"\n  FLAC 339 KB median : {mf:6.0f} ms")
print(f"  WAV  604 KB median : {mw:6.0f} ms")
print(f"  -> {'WAV WINS by %.0f ms' % (mf-mw) if mw < mf else 'FLAC WINS by %.0f ms' % (mw-mf)}")

print("\n" + "=" * 74)
print("2. RUSSIAN transcription")
print("=" * 74)
spoken = ("Это тест задержки для проекта БиллиТолк. Надо поднять впэ эн на сервере, "
          "настроить реалити и проверить эс эн ай. Потом выкатим прод после бэкапа.")
print(f"  SPOKEN : {spoken}\n")

m, t = call(s, ru_wav, 'a.wav', 'audio/wav', {'language': 'ru'})
print(f"  [ru, no prompt]   {m:.0f} ms")
print(f"    {t.strip()}\n")

m, t = call(s, ru_wav, 'a.wav', 'audio/wav',
            {'language': 'ru', 'prompt': 'BillyTalk, VPN, Reality, SNI, Remnawave, XTLS, VPS'})
print(f"  [ru, WITH prompt] {m:.0f} ms")
print(f"    {t.strip()}\n")

m, t = call(s, ru_wav, 'a.wav', 'audio/wav')
print(f"  [autodetect]      {m:.0f} ms")
print(f"    {t.strip()}")
s.close()
