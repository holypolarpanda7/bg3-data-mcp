-- bg3-data-mcp in-game test harness (server context). Mod-agnostic: uses only Osi.* and Ext.*.
-- Installed by bg3data/testing.py as a loose file and loaded through the SE console; lives in the
-- global BG3T until the Lua state resets (game restart / console `reset`), then gets re-installed.
-- Everything it creates is tracked so cleanup() can undo it: spawns, boosts (tag "BG3Test"), statuses.
local VERSION = "__VERSION__"
if BG3T and BG3T.version == VERSION then return "present" end

local T = BG3T or {}
BG3T = T
T.version = VERSION
T.TAG = "BG3Test"
T.spawns = T.spawns or {}      -- alias -> guid
T.grants = T.grants or {}      -- { {guid, boost} }
T.applied = T.applied or {}    -- { {guid, status} }
T.added_passives = T.added_passives or {}  -- { {guid, passive} } added by tests (T.passives() is the snapshot function)
T.events = T.events or {}
T.seq = T.seq or 0
T.recording = T.recording or false
T.safety = T.safety or { enabled = true, floor = 35, tripped = false }
T.rolls = T.rolls or {}        -- tag -> { pass, fail, n } from passive rolls requested by T.roll

local NULL = "NULL_00000000-0000-0000-0000-000000000000"

local function uuid(g)
    if type(g) ~= "string" then return nil end
    return #g >= 36 and g:sub(-36) or g
end
T.uuid = uuid

function T.host() return uuid(Osi.GetHostCharacter()) end

local function isTracked(g)
    g = uuid(g)
    if g == T.host() then return "host" end
    for alias, s in pairs(T.spawns) do if s == g then return alias end end
    return nil
end

