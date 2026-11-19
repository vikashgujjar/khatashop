"""Lists every route of the old Laravel app (routes/api.php + routes/web.php) with its full URL,
tracking Route::group([... 'prefix' => ...]) and Route::prefix(...)->group(...) nesting.

Usage:  python tools/list_legacy_routes.py <laravel project dir>  > routes.tsv
Output: METHOD <tab> URL <tab> Controller@method
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROUTE = re.compile(r"Route::(get|post|put|patch|delete|any)\(\s*['\"]([^'\"]*)['\"]\s*,\s*\[\s*([A-Za-z_]+)::class\s*,\s*['\"]([^'\"]+)['\"]")
MATCH = re.compile(r"Route::match\(\s*\[([^\]]*)\]\s*,\s*['\"]([^'\"]*)['\"]\s*,\s*\[\s*([A-Za-z_]+)::class\s*,\s*['\"]([^'\"]+)['\"]")
GROUP_PREFIX = re.compile(r"['\"]prefix['\"]\s*=>\s*['\"]([^'\"]+)['\"]")
PREFIX_CALL = re.compile(r"Route::prefix\(\s*['\"]([^'\"]+)['\"]\s*\)")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r"(?<!:)//.*$", "", line) for line in text.splitlines())


def parse(path: Path, base: str):
    text = strip_comments(path.read_text(encoding="utf-8", errors="replace"))
    stack, pending, depth_stack, depth = [], None, [], 0
    out = []
    i = 0
    tokens = re.finditer(r"Route::group\(|Route::prefix\([^)]*\)|Route::(?:get|post|put|patch|delete|any|match)\([^;]*;|\{|\}|['\"]prefix['\"]\s*=>\s*['\"][^'\"]+['\"]", text)
    for m in tokens:
        tok = m.group(0)
        if tok.startswith("Route::group("):
            pending = ""          # prefix may follow inside the [ ... ] options
        elif tok.startswith("Route::prefix("):
            pending = PREFIX_CALL.match(tok).group(1)
        elif GROUP_PREFIX.match(tok):
            if pending == "":
                pending = GROUP_PREFIX.match(tok).group(1)
        elif tok == "{":
            depth += 1
            if pending is not None:
                stack.append(pending.strip("/"))
                depth_stack.append(depth)
                pending = None
        elif tok == "}":
            if depth_stack and depth_stack[-1] == depth:
                depth_stack.pop()
                stack.pop()
            depth -= 1
        else:
            prefix = "/".join(p for p in [base] + stack if p)
            r = ROUTE.search(tok)
            if r:
                methods, uri, ctrl, action = [r.group(1).upper()], r.group(2), r.group(3), r.group(4)
            else:
                mm = MATCH.search(tok)
                if not mm:
                    continue
                methods = [x.strip(" '\"").upper() for x in mm.group(1).split(",")]
                uri, ctrl, action = mm.group(2), mm.group(3), mm.group(4)
            url = "/" + "/".join(p for p in [prefix, uri.strip("/")] if p)
            for meth in methods:
                out.append((meth, url, f"{ctrl}@{action}"))
    return out


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    rows = parse(root / "routes" / "api.php", "api") + parse(root / "routes" / "web.php", "")
    seen = set()
    for meth, url, target in rows:
        if (meth, url) in seen:
            continue
        seen.add((meth, url))
        print(f"{meth}\t{url}\t{target}")


if __name__ == "__main__":
    main()
