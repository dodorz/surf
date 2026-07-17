# Regression test: anti-bot challenge detection (Anubis/hashcash, Cloudflare).
#
# acoup.blog serves a JavaScript proof-of-work challenge ("Checking your browser",
# hashcash "/__challenge" endpoint) that auto-submits and reloads to the real page,
# or asks the visitor to tick an "I am human" checkbox. surf must (a) recognise the
# challenge page and (b) NOT falsely flag ordinary pages that merely contain the
# harmless `data-cfasync="false"` WordPress attribute.
import sys
sys.path.insert(0, ".")

from surf import Fetcher

# Captured Anubis/hashcash challenge interstitial (title "Checking your browser...").
CHALLENGE_HTML = """<!DOCTYPE html><html><head><title>Checking your browser...</title>\
<script>(()=>{let e,t,n,o,r,a,l,c,d="";async function f(e){let t=(new TextEncoder)\
.encode(e),n=await crypto.subtle.digest("SHA-256",t);return Array.from(new Uint8Array(n))\
.map((e=>e.toString(16).padStart(2,"0"))).join("")}function p(){r||!a||l||o&&!d||(r=!0,\
fetch("/__challenge",{method:"POST",headers:{"X-Hashcash-Solution":btoa(a),
"X-Interactive":d}}).then((e=>{window.location.reload()})))}window.addEventListener("load",\
(()=>{let _hcc="v1:...:0000|8|";let i=10;setTimeout(()=>{l=null;p()},3500);async function(t){\
for(let n=0;n<2e8;n++){let i=e+n;if((await f(i)).substring(0,t.length)===t)return a=i,void p()}\
})(["0000"])}))})()</script></head><body><div id="main"><h1 id="head">Checking your browser</h1>\
<div id="active"><input type="checkbox" id="x"><label for="x">I am human</label></div></div>\
<div id="brand">Secured by <a href="https://wordpress.com">wp.com</a></div></body></html>"""

# Real article page that happens to contain the harmless `data-cfasync="false"` attribute
# (extremely common on WordPress/Cloudflare-served sites) but is NOT a challenge.
REAL_PAGE_HTML = """<!DOCTYPE html><html lang="en-US"><head><meta charset="UTF-8">\
<title>Collections: The Late Bronze Age Collapse, A Very Brief Introduction</title>\
<script data-cfasync="false" src="https://example.com/app.js"></script></head>\
<body><article><h1>The Late Bronze Age Collapse</h1><p>This week we discuss the LBAC, the\
shocking collapse of the Late Bronze Age state system across the Eastern Mediterranean.\
The Sea Peoples played a role, and the transition to the Iron Age was profound and lasting.\
Trade networks unraveled and writing systems were lost for centuries in some regions.</p>\
<p>Scholars debate the exact causes, but climate and systemic fragility both contributed.\
The collapse remains one of history's great puzzles and a warning for interconnected systems.</p>\
</article></body></html>"""

# Cloudflare "Checking your browser" interstitial.
CLOUDFLARE_HTML = """<html><head><title>Just a moment...</title></head><body>\
<p>Checking your browser before accessing example.com.</p>\
<script data-cfasync="false" src="https://challenges.cloudflare.com/c.js"></script>\
</body></html>"""


def main():
    # 1. Challenge page must be detected.
    assert Fetcher._is_antibot_challenge_page(CHALLENGE_HTML), \
        "FAIL: Anubis/hashcash challenge page not detected"
    print("PASS: Anubis/hashcash challenge detected")

    # 2. The interactive 'I am human' checkbox marker must be present.
    assert 'id="x"' in CHALLENGE_HTML and "I am human" in CHALLENGE_HTML
    print("PASS: interactive 'I am human' checkbox present in challenge")

    # 3. A real article page with data-cfasync=\"false\" must NOT be flagged as a challenge.
    assert not Fetcher._is_antibot_challenge_page(REAL_PAGE_HTML), \
        "FAIL: ordinary page with data-cfasync=false falsely flagged as challenge"
    print("PASS: ordinary WordPress page (data-cfasync=false) not flagged")

    # 4. Real page must also pass the broader Cloudflare check (no false positive).
    assert not Fetcher._is_cloudflare_challenge(REAL_PAGE_HTML), \
        "FAIL: data-cfasync=false falsely triggers Cloudflare challenge detection"
    print("PASS: data-cfasync=false no longer triggers _is_cloudflare_challenge")

    # 5. Genuine Cloudflare interstitial must still be detected.
    assert Fetcher._is_antibot_challenge_page(CLOUDFLARE_HTML), \
        "FAIL: Cloudflare interstitial not detected"
    assert Fetcher._is_cloudflare_challenge(CLOUDFLARE_HTML), \
        "FAIL: Cloudflare interstitial not detected by _is_cloudflare_challenge"
    print("PASS: Cloudflare interstitial still detected")

    print("\nAll anti-bot challenge tests passed!")


if __name__ == "__main__":
    main()
