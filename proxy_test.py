import urllib.request
import urllib.error

proxy_url = "http://31.59.20.176:6754"
user = "oxihnext"
pass_ = "gj8elbyd3on0"

proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
auth_handler = urllib.request.ProxyBasicAuthHandler()
auth_handler.add_password(realm=None, uri=proxy_url, user=user, passwd=pass_)

opener = urllib.request.build_opener(proxy_handler, auth_handler)
urllib.request.install_opener(opener)

try:
    print("Testing urllib with explicit auth handler...")
    req = urllib.request.Request("https://api.telegram.org")
    resp = urllib.request.urlopen(req, timeout=10)
    print("Status:", resp.status)
except urllib.error.HTTPError as e:
    print("HTTPError:", e.code, e.reason)
except Exception as e:
    print("Error:", type(e), e)
