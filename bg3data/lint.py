"""Static stats lint: catch the values the engine silently drops, before the game ever loads them.

The game accepts any text in a stats file and quietly ignores what it doesn't understand (an invalid
Cooldown means no cooldown, an unknown StatsFunctorContext means the passive never fires). There's no
schema to check against, so the vocabulary is learned from everything the OTHER layers use (the base game
plus any mod layers below): a value no shipped content ever uses is very likely a typo or an invented name.

Checks for every entry a layer defines:
  - enum fields (Cooldown, StatsFunctorContext, RemoveEvents, SpellFlags, TickType, ...): unknown values
  - functors / conditions / boosts (any `Name(` in an expression field): unknown names
  - references: statuses in ApplyStatus/RemoveStatus/DownedStatus, UnlockSpell/UnlockInterrupt, using,
    ContainerSpells, SpellContainerID: must exist in the active layers
  - resources in UseCosts/Cost/HitCosts and ActionResource(...): must be used or defined somewhere
"""
import glob
import json
import os
import re

from . import sources

ENUM_FIELDS = {
    "Cooldown", "StatsFunctorContext", "RemoveEvents", "SpellFlags", "StatusPropertyFlags", "TickType", "DeathType",
    "InterruptContext", "InterruptContextScope", "InterruptDefaultValue", "Container", "SpellType", "StatusType",
    "VerbalIntent", "SpellSchool", "SpellStyleGroup", "HitAnimationType", "Sheathing", "SpellAnimationIntentType",
    "StatusGroups", "WeaponTypes", "SpellActionType", "AIFlags", "EnableContext", "ProjectileType",
    "SpellSoundMagnitude", "CastTextEvent", "PreviewCursor",
}
# "Properties" is an enum of flags on passives but a functor list on interrupts
PASSIVE_PROPERTIES = {"PassiveData"}
EXPR_FIELDS_SUFFIX = ("Conditions", "Condition")
EXPR_FIELDS = {
    "SpellProperties", "SpellSuccess", "SpellFail", "StatsFunctors", "OnApplyFunctors", "OnRemoveFunctors",
    "OnTickFunctors", "OnApplySuccess", "OnApplyFail", "OnSuccess", "OnRollsFailed", "OnTickSuccess", "OnTickFail",
    "Boosts", "SpellRoll", "TooltipDamageList", "TooltipAttackSave", "Roll", "Success", "Failure", "OnTickRoll",
    "ToggleOnFunctors", "ToggleOffFunctors", "SuccessProperties", "FailProperties",
}
KEYWORDS = {"IF", "NOT", "AND", "OR", "TARGET", "SELF", "GROUND", "SWAP", "AI_ONLY", "AI_IGNORE", "CAST", "CASTER"}
TARGET_ARGS = {"SELF", "SWAP", "TARGET", "SOURCE", "OBSERVER_OBSERVER", "OBSERVER_SOURCE", "OBSERVER_TARGET",
               "CASTER", "OWNER", "GROUND", "AI_ONLY", "AI_IGNORE"}
CALL = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*)\s*\(")
STATUS_REF = re.compile(r"\b(ApplyStatus|RemoveStatus|DownedStatus|StatusImmunity|RemoveUniqueStatus|ApplyEquipmentStatus)\(([^)]*)\)")
UNLOCK_REF = re.compile(r"\b(UnlockSpell|UnlockInterrupt|UnlockSpellVariant)\(\s*([A-Za-z0-9_]+)")
RES_COST = re.compile(r"([A-Za-z][A-Za-z0-9_]*):\d")
RES_BOOST = re.compile(r"\bActionResource(?:Override|Multiplier|Block|ReplenishTypeOverride)?\(\s*([A-Za-z][A-Za-z0-9_]*)")


def _fields(data):
    try:
        return json.loads(data)
    except ValueError:
        return {}


def _enum_values(field, value, typ):
    if field == "Properties" and typ not in PASSIVE_PROPERTIES:
        return []
    return [v.strip() for v in re.split(r"[;,]", value or "") if v.strip()]


def _is_expr(field, typ):
    return field in EXPR_FIELDS or field.endswith(EXPR_FIELDS_SUFFIX) or (field == "Properties" and typ == "InterruptData")


def _resource_names(cfg, mods):
    """Action resource names defined by mod layers' ActionResourceDefinitions (the base game's are learned from use)."""
    names = set()
    for m in cfg["mods"]:
        if m["name"] not in mods or m["path"].lower().endswith(".pak"):
            continue
        for f in glob.glob(os.path.join(m["path"], "Public", "*", "ActionResourceDefinitions", "*.lsx")):
            names |= set(re.findall(r'id="Name" type="FixedString" value="([^"]+)"', open(f, encoding="utf-8").read()))
    return names


def vocabulary(store, active, layer):
    """Everything the layers other than `layer` use: enum values per field, callable names, resources, entry names."""
    others = [l for l in active if l != layer]
    w, p = store._where(others)
    enums, calls, resources, fields = {}, set(), set(), {}
    for typ, data in store.db.execute(f"SELECT type, data FROM stats WHERE {w}", p):
        for k, v in _fields(data).items():
            fields.setdefault(typ, set()).add(k)
            if not isinstance(v, str):
                continue
            if k in ENUM_FIELDS or (k == "Properties" and typ in PASSIVE_PROPERTIES):
                enums.setdefault(k, set()).update(_enum_values(k, v, typ))
            if _is_expr(k, typ) or k in ("DescriptionParams", "TooltipStatusApply", "TooltipDamageList"):
                calls.update(CALL.findall(v))
            if k in ("UseCosts", "Cost", "HitCosts", "DualWieldingUseCosts", "RitualCosts"):
                resources.update(RES_COST.findall(v))
            resources.update(RES_BOOST.findall(v))
    resources |= _resource_names(store.cfg, active)
    wr, pr = store._where(active)
    resources |= {n for (n,) in store.db.execute(f"SELECT DISTINCT name FROM staticdata WHERE kind='ActionResourceDefinition' AND {wr}", pr) if n}
    wa, pa = store._where(active)
    names = {}
    for n, t in store.db.execute(f"SELECT DISTINCT name, type FROM stats WHERE {wa}", pa):
        names.setdefault(n, set()).add(t)
    return enums, calls, resources, names, fields


def lint_stats(store, active, layer, limit=200):
    enums, calls, resources, names, known_fields = vocabulary(store, active, layer)
    rows = store.db.execute("SELECT name, type, file, using_, data FROM stats WHERE layer=? ORDER BY file, name", (layer,)).fetchall()
    issues = []

    def add(kind, name, file, msg):
        issues.append((kind, name, os.path.basename(file or ""), msg))

    for name, typ, file, using, data in rows:
        f = _fields(data)
        if using and using != name and using not in names:
            add("REF", name, file, f"using '{using}' doesn't exist")
        for k, v in f.items():
            if typ in known_fields and k not in known_fields[typ]:
                add("FIELD", name, file, f"'{k}' isn't a field any other layer uses on {typ} (the engine ignores unknown fields)")
            if not isinstance(v, str) or not v:
                continue
            if k in enums and (k != "Properties" or typ in PASSIVE_PROPERTIES):
                for val in _enum_values(k, v, typ):
                    if val not in enums[k] and not (k == "StatusGroups" and val.startswith("SG_")):
                        add("ENUM", name, file, f"{k} '{val}' isn't used by any other layer (the engine may drop it)")
            if _is_expr(k, typ):
                for c in sorted(set(CALL.findall(v)) - calls - KEYWORDS):
                    add("CALL", name, file, f"{k}: '{c}(' isn't used by any other layer")
                for fn, args in STATUS_REF.findall(v):
                    for a in [x.strip() for x in args.split(",")]:
                        if re.fullmatch(r"[A-Z][A-Z0-9_]+", a) and a not in TARGET_ARGS:
                            if a not in names and not a.startswith("SG_"):
                                add("REF", name, file, f"{fn}: status '{a}' doesn't exist")
                            break
                for fn, ref in UNLOCK_REF.findall(v):
                    if ref not in names:
                        add("REF", name, file, f"{fn}: '{ref}' doesn't exist")
                for r in RES_BOOST.findall(v):
                    if r not in resources:
                        add("RES", name, file, f"ActionResource '{r}' isn't defined or used anywhere")
            if k in ("UseCosts", "Cost", "HitCosts"):
                for r in RES_COST.findall(v):
                    if r not in resources:
                        add("RES", name, file, f"{k}: resource '{r}' isn't defined or used anywhere")
            if k in ("ContainerSpells",):
                for ref in [x for x in v.split(";") if x]:
                    if ref not in names:
                        add("REF", name, file, f"ContainerSpells: '{ref}' doesn't exist")
            if k in ("SpellContainerID", "RootSpellID", "ConcentrationSpellID") and v not in names:
                add("REF", name, file, f"{k} '{v}' doesn't exist")
    by_kind = {}
    for kind, *_ in issues:
        by_kind[kind] = by_kind.get(kind, 0) + 1
    head = (f"stats lint for {layer}: {len(rows)} entries, {len(issues)} issue(s)"
            + (" (" + ", ".join(f"{v} {k}" for k, v in sorted(by_kind.items())) + ")" if issues else " - clean")
            + f"; vocabulary from {'+'.join(l for l in active if l != layer)}")
    lines = [head, "  ENUM/CALL/FIELD = value, name or field no other layer uses (likely silently dropped); REF = missing entry; RES = unknown resource"]
    lines += [f"  {kind:4} {name} [{file}]: {msg}" for kind, name, file, msg in issues[:limit]]
    if len(issues) > limit:
        lines.append(f"  ... {len(issues) - limit} more")
    return "\n".join(lines)
