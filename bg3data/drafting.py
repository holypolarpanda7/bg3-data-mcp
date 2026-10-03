"""Draft bg3 test cases (TOML) for features, from their stats.

A feature is a passive (directly, or every passive a progression node adds). What it unlocks and does decides
the case: a spell it unlocks is cast at a fitting target with saves forced to fail / attacks forced to hit, an
interrupt gets the attack or roll that triggers it, a rest/initiative functor gets the matching staging, plain
boosts get grant_passive or a damage check with a control. Each draft says how sure it is; `# TODO` marks what
stats alone can't decide. Lessons built in (2026-10-02): resources the feature spends but doesn't grant are set up,
reactions under test are listed in `reactions`, forced outcomes keep one roll decisive, damage checks get a
`-control` twin whose range can't overlap, values that scale with a class's level are flagged.
"""
import re

STD_RESOURCES = {"ActionPoint", "BonusActionPoint", "ReactionActionPoint", "Movement", "SpellSlot", "SpellSlotsGroup",
                 "WarlockSpellSlot", "ShadowSpellSlot"}
DAMAGE_SPELLS = {  # damage type -> (spell a wolf can cast at the host, raw range)
    "Fire": "Projectile_FireBolt", "Cold": "Projectile_RayOfFrost", "Lightning": "Target_ShockingGrasp",
    "Necrotic": "Target_ChillTouch", "Poison": "Projectile_RayOfSickness", "Radiant": "Target_SacredFlame",
    "Thunder": "Zone_Thunderwave", "Force": "Projectile_MagicMissile", "Acid": "Projectile_AcidSplash",
    "Psychic": "Target_ViciousMockery",
}
APPLY = re.compile(r"ApplyStatus\(\s*(?:(SELF|TARGET|SOURCE|OBSERVER_\w+)\s*,\s*)?([A-Z][A-Z0-9_]+)")
RESTORE = re.compile(r"RestoreResource\(\s*(?:SELF\s*,\s*)?(\w+)\s*,\s*(\d+)")
COST = re.compile(r"(\w+):(\d+)(?::(\d+))?")
UNLOCK_SPELL = re.compile(r"UnlockSpell\(\s*(\w+)")
UNLOCK_INT = re.compile(r"UnlockInterrupt\(\s*(\w+)")
GRANT = re.compile(r"ActionResource\(\s*(\w+)\s*,\s*(\d+)\s*,\s*(\d+)")
SAVE = re.compile(r"SavingThrow\(\s*Ability\.(\w+)")
RESIST = re.compile(r"Resistance\(\s*(\w+)\s*,\s*(Resistant|Immune)")
DICE = re.compile(r"DealDamage\(\s*(?:\w+\s*,\s*)?((\d+)d(\d+))")
ABILITY = re.compile(r"(?<![\w.])Ability\(\s*(\w+)\s*,\s*(\d+)")


def _fields(store, active, name):
    r = store.resolve(name, active)
    return ({k: str(v[0]) for k, v in (r.get("fields") or {}).items()}, r) if r else (None, None)


def _costs(text):
    out = []
    for name, n, lvl in COST.findall(text or ""):
        if name not in STD_RESOURCES:
            out.append((name, int(n), int(lvl or 0)))
    return out


def _scales(f):
    blob = " ".join(f.values())
    return "ClassLevel(" in blob or "LevelMapValue(" in blob or "Cause.LevelMapValue(" in blob


def _toml(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_toml(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml(x)}" for k, x in v.items()) + " }"
    raise TypeError(v)


