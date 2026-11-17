import ast, sys, collections
src = open('/mnt/user-data/uploads/purity_bot_1.py', encoding='utf-8').read()
tree = ast.parse(src)
defs = {}          # name -> (node, start, end)
assigns = {}
for n in tree.body:
    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        defs[n.name] = n
    elif isinstance(n, (ast.Assign, ast.AnnAssign)):
        targets = n.targets if isinstance(n, ast.Assign) else [n.target]
        for t in targets:
            for x in ast.walk(t):
                if isinstance(x, ast.Name):
                    assigns[x.id] = n
allnames = set(defs) | set(assigns)
def refs(node):
    out = set()
    for x in ast.walk(node):
        if isinstance(x, ast.Name) and x.id in allnames:
            out.add(x.id)
    return out
def span(n):
    s = min([n.lineno] + [d.lineno for d in getattr(n, 'decorator_list', [])])
    return s, n.end_lineno
def closure(seeds, stop=()):
    seen, q = set(), list(seeds)
    while q:
        c = q.pop()
        if c in seen or c in stop: continue
        seen.add(c)
        node = defs.get(c) or assigns.get(c)
        if node is None: continue
        q.extend(refs(node) - seen)
    return seen
if __name__ == "__main__":
    seeds = sys.argv[1].split(',')
    stop = set(sys.argv[2].split(',')) if len(sys.argv) > 2 else set()
    cl = closure(seeds, stop)
    lines = 0
    for c in sorted(cl, key=lambda c: span(defs.get(c) or assigns[c])[0]):
        n = defs.get(c) or assigns.get(c)
        s, e = span(n); lines += e - s + 1
    print(len(cl), "names,", lines, "lines")
    print(" ".join(sorted(cl, key=lambda c: span(defs.get(c) or assigns[c])[0])))
