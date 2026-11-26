"""Cartridge load/save + sympy checks."""
import ast
import concurrent.futures as cf
import threading
import types

import sympy
import yaml

import config as C

_pool = cf.ThreadPoolExecutor(2)
_BAD = ("__", "import", "lambda", "exec", "eval", "open", "os.", "sys.")


_cache = {"sig": None, "data": {}, "files": {}}
_lock = threading.Lock()
try:
    _Loader = yaml.CSafeLoader                   # libyaml: ~10x faster than the pure-python parser
except AttributeError:
    _Loader = yaml.SafeLoader


def load_all():
    """Parsed cartridges, cached until any YAML changes. Only changed files are re-parsed. Server routes call this
    in a worker thread (a full parse takes seconds): never on the event loop while an interview runs."""
    ps = sorted(C.CARTS.glob("*.yaml"))
    sig = tuple((p.name, p.stat().st_mtime_ns) for p in ps)
    if sig == _cache["sig"]:
        return dict(_cache["data"])
    with _lock:
        if sig == _cache["sig"]:
            return dict(_cache["data"])
        files, out = {}, {}
        for p, (_, mt) in zip(ps, sig):
            old = _cache["files"].get(p.name)
            if old and old[0] == mt:
                files[p.name] = old
            else:
                try:
                    files[p.name] = (mt, yaml.load(p.read_text(), Loader=_Loader))
                except yaml.YAMLError:
                    files[p.name] = (mt, None)
            d = files[p.name][1]
            if isinstance(d, dict) and d.get("id") and d.get("stages") is not None:
                out[d["id"]] = d
        _cache.update(sig=sig, data=out, files=files)
        return dict(out)


def load(cid):
    return load_all().get(cid)


def save(cart):
    (C.CARTS / f"{cart['id']}.yaml").write_text(
        yaml.safe_dump(cart, sort_keys=False, allow_unicode=True, width=100))


def summary(c):
    return {k: c.get(k) for k in ("id", "title", "topics", "warmup", "verified", "answer_source", "source",
                                   "rating", "refined", "tags")} | {
        "stages": len(c.get("stages", []) or []), "statement": (c.get("statement") or "")[:600]}


def personal_bank():
    p = C.CARTS / "personal_bank.yaml"
    return yaml.safe_load(p.read_text()).get("questions", []) if p.exists() else []


def _namespace():
    """Only sympy's public functions/classes/constants (no modules, no builtins). `sympy.x` / `sp.x` work too."""
    ns = {}
    for k in dir(sympy):
        if k.startswith("_"):
            continue
        v = getattr(sympy, k)
        if isinstance(v, types.ModuleType):
            continue
        ns[k] = v
    for k in _NO_CALL:
        ns.pop(k, None)
    proxy = types.SimpleNamespace(**ns)
    import builtins
    safe = {k: getattr(builtins, k) for k in _SAFE_BUILTINS}
    ns.update(safe, sympy=proxy, sp=proxy, __builtins__={})
    return ns


_NS = None
_SAFE_BUILTINS = ("range", "len", "all", "any", "sum", "min", "max", "abs", "int", "float", "bool", "round", "sorted",
                  "set", "frozenset", "zip", "list", "tuple", "dict", "enumerate", "reversed", "pow", "divmod")
# names that turn a STRING into code (sympify / parse_expr / lambdify evaluate with full builtins): never callable
_NO_CALL = {"sympify", "S", "parse_expr", "lambdify", "eval", "exec", "compile", "getattr", "setattr", "delattr", "vars",
            "globals", "locals", "open", "input", "type", "object", "srepr", "parse_latex", "evaluate", "init_session",
            "init_printing", "preview", "pprint", "plot", "var", "symarray", "Function"}
_IDENT = __import__("re").compile(r"^([A-Za-z][A-Za-z0-9 ,]*|[+-]{1,2})$")   # Symbol("m"), symbols("x y"), dir="+": no code
_OK_NODES = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.Call, ast.Name, ast.Load, ast.Store,
             ast.Constant, ast.Tuple, ast.List, ast.Dict, ast.Set, ast.keyword, ast.Attribute, ast.Subscript, ast.Slice,
             ast.IfExp, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp, ast.comprehension,
             ast.operator, ast.unaryop, ast.boolop, ast.cmpop, ast.expr_context)


def _safe_ast(expr):
    """AI-written check strings: plain maths expressions only. No dunder / private names or attributes,
    no lambdas, comprehensions, assignments or starred tricks."""
    try:
        tree = ast.parse(expr.replace("^", "**"), mode="eval")
    except SyntaxError:
        return False
    for n in ast.walk(tree):
        if not isinstance(n, _OK_NODES):
            return False
        if isinstance(n, ast.Name) and (n.id.startswith("_") or n.id in _NO_CALL):
            return False
        if isinstance(n, ast.Attribute) and (n.attr.startswith("_") or n.attr in _NO_CALL):
            return False
        if isinstance(n, ast.Constant) and isinstance(n.value, (str, bytes)) and not (isinstance(n.value, str) and _IDENT.match(n.value)):
            return False                     # a string could be handed to sympify inside sympy (e.g. .subs("..."))
    return True


def _verify(expr):
    global _NS
    if _NS is None:
        _NS = _namespace()
    if not _safe_ast(expr):
        return None
    from sympy.parsing.sympy_parser import parse_expr, standard_transformations, convert_xor
    v = parse_expr(expr, local_dict={}, global_dict=dict(_NS),
                   transformations=standard_transformations + (convert_xor,), evaluate=True)
    if v is sympy.true or v is True:
        return True
    if v is sympy.false or v is False:
        return False
    if hasattr(v, "doit"):
        v = v.doit()
    if isinstance(v, sympy.logic.boolalg.Boolean):
        return bool(sympy.simplify(v))
    return sympy.simplify(v) == 0


def verify(expr, timeout=3.0):
    """True / False / None (couldn't compute). AI strings -> crude sandbox + timeout."""
    expr = (expr or "").strip()
    if not expr or any(b in expr for b in _BAD) or len(expr) > 400:
        return None
    try:
        return _pool.submit(_verify, expr).result(timeout=timeout)
    except Exception:
        return None
