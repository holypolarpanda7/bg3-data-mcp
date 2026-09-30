# bg3-data-mcp in-game ground-truth report

Run 2026-09-30 14:35 CDT. Layers compared: base+dnd55e+apotheosis (entries defined by none skipped).
652 entries (10 overridden by a mod), 10298 fields, 52 mismatches (0.50%), 656 functor fields not comparable (game exposes parsed userdata), 0 entries not loaded in game; 36.1s of game round-trips.

## Mismatches by field
- SpellFlags: 7
- TargetRadius: 5
- Cooldown: 4
- ContainerSpells: 3
- TickType: 2
- TargetConditions: 2
- Icon: 2
- TooltipDamageList: 2
- CastSound: 2
- TargetSound: 2
- CastTextEvent: 2
- SpellAnimation: 2
- VerbalIntent: 2
- SaveDC: 2
- HitAnimationType: 2
- MemoryCost: 2
- RemoveEvents: 1
- DeathType: 1
- TooltipHealList: 1
- SpellProperties: 1
- DescriptionParams: 1
- TooltipStatusApply: 1
- CycleConditions: 1
- SpellRoll: 1
- StatsFunctorContext: 1

## Mismatches by layer that set the field
- apotheosis: 46
- dnd55e: 6

## Examples
- Shout_DivineForeknowledge.Cooldown [apotheosis]: index='OncePerLongRest' game='None'
- Shout_Wish_GreaterDivineIntervention.Cooldown [apotheosis]: index='OncePerLongRest' game='OncePerCombat'
- BRUTAL_STRIKE_STAGGERED.RemoveEvents [apotheosis]: index='OnSavingThrowRolled' game='[]'
- MM_HOLD.TickType [apotheosis]: index='None' game='EndTurn'
- MM_SLOW.TickType [apotheosis]: index='None' game='EndTurn'
- Projectile_Apotheosis_WarpingImplosion.DeathType [apotheosis]: index='Explosion' game='None'
- Shout_Apo_MagnificentMansion.TooltipHealList [apotheosis]: index='RegainHitPoints(4d8)' game=None
- Shout_Apotheosis_TelekineticThrust.Cooldown [apotheosis]: index='OncePerLongRest' game='None'
- Shout_KeeperOfSouls_Heal.SpellFlags [apotheosis]: index='ImmediateCast;CannotTargetSelf;NoAOEDamageOnLand' game='["NoAOEDamageOnLand","ImmediateCast"]'
- Target_AnimateDead_7.ContainerSpells [dnd55e]: index='Target_AnimateDead_YMS_Skeleton;Target_AnimateDead_YMS_Zombie' game='Target_AnimateDead_Skeleton;Target_AnimateDead_Zombie'
- Target_AnimateDead_7.TargetRadius [dnd55e]: index='9' game='3'
- Target_AnimateDead_8.ContainerSpells [dnd55e]: index='Target_AnimateDead_YMS_Skeleton;Target_AnimateDead_YMS_Zombie' game='Target_AnimateDead_Skeleton;Target_AnimateDead_Zombie'
- Target_AnimateDead_8.TargetRadius [dnd55e]: index='9' game='3'
- Target_AnimateDead_9.ContainerSpells [dnd55e]: index='Target_AnimateDead_YMS_Skeleton;Target_AnimateDead_YMS_Zombie' game='Target_AnimateDead_Skeleton;Target_AnimateDead_Zombie'
- Target_AnimateDead_9.TargetRadius [dnd55e]: index='9' game='3'
- Target_Apo_Befuddlement.SpellProperties [apotheosis]: index='DealDamage(4d6,Psychic);ApplyStatus(FEEBLEMIND,100,-1)' game=None
- Target_Apo_Befuddlement.TargetRadius [apotheosis]: index='45' game=''
- Target_Apo_Befuddlement.TargetConditions [apotheosis]: index='Character() and not Dead() and not Self()' game=''
- Target_Apo_Befuddlement.Icon [apotheosis]: index='Spell_Enchantment_CrownOfMadness' game=''
- Target_Apo_Befuddlement.DescriptionParams [apotheosis]: index='DealDamage(4d6,Psychic)' game=''
- Target_Apo_Befuddlement.TooltipDamageList [apotheosis]: index='DealDamage(4d6,Psychic)' game=''
- Target_Apo_Befuddlement.TooltipStatusApply [apotheosis]: index='ApplyStatus(FEEBLEMIND,100,-1)' game=''
- Target_Apo_Befuddlement.CastSound [apotheosis]: index='Spell_Cast_Damage_Psychic_FingerOfDeath_L7to9' game=''
- Target_Apo_Befuddlement.TargetSound [apotheosis]: index='Spell_Impact_Damage_Psychic_FingerOfDeath_L7to9' game=''
- Target_Apo_Befuddlement.CastTextEvent [apotheosis]: index='Cast' game=''
- Target_Apo_Befuddlement.CycleConditions [apotheosis]: index='Enemy() and not Dead()' game=''
- Target_Apo_Befuddlement.SpellAnimation [apotheosis]: index='554a18f7-952e-494a-b301-7702a85d4bc9,,;,,;e334be54-dd63-4bab-8009-de0f9ced73ce,12afdff1-6b1b-430a-9161-63ae25be4c32,76073f60-94ba-411a-b3fb-688f75772bfa;ed314891-666b-4e97-84a4-15353a3d999a,,;22dfbbf4-f417-4c84-b39e-2039315961e6,,;,,;5bfbe9f9-4fc3-4f26-b112-43d404db6a89,,;,,;,,' game=''
- Target_Apo_Befuddlement.VerbalIntent [apotheosis]: index='Damage' game='None'
- Target_Apo_Befuddlement.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;HasHighGroundRangeExtension;IsHarmful' game='[]'
- Target_Apo_Befuddlement.SpellRoll [apotheosis]: index='Attack(AttackType.RangedSpellAttack)' game=None
- Target_Apo_Befuddlement.SaveDC [apotheosis]: index='14' game=None
- Target_Apo_Befuddlement.HitAnimationType [apotheosis]: index='MagicalDamage_Internal' game='Default'
- Target_Apo_Befuddlement.MemoryCost [apotheosis]: index='1' game='0'
- Target_Apo_TrueResurrection.TargetRadius [apotheosis]: index='1.5' game=''
- Target_Apo_TrueResurrection.TargetConditions [apotheosis]: index="Dead() and not Tagged('YOURFACTION') and not Tagged('YOURFACTION')" game=''
- Target_Apo_TrueResurrection.Icon [apotheosis]: index='Spell_Necromancy_Revivify' game=''
- Target_Apo_TrueResurrection.TooltipDamageList [apotheosis]: index='RegainHitPoints(1)' game=''
- Target_Apo_TrueResurrection.CastSound [apotheosis]: index='Spell_Cast_Healing_Heal_L6to8' game=''
- Target_Apo_TrueResurrection.TargetSound [apotheosis]: index='Spell_Impact_Healing_Revivify_L1to3' game=''
- Target_Apo_TrueResurrection.CastTextEvent [apotheosis]: index='Cast' game=''
- Target_Apo_TrueResurrection.SpellAnimation [apotheosis]: index='414bbf02-2918-4f01-83fb-1ddc7a588d88,,;,,;8252328a-66dd-4dc0-bbe0-00eea3204922,,;982d842b-5d44-4ef6-ab33-14d5ae514a50,,;0c5dcc83-fa78-41da-b6a5-440b5ea30936,,;,,;bea988a0-2ec5-40d8-a67e-ffbd7454bc53,,;bcc3b0d9-f04f-4448-aab0-e0ad641167cc,,;bf924cc6-8b39-4c3b-b1c0-eda264cf6150,,' game=''
- Target_Apo_TrueResurrection.VerbalIntent [apotheosis]: index='Healing' game='None'
- Target_Apo_TrueResurrection.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsMelee' game='[]'
- Target_Apo_TrueResurrection.HitAnimationType [apotheosis]: index='MagicalNonDamage' game='Default'
- Target_Apo_TrueResurrection.MemoryCost [apotheosis]: index='1' game='0'
- Target_Apotheosis_ChemicalMastery.Cooldown [apotheosis]: index='OncePerLongRest' game='None'
- Target_Feeblemind.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;HasHighGroundRangeExtension;IsHarmful' game='["HasSomaticComponent","HasVerbalComponent","IsSpell","IsHarmful","HasHighGroundRangeExtension"]'
- Target_Feeblemind.SaveDC [apotheosis]: index='14' game=None
- Target_ProjectImage.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsConcentration' game='["IsConcentration","HasSomaticComponent","HasVerbalComponent","IsSpell"]'
- Target_Regenerate.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsMelee' game='["IsMelee","HasSomaticComponent","HasVerbalComponent","IsSpell"]'
- Target_Resurrection.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsMelee' game='["IsMelee","HasSomaticComponent","HasVerbalComponent","IsSpell"]'
- WildMagic_SurgeOfUndeath_Passive.StatsFunctorContext [apotheosis]: index='OnDeath' game='[]'

## Not loaded in game