class Draft:
    def __init__(self, cid, title):
        self.c = {"id": cid, "title": title}
        self.setup, self.expect, self.notes, self.todo = [], [], [], []
        self.confidence = "high"

    def lower(self, why):
        self.confidence = "low" if self.confidence == "medium" else "medium"
        self.todo.append(why)

    def render(self):
        c = dict(self.c)
        if self.setup:
            c["setup"] = self.setup
        c["expect"] = self.expect or [{"target": "host", "note": "TODO: what proves it worked"}]
        if self.notes:
            c["notes"] = " ".join(self.notes)
        lines = [f"# draft confidence: {self.confidence}"] + [f"# TODO: {t}" for t in self.todo] + ["[[case]]"]
        order = ["id", "title", "mode", "grant_passive", "spell", "caster", "target", "real_rolls", "combat", "reactions",
                 "ai_rounds", "sanctuary", "safety", "spawn", "setup", "casts", "expect", "notes"]
        for k in order + [k for k in c if k not in order]:
            if k in c:
                lines.append(f"{k} = {_toml(c[k])}")
        return "\n".join(lines) + "\n"


def _resource_setup(d, passive_grants, costs, required_empty=()):
    for name, n, lvl in costs:
        if name not in passive_grants:  # a pool the feature spends but doesn't grant (Seal, Risk...): give one
            d.setup.insert(0, {"target": "host", "boost": f"ActionResource({name},{max(n, 1) + 6},{lvl})"})
    for name in required_empty:
        d.setup.append({"target": "host", "resource": name, "amount": 0})


def _wolf(distance=6, faction="hostile", alias="A"):
    return {"as": alias, "template": "wolf", "faction": faction, "hp": 300, "distance": distance}


CONDITIONAL = re.compile(r"IF\(((?:[^():]|\([^()]*(?:\([^()]*\))*[^()]*\))*)\):\s*ApplyStatus\(\s*(?:\w+\s*,\s*)?([A-Z][A-Z0-9_]+)")


def _status_expects(text, target_alias, d=None):
    out = []
    conditional = {st: cond for cond, st in CONDITIONAL.findall(text or "")}
    for who, st in APPLY.findall(text or ""):
        if st in conditional and d is not None:
            d.lower(f"{st} only lands if `{conditional[st][:70]}` - set that up, or expect it absent")
        out.append(("host" if who in ("SELF", "OBSERVER_OBSERVER") else target_alias, st))
    return out


def spell_cases(store, active, passive, spell, grants, scales, prefix):
    f, _ = _fields(store, active, spell)
    if not f:
        return []
    if f.get("ContainerSpells"):
        kids = [x for x in f["ContainerSpells"].split(";") if x]
        out = []
        for child in (kids if len(kids) <= 4 else kids[:1]):  # many near-identical options: one stands for all
            out += spell_cases(store, active, passive, child, grants, scales, prefix)
        if len(kids) > 4 and out:
            out[0].notes.append(f"One of {len(kids)} options ({', '.join(k.split('_')[-1] for k in kids[:6])}...) stands for the rest.")
        return out
    tc, typ = f.get("TargetConditions", ""), f.get("SpellType", "")
    self_cast = typ == "Shout" or tc.strip().startswith("Self()")
    d = Draft(f"{prefix}-{spell.lower().replace('_', '-')}", f"{passive}: {spell}" if passive else spell)
    d.c["spell"] = spell
    if passive:
        d.setup.append({"target": "host", "passive": passive})
    if f.get("RequirementConditions", "").find("not Combat(") >= 0:
        d.c["combat"] = False
        d.notes.append("Out of combat only (a long casting time).")
    if "Dead()" in f.get("OriginTargetConditions", "") + tc and "not Dead()" not in f.get("OriginTargetConditions", "") + tc:
        d.lower("targets a dead creature: add a spawn with `dead = true` setup (and a friendly faction for allies)")
    target = "host"
    if not self_cast:
        ally = "Ally()" in tc and "not Ally()" not in tc
        melee = f.get("TargetRadius", "") in ("1.5", "MeleeMainWeaponRange") or "IsMelee" in f.get("SpellFlags", "")
        d.c["spawn"] = [_wolf(1.2 if melee else 6, "friendly" if ally else "hostile")]
        target = "A"
        if melee and not ally:
            d.setup.append({"target": "A", "boost": "ActionResourceBlock(ReactionActionPoint)"})
    d.c["target"] = target
    roll = f.get("SpellRoll", "")
    saves = SAVE.findall(roll)
    body = " ".join(f.get(k, "") for k in ("SpellProperties", "SpellSuccess"))
    for st, mine in re.findall(r"HasStatus\('(\w+)',\s*context\.Target(\s*,\s*context\.Source)?\)", tc):  # it only targets X-carriers
        if target != "host":
            d.setup.append({"target": target, "status": st, "turns": -1, **({"by": "host"} if mine else {})})
    for st in re.findall(r"RemoveStatus\(\s*(?:\w+\s*,\s*)?([A-Z][A-Z0-9_]+)\)", body):
        if any(x.get("status") == st for x in d.setup):
            d.expect.append({"target": target, "status_removed": [st]})
    if saves:
        d.c["real_rolls"] = True
        d.setup.append({"target": target, "boost": f"AbilityFailedSavingThrow({saves[0]})"})
        d.notes.append(f"The {saves[0]} save is forced to fail; add a -save twin without it for the success branch.")
    elif "Attack(" in roll:
        d.c["real_rolls"] = True
        kind = "RangedSpellAttack" if "SpellAttack" in roll and "Ranged" in roll else "MeleeSpellAttack" if "SpellAttack" in roll else "Attack"
        d.setup.append({"target": "host", "boost": f"RollBonus({kind},30)"})
        if target != "host":  # a critical hit doubles the dice and breaks exact damage ranges (seen 2026-10-03)
            d.setup.append({"target": target, "boost": "CriticalHit(AttackTarget,Success,Never)"})
    # requirements like "not HasActionResource('X'...)" need X empty first
    empty = re.findall(r"not\s+HasActionResource\('(\w+)'", f.get("RequirementConditions", ""))
    costs = _costs(f.get("UseCosts", ""))
    _resource_setup(d, grants, costs, empty)
    for who, st in _status_expects(body, target, d):
        d.expect.append({"target": who, "status_applied": [st]})
        sf_, _ = _fields(store, active, st)
        aura = re.findall(r"ApplyStatus\(\s*(?:\w+\s*,\s*)?([A-Z][A-Z0-9_]+)", (sf_ or {}).get("AuraStatuses", ""))
        if aura:  # an aura: an ally standing in it gets the buff
            d.c.setdefault("spawn", []).append(_wolf(3, "friendly", "B"))
            d.expect.append({"target": "B", "status_present": aura[:1]})
            d.notes.append(f"{st} is an aura: a friendly wolf 3 m away should get {aura[0]}.")
    for res, n in RESTORE.findall(body):
        d.expect.append({"resource": res, "level": 0, "amount_change": int(n)})
    if "DealDamage" in body and target != "host":
        m_ = DICE.search(body)
        lo_, hi_ = (int(m_.group(2)), int(m_.group(2)) * int(m_.group(3))) if m_ else (1, 300)
        d.expect.append({"target": target, "hp_change": [-min(300, hi_ * 2), -lo_]})
        if scales:
            d.lower("damage scales with a class level: on a host of another class it can be 0 - test on a real character")
    if "RegainHitPoints" in body and target == "host":
        d.setup.append({"target": "host", "hp": 1})
        d.c["safety"] = False
        d.expect.append({"target": "host", "hp_change": [1, 999]})
    for name, n, lvl in costs:
        d.expect.append({"resource": name, "level": lvl, "change": -n})
    if not d.expect:
        d.lower("the spell applies nothing a test can see from stats (Lua-driven?) - add the status/log it produces")
    if "TeleportSource" in body or "Teleport" in body:
        d.lower("teleports: the harness can't check positions - check a status/cast event instead")
    return [d]


