"""Golden-set benchmark for web_search backends. Appends one JSON line per (config, query) to results.jsonl."""
import json, sys, time
from urllib.parse import urlparse
sys.path.insert(0, __import__("os").path.expanduser("~/.hermes/hermes-agent"))
from hermes_cli.plugins import discover_plugins
discover_plugins()
from agent.web_search_registry import get_provider
import importlib

HERE = "bench/"
GOLD = json.load(open(HERE + "golden.json"))
gg_prov = get_provider("google_grounding")
gg = importlib.import_module(type(gg_prov).__module__)
configs = sys.argv[1].split(",")
out = open(HERE + "results.jsonl", "a")


def run(cfg, q):
    if cfg.startswith("gg-"):
        gg._web_config = lambda m=cfg[3:]: {"mode": m}
        return gg_prov.search(q, 5)
    return get_provider(cfg).search(q, 5)


def grade(item, r):
    d = r.get("data") or {}
    rows = d.get("web") or []
    hay = " ".join([d.get("answer") or ""] + [x.get("title", "") + " " + x.get("description", "") for x in rows]).lower()
    hosts = [urlparse(x.get("url", "")).netloc.lower() for x in rows]
    ans = any(e.lower() in hay for e in item["expect_any"]) if item["expect_any"] else None
    dom = any(h == dm or h.endswith("." + dm) for h in hosts for dm in item["domains"]) if item["domains"] else None
    redirects = sum("vertexaisearch" in h for h in hosts)
    return {"answer_hit": ans, "domain_hit": dom, "n": len(rows), "unique_hosts": len(set(hosts)),
            "unresolved": redirects, "hosts": hosts}


for item in GOLD:
    for cfg in configs:
        t = time.time()
        try:
            r = run(cfg, item["q"])
        except Exception as e:
            r = {"success": False, "error": str(e)}
        rec = {"tier": ((r.get("data") or {}).get("tier")), "cfg": cfg, "id": item["id"], "cat": item["cat"], "sec": round(time.time() - t, 2),
               "ok": bool(r.get("success")), "err": (r.get("error") or "")[:150]}
        if rec["ok"]:
            rec.update(grade(item, r))
        out.write(json.dumps(rec, ensure_ascii=False) + "\n"); out.flush()
        print(cfg, item["id"], rec["sec"], rec.get("answer_hit"), rec.get("domain_hit"), rec.get("n"), rec["err"][:60], flush=True)
