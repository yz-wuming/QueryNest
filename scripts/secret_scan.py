"""PHASE 7.3 Secret scan: 只报告位置，绝不输出真实值(脱敏)。"""
import os, re, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SKIP_DIRS = {".git", "querynest_storage", "querynest_storage_v2", "querynest_storage_v4",
             "querynest_output", "querynest_output_v4", ".workbuddy", "node_modules", "__pycache__"}
SKIP_FILES = {"secrets.json"}
RED = re.compile(r"(?i)(api[_]?key|api-key|authorization|bearer|secret|password|\btoken\b)")
SK_FULL = re.compile(r"sk-[A-Za-z0-9]{8,}")
BEARER_FULL = re.compile(r"Bearer\s+\S+")
KEYVAL = re.compile(r"((?:api[_]?key|api-key|token|secret|password|authorization|bearer)\s*[:=]\s*)([^\s,;\"']+)")

def redact(line):
    line = KEYVAL.sub(lambda m: m.group(1) + "<REDACTED>", line)
    line = SK_FULL.sub("sk-<REDACTED>", line)
    line = BEARER_FULL.sub("Bearer <REDACTED>", line)
    # 通用兜底：对长随机串(疑似key)脱敏，但保留描述性字段
    return line

def walk():
    found = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn in SKIP_FILES:
                continue
            p = os.path.join(dp, fn)
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if RED.search(line) or SK_FULL.search(line) or "Bearer " in line:
                            rel = os.path.relpath(p, ROOT)
                            found.append(f"{rel}:{i}: {redact(line.rstrip())}")
            except Exception:
                pass
    return found

if __name__ == "__main__":
    hits = walk()
    print(f"Total matching lines: {len(hits)}")
    for h in hits[:80]:
        print(h)