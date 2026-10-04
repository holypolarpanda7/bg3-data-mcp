"""Offline checks for level_check's resource accounting (bg3data/testing.py). No game needed.

  UV_PROJECT_ENVIRONMENT=~/.cache/bg3-data-mcp/venv uv run python tests/level_check_selftest.py
"""
import sys

from bg3data.testing import _group, _resource_boosts

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


WARD = ("ActionResource(ArcaneWard,1,0);" + ";".join(
    f"IF(AbilityGreaterThan('Intelligence',{n},context.Source)):ActionResource(ArcaneWard,1,0)" for n in (13, 15, 17, 19, 21, 23)))
r = _resource_boosts(WARD, {"Intelligence": 17})
check("Arcane Ward at INT 17: 1 + 2 thresholds met = INT mod 3", sum(a for *_, a, on in r if on) == 3, r)
check("thresholds not met are inactive, not unknown", sum(1 for *_, on in r if on is False) == 4, r)
r = _resource_boosts(WARD, {"Intelligence": 20})
check("Arcane Ward at INT 20 = 5 (mod 5)", sum(a for *_, a, on in r if on) == 5, r)
r = _resource_boosts("ActionResource(SpellSlot,1,1);IF(HasStatus('X')):ActionResource(SpellSlot,1,2)", {"Intelligence": 17})
check("other conditions are unknown (None), not guessed", r == [("SpellSlot", 1, 1.0, True), ("SpellSlot", 2, 1.0, None)], r)
check("non-resource boosts are ignored", _resource_boosts("Advantage(AttackRoll);ProficiencyBonus(SavingThrow,Intelligence)", {}) == [])
check("an AbilityGreaterThan with no ability data stays unknown",
      _resource_boosts("IF(AbilityGreaterThan('Wisdom',13)):ActionResource(KiPoint,1,0)", {})[0][3] is None)
check("boosts from one passive are summed in the report", _group([("A", 1), ("B", 2), ("A", 1)]) == [("A", 2), ("B", 2)])

from bg3data.testing import MULTICLASS_SLOTS, _caster_mod


class FakeStore:
    MODS = {"Wizard": "1", "Cleric": "1", "Paladin": "0.5", "Fighter": "0", "EldritchKnight": "0.333"}

    def static(self, kind, name, active):
        return ("x", "y", {"MulticlassSpellcasterModifier": self.MODS[name]}) if name in self.MODS else None


st = FakeStore()
check("multiclass table at caster level 9 = 4/3/3/3/1 (as the game applied for Wizard 8 / Cleric 1)", MULTICLASS_SLOTS[9] == [4, 3, 3, 3, 1])
check("table has all 20 caster levels, 9th-level slot from 17", len(MULTICLASS_SLOTS) == 20 and len(MULTICLASS_SLOTS[17]) == 9)
check("full caster modifier 1", _caster_mod(st, None, {"class": "Wizard"}) == 1)
check("Paladin 5 -> 2 caster levels (half, rounded down per class)", int(5 * _caster_mod(st, None, {"class": "Paladin"})) == 2)
check("Eldritch Knight's modifier comes from the subclass", abs(_caster_mod(st, None, {"class": "Fighter", "subclass": "EldritchKnight"}) - 0.333) < 1e-9)
check("non-caster = 0", _caster_mod(st, None, {"class": "Fighter"}) == 0)

print(f"{len(FAILS)} FAILED" if FAILS else "all passed")
sys.exit(1 if FAILS else 0)