def interrupt_case(store, active, passive, name, grants, scales, prefix):
    f, _ = _fields(store, active, name)
    if not f:
        return []
    cond, ctx = f.get("Conditions", ""), f.get("InterruptContext", "")
    short = re.sub(r"^Interrupt_", "", name)
    d = Draft(f"{prefix}-{short.lower().replace('_', '-')}", f"{passive}: {name}")
    d.c["reactions"] = [short]
    d.setup.append({"target": "host", "passive": passive})
    own = "Self(context.Source,context.Observer)" in cond.replace(" ", "") or "Self(context.Observer,context.Source)" in cond.replace(" ", "")
    if own:  # your own attack/spell roll triggers it
        if "IsSpell" in cond or "SpellAttack" in cond:
            d.c["spell"] = "Projectile_FireBolt"
            d.setup.append({"target": "host", "boost": "RollBonus(RangedSpellAttack,30)"})
        elif "IsUnarmedAttack" in cond:
            d.c["spell"] = "Target_UnarmedAttack"
            d.setup.append({"target": "host", "boost": "RollBonus(Attack,30)"})
        elif "IsRanged" in cond:
            d.c["spell"] = "Projectile_MainHandAttack"
            d.setup.append({"target": "host", "boost": "RollBonus(Attack,30)"})
        else:
            d.c["spell"] = "Target_MainHandAttack"
            d.setup.append({"target": "host", "boost": "RollBonus(Attack,30)"})
        melee = d.c["spell"] in ("Target_MainHandAttack", "Target_UnarmedAttack")
        d.c.update({"target": "A", "real_rolls": True, "spawn": [_wolf(1.2 if melee else 6)]})
        if melee:
            d.setup.append({"target": "A", "boost": "ActionResourceBlock(ReactionActionPoint)"})
        if "IsCritical" in cond:
            d.setup.append({"target": "host", "boost": "CriticalHit(AttackRoll,Success,ForcedAlways)"})
        for st in re.findall(r"HasStatus\('(\w+)',\s*context\.Target\)", cond):
            d.setup.append({"target": "A", "status": st, "turns": -1, "by": "host"})
        if "IsMiss" in cond and "not IsMiss" not in cond:
            d.setup[-1:] = [x for x in d.setup[-1:] if "RollBonus" not in x.get("boost", "")]
            d.setup.append({"target": "A", "boost": "AC(40)"})
            d.notes.append("Triggers on a miss: the target gets AC 40.")
    elif ctx == "OnSpellCast":
        d.c.update({"mode": "ai", "caster": "A", "target": "host", "ai_rounds": 6, "sanctuary": False,
                    "spawn": [_wolf(4)], "spell": "TODO_enemy_spell"})
        d.lower("reacts to an enemy's spell: give the case a spawn template that casts one (bg3_save_spells)")
    else:  # an enemy's attack or roll near you
        d.c.update({"mode": "ai", "caster": "A", "spell": "Target_Bite_Wolf", "target": "host", "ai_rounds": 8,
                    "sanctuary": False, "spawn": [_wolf(2)]})
        d.setup.insert(0, {"target": "host", "max_hp": 200})
        for st in re.findall(r"HasStatus\('(\w+)',\s*context\.Source\)", cond):
            d.setup.append({"target": "A", "status": st, "turns": -1, "by": "host"})
        d.notes.append("AI-driven: a miss or a roll outside the interrupt's window won't trigger it - "
                       "check the trigger (a status it applies, its cost) rather than downstream effects.")
    for st in re.findall(r"HasStatus\('(\w+)',\s*context\.Observer\)", cond):  # it only works while you have X
        d.setup.append({"target": "host", "status": st, "turns": -1})
    props = " ".join(f.get(k, "") for k in ("Properties", "Success"))
    for who, st in _status_expects(props, "A", d):
        tgt = "host" if who == "host" else ("A" if own else "host")
        if who == "A" and not own:
            tgt = "A"
        d.expect.append({"target": tgt, "status_applied": [st]})
    costs = _costs(f.get("Cost", ""))
    _resource_setup(d, grants, costs)
    for res, n, lvl in costs:
        rep = (store.static("ActionResourceDefinition", res, active) or (None, None, {}))[2].get("ReplenishType", "")
        if d.c.get("mode") == "ai" and rep == "Turn":  # refills on your turn before the run ends: unreadable
            d.notes.append(f"{res} refills every turn, so its cost can't be read after an AI run - the other checks prove it fired.")
            continue
        d.expect.append({"resource": res, "level": lvl, "amount_change": -n})
    if d.c.get("mode") == "ai" and re.search(r"SetRoll\(\s*1\s*\)|AdjustRoll\([^)]*-", props):
        d.expect.append({"target": "host", "damage_count": {"by": "A", "count": [0, 0]}})
        d.notes.append("It turns the hit into a miss, so the attacker never damages you.")
    if "DealDamage" in props and own:
        d.expect.append({"target": "A", "damage_count": {"by": "host", "count": [2, 9]}})
    if scales:
        d.lower("scales with a class level - weak on a host of another class")
    return [d]