local function push(ev)
    T.seq = T.seq + 1
    ev.seq = T.seq
    ev.ms = Ext.Utils.MonotonicTime()
    T.events[#T.events + 1] = ev
    if #T.events > 800 then table.remove(T.events, 1) end
end

-- ------------------------------------------------------------------ snapshots
function T.resources(g)
    local out = {}
    local e = Ext.Entity.Get(g)
    if not (e and e.ActionResources) then return out end
    for u, list in pairs(e.ActionResources.Resources) do
        local d = Ext.StaticData.Get(u, "ActionResource")
        local name = d and d.Name or tostring(u)
        out[name] = out[name] or {}
        for _, x in ipairs(list) do out[name][tostring(x.Level)] = { x.Amount, x.MaxAmount } end
    end
    return out
end

function T.statuses(g)
    local t = {}
    local e = Ext.Entity.Get(g)
    pcall(function() for _, v in pairs(e.StatusContainer.Statuses) do t[#t + 1] = tostring(v) end end)
    return t
end

function T.passives(g)
    local t = {}
    local e = Ext.Entity.Get(g)
    pcall(function() for _, p in ipairs(e.PassiveContainer.Passives) do t[#t + 1] = p.Passive.PassiveId end end)
    return t
end

function T.spells(g)
    local t = {}
    local e = Ext.Entity.Get(g)
    pcall(function()
        for _, s in ipairs(e.SpellBook.Spells) do
            t[#t + 1] = { id = s.Id.Prototype, source = tostring(s.Id.SourceType) }
        end
    end)
    return t
end

function T.classes(g)
    local t = {}
    local e = Ext.Entity.Get(g)
    pcall(function()
        for _, c in ipairs(e.Classes.Classes) do
            local cd = Ext.StaticData.Get(c.ClassUUID, "ClassDescription")
            local sd = c.SubClassUUID and Ext.StaticData.Get(c.SubClassUUID, "ClassDescription")
            t[#t + 1] = {
                class = cd and cd.Name, class_table = cd and tostring(cd.ProgressionTableUUID),
                subclass = sd and sd.Name or nil, subclass_table = sd and tostring(sd.ProgressionTableUUID) or nil,
                level = c.Level,
            }
        end
    end)
    return t
end

function T.races(g)  -- race and subrace with their progression tables (they grant passives, spells and resources by character level)
    local t = {}
    pcall(function()
        local ccs = Ext.Entity.Get(g).CharacterCreationStats
        for _, k in ipairs({ "Race", "SubRace" }) do
            local r = ccs[k] and Ext.StaticData.Get(ccs[k], "Race")
            if r and r.ProgressionTableUUID then t[#t + 1] = { name = r.Name, table = tostring(r.ProgressionTableUUID) } end
        end
    end)
    return t
end

function T.snapshot(g, full)
    g = uuid(g)
    local s = {
        guid = g, hp = Osi.GetHitpoints(g), max_hp = Osi.GetMaxHitpoints(g), dead = Osi.IsDead(g) == 1,
        in_combat = Osi.IsInCombat(g) == 1, statuses = T.statuses(g), resources = T.resources(g),
    }
    pcall(function() s.temp_hp = Ext.Entity.Get(g).Health.TemporaryHp end)
    pcall(function() local x, y, z = Osi.GetPosition(g) s.pos = { x, y, z } end)
    pcall(function()  -- ability scores and skill bonuses, by name (exact checks for boosts like Boon of Skill)
        local st = Ext.Entity.Get(g).Stats
        s.abilities, s.skills = {}, {}
        for i, name in ipairs({ "None", "Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma" }) do
            if i > 1 then s.abilities[name] = st.Abilities[i] end
        end
        for name, v in pairs(Ext.Enums.SkillId) do
            if type(name) == "string" and name ~= "Sentinel" and name ~= "Invalid" then
                local ok, val = pcall(function() return st.Skills[v.Value + 1] end)
                if ok and val then s.skills[name] = val end
            end
        end
    end)
    if full then
        local e = Ext.Entity.Get(g)
        s.level = Osi.GetLevel(g)
        s.classes = T.classes(g)
        s.races = T.races(g)
        s.passives = T.passives(g)
        s.spells = T.spells(g)
        s.region = Osi.GetRegion(g)
        pcall(function() s.xp = e.Experience.TotalExperience end)
    end
    return s
end

-- Creatures `owner` has summoned (IsSummon.Summoner), with what a stat-block check needs.
function T.summons(owner)
    local oe, out = Ext.Entity.Get(uuid(owner)), {}
    for _, e in ipairs(Ext.Entity.GetAllEntitiesWithComponent("IsSummon")) do
        local ok, mine = pcall(function() return e.IsSummon.Summoner == oe end)
        if ok and mine then
            local g = e.Uuid.EntityUuid
            local s = { guid = g, stats = e.Data and e.Data.StatsId, template = Osi.GetTemplate(g), hp = Osi.GetHitpoints(g),
                        max_hp = Osi.GetMaxHitpoints(g), dead = Osi.IsDead(g) == 1, statuses = T.statuses(g),
                        passives = T.passives(g), spells = {} }
            for _, sp in ipairs(T.spells(g)) do s.spells[#s.spells + 1] = sp.id end
            pcall(function() s.ac = e.Resistances.AC end)
            pcall(function() s.level = e.EocLevel.Level end)
            out[#out + 1] = s
        end
    end
    return out
end

-- Give the newest summon this case created (optionally of Stats `stats`) a spawn alias, so casts/expectations can
-- name it; it is then removed at cleanup like a spawn.
function T.adoptSummon(alias, stats)
    local best
    for _, x in ipairs(T.summons(T.host())) do
        if not (T.preSummons or {})[x.guid] and (not stats or stats == "" or x.stats == stats) then best = x.guid end
    end
    if best then T.spawns[alias] = best end
    return best
end

-- Put `g` d metres from `target`, on the side facing the host (summon_as + near: ground casts go where the caster
-- faces while spawns are offset on world X, so a summon could land out of reach of the spawn it should hit).
function T.placeNear(g, target, d)
    local tx, ty, tz = Osi.GetPosition(target)
    local hx, _, hz = Osi.GetPosition(T.host())
    local dx, dz = hx - tx, hz - tz
    local len = math.sqrt(dx * dx + dz * dz)
    if len < 0.01 then dx, dz, len = 1, 0, 1 end
    d = d or 1.2
    Osi.TeleportToPosition(g, tx + dx / len * d, ty, tz + dz / len * d, "", 0, 0, 0, 0, 1)
    return true
end

-- Snapshot the host plus every spawn, keyed by alias; `_summons` = the host's summons. The first snapshot of a
-- case remembers which summons already existed, so cleanup only removes the ones the case created.
function T.world()
    local w = { host = T.snapshot(T.host()) }
    for alias, g in pairs(T.spawns) do w[alias] = T.snapshot(g) end
    local ok, sm = pcall(T.summons, T.host())
    w._summons = ok and sm or {}
    if not T.preSummons then
        T.preSummons = {}
        for _, x in ipairs(w._summons) do T.preSummons[x.guid] = true end
    end
    return w
end

-- ------------------------------------------------------------------ changes (all tracked)
function T.spawn(alias, template, faction, dx, dz)
    local h = T.host()
    local x, y, z = Osi.GetPosition(h)
    -- the requested spot can be blocked terrain (cliff, water, wall) depending on where the save left the host:
    -- try it, then the other directions and closer distances (2026-10-02: a new save made every +8 m spawn fail)
    dx, dz = dx or 6, dz or 0
    local tries = { { dx, dz }, { -dx, dz }, { dz, dx }, { dz, -dx } }
    for _, f in ipairs({ 0.6, 0.35 }) do
        for _, d in ipairs({ { dx, dz }, { -dx, dz }, { dz, dx }, { dz, -dx } }) do tries[#tries + 1] = { d[1] * f, d[2] * f } end
    end
    local g
    for _, d in ipairs(tries) do
        g = Osi.CreateAt(template, x + d[1], y, z + d[2], 0, 0, "")
        if g then break end
    end
    if not g then return nil, "CreateAt returned nothing for template " .. tostring(template) .. " (12 spots around the host)" end
    g = uuid(g)
    if faction and faction ~= "" then pcall(Osi.SetFaction, g, faction) end
    T.spawns[alias] = g
    return g
end

-- a real party member for a test (an origin companion already in the world): features that react to an ALLY dropping need
-- one - a spawned NPC dies at 0 HP instead of going Downed (2026-10-06). Recruited with the story's own party proc if it
-- isn't in the party, placed next to the host, and handed back at cleanup (revived, out of the party again, back where it
-- stood). Listed in T.spawns under its alias so cases address it like a spawn; cleanup never kills or deletes it.
T.borrowed = T.borrowed or {}  -- alias -> { guid, was_member, x, y, z }
function T.borrow(alias, g, dx, dz)
    g = uuid(g)
    if not Ext.Entity.Get(g) or Osi.IsDead(g) == 1 then return nil, "companion " .. g .. " isn't in this level or is dead" end
    local h = T.host()
    local was = #(Osi.DB_Players:Get(g) or {}) > 0
    local ox, oy, oz = Osi.GetPosition(g)
    if not was then
        local ok, err = pcall(function() Osi.PROC_GLO_PartyMembers_Add(g, h) end)
        if not ok then return nil, "couldn't add " .. g .. " to the party: " .. tostring(err) end
    end
    local x, y, z = Osi.GetPosition(h)
    if not pcall(Osi.TeleportToPosition, g, x + (dx or 2), y, z + (dz or 0), "", 0, 0, 0, 0, 1) then
        pcall(Osi.TeleportTo, g, h, "", 0, 0, 0, 0, 0)
    end
    T.revive(g)
    T.borrowed[alias] = { g, was, ox, oy, oz }
    T.spawns[alias] = g
    return g
end

local function giveBack()
    local n = 0
    for alias, b in pairs(T.borrowed) do
        T.spawns[alias] = nil
        pcall(Osi.LeaveCombat, b[1])
        T.revive(b[1])
        if not b[2] then
            pcall(function() Osi.PROC_GLO_PartyMembers_Remove(b[1], T.host(), 1) end)
            if #(Osi.DB_Players:Get(b[1]) or {}) > 0 then pcall(function() Osi.PROC_GLO_PartyMembers_Remove(b[1], T.host()) end) end
        end
        if b[3] then pcall(Osi.TeleportToPosition, b[1], b[3], b[4], b[5], "", 0, 0, 0, 0, 1) end
        n = n + 1
    end
    T.borrowed = {}
    return n
end

function T.grant(g, boost)
    g = uuid(g)
    Osi.AddBoosts(g, boost, T.TAG, g)
    T.grants[#T.grants + 1] = { g, boost }
end

function T.addPassive(g, passive)
    g = uuid(g)
    if Osi.HasPassive(g, passive) == 1 then return end  -- already owned: leave it alone at cleanup
    Osi.AddPassive(g, passive)
    T.added_passives[#T.added_passives + 1] = { g, passive }
end

function T.apply(g, status, turns, by)
    g = uuid(g)
    -- by: who applies it (the status's cause - e.g. a seal the host placed); default the target itself
    Osi.ApplyStatus(g, status, (turns or 10) * 6.0, 1, by and uuid(by) or g)
    T.applied[#T.applied + 1] = { g, status }
end

-- HP to an exact value; above max needs grant(IncreaseMaxHP) first and a tick before calling this.
-- set a resource's current amount (e.g. spell slots of a level to 0, to watch a refund)
function T.setResource(g, name, level, amount)
    g = uuid(g)
    local e = Ext.Entity.Get(g)
    local n = 0
    for u, entries in pairs(e.ActionResources.Resources) do
        local def = Ext.StaticData.Get(u, "ActionResource")
        if def and def.Name == name then
            for _, x in ipairs(entries) do if (x.ResourceId or 0) == (level or 0) then x.Amount = amount n = n + 1 end end
        end
    end
    e:Replicate("ActionResources")
    return n
end

function T.removeStatuses(g, pattern)
    g = uuid(g)
    local prefix = pattern:sub(-1) == "*" and pattern:sub(1, -2) or nil
    for _, s in ipairs(T.statuses(g)) do
        if s == pattern or (prefix and s:sub(1, #prefix) == prefix) then Osi.RemoveStatus(g, s) end
    end
end

-- Back on your feet with full HP. Removing DOWNED (or an Osiris HP change at 0 HP) can leave a character alive at
-- 0 HP that SetHitpoints doesn't move; writing Health and replicating does (2026-10-03).
function T.revive(g)
    g = uuid(g)
    for _, s in ipairs({ "DOWNED", "UNCONSCIOUS", "KNOCKED_OUT" }) do
        if Osi.HasActiveStatus(g, s) == 1 then pcall(Osi.RemoveStatus, g, s) end
    end
    pcall(Osi.SetHitpointsPercentage, g, 100)
    pcall(function()
        local e = Ext.Entity.Get(g)
        if Osi.GetHitpoints(g) < e.Health.MaxHp then e.Health.Hp = e.Health.MaxHp; e:Replicate("Health") end
    end)
    return Osi.GetHitpoints(g)
end

function T.setHp(g, hp)
    g = uuid(g)
    if hp == "full" then Osi.SetHitpointsPercentage(g, 100) else Osi.SetHitpoints(g, hp) end
end

-- Restore every action resource (spell slots, action points...) to its maximum: tests start fresh.
function T.refill(g)
    g = uuid(g)
    local e = Ext.Entity.Get(g)
    for _, list in pairs(e.ActionResources.Resources) do
        for _, x in ipairs(list) do x.Amount = x.MaxAmount end
    end
    e:Replicate("ActionResources")
end

-- Spell cooldowns ("until rest" ones too): cleared before a case so it can be repeated, and after it so
-- test casts don't leave cooldowns in a save.
function T.clearCooldowns(g)
    g = uuid(g)
    local e = Ext.Entity.Get(g)
    local n = 0
    pcall(function()
        n = #e.SpellBookCooldowns.Cooldowns
        e.SpellBookCooldowns.Cooldowns = {}
        e:Replicate("SpellBookCooldowns")
    end)
    return n
end

function T.enterCombat()
    local h = T.host()
    for _, g in pairs(T.spawns) do pcall(Osi.EnterCombat, g, h) end
end

-- realRolls: Osiris queues UseSpell with the IgnoreSpellRolls cast option, so the target's saving throw (and the
-- attack roll) is never made (seen in game 2026-10-01). Strip that option from our request when it reaches the
-- server's cast queue (a tick or two later) so the spell rolls like a real cast.
T.pendingRolled = T.pendingRolled or {}
function T.cast(caster, spell, target, realRolls)
    if realRolls then
        table.insert(T.pendingRolled, { spell = spell, ticks = 0 })
        if T.castSubVersion ~= VERSION then  -- re-subscribe after a harness reinstall (see T.saveSub)
            if T.castSub then pcall(function() Ext.Events.Tick:Unsubscribe(T.castSub) end) end
            T.castSubVersion = VERSION
            T.castSub = Ext.Events.Tick:Subscribe(function()
                if #T.pendingRolled == 0 then return end
                for _, r in ipairs(Ext.System.ServerCastRequest.OsirisCastRequests) do
                    for i, p in ipairs(T.pendingRolled) do
                        if r.Spell.OriginatorPrototype == p.spell then
                            local keep = {}
                            for _, o in ipairs(r.CastOptions) do if o ~= "IgnoreSpellRolls" then keep[#keep + 1] = o end end
                            r.CastOptions = keep
                            table.remove(T.pendingRolled, i)
                            break
                        end
                    end
                end
                for i = #T.pendingRolled, 1, -1 do  -- give up on requests that never showed up
                    local p = T.pendingRolled[i]
                    p.ticks = p.ticks + 1
                    if p.ticks > 60 then table.remove(T.pendingRolled, i) end
                end
            end)
        end
    end
    T.singlePick(spell)
    Osi.UseSpell(uuid(caster), spell, uuid(target))
end

-- A scripted cast of a multi-target spell (AmountOfTargets N) puts all N picks on the one target - the engine fills the
-- missing picks (the queued request holds one target; seen 2026-10-05: Entrancing Mirrors hit its wolf 3 times, Wail of
-- the Banshee's 10 Kill()s on one creature crashed the game). In play IgnorePreviouslyPickedEntities forbids that, so
-- the scripted cast gets one pick: AmountOfTargets 1 for the cast, restored a few seconds later.
T.restorePicks = T.restorePicks or {}
function T.singlePick(spell)
    local st = Ext.Stats.Get(spell)
    local n = st and tonumber(st.AmountOfTargets)
    if not n or n <= 1 then return end
    T.restorePicks[spell] = T.restorePicks[spell] or tostring(st.AmountOfTargets)
    st.AmountOfTargets = "1"
    st:Sync()
    Ext.Timer.WaitFor(5000, function()
        local orig = T.restorePicks[spell]
        if orig then
            local s2 = Ext.Stats.Get(spell)
            s2.AmountOfTargets = orig
            s2:Sync()
            T.restorePicks[spell] = nil
        end
    end)
end

-- ground-targeted spells (summons, zones): cast at a point dx metres in front of the caster
function T.castAt(caster, spell, dx)
    caster = uuid(caster)
    local x, y, z = Osi.GetPosition(caster)
    Osi.UseSpellAtPosition(caster, spell, x + (dx or 3), y, z, 1)
end

-- ------------------------------------------------------------------ passive rolls
-- A DifficultyClass GUID whose (first) difficulty is `value` (the game ships Legacy_<n> ones).
function T.dc(value)
    local best
    for _, g in ipairs(Ext.StaticData.GetAll("DifficultyClass")) do
        local d = Ext.StaticData.Get(g, "DifficultyClass")
        if d and d.Difficulties and d.Difficulties[1] == value then
            if tostring(d.Name):find("Legacy_", 1, true) == 1 then return g end
            best = best or g
        end
    end
    return best
end

-- Request n passive rolls (rollType SavingThrow/SkillCheck/RawAbility, id Strength/Athletics/...) against DC
-- `dc`; results tally into T.rolls[tag] through RollResult. Real engine rolls with every boost applied
-- (MinimumRollResult, RollBonus, advantage). Note: a passive save sets no CheckedAbility/HitDescription,
-- so save boosts conditioned on the ability don't apply to it (seen in game 2026-10-01).
function T.roll(tag, rollType, id, dc, n, roller)
    local g = T.dc(dc)
    if not g then return nil, "no DifficultyClass with value " .. tostring(dc) end
    T.rolls[tag] = { pass = 0, fail = 0, n = n }
    for _ = 1, n do Osi.RequestPassiveRoll(uuid(roller) or T.host(), NULL, rollType, id, g, 0, "BG3T_ROLL_" .. tag) end
    return g
end

function T.drain(since)
    local out = {}
    for _, ev in ipairs(T.events) do if ev.seq > (since or 0) then out[#out + 1] = ev end end
    return out
end

function T.cleanup()
    T.aiClear, T.seenRolls, T.aiLocks = {}, {}, {}
    local report = { spawns = 0, grants = 0, statuses = 0, passives = 0, cooldowns = T.clearCooldowns(T.host()) }
    for _, ps in ipairs(T.added_passives) do
        pcall(Osi.RemovePassive, ps[1], ps[2])
        report.passives = report.passives + 1
    end
    T.added_passives = {}
    report.borrowed = giveBack()
    for _, g in pairs(T.spawns) do
        if Osi.IsDead(g) == 0 then pcall(Osi.Die, g, 0, NULL, 0, 1) end
        pcall(Osi.RequestDelete, g)
        report.spawns = report.spawns + 1
    end
    for _, gr in ipairs(T.grants) do
        pcall(Osi.RemoveBoosts, gr[1], gr[2], 0, T.TAG, gr[1])
        report.grants = report.grants + 1
    end
    for _, st in ipairs(T.applied) do
        pcall(Osi.RemoveStatus, st[1], st[2])
        report.statuses = report.statuses + 1
    end
    T.spawns, T.grants, T.applied = {}, {}, {}
    local ok, sm = pcall(T.summons, T.host())
    for _, x in ipairs(ok and sm or {}) do
        if not (T.preSummons or {})[x.guid] then  -- RequestDelete alone leaves a summon standing (2026-10-02)
            if Osi.IsDead(x.guid) == 0 then pcall(Osi.Die, x.guid, 0, NULL, 0, 1) end
            pcall(Osi.RequestDelete, x.guid)
            report.summons = (report.summons or 0) + 1
        end
    end
    T.preSummons = nil
    if T.restoreReactions then T.restoreReactions() end
    -- A case that downs the host on purpose (Desperado, Cheat Death...) must not leave it dying: death saves would end
    -- in a Game Over box minutes later (seen 2026-10-03).
    local host = T.host()
    if host and Osi.IsDead(host) == 0 and (Osi.HasActiveStatus(host, "DOWNED") == 1 or Osi.GetHitpoints(host) <= 0) then
        T.revive(host)
        report.revived = true
    end
    T.recording = false
    T.safety.tripped = false
    return report
end

-- ------------------------------------------------------------------ safety watch
-- While test spawns are alive, a party member dropping under the floor (percent) ends the encounter:
-- every spawn dies, the party member is healed, and a SAFETY event records that the tool stepped in.
-- Triggered from HitpointsChanged, from AttackedBy (backup: HitpointsChanged doesn't always fire) and
-- from a DOWNED status on a party member.
local function safetyCheck(entity, pct)
    if not (T.safety.enabled and T.recording) or T.safety.tripped or next(T.spawns) == nil then return end
    local g = uuid(entity)
    if isTracked(g) ~= "host" and Osi.IsPartyMember(g, 1) ~= 1 then return end
    pct = pct or (Osi.GetHitpoints(g) * 100 / math.max(1, Osi.GetMaxHitpoints(g)))
    if pct >= T.safety.floor and Osi.HasActiveStatus(g, "DOWNED") ~= 1 then return end
    T.safety.tripped = true
    local lent = {}
    for _, b in pairs(T.borrowed or {}) do lent[b[1]] = true end
    for _, s in pairs(T.spawns) do if not lent[s] and Osi.IsDead(s) == 0 then pcall(Osi.Die, s, 0, NULL, 0, 1) end end
    pcall(Osi.RemoveStatus, g, "DOWNED")
    Osi.SetHitpointsPercentage(g, 100)
    push({ kind = "SAFETY", who = g, pct = pct })
end

-- ------------------------------------------------------------------ event recorder
-- Osiris listeners can't be removed, so they're registered once per Lua session and dispatch through
-- T.on[name]: installing a newer harness replaces the handlers instead of stacking listeners.
T.on = {
    StatusApplied = function(obj, status, causee)
        for _, cl in ipairs(T.aiClear) do  -- ai mode: lift it right away, so its own follow-up saves don't muddy the run
            if uuid(obj) == cl.target then
                for _, st in ipairs(cl.statuses) do
                    if st == status then Ext.Timer.WaitFor(400, function() pcall(Osi.RemoveStatus, cl.target, st) end) end
                end
            end
        end
        if T.recording and isTracked(obj) then push({ kind = "StatusApplied", who = uuid(obj), status = status, by = uuid(causee) }) end
        if status == "DOWNED" then safetyCheck(obj, 0) end
    end,
    StatusRemoved = function(obj, status)
        if T.recording and isTracked(obj) then push({ kind = "StatusRemoved", who = uuid(obj), status = status }) end
    end,
    CastedSpell = function(caster, spell)
        if T.recording and isTracked(caster) then push({ kind = "CastedSpell", who = uuid(caster), spell = spell }) end
    end,
    UsingSpellOnTarget = function(caster, target, spell)
        if T.recording and (isTracked(caster) or isTracked(target)) then
            push({ kind = "SpellOnTarget", who = uuid(caster), target = uuid(target), spell = spell })
        end
    end,
    AttackedBy = function(defender, attackerOwner, attacker, damageType, amount, cause)
        if T.recording and isTracked(defender) then
            push({ kind = "Damage", who = uuid(defender), by = uuid(attackerOwner), type = damageType, amount = amount, cause = cause })
        end
        safetyCheck(defender, nil)
    end,
    Died = function(ch)
        if T.recording and isTracked(ch) then push({ kind = "Died", who = uuid(ch) }) end
    end,
    TurnStarted = function(ch)
        if T.aiLocks[uuid(ch)] then pcall(T.applyAiLock, uuid(ch)) end
        for _, cl in ipairs(T.aiClear) do
            if uuid(ch) == cl.caster then
                for _, st in ipairs(cl.statuses) do if Osi.HasActiveStatus(cl.target, st) == 1 then Osi.RemoveStatus(cl.target, st) end end
            end
        end
        -- incap: the engine still starts a turn for a creature that can't act (sleeping, frozen...) and skips it;
        -- item: scenery with an environment turn (e.g. the beach's clamshells, seen 2026-10-01)
        if T.recording then push({ kind = "TurnStarted", who = uuid(ch), tracked = isTracked(ch), name = (function() local ok, n = pcall(function() return Ext.Loca.GetTranslatedString(Osi.GetDisplayName(ch)) end) return ok and n or nil end)(),
            incap = Osi.HasAppliedStatusOfType(ch, "INCAPACITATED") == 1, item = Osi.IsItem(ch) == 1 }) end
    end,
    CombatStarted = function() if T.recording then push({ kind = "CombatStarted" }) end end,
    CombatEnded = function() if T.recording then push({ kind = "CombatEnded" }) end end,
    LeveledUp = function(ch)
        if isTracked(ch) or Osi.IsPartyMember(ch, 1) == 1 then push({ kind = "LeveledUp", who = uuid(ch), level = Osi.GetLevel(ch) }) end
    end,
    HitpointsChanged = function(entity, pct) safetyCheck(entity, pct) end,
    RollResult = function(ev, roller, subject, result)
        if type(ev) == "string" and ev:sub(1, 10) == "BG3T_ROLL_" then
            local r = T.rolls[ev:sub(11)]
            if r then if result == 1 then r.pass = r.pass + 1 else r.fail = r.fail + 1 end end
        end
    end,
}

-- ------------------------------------------------------------------ AI-driven casts (mode = "ai")
-- The caster spawn keeps only the spells under test, so its own AI casts them on its turns: real rolls,
-- interrupts and reactions, unlike Osi.UseSpell (verified 2026-10-02: Osiris casts never raise OnPostRoll
-- interrupts). Spells flagged AIFlags CanNotUse are never chosen (e.g. Target_Bash).
-- Osi.RemoveSpell doesn't remove a template's innate attacks (seen 2026-10-02), so every other spell in the
-- caster's spellbook is put on cooldown instead, re-applied at the start of each of its turns.
T.aiLocks = T.aiLocks or {}
function T.applyAiLock(caster)
    local keep = T.aiLocks[caster]
    if not keep then return 0 end
    local e = Ext.Entity.Get(caster)
    local cds, n = {}, 0
    for _, sp in ipairs(e.SpellBook.Spells) do
        if not keep[sp.Id.OriginatorPrototype] then
            cds[#cds + 1] = { SpellId = sp.Id, Cooldown = 99, CooldownType = "OncePerCombat", CooldownType2 = "OncePerCombat" }
            n = n + 1
        end
    end
    e.SpellBookCooldowns.Cooldowns = cds
    e:Replicate("SpellBookCooldowns")
    return n
end

function T.aiOnly(caster, keep)
    caster = uuid(caster)
    local want = {}
    for _, s in ipairs(keep) do want[s] = true end
    for s in pairs(want) do if Osi.HasSpell(caster, s) ~= 1 then Osi.AddSpell(caster, s, 0, 1) end end
    T.aiLocks[caster] = want
    return T.applyAiLock(caster)
end

-- one cheap poll for the runner: how many events since `since`, and how many casts of `spell`
function T.progress(since, spell)
    local n, casts = 0, 0
    for _, ev in ipairs(T.events) do
        if ev.seq > since then
            n = n + 1
            if ev.kind == "CastedSpell" and ev.spell == spell then casts = casts + 1 end
        end
    end
    return { n = n, casts = casts }
end

-- stage pre-check in one round trip: cleanup + host snapshot + current statuses
function T.precheck()
    T.cleanup()
    return { snapshot = T.snapshot(T.host(), true), statuses = T.statuses(T.host()) }
end

function T.spellbook(g)
    local out = {}
    for _, sp in ipairs(Ext.Entity.Get(uuid(g)).SpellBook.Spells) do out[#out + 1] = sp.Id.OriginatorPrototype end
    return out
end

-- statuses cleared from a target at the start of the caster's turns, so the AI keeps choosing the spell
T.aiClear = T.aiClear or {}

-- every damaging hit while recording, with the hit's flags (Hit, Critical, Miss, AttackAdvantage,
-- AttackDisadvantage...): what an attack roll had, which no other event shows (Precise Hunter, 2026-10-02)
if T.hitSub then pcall(function() Ext.Events.DealDamage:Unsubscribe(T.hitSub) end) end
T.hitSub = Ext.Events.DealDamage:Subscribe(function(e)
    if not T.recording then return end
    local ok, err = pcall(function()
        local function g(h) local ok2, v = pcall(function() return h.Uuid.EntityUuid end) return ok2 and v or nil end
        local who, by = g(e.Target), g(e.Caster) or g(e.Hit.Inflicter)
        if not (isTracked(who) or isTracked(by)) then return end
        local flags = {}
        for f in tostring(e.Hit.EffectFlags):gmatch("[%w_]+") do if f ~= "DamageFlags" then flags[#flags + 1] = f end end
        local amount
        for _, f in ipairs({ function() return e.Hit.TotalDamageDone end, function() return e.Result.TotalDamageDone end,
                             function() return e.Hit.DamageList and #e.Hit.DamageList > 0 and e.Hit.DamageList[1].Amount end }) do
            local okA, v = pcall(f)
            if okA and type(v) == "number" then amount = v break end
        end
        push({ kind = "Hit", who = who, by = by, flags = flags, attack = tostring(e.Hit.SpellAttackType), amount = amount,
               spell = tostring(e.SpellId and e.SpellId.Prototype or "") })
    end)
    if not ok then Ext.Utils.PrintWarning("[BG3T] Hit: " .. tostring(err)) end
end)

-- every saving throw while recording (deduped per roll): saver, source, ability, natural, total, DC.
-- SavingThrowRolledEvent fires twice per roll with opposite Success, so success is total >= DC here.
T.seenRolls = T.seenRolls or {}
-- replaced on every harness (re)install: a guard like `if not T.saveSub` keeps the OLD closure running after the
-- harness file changes (cost a debugging round on 2026-10-02)
if T.saveSub then pcall(Ext.Entity.Unsubscribe, T.saveSub) end
do
    T.saveSub = Ext.Entity.OnCreate("SavingThrowRolledEvent", function(_, _, c)
        if not T.recording then return end
        local ok, err = pcall(function()
            local cr = c.ConditionRoll
            local key = tostring(cr.RollUuid)
            if T.seenRolls[key] then return end
            T.seenRolls[key] = true
            local r = cr.Roll.Result
            local function g(h) local ok2, v = pcall(function() return h.Uuid.EntityUuid end) return ok2 and v or nil end
            local saver, source = g(c.Target), g(c.Source)
            local sc = tostring(c.SpellCastUuid)
            local spellcast = sc ~= "00000000-0000-0000-0000-000000000000" and sc ~= "nil"
            -- a spell's own save names the caster as Target and the saver as Source (bonus checked 2026-10-02:
            -- the host's +3 Strength on a "Target = wolf" event); status/passive saves are the other way round
            if spellcast then saver, source = source, saver end
            if cr.SwappedSourceAndTarget then saver, source = source, saver end
            if not (isTracked(saver) or isTracked(source)) then return end
            -- spellcast: the save is a spell's own roll (SpellCastUuid set) - the only kind OnPostRoll interrupts see;
            -- surface/status/passive saves (vines, trip-on-hit...) have none
            push({ kind = "Save", who = saver, by = source, ability = tostring(c.Ability), natural = r.NaturalRoll,
                   advantage = c.Advantage, disadvantage = c.Disadvantage,
                   total = r.Total, dc = cr.Difficulty, saved = r.Total >= cr.Difficulty, swapped = cr.SwappedSourceAndTarget,
                   spellcast = spellcast })
        end)
        if not ok then Ext.Utils.PrintWarning("[BG3T] Save: " .. tostring(err)) end
    end)
end

-- interrupts the engine considers (an InterruptDecision entity appears) and uses (ServerInterruptUsed), by name.
-- "considered but not used" = conditions passed but nobody decided to use it (NPC AI declined, or no player decision)
local function interruptName(e) local ok, n = pcall(function() return e.InterruptData.Interrupt end) return ok and n or nil end
for _, s in ipairs(T.interruptSubs or {}) do pcall(Ext.Entity.Unsubscribe, s) end
T.interruptSubs = {}
table.insert(T.interruptSubs, Ext.Entity.OnCreate("InterruptDecision", function(e)
    if T.recording then push({ kind = "InterruptConsidered", interrupt = interruptName(e) }) end
end))
table.insert(T.interruptSubs, Ext.Entity.OnCreate("ServerInterruptUsed", function(e)
    if not T.recording then return end
    pcall(function()
        for ent in pairs(e.ServerInterruptUsed.Interrupts) do push({ kind = "InterruptUsed", interrupt = interruptName(ent) }) end
    end)
end))

-- reactions on auto (Enabled, Ask off) for every interrupt the character has. Set key by key: assigning the
-- whole Preferences map back clears it (seen 2026-10-02).
function T.autoReactions(g)
    local e = Ext.Entity.Get(uuid(g))
    local n = 0
    for _, ie in ipairs(e.InterruptContainer.Interrupts) do
        local name = interruptName(ie)
        if name then e.InterruptPreferences.Preferences[name] = { "Enabled" } n = n + 1 end
    end
    e:Replicate("InterruptPreferences")
    return n
end

-- reactions off for a test: every interrupt the character has is disabled except names containing one of `keep`
-- (those go on auto). Stops the character's own reactions (Shield, Arcane/Projected Ward, opportunity attacks)
-- from changing the rolls a case measures. The previous preferences come back in T.cleanup().
T.savedPrefs = T.savedPrefs or {}
function T.setReactions(g, keep)
    local id = uuid(g)
    local e = Ext.Entity.Get(id)
    local saved = T.savedPrefs[id] or {}
    local off, on = 0, 0
    for _, ie in ipairs(e.InterruptContainer.Interrupts) do
        local name = interruptName(ie)
        if name then
            if saved[name] == nil then
                local cur = e.InterruptPreferences.Preferences[name]
                local copy = false  -- false: there was no preference; cleanup removes the key instead of writing {}
                if cur then copy = {} for _, f in pairs(cur) do copy[#copy + 1] = f end end
                saved[name] = copy
            end
            local wanted = false
            for _, k in ipairs(keep or {}) do if k == "*" or name:find(k, 1, true) then wanted = true end end
            e.InterruptPreferences.Preferences[name] = wanted and { "Enabled" } or {}
            if wanted then on = on + 1 else off = off + 1 end
        end
    end
    T.savedPrefs[id] = saved
    e:Replicate("InterruptPreferences")
    return { off = off, on = on }
end

function T.restoreReactions()
    for id, saved in pairs(T.savedPrefs) do
        pcall(function()
            local e = Ext.Entity.Get(id)
            -- an empty flag set means DISABLED: restoring {} for a preference that didn't exist turned a newly
            -- unlocked interrupt off for every later run (Unbreakable Majesty, 2026-10-02)
            for name, flags in pairs(saved) do e.InterruptPreferences.Preferences[name] = flags or nil end
            e:Replicate("InterruptPreferences")
        end)
    end
    T.savedPrefs = {}
end

local ARITY = { StatusApplied = 4, StatusRemoved = 4, CastedSpell = 5, UsingSpellOnTarget = 6, AttackedBy = 7, Died = 1,
                TurnStarted = 1, CombatStarted = 1, CombatEnded = 1, LeveledUp = 1, HitpointsChanged = 2, RollResult = 6 }
T.listen_errors = {}
T.registered = T.registered or {}
for name, arity in pairs(ARITY) do
    if not T.registered[name] then
        local ok, err = pcall(Ext.Osiris.RegisterListener, name, arity, "after", function(...)
            local h = BG3T and BG3T.on and BG3T.on[name]
            if h then
                local ok2, err2 = pcall(h, ...)
                if not ok2 then Ext.Utils.PrintWarning("[BG3T] " .. name .. ": " .. tostring(err2)) end
            end
        end)
        if ok then T.registered[name] = true else T.listen_errors[#T.listen_errors + 1] = name .. ": " .. tostring(err) end
    end
end

return "installed"
