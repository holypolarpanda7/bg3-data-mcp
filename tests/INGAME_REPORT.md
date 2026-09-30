# bg3-data-mcp in-game ground-truth report

Run 2026-09-30 16:05 CDT. Layers compared: base+dnd55e+apotheosis (entries defined by none skipped).
652 entries (7 overridden by a mod), 10326 fields, 26 mismatches (0.25%), 657 functor fields not comparable (game exposes parsed userdata), 0 entries not loaded in game; 35.5s of game round-trips.

## Mismatches by field
- SpellFlags: 7
- Cooldown: 4
- ContainerSpells: 3
- TargetRadius: 3
- TickType: 2
- SaveDC: 2
- RemoveEvents: 1
- Description: 1
- DeathType: 1
- TooltipHealList: 1
- StatsFunctorContext: 1

## Mismatches by layer that set the field
- apotheosis: 19
- dnd55e: 6
- base/Shared: 1

## Examples
- Shout_DivineForeknowledge.Cooldown [apotheosis]: index='OncePerLongRest' game='None'
- Shout_Wish_GreaterDivineIntervention.Cooldown [apotheosis]: index='OncePerLongRest' game='None'
- BRUTAL_STRIKE_STAGGERED.RemoveEvents [apotheosis]: index='OnSavingThrowRolled' game='[]'
- MM_HOLD.TickType [apotheosis]: index='None' game='EndTurn'
- MM_SLOW.Description [base/Shared]: index='hf0d5c7f8g9e5fg41e4g99d5gd97fe3698321;7' game='hc527804eg91bbg435dgb2fbg1f64d787dddd'
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
- Target_Apo_Befuddlement.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;HasHighGroundRangeExtension;IsHarmful' game='["HasSomaticComponent","HasVerbalComponent","IsSpell","IsHarmful","HasHighGroundRangeExtension"]'
- Target_Apo_Befuddlement.SaveDC [apotheosis]: index='14' game=None
- Target_Apo_TrueResurrection.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsMelee' game='["IsMelee","HasSomaticComponent","HasVerbalComponent","IsSpell"]'
- Target_Apotheosis_ChemicalMastery.Cooldown [apotheosis]: index='OncePerLongRest' game='None'
- Target_Feeblemind.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;HasHighGroundRangeExtension;IsHarmful' game='["HasSomaticComponent","HasVerbalComponent","IsSpell","IsHarmful","HasHighGroundRangeExtension"]'
- Target_Feeblemind.SaveDC [apotheosis]: index='14' game=None
- Target_ProjectImage.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsConcentration' game='["IsConcentration","HasSomaticComponent","HasVerbalComponent","IsSpell"]'
- Target_Regenerate.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsMelee' game='["IsMelee","HasSomaticComponent","HasVerbalComponent","IsSpell"]'
- Target_Resurrection.SpellFlags [apotheosis]: index='HasVerbalComponent;HasSomaticComponent;HasMaterialComponent;IsSpell;IsMelee' game='["IsMelee","HasSomaticComponent","HasVerbalComponent","IsSpell"]'
- WildMagic_SurgeOfUndeath_Passive.StatsFunctorContext [apotheosis]: index='OnDeath' game='[]'

## Not loaded in game
