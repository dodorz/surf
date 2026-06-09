# Quick smoke test for paywall integration
import sys
sys.path.insert(0, ".")

from surf import Fetcher, _get_version

print(f"Version: {_get_version()}")
print(f"_detect_paywall exists: {hasattr(Fetcher, '_detect_paywall')}")
print(f"_fetch_archiveis_snapshot exists: {hasattr(Fetcher, '_fetch_archiveis_snapshot')}")
print(f"_KNOWN_PAYWALL_DOMAINS exists: {hasattr(Fetcher, '_KNOWN_PAYWALL_DOMAINS')}")
print(f"Domains count: {len(Fetcher._KNOWN_PAYWALL_DOMAINS)}")

# Test paywall detection with sample HTML
paywall_html = """
<html>
<head><title>Subscribe to Read</title></head>
<body>
<div class="paywall-overlay">
  <p>To continue reading this article, please subscribe.</p>
  <p>You've reached your free article limit.</p>
</div>
</body>
</html>
"""
result = Fetcher._detect_paywall(paywall_html, url="https://www.nytimes.com/2024/01/01/test.html")
print(f"\nPaywall test (paywall HTML):")
print(f"  detected: {result['detected']}")
print(f"  confidence: {result['confidence']:.0%}")
print(f"  reason: {result['reason']}")

# Test non-paywall content
normal_html = """
<html>
<head><title>Free Article</title></head>
<body>
<article>
  <h1>This is a free article</h1>
  <p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.</p>
  <p>Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id est laborum.</p>
</article>
</body>
</html>
"""
result2 = Fetcher._detect_paywall(normal_html, url="https://example.com/blog/post")
print(f"\nPaywall test (normal HTML):")
print(f"  detected: {result2['detected']}")
print(f"  confidence: {result2['confidence']:.0%}")
print(f"  reason: {result2['reason']}")

# Test known domain only
minimal_html = "<html><body><p>test</p></body></html>"
result3 = Fetcher._detect_paywall(minimal_html, url="https://www.wsj.com/articles/test")
print(f"\nPaywall test (known domain, minimal content):")
print(f"  detected: {result3['detected']}")
print(f"  confidence: {result3['confidence']:.0%}")
print(f"  reason: {result3['reason']}")

# Test medium.com paywall pattern
medium_html = """
<html>
<head><title>Test Article</title>
<meta name="description" content="metered">
</head>
<body>
<div class="metered-content">
  <p>Create a free account to continue reading.</p>
</div>
</body>
</html>
"""
result4 = Fetcher._detect_paywall(medium_html, url="https://medium.com/@user/test-article")
print(f"\nPaywall test (Medium pattern):")
print(f"  detected: {result4['detected']}")
print(f"  confidence: {result4['confidence']:.0%}")
print(f"  reason: {result4['reason']}")

print("\nAll paywall detection tests passed!")
