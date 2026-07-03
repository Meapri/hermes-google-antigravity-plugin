#!/usr/bin/env python3
"""Google Antigravity OAuth login (headless, manual code paste).
Writes tokens to ~/.hermes/auth/google_antigravity.json (access/refresh/expires)."""
import base64, hashlib, json, os, secrets, sys, time, urllib.parse, urllib.request, urllib.error

AUTH="https://accounts.google.com/o/oauth2/auth"; TOKEN="https://oauth2.googleapis.com/token"
REDIRECT="https://antigravity.google/oauth-callback"
SCOPES=("https://www.googleapis.com/auth/cloud-platform https://www.googleapis.com/auth/userinfo.email "
        "https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/cclog "
        "https://www.googleapis.com/auth/experimentsandconfigs openid")
HOME=os.path.expanduser("~/.hermes")
CLIENT=os.path.join(HOME,"auth","google_antigravity_client.json")
CRED=os.path.join(HOME,"auth","google_antigravity.json")

def pkce():
    v=base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    c=base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v,c

def auth_url():
    d=json.load(open(CLIENT,encoding="utf-8")); cid,csec=d["client_id"],d["client_secret"]
    v,c=pkce(); st=secrets.token_urlsafe(16)
    p={"access_type":"offline","client_id":cid,"code_challenge":c,"code_challenge_method":"S256",
       "prompt":"consent","redirect_uri":REDIRECT,"response_type":"code","scope":SCOPES,"state":st}
    return AUTH+"?"+urllib.parse.urlencode(p), cid, csec, v, st

def exchange(cid,csec,verifier,code):
    data=urllib.parse.urlencode({"code":code,"client_id":cid,"client_secret":csec,
        "code_verifier":verifier,"grant_type":"authorization_code","redirect_uri":REDIRECT}).encode()
    req=urllib.request.Request(TOKEN,data=data,headers={"Content-Type":"application/x-www-form-urlencoded"})
    return json.load(urllib.request.urlopen(req,timeout=30))

def main():
    url,cid,csec,verifier,state=auth_url()
    print("\n[1] Open this URL in a browser and sign in with Google:\n\n"+url)
    print("\n[2] You will be redirected to https://antigravity.google/oauth-callback?code=...")
    print("    Copy that FULL URL (or just the code) and paste below.\n")
    if "--print-url-only" in sys.argv:
        print("(url-only mode; not waiting for input)"); return
    raw=input("Paste callback URL or code: ").strip()
    if "code=" in raw:
        q=urllib.parse.urlparse(raw).query or raw.split("?",1)[-1]; pr=urllib.parse.parse_qs(q)
        if pr.get("state",[""])[0] and pr["state"][0]!=state: sys.exit("ERROR: state mismatch")
        code=pr.get("code",[""])[0]
    else: code=raw
    if not code: sys.exit("ERROR: no code")
    try: r=exchange(cid,csec,verifier,code)
    except urllib.error.HTTPError as e: sys.exit("Token exchange failed: "+e.read().decode()[:300])
    acc=r.get("access_token")
    if not acc: sys.exit("ERROR: no access token: "+json.dumps(r)[:200])
    out={"access":acc,"refresh":r.get("refresh_token",""),"expires":int(time.time())+int(r.get("expires_in",3600)),"email":""}
    try:
        old=json.load(open(CRED,encoding="utf-8"))
        if old.get("email"): out["email"]=old["email"]
    except Exception: pass
    os.makedirs(os.path.dirname(CRED),exist_ok=True)
    json.dump(out,open(CRED,"w",encoding="utf-8"),indent=2); os.chmod(CRED,0o600)
    print(f"\n[OK] Logged in -> {CRED}  (refresh_token={'yes' if out['refresh'] else 'no'})")

if __name__=="__main__": main()
