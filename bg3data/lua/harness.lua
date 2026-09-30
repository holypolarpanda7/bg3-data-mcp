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

function T.snapshot(g, full)
    g = uuid(g)
    local s = {
        guid = g, hp = Osi.GetHitpoints(g), max_hp = Osi.GetMaxHitpoints(g), dead = Osi.IsDead(g) == 1,
        in_combat = Osi.IsInCombat(g) == 1, statuses = T.statuses(g), resources = T.resources(g),
    }
    if full then
        local e = Ext.Entity.Get(g)
        s.level = Osi.GetLevel(g)
        s.classes = T.classes(g)
        s.passives = T.passives(g)
        s.spells = T.spells(g)
        s.region = Osi.GetRegion(g)
        pcall(function() s.xp = e.Experience.TotalExperience end)
    end
    return s
end

-- Snapshot the host plus every spawn, keyed by alias.
function T.world()
    local w = { host = T.snapshot(T.host()) }
    for alias, g in pairs(T.spawns) do w[alias] = T.snapshot(g) end
    return w
end

-- ------------------------------------------------------------------ changes (all tracked)
function T.spawn(alias, template, faction, dx, dz)
    local h = T.host()
    local x, y, z = Osi.GetPosition(h)
    local g = Osi.CreateAt(template, x + (dx or 6), y, z + (dz or 0), 0, 0, "")
    if not g then return nil, "CreateAt returned nothing for template " .. tostring(template) end
    g = uuid(g)
    if faction and faction ~= "" then pcall(Osi.SetFaction, g, faction) end
    T.spawns[alias] = g
    return g
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

function T.apply(g, status, turns)
    g = uuid(g)
    Osi.ApplyStatus(g, status, (turns or 10) * 6.0, 1, g)
    T.applied[#T.applied + 1] = { g, status }
end

-- HP to an exact value; above max needs grant(IncreaseMaxHP) first and a tick before calling this.
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

function T.enterCombat()
    local h = T.host()
    for _, g in pairs(T.spawns) do pcall(Osi.EnterCombat, g, h) end
end

function T.cast(caster, spell, target)
    Osi.UseSpell(uuid(caster), spell, uuid(target))
end

function T.drain(since)
    local out = {}
    for _, ev in ipairs(T.events) do if ev.seq > (since or 0) then out[#out + 1] = ev end end
    return out
end

function T.cleanup()
    local report = { spawns = 0, grants = 0, statuses = 0, passives = 0 }
    for _, ps in ipairs(T.added_passives) do
        pcall(Osi.RemovePassive, ps[1], ps[2])
        report.passives = report.passives + 1
    end
    T.added_passives = {}
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
    for _, s in pairs(T.spawns) do if Osi.IsDead(s) == 0 then pcall(Osi.Die, s, 0, NULL, 0, 1) end end
    pcall(Osi.RemoveStatus, g, "DOWNED")
    Osi.SetHitpointsPercentage(g, 100)
    push({ kind = "SAFETY", who = g, pct = pct })
end

-- ------------------------------------------------------------------ event recorder
-- Osiris listeners can't be removed, so they're registered once per Lua session and dispatch through
-- T.on[name]: installing a newer harness replaces the handlers instead of stacking listeners.
T.on = {
    StatusApplied = function(obj, status, causee)
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
        if T.recording then push({ kind = "TurnStarted", who = uuid(ch), tracked = isTracked(ch) }) end
    end,
    CombatStarted = function() if T.recording then push({ kind = "CombatStarted" }) end end,
    CombatEnded = function() if T.recording then push({ kind = "CombatEnded" }) end end,
    LeveledUp = function(ch)
        if isTracked(ch) or Osi.IsPartyMember(ch, 1) == 1 then push({ kind = "LeveledUp", who = uuid(ch), level = Osi.GetLevel(ch) }) end
    end,
    HitpointsChanged = function(entity, pct) safetyCheck(entity, pct) end,
}

local ARITY = { StatusApplied = 4, StatusRemoved = 4, CastedSpell = 5, UsingSpellOnTarget = 6, AttackedBy = 7, Died = 1,
                TurnStarted = 1, CombatStarted = 1, CombatEnded = 1, LeveledUp = 1, HitpointsChanged = 2 }
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
