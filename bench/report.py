import json, statistics
from collections import defaultdict
R = [json.loads(l) for l in open("bench/results.jsonl")]
by = defaultdict(list)
for r in R:
    by[r["cfg"]].append(r)
for cfg, rows in by.items():
    ok = [r for r in rows if r["ok"]]
    secs = sorted(r["sec"] for r in ok)
    a = [r["answer_hit"] for r in ok if r.get("answer_hit") is not None]
    d = [r["domain_hit"] for r in ok if r.get("domain_hit") is not None]
    p90 = secs[int(len(secs) * 0.9) - 1] if secs else 0
    print(f"{cfg}: ok {len(ok)}/{len(rows)} | median {statistics.median(secs):.1f}s p90 {p90:.1f}s | "
          f"answer {sum(a)}/{len(a)} | domain {sum(d)}/{len(d)} | rows avg {statistics.mean(r['n'] for r in ok):.1f} "
          f"| uniq hosts {statistics.mean(r['unique_hosts'] for r in ok):.1f} | redirects {sum(r['unresolved'] for r in ok)}")
    cats = defaultdict(lambda: [0, 0])
    for r in ok:
        hit = r.get("answer_hit") if r.get("answer_hit") is not None else r.get("domain_hit")
        if hit is not None:
            cats[r["cat"]][0] += bool(hit); cats[r["cat"]][1] += 1
    print("   by cat:", {k: f"{v[0]}/{v[1]}" for k, v in cats.items()})
    miss = [r["id"] for r in ok if r.get("answer_hit") is False or r.get("domain_hit") is False]
    print("   misses:", miss, "| errors:", [(r["id"], r["err"][:50]) for r in rows if not r["ok"]])
