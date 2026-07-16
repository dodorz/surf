# Regression test: Wired article behind a paywall (truncated teaser)
# should be detected so the archive.is fallback is triggered.
import sys
sys.path.insert(0, ".")

from surf import Fetcher

WIRED_URL = "https://www.wired.com/story/what-happens-if-china-hacks-the-us-water-supply-war-game-volt-typhoon/"

# A metered/locked Wired page: only the opening sentence plus a subscribe prompt.
wired_paywalled = f"""<html lang="en"><head>
<title>What Happens if China Hacks the US Water Supply? | WIRED</title>
<meta property="article:content_tier" content="metered"/>
<meta property="og:title" content="What Happens if China Hacks the US Water Supply?">
</head><body>
<article>
<h1>What Happens if China Hacks the US Water Supply? I Went to a Secret War Game to Find Out</h1>
<p>Last spring, a group of former military and intelligence officials gathered in a windowless room to simulate what would happen if Beijing weaponized America's water systems.</p>
<p class="paywall-cta">Subscribe to read the full story. Start your free trial today.</p>
</article>
</body></html>"""

r = Fetcher._detect_paywall(wired_paywalled, url=WIRED_URL)
print(f"Wired paywalled teaser:")
print(f"  detected: {r['detected']} (expected: True)")
print(f"  confidence: {r['confidence']:.0%}")
print(f"  reason: {r['reason']}")
assert r['detected'], "FAIL: Wired paywalled teaser not detected as paywall"
print("  PASS")

# Free Wired article must still NOT be flagged (explicit free tier signal).
wired_free = """<html lang="en"><head>
<title>All the Ways Europe Is Ditching American Technology | WIRED</title>
<meta property="article:content_tier" content="free"/>
<meta property="og:title" content="All the Ways Europe Is Ditching American Technology"/>
</head><body>
<script data-cfasync="false" src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
<article>
<h1>All the Ways Europe Is Ditching American Technology</h1>
<p>A WIRED timeline shows how dozens of governments, companies, and other organizations across Europe are moving away from US Big Tech.</p>
<p>This shift represents one of the most significant realignments in the global technology landscape since the rise of Silicon Valley dominance in the early 2000s.</p>
<p>More paragraphs of real content follow here describing the broader trend in detail across many sectors and nations.</p>
</article>
</body></html>"""
r2 = Fetcher._detect_paywall(wired_free, url="https://www.wired.com/story/europe-ditching-american-technology/")
print(f"\nWired free article (with Cloudflare challenge):")
print(f"  detected: {r2['detected']} (expected: False)")
print(f"  confidence: {r2['confidence']:.0%}")
print(f"  reason: {r2['reason']}")
assert not r2['detected'], "FAIL: Free Wired article incorrectly flagged as paywall"
print("  PASS")

print("\nAll Wired paywall tests passed!")
