"""Summarise a play.py log: games, wins, best scores, desyncs."""
import json
import sys

rs = [json.loads(l) for l in open(sys.argv[1]) if l.startswith("{")]
w = [r for r in rs if r["status"] == "won"]
print(len(rs), "games,", len(w), "wins")
print("best:", sorted([(r["score"], r["passes"], r["dir"][-11:]) for r in w], reverse=True)[:6])
print("desyncs:", [r["status"][:60] for r in rs if r["status"].startswith("desync")])