def passive_cases(store, active, passive, prefix=None):
    f, r = _fields(store, active, passive)
    if not f:
        return [], [f"{passive}: not in the index"]
    prefix = prefix or "draft-" + passive.lower().replace("_", "-")
    boosts = f.get("Boosts", "")
    grants = {g[0] for g in GRANT.findall(boosts)}
    scales = _scales(f)
    out, skipped = [], []
    for sp in UNLOCK_SPELL.findall(boosts):
        out += spell_cases(store, active, passive, sp, grants, scales or _scales(_fields(store, active, sp)[0] or {}), prefix)
    for it in UNLOCK_INT.findall(boosts):
        out += interrupt_case(store, active, passive, it, grants, scales or _scales(_fields(store, active, it)[0] or {}), prefix)
    ctx, fun = f.get("StatsFunctorContext", ""), f.get("StatsFunctors", "")
    props = f.get("Properties", "")
    if "IsToggled" in props:
        on = APPLY.findall(f.get("ToggleOnFunctors", ""))
        d = Draft(prefix + "-toggle", f"{passive}: toggled on")
        d.setup.append({"target": "host", "passive": passive})
        for _, st in on:
            d.setup.append({"target": "host", "status": st, "turns": -1})
        d.lower("a toggle: say what it changes while on (it's often Lua-driven)")
        out.append(d)
    if "OnCombatStarted" in ctx:
        d = Draft(prefix + "-initiative", f"{passive}: rolling Initiative")
        d.c.update({"combat": "after_setup", "spawn": [_wolf(18)]})
        d.setup.append({"target": "host", "passive": passive})
        for res, n in RESTORE.findall(fun):
            if res not in grants:
                d.setup.insert(0, {"target": "host", "boost": f"ActionResource({res},6,0)"})
            d.setup.append({"target": "host", "resource": res, "amount": 0})
            d.expect.append({"resource": res, "level": 0, "amount_change": int(n)})
        for who, st in _status_expects(fun, "host"):
            d.expect.append({"target": "host", "status_applied": [st]})
        d.notes.append("18 m away: a hostile the host can see starts the fight during staging.")
        out.append(d)
    if any(x in ctx for x in ("OnCreate", "OnShortRest", "OnLongRest")) and APPLY.search(fun):
        d = Draft(prefix + "-standing", f"{passive}: its standing status")
        d.c["grant_passive"] = passive
        for _, st in APPLY.findall(fun):
            d.expect.append({"target": "host", "status_present": [st]})
        d.notes.append("Gained at level-up, OnCreate doesn't run: check the feature's Lua applies it then.")
        out.append(d)
    if "OnAttack" in ctx or "OnDamage" in ctx:
        d = Draft(prefix + "-on-hit", f"{passive}: on your hit")
        ranged = "Ranged" in f.get("Conditions", "") + fun
        d.c.update({"spell": "Projectile_MainHandAttack" if ranged else "Target_MainHandAttack", "target": "A", "real_rolls": True,
                    "spawn": [_wolf(6 if ranged else 1.2)]})
        d.setup += [{"target": "host", "passive": passive}, {"target": "host", "boost": "RollBonus(Attack,30)"}]
        if not ranged:
            d.setup.append({"target": "A", "boost": "ActionResourceBlock(ReactionActionPoint)"})
        if "Critical" in f.get("Conditions", "") + fun:
            d.setup.append({"target": "host", "boost": "CriticalHit(AttackRoll,Success,ForcedAlways)"})
        for who, st in _status_expects(fun, "A", d):
            d.expect.append({"target": "host" if who == "host" else "A", "status_applied": [st]})
        for res, n in RESTORE.findall(fun):
            d.expect.append({"resource": res, "level": 0, "amount_change": int(n)})
        for sv in SAVE.findall(fun):
            d.setup.append({"target": "A", "boost": f"AbilityFailedSavingThrow({sv})"})
        if not d.expect:
            d.lower("say what the hit does")
        out.append(d)
    # advantage on your attacks under a condition (Precise Hunter: vs your Hunter's Mark target)
    for cond_, kind in re.findall(r"IF\(([^:]*)\):\s*(Advantage|Disadvantage)\(\s*AttackRoll", boosts):
        flag = "Attack" + kind
        for suffix, with_passive in (("", True), ("-control", False)):
            d = Draft(f"{prefix}-attack-{kind.lower()}{suffix}", f"{passive}: {kind} on attacks{'' if with_passive else ' (control, no feature)'}")
            d.c.update({"spell": "Target_MainHandAttack", "target": "A", "real_rolls": True, "spawn": [_wolf(1.2)]})
            d.setup = [{"target": "host", "boost": "RollBonus(Attack,30)"}, {"target": "A", "boost": "ActionResourceBlock(ReactionActionPoint)"}]
            if with_passive:
                d.setup.insert(0, {"target": "host", "passive": passive})
            sts = re.findall(r"HasStatus\('(\w+)',\s*context\.Target", cond_)
            for st in sts:
                d.setup.append({"target": "A", "status": st, "turns": -1, "by": "host"})
            if not sts:
                d.lower(f"set up the condition `{cond_[:80]}`")
            d.expect.append({"target": "A", "hits": {"by": "host", "flag": flag, "count": [1, 9]} if with_passive
                             else {"by": "host", "flag": flag, "count": [1, 9], "absent": True}})
            d.notes.append("Hit flags show whether the attack roll had " + kind.lower() + ".")
            out.append(d)
    # plain boosts
    ab = ABILITY.findall(boosts)
    if ab:
        d = Draft(prefix + "-ability", f"{passive}: ability increase")
        d.c["grant_passive"] = passive
        for name, n in ab:
            d.expect.append({"target": "host", "ability": name, "change": int(n)})
        out.append(d)
    resists = []  # (type, level, condition or "") per boost, so a resistance carries its own IF
    for b in [x.strip() for x in boosts.split(";") if x.strip()]:
        m = re.match(r"(?:IF\((.*)\):)?\s*Resistance\(\s*(\w+)\s*,\s*(Resistant|Immune)", b)
        if m:
            resists.append((m.group(2), m.group(3), m.group(1) or ""))
    resists.sort(key=lambda r: bool(r[2]))  # unconditional first
    usable = [r for r in resists if r[0] in DAMAGE_SPELLS]
    if resists and not usable:
        skipped.append(f"{passive}: Resistance({', '.join(r[0] for r in resists)}) - no stock spell for those types")
    if usable:
        dtype, level, cond_text = usable[0]
        if len(resists) > 1:
            skipped.append(f"{passive}: drafted {dtype} {level} only, of {len(resists)} resistance boosts "
                           f"({', '.join(r[0] + (' if ' + r[2][:30] if r[2] else '') for r in resists)})")
        sp = DAMAGE_SPELLS[dtype]
        sf, _ = _fields(store, active, sp)
        m = DICE.search(" ".join((sf or {}).get(k, "") for k in ("SpellSuccess", "SpellProperties", "TooltipDamageList")))
        n, die = (int(m.group(2)), int(m.group(3))) if m else (1, 10)
        lo, hi = n, n * die
        bonus = max(0, hi - 2 * lo + 1)  # halved max stays below full min
        full = [-(hi + bonus), -(lo + bonus)]
        half = [-((hi + bonus) // 2), -((lo + bonus) // 2)]
        cond = cond_text
        base = {"spell": sp, "caster": "A", "target": "host", "spawn": [_wolf(4)]}
        for suffix, with_passive in (("", True), ("-control", False)):
            d = Draft(f"{prefix}-{dtype.lower()}{suffix}", f"{passive}: {dtype} {'resistance' if with_passive else '(control, no feature)'}")
            d.c.update(base)
            d.setup = [{"target": "host", "remove_status": "ARCANE_WARD*"}, {"target": "host", "max_hp": 100}]
            if bonus:
                d.setup.append({"target": "A", "boost": f"DamageBonus({bonus})"})
            if with_passive:
                d.setup.insert(1, {"target": "host", "passive": passive})
            if cond:
                c_ = cond
                if "BLOODED" in c_:
                    d.setup.append({"target": "host", "hp": 40})
                    d.c["safety"] = False
                for st in re.findall(r"HasStatus\('(\w+)'", c_):
                    if st != "BLOODED":
                        d.setup.append({"target": "host", "status": st, "turns": -1})
                if not re.search(r"HasStatus\('", c_):
                    d.lower(f"the resistance needs `{c_[:80]}`: set that up")
            d.expect.append({"target": "host", "hp_change": ([0, 0] if level == "Immune" else half) if with_passive else full})
            d.notes.append(f"{sp} rolls {n}d{die}" + (f"; the flat +{bonus} keeps halved and full damage apart." if bonus else "."))
            out.append(d)
    if not out:
        skipped.append(f"{passive}: nothing a draft can test from its stats ({boosts[:60] or 'no Boosts'}) - "
                       "a marker passive (Lua/other features read it)?")
    return out, skipped


def drafts(store, active, passives=None, key=None, level=None):
    names = list(passives or [])
    if key:
        for lvl, _name, _table, _src, attrs in store.progression(key, active, level):
            for p in re.split(r"[;,]", attrs.get("PassivesAdded", "")):
                if p.strip() and p.strip() not in names:
                    names.append(p.strip())
    if not names:
        return "no passives (give passives, or a class/subclass key and level)"
    from . import testing
    out, skipped = [], []
    for p in names:
        f_, r_ = _fields(store, active, p)
        if r_ and (r_.get("type") == "SpellData" or (f_ or {}).get("SpellType")):  # a spell: draft its cast directly
            cases, sk = spell_cases(store, active, None, p, set(), _scales(f_), "draft"), []
        else:
            cases, sk = passive_cases(store, active, p)
        skipped += sk
        for d in cases:
            errs = testing.validate(store, active, {**d.c, "setup": d.setup, "expect": d.expect})
            warns = testing.design_warnings(store, active, {**d.c, "setup": d.setup, "expect": d.expect})
            for e in errs:
                d.todo.append("invalid: " + e)
            for w in warns:
                d.todo.append("design: " + w)
            out.append(d.render())
    head = ["# Drafted by bg3_test_draft from stats. Review every case: confidence and TODOs say what to check.",
            f"# {len(out)} case(s) for: {', '.join(names)}"]
    head += [f"# skipped: {s}" for s in skipped]
    return "\n".join(head) + "\n\n" + "\n".join(out)
