"""Spell containers (pickers): which children behave alike (2026-10-05).

A container's children often differ only cosmetically - a damage type (Dragon's Breath), the spell or status a picker
option names (Magic School Mastery, Siberys) - and one test stands for all of them. Children whose mechanics differ (Wish's
options, Lay on Hands' Cure vs Restore) each need their own. `groups` splits the children by a behaviour signature: their
resolved fields minus presentation, with damage types and each child's own distinguishing name token blanked out.
Used by testing.lint_container_tests (coverage) and drafting.spell_cases (one draft per group).
"""
import json
import re

DAMAGE = ["Acid", "Bludgeoning", "Cold", "Fire", "Force", "Lightning", "Necrotic", "Piercing", "Poison", "Psychic", "Radiant",
          "Slashing", "Thunder"]
# presentation and container bookkeeping: never behaviour
COSMETIC = re.compile(r"Sound|Effect|Animation|Visual|Prepare|CastText|Beam|Icon|DisplayName|Description|Tooltip|Vocal|Memory|"
                      r"Hover|Sheathing|Trajectories|PreviewCursor|Textures|Material|StyleGroup|VerbalIntent|SpellContainerID|"
                      r"ContainerSpells|DamageType|DescriptionParams|CombatAIOverrideSpell|AiCalculationSpellOverride")
DMG_RE = re.compile(r"\b(" + "|".join(DAMAGE) + r")\b", re.I)


def kids(store, active, container):
    r = store.resolve(container, active)
    cs = (r or {"fields": {}})["fields"].get("ContainerSpells", ("",))[0] or ""
    return [k for k in cs.split(";") if k]


def _parts(name):
    return [p for p in re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", name) if len(p) > 2]


def _tokens(names):
    """Each name's own part: its tail between the siblings' common leading and trailing `_` segments
    (Sib_Pick_Projectile_Catapult -> Projectile_Catapult, DisguiseSelf_Tiefling_Male_SM -> Tiefling_Male); signature()
    falls back to the name parts no sibling has (RestoreBlinded -> Blinded, SmallHeal -> Small) when the tail isn't in
    any field."""
    count = {}
    for n in names:
        for p in set(_parts(n)):
            count[p] = count.get(p, 0) + 1
    segs = [n.split("_") for n in names]
    pre = 0
    while segs and all(len(x) > pre + 1 for x in segs) and len({x[pre] for x in segs}) == 1:
        pre += 1
    suf = 0
    while segs and all(len(x) > pre + suf + 1 for x in segs) and len({x[-1 - suf] for x in segs}) == 1:
        suf += 1
    out = {}
    for n, x in zip(names, segs):
        tail = "_".join(x[pre:len(x) - suf]) if len(names) > 1 else ""
        out[n] = ([tail] if len(tail) > 2 else [], [p for p in set(_parts(n)) if count[p] == 1])
    return out


def signature(store, active, child, tokens):
    r = store.resolve(child, active)
    if not r:
        return json.dumps({"missing": True})
    fields = {k: str(v) for k, (v, _src) in sorted(r["fields"].items()) if not COSMETIC.search(k) and v not in (None, "")}
    tail, parts = tokens
    blob = " ".join(fields.values()).lower()
    use = tail if tail and any(t.lower() in blob for t in tail) else parts
    out = {}
    for k, v in fields.items():
        for t in sorted(use, key=len, reverse=True):  # as a whole name part: "Ice" (Ice Knife) not inside "Choice"
            for form in {t, t.upper(), t.lower()}:
                v = re.sub(r"(?<![a-z])" + re.escape(form) + r"(?![a-z])" if not form.isupper() else
                           r"(?<![A-Z])" + re.escape(form) + r"(?![A-Z])", "<X>", v)
        out[k] = DMG_RE.sub("<D>", v)
    return json.dumps(out, sort_keys=True)


def groups(store, active, container):
    """[[child, ...], ...]: the container's children grouped by behaviour, in container order."""
    ks = kids(store, active, container)
    tok = _tokens(ks)
    by = {}
    for k in ks:
        by.setdefault(signature(store, active, k, tok[k]), []).append(k)
    return list(by.values())
