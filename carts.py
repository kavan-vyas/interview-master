"""Cartridge load/save + sympy checks."""
import concurrent.futures as cf

import sympy
import yaml

import config as C

_pool = cf.ThreadPoolExecutor(2)
_BAD = ("__", "import", "lambda", "exec", "eval", "open", "os.", "sys.")


_cache = {"sig": None, "data": {}}


def load_all():
    """Parsed cartridges, cached until any YAML changes (pure-python YAML is ~1.5 s for 300 files)."""
    ps = sorted(C.CARTS.glob("*.yaml"))
    sig = tuple((p.name, p.stat().st_mtime_ns) for p in ps)
    if sig == _cache["sig"]:
        return dict(_cache["data"])
    out = {}
    for p in ps:
        try:
            d = yaml.safe_load(p.read_text())
            if isinstance(d, dict) and d.get("id") and d.get("stages") is not None:
                out[d["id"]] = d
        except yaml.YAMLError:
            pass
    _cache.update(sig=sig, data=out)
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


def _verify(expr):
    v = sympy.sympify(expr)
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
