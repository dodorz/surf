# Paywall detection edge-case test — WSJ DataDome CAPTCHA and bare-domain titles
import sys
sys.path.insert(0, ".")

from surf import Fetcher

# ---- Test 1: WSJ DataDome CAPTCHA page ----
wsj_captcha = """<html lang="en"><head><title>wsj.com</title>
<style>#cmsg{animation: A 1.5s;}@keyframes A{0%{opacity:0;}99%{opacity:0;}100%{opacity:1;}}</style>
</head><body style="margin:0">
<p id="cmsg">Please enable JS and disable any ad blocker</p>
<script data-cfasync="false">var dd={'rt':'c','cid':'AHrlq'};</script>
<script data-cfasync="false" src="https://ct.captcha-delivery.com/c.js"></script>
<iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=foo" width="100%" height="100%"></iframe>
</body></html>"""
r = Fetcher._detect_paywall(wsj_captcha, url="https://www.wsj.com/tech/ai/test-article")
print(f"Test 1 — WSJ DataDome CAPTCHA:")
print(f"  detected: {r['detected']} (expected: True)")
print(f"  confidence: {r['confidence']:.0%}")
print(f"  reason: {r['reason']}")
assert r['detected'], "FAIL: WSJ CAPTCHA page not detected as paywall"
print("  PASS")

# ---- Test 2: Bare-domain title on known paywall domain ----
bare_title_html = """<html><head><title>wsj.com</title></head>
<body><div id="root"></div><script>window.__DATA__={};</script></body></html>"""
r2 = Fetcher._detect_paywall(bare_title_html, url="https://www.wsj.com/business/article123")
print(f"\nTest 2 — Bare domain title 'wsj.com':")
print(f"  detected: {r2['detected']} (expected: True)")
print(f"  confidence: {r2['confidence']:.0%}")
print(f"  reason: {r2['reason']}")
assert r2['detected'], "FAIL: Bare domain title not detected"
print("  PASS")

# ---- Test 3: Bloomberg anti-bot ----
bloomberg_captcha = """<html><head><title>Bloomberg</title></head>
<body><p>Checking your browser before accessing bloomberg.com.</p>
<script data-cfasync="false" src="https://geo.captcha-delivery.com/c.js"></script>
</body></html>"""
r3 = Fetcher._detect_paywall(bloomberg_captcha, url="https://www.bloomberg.com/news/articles/test")
print(f"\nTest 3 — Bloomberg anti-bot page:")
print(f"  detected: {r3['detected']} (expected: True)")
print(f"  confidence: {r3['confidence']:.0%}")
print(f"  reason: {r3['reason']}")
assert r3['detected'], "FAIL: Bloomberg anti-bot not detected"
print("  PASS")

# ---- Test 4: Non-truncated normal article on known domain (should NOT trigger) ----
normal_article = """<html><head><title>How AI Is Transforming Healthcare — WSJ</title>
<meta name="description" content="An in-depth look at AI in medicine">
</head><body>
<article>
<h1>How AI Is Transforming Healthcare</h1>
<p>Artificial intelligence is reshaping the medical industry in profound ways. From diagnostic imaging to drug discovery, machine learning models are achieving results that rival or exceed human experts. Hospitals across the United States are deploying AI-driven tools to reduce wait times, predict patient deterioration, and personalize treatment plans.</p>
<p>At Massachusetts General Hospital, an AI system now screens CT scans for signs of internal bleeding with 95% accuracy, alerting radiologists within seconds rather than minutes. "This isn't about replacing doctors," says Dr. Sarah Chen, chief of radiology. "It's about giving them superpowers."</p>
<p>The pharmaceutical industry has also embraced AI, with companies like Insilico Medicine using generative models to design novel drug candidates in weeks rather than years. In 2023, the first AI-designed drug entered Phase 2 clinical trials, marking a milestone for the field.</p>
</article>
</body></html>"""
r4 = Fetcher._detect_paywall(normal_article, url="https://www.wsj.com/health/ai-medicine")
print(f"\nTest 4 — Real article on WSJ (known domain, real content):")
print(f"  detected: {r4['detected']} (expected: False)")
print(f"  confidence: {r4['confidence']:.0%}")
print(f"  reason: {r4['reason']}")
assert not r4['detected'], "FAIL: Real article incorrectly flagged as paywall"
print("  PASS")

# ---- Test 5: FT anti-bot page ----
ft_captcha = """<html><head><title>ft.com</title></head>
<body><h1>ft.com</h1><p>Please enable JS and disable any ad blocker</p>
<script src="https://ct.captcha-delivery.com/c.js"></script>
</body></html>"""
r5 = Fetcher._detect_paywall(ft_captcha, url="https://www.ft.com/content/test-article")
print(f"\nTest 5 — FT anti-bot page:")
print(f"  detected: {r5['detected']} (expected: True)")
print(f"  confidence: {r5['confidence']:.0%}")
print(f"  reason: {r5['reason']}")
assert r5['detected'], "FAIL: FT anti-bot not detected"
print("  PASS")

# ---- Test 6: Normal non-paywall site (example.com) ----
normal_site = """<html><head><title>Example Page</title></head>
<body><article><h1>Welcome</h1><p>This is a free resource.</p></article></body></html>"""
r6 = Fetcher._detect_paywall(normal_site, url="https://example.com/free")
print(f"\nTest 6 — Normal free site:")
print(f"  detected: {r6['detected']} (expected: False)")
print(f"  confidence: {r6['confidence']:.0%}")
print(f"  reason: {r6['reason']}")
assert not r6['detected'], "FAIL: Normal site flagged as paywall"
print("  PASS")

print("\nAll tests passed!")
