"""Cast spells the way a player does: through the hotbar UI, real mouse clicks on the target, real reaction prompts (2026-10-29).

Scripted casts (Osi.UseSpell) skip the UI: they never show whether a spell is reachable from the hotbar, never charge slots and
skip pickers, and a toggle that never reached the hotbar (Overchannel, PTA 1.0.5.0) passed every scripted test. This module:

- reads the hotbar's own view model (HotBar widget -> CurrentPlayer.SelectedCharacter.PlayerCharacterProperties: ClassHotBar,
  CommonHotBar, CustomHotBar, PassivesHotBar, KeyboardHotBars; Interrupts = the Reactions panel with Ask/Enabled),
- starts the cast with the hotbar's UseSlotCommand and picks the slot level with LaunchUpcastCommand (the commands a click on
  the tile and on the upcast row send),
- aims with REAL clicks: the target's world position is projected through the active camera (Camera.Controller.Camera
  ViewMatrix/ProjectionMatrix, row vectors) and the hover is verified with the game's picking helper before each click,
- answers reaction prompts (ReactionDecisionPopup) with real clicks on the decision's ReactButton / DoNotReactButton, found by
  hovering and asking Noesis IsMouseOver (the view-model commands run but don't decide - seen 2026-10-29).

Speed: the client helpers are installed once as the global BG3UI, so each step is one short console call; waits are polls on
game state, never fixed sleeps; the reaction button's position is cached between prompts.
"""
import json
import os
import time

from . import gameui, se

VERSION = 8

CLIENT_LIB = r"""
if BG3UI and BG3UI.v == %d then return true end
local U = {v = %d}
local function find(n, name, d)
  if not n or d > 9 then return nil end
  local ok, nm = pcall(function() return n.Name end)
  if ok and nm == name then return n end
  local okc, cnt = pcall(function() return n.VisualChildrenCount end)
  if okc and cnt then for i = 1, cnt do local okv, ch = pcall(function() return n:VisualChild(i) end) if okv and ch then local r = find(ch, name, d + 1) if r then return r end end end end
end
U.find = find
function U.widget(name) return find(Ext.UI.GetRoot(), name, 0) end
function U.hb() local h = U.widget("HotBar") return h and h.DataContext end
function U.pcp() return U.hb().CurrentPlayer.SelectedCharacter.PlayerCharacterProperties end
local function id(o) local ok, v = pcall(function() return o.PrototypeID end) return ok and v or nil end
function U.bars()
  local pp, out = U.pcp(), {}
  for _, b in ipairs({"ClassHotBar", "CommonHotBar", "CustomHotBar", "PassivesHotBar"}) do
    local ok, bar = pcall(function() return pp[b] end)
    if ok and bar then table.insert(out, {b, bar}) end
  end
  local ok, kb = pcall(function() return pp.KeyboardHotBars end)
  if ok and kb then for i = 1, #kb do table.insert(out, {"KeyboardHotBar" .. i, kb[i]}) end end
  return out
end
-- where a spell sits: the slot holding it, or a container/upcast slot whose entries name it
function U.locate(spell)
  for _, bb in ipairs(U.bars()) do
    local sl = bb[2].SlotList
    for i = 1, #sl do
      local c = sl[i].Content
      if c and id(c) == spell then return bb[1], i, sl[i], nil end
      if c then
        local okv, vs = pcall(function() return c.SpellVariants end)
        if okv and vs then for j = 1, #vs do if id(vs[j]) == spell then return bb[1], i, sl[i], vs[j] end end end
      end
    end
  end
end
function U.state()
  local out = {bars = {}, interrupts = {}}
  for _, bb in ipairs(U.bars()) do
    local ids, sl = {}, bb[2].SlotList
    for i = 1, #sl do local c = sl[i].Content if c then local v = id(c) if v then table.insert(ids, v) end end end
    out.bars[bb[1]] = ids
  end
  local it = U.pcp().Interrupts
  for i = 1, #it do table.insert(out.interrupts, {id = it[i].PrototypeID, ask = it[i].Ask, enabled = it[i].Enabled}) end
  return out
end
function U.select(spell, level)
  local hb = U.hb()
  local bar, idx, slot, var = U.locate(spell)
  if not bar then return {error = "not on any hotbar"} end
  if not hb.UseSlotCommand:CanExecute(slot) then return {error = "the hotbar can't use slot " .. idx .. " of " .. bar .. " (greyed out)", bar = bar, index = idx} end
  hb.UseSlotCommand:Execute(slot)            -- a container opens its variant row ("Select Variant")
  local res = {bar = bar, index = idx}
  local entry = slot.Content
  if var then
    hb.UseSlotCommand:Execute(var)           -- the variant tile: closes the row and starts that variant (2026-10-29)
    res.variant = id(var)
    entry = var
  end
  local okU, up = pcall(function() return entry.SpellUpcast end)
  if okU and up and #up > 0 and (level or hb.IsSelectingUpcastedSpell) then
    local pick = nil
    for j = 1, #up do
      local lv = up[j].SlotLevel
      if (level and lv == level) or (not level and up[j].CanUse and not pick) then pick = up[j] end
    end
    if not pick then hb.ActionCancelCommand:Execute(nil) return {error = "no usable slot level " .. tostring(level), bar = bar, index = idx} end
    hb.LaunchUpcastCommand:Execute(pick)
    res.level = pick.SlotLevel
  end
  return res
end
-- the spellbook entry for a spell: (vm, book index) - Activated = prepared (or always available)
function U.book(spell)
  local pp = U.pcp()
  for b = 1, #pp.SpellBooks do
    local gs = pp.SpellBooks[b].ActionGroups
    for g = 1, #gs do local a = gs[g].Actions for j = 1, #a do if id(a[j]) == spell then return a[j], b end end end
  end
end
function U.prepared() local ok, v = pcall(function() return U.pcp().PreparedSpellsStat end) if ok and v then return v.Value, v.Max end end
-- what to unprepare so `spell` fits: nil when there is room; `swap` if given, else the last prepared levelled class spell
function U.swapFor(spell, swap)
  local n, max = U.prepared()
  if not (n and max and n >= max) then return nil end
  if swap and U.book(swap) then return swap end
  local vm, b = U.book(spell)
  local gs, out = U.pcp().SpellBooks[b].ActionGroups, nil
  for g = 1, #gs do local a = gs[g].Actions for j = 1, #a do
    local okl, lv = pcall(function() return a[j].SpellSlotLevel end)
    if a[j].Activated and okl and lv and lv > 0 and id(a[j]) ~= spell then out = id(a[j]) end
  end end
  return out or false
end
function U.toggleBook(spell) local vm = U.book(spell) if vm then U.hb().ToggleCharacterAction:Execute(vm) return true end end
function U.isPrepared(spell) local vm = U.book(spell) return vm and vm.Activated or false end
function U.cancel() pcall(function() U.hb().ActionCancelCommand:Execute(nil) end) end
local function camera()
  for _, e in ipairs(Ext.Entity.GetAllEntitiesWithComponent("Camera")) do if e.Camera.Active then return e.Camera.Controller.Camera end end
end
local function mul(v, M) local r = {} for c = 1, 4 do r[c] = v[1]*M[c] + v[2]*M[4+c] + v[3]*M[8+c] + v[4]*M[12+c] end return r end
function U.project(p)
  local cam = camera()
  local c = mul(mul({p[1], p[2], p[3], 1}, cam.ViewMatrix), cam.ProjectionMatrix)
  if c[4] <= 0 then return nil end
  return {(c[1] / c[4] + 1) / 2, (1 - c[2] / c[4]) / 2}
end
function U.aim(uuid, dy)
  local e = Ext.Entity.Get(uuid)
  if not e then return nil end
  local p = e.Transform.Transform.Translate
  return U.project({p[1], p[2] + (dy or 0.8), p[3]})
end
function U.hovered()
  local ph = Ext.UI.GetPickingHelper(1)
  local i = ph and ph.Inner and ph.Inner.Inner[1]
  if not i then return nil end
  for _, k in ipairs({"Character", "Item", "DeadCharacter"}) do
    local ok, u = pcall(function() return i[k].Uuid.EntityUuid end)
    if ok and u then return u end
  end
end
function U.pointed()
  local ph = Ext.UI.GetPickingHelper(1)
  local p = ph and ph.Inner and ph.Inner.Position
  return p and {p[1], p[2], p[3]} or nil
end
function U.casting() local ok, v = pcall(function() return U.pcp().IsCasting end) return ok and v or false end
-- the open reaction prompt: {spell, decisions = {{id, decision}}}
function U.prompt()
  local w = U.widget("ReactionDecisionPopup")
  if not w then return nil end
  local ev = w.DataContext.Data.InterruptEvents
  if #ev == 0 then return nil end
  local e, out = ev[1], {decisions = {}}
  pcall(function() out.spell = e.SourceSpell.PrototypeID end)
  for a = 1, #e.Interruptors do
    local ds = e.Interruptors[a].InterruptDecisions
    for b = 1, #ds do table.insert(out.decisions, {id = ds[b].Interrupt.PrototypeID, decision = tostring(ds[b].Decision)}) end
  end
  return out
end
-- is the mouse over the prompt button for `which` (an interrupt id -> its ReactButton, or "DoNotReact")?
function U.overButton(which)
  local w = U.widget("ReactionDecisionPopup")
  if not w then return nil end
  local hit = false
  local function walk(n, d)
    if n == nil or d > 45 or hit then return end
    local okn, nm = pcall(function() return n.Name end)
    if okn and ((which == "DoNotReact" and nm == "DoNotReactButton") or (which ~= "DoNotReact" and nm == "ReactButton")) then
      local mine = which == "DoNotReact"
      if not mine then pcall(function() mine = n.DataContext.Interrupt.PrototypeID == which end) end
      if mine then local ok, o = pcall(function() return n.IsMouseOver end) if ok and o then hit = true end return end
    end
    local okc, cnt = pcall(function() return n.VisualChildrenCount end)
    if okc and cnt then for i = 1, cnt do local okv, ch = pcall(function() return n:VisualChild(i) end) if okv and ch then walk(ch, d + 1) end end end
  end
  walk(w, 0)
  return hit
end
-- element locator (2026-10-29): U.mark finds the element matching `pred(n)` under widget `root` and keeps its ancestor
-- chain (1 = the element, #chain = root). U.where() reports the deepest chain element under the mouse and, inside it, which
-- child is hovered vs which child leads to the target, with the children's sizes (screen fractions, margins included) so
-- Python can jump from the hovered sibling straight to the target's one (uicast.locate_element). No screenshot.
local function frac(n, W, H)
  local okw, aw = pcall(function() return n.ActualWidth end)
  local okh, ah = pcall(function() return n.ActualHeight end)
  local m = {0, 0, 0, 0}
  pcall(function() local g = n.Margin if g then m = {g.Left or g[1] or 0, g.Top or g[2] or 0, g.Right or g[3] or 0, g.Bottom or g[4] or 0} end end)
  return {((okw and aw or 0) + m[1] + m[3]) / W, ((okh and ah or 0) + m[2] + m[4]) / H, (okw and aw or 0) / W, (okh and ah or 0) / H}
end
function U.mark(root, pred)
  local w = type(root) == "string" and U.widget(root) or root
  if not w then return nil end
  local full = U.widget("ContentRoot")
  U.W, U.H = full.ActualWidth, full.ActualHeight
  local stack, found = {}, nil
  local function walk(n, d)
    if found or n == nil or d > 80 then return end
    table.insert(stack, n)
    local ok, hit = pcall(pred, n)
    if ok and hit then found = {table.unpack(stack)} table.remove(stack) return end
    local okc, cnt = pcall(function() return n.VisualChildrenCount end)
    if okc and cnt then for i = 1, cnt do local okv, ch = pcall(function() return n:VisualChild(i) end) if okv and ch then walk(ch, d + 1) end if found then break end end end
    table.remove(stack)
  end
  walk(w, 0)
  if not found then U.chain = nil return nil end
  local chain = {}
  for i = #found, 1, -1 do table.insert(chain, found[i]) end
  U.chain = chain
  local sizes = {}
  for i, n in ipairs(chain) do local f = frac(n, U.W, U.H) sizes[i] = {f[3], f[4]} end
  return sizes
end
local function over(n) local ok, o = pcall(function() return n.IsMouseOver end) return ok and o end
function U.where()
  local ch = U.chain
  if not ch then return nil end
  local d
  for i = 1, #ch do if over(ch[i]) then d = i break end end
  if not d then return {d = 0} end
  if d == 1 then return {d = 1} end
  local parent, want = ch[d], ch[d - 1]
  local res = {d = d, p = frac(parent, U.W, U.H), kids = {}}
  local okc, cnt = pcall(function() return parent.VisualChildrenCount end)
  for i = 1, (okc and cnt or 0) do
    local okv, k = pcall(function() return parent:VisualChild(i) end)
    if okv and k then
      res.kids[i] = frac(k, U.W, U.H)
      if k == want then res.tc = i end
      if not res.hc and over(k) then res.hc = i end
    end
  end
  return res
end
BG3UI = U
return true
""" % (VERSION, VERSION)

SERVER_SNAPSHOT = r"""
local h = Osi.GetHostCharacter()
local out = {host_hp = Osi.GetHitpoints(h), slots = {}}
local ar = Ext.Entity.Get(h).ActionResources.Resources["d136c5d9-0ff0-43da-acce-a74a07f8d6bf"]
if ar then for _, r in ipairs(ar) do if r.MaxAmount > 0 then out.slots[tostring(r.Level)] = r.Amount end end end
local t = %s
if t then out.target_hp = Osi.GetHitpoints(t) out.target_dead = Osi.IsDead(t) == 1 end
return out
"""

# where the prompt buttons were last found ("react" = the first reaction row, "skip" = Do Not React), kept on disk so a new
# process starts there; defaults = a one-reaction prompt at 16:9 (2026-10-29)
_CACHE_FILE = os.path.expanduser("~/.cache/bg3-data-mcp/uicast.json")
_DEFAULT_BUTTONS = {"react": (0.469, 0.678), "skip": (0.5, 0.770)}


def _load_cache():
    try:
        with open(_CACHE_FILE) as f:
            return {k: tuple(v) for k, v in json.load(f).items()}
    except (OSError, ValueError):
        return {}


def _save_cache():
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w") as f:
            json.dump(_button_cache, f)
    except OSError:
        pass


_button_cache = _load_cache()


def _c(code, timeout=10):
    r = se.eval_lua(code, "client", timeout=timeout)
    if not r.get("ok"):
        raise RuntimeError("client Lua failed: " + str(r.get("result"))[:300])
    return r["result"]


def install():
    _c(CLIENT_LIB, timeout=15)


def _lua(s):
    return "nil" if s is None else '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def state():
    """What the UI offers the selected character: the spells on each hotbar and the Reactions panel (Ask/Enabled)."""
    install()
    return _c("return BG3UI.state()")


def _snapshot(target):
    r = se.eval_lua(SERVER_SNAPSHOT % _lua(target if target not in (None, "self") else None), "server", timeout=10)
    return r.get("result") if r.get("ok") else None


def _poll(fn, timeout, every=0.1):
    end = time.time() + timeout
    while True:
        v = fn()
        if v or time.time() >= end:
            return v
        time.sleep(every)


def _hover_target(uuid, log):
    """Move the mouse over `uuid` and confirm with the game's picking that it is the hovered object; returns the fractions."""
    for dy in (0.8, 1.3, 0.4, 1.8):
        p = _c(f"return BG3UI.aim({_lua(uuid)}, {dy})")
        if not p:
            continue
        fx, fy = p
        if not (0.01 < fx < 0.99 and 0.01 < fy < 0.99):
            log.append(f"target off screen at ({fx:.2f}, {fy:.2f})")
            return None
        gameui._fast(f"move {fx:.5f} {fy:.5f}")
        if _poll(lambda: _c("return BG3UI.hovered()") == uuid, 0.6, 0.08):
            return fx, fy
    return None


def _hover_ground(point, log, tol=1.0):
    """Move the mouse over a world point and confirm with the game's picking that the cursor's ground hit is within `tol` m."""
    p = _c(f"return BG3UI.project({{{point[0]}, {point[1]}, {point[2]}}})")
    if not p:
        return None
    fx, fy = p
    if not (0.01 < fx < 0.99 and 0.01 < fy < 0.99):
        log.append(f"ground point off screen at ({fx:.2f}, {fy:.2f})")
        return None
    gameui._fast(f"move {fx:.5f} {fy:.5f}")

    def near():
        q = _c("return BG3UI.pointed()")
        return q and ((q[0] - point[0]) ** 2 + (q[2] - point[2]) ** 2) ** 0.5 <= tol
    return (fx, fy) if _poll(near, 0.6, 0.08) else None


def _jump(w, p):
    """From the hovered child (hc) to the child leading to the target (tc) of the same parent: stacked children add up
    their sizes, a wrap grid moves by whole cells. Returns the new point or None."""
    hc, tc, kids = w.get("hc"), w.get("tc"), w.get("kids") or {}
    if not hc or not tc:
        return None
    kids = {int(k): v for k, v in kids.items()} if isinstance(kids, dict) else {i + 1: v for i, v in enumerate(kids)}
    pw, ph = w["p"][2], w["p"][3]
    lo, hi = sorted((hc, tc))
    span = [kids[i] for i in range(lo, hi) if i in kids]
    sign = 1 if tc > hc else -1
    vertical = all(abs(k[2] - pw) < 0.004 for k in kids.values() if k[2] > 0)
    horizontal = all(abs(k[3] - ph) < 0.004 for k in kids.values() if k[3] > 0)
    if vertical and not horizontal:
        return p[0], p[1] + sign * sum(k[1] for k in span)
    if horizontal and not vertical:
        return p[0] + sign * sum(k[0] for k in span), p[1]
    cw, chh = kids[hc][0], kids[hc][1]      # a wrap grid of equal cells
    if cw <= 0 or chh <= 0:
        return None
    cols = max(1, int(pw / cw + 0.01))
    return (p[0] + ((tc - 1) % cols - (hc - 1) % cols) * cw, p[1] + ((tc - 1) // cols - (hc - 1) // cols) * chh)


def locate_element(root, pred_lua, starts=None, region=None, budget=60):
    """Screen position (fractions) of the element under widget `root` for which the Lua predicate `pred_lua` (an expression
    of `n`, the element) is true, found by hovering + Noesis IsMouseOver - no position API (PointToScreen HANGS the game).
    Start points: `starts`, or icon-like blobs the screen helper finds in `region` (x, y, w, h fractions), or the screen
    centre. From each, a hover reports the deepest of the element's ancestors under the mouse and which of its children is
    hovered; the next hover jumps from that sibling to the child that leads to the target (stacks add up sizes, wrap grids
    move by cells), so a start on any sibling icon reaches the target in a few hovers. Gaps between icons don't respond to
    the mouse, which is why blobs (icons) make better starts than a grid. Returns ((fx, fy), hovers) or (None, hovers)."""
    sizes = _c(f"return BG3UI.mark({_lua(root)}, function(n) return {pred_lua} end)")
    if not sizes:
        return None, 0
    n = 0

    def probe(q):
        nonlocal n
        n += 1
        gameui._fast(f"move {min(max(q[0], 0.001), 0.999):.5f} {min(max(q[1], 0.001), 0.999):.5f}")
        return _c("return BG3UI.where()") or {"d": 0}

    if starts is None and region:
        ok, r = gameui._fast("tiles %.5f %.5f %.5f %.5f" % tuple(region), 10)
        parts = r.split() if ok and r.startswith("ok") else []
        if len(parts) > 2:
            hgt = int(parts[1])
            wid = hgt * 16 / 9
            starts = [(int(a_) / wid, int(b_) / hgt) for a_, b_ in (q.split(":") for q in parts[2].split(","))]
    starts = list(starts or [(0.5, 0.5)])
    tried = set()
    for p in starts:
        if n >= budget:
            break
        w = probe(p)
        for _ in range(8):                         # follow sibling jumps from this start
            if w.get("d") == 1:
                return p, n
            key = (w.get("d"), w.get("hc"))
            q = _jump(w, p) if w.get("d") and key not in tried else None
            tried.add(key)
            if not q or n >= budget:
                break
            w2 = probe(q)
            if not (w2.get("d") and w2["d"] <= w["d"]):
                break
            p, w = q, w2
    return None, n


def _find_button(which, kind, log):
    """Hover-scan for a prompt button (IsMouseOver), starting at its cached position. Returns (fx, fy) or None."""
    check = f"return BG3UI.overButton({_lua(which)})"
    cand = [p for p in (_button_cache.get(kind), _DEFAULT_BUTTONS[kind]) if p]
    xs = (0.469, 0.5) if kind == "react" else (0.5,)
    for y in [i / 100 for i in range(50, 86, 2)]:
        for x in xs:
            cand.append((x, y))
    for fx, fy in cand:
        gameui._fast(f"move {fx:.5f} {fy:.5f}")
        if _c(check):
            if _button_cache.get(kind) != (fx, fy):
                _button_cache[kind] = (fx, fy)
                _save_cache()
            return fx, fy
    log.append(f"couldn't find the prompt's {which} button by hovering")
    return None


def answer_prompt(reactions, log, timeout=6.0):
    """Answer the open reaction prompt(s): reactions = {interrupt id or display substring: True/False}; unnamed = Do Not React."""
    answered = []
    while True:
        # a prompt opens while the cast is still on; once the cast has ended there is nothing to wait for
        r = _poll(lambda: _c("local p = BG3UI.prompt() if p then return p end return not BG3UI.casting() and 'done' or nil"),
                  timeout, 0.15)
        p = r if isinstance(r, dict) else None
        if not p:
            return answered
        timeout = 0.5                                 # a chained prompt (another reaction to the same cast) opens at once
        ids = [d["id"] for d in p["decisions"]]
        want = next((i for i in ids for k, v in (reactions or {}).items() if v and k.lower() in i.lower()), None)
        kind, which = ("react", want) if want else ("skip", "DoNotReact")
        pos = _find_button(which, kind, log)
        if not pos:
            return answered + [{"prompt": p, "answered": None}]
        gameui._fast(f"click {pos[0]:.5f} {pos[1]:.5f} L 1")
        closed = _poll(lambda: _c("return BG3UI.prompt()") != p and True, 2.0, 0.1)
        answered.append({"spell": p.get("spell"), "offered": ids, "answer": want or "Do Not React", "closed": bool(closed)})


def _toggle(spell, want, timeout=4.0):
    """Toggle `spell` in the spellbook and wait until the game shows it prepared (want=True) or not."""
    if _c(f"return BG3UI.isPrepared({_lua(spell)})") == want:
        return True
    _c(f"return BG3UI.toggleBook({_lua(spell)})")
    return bool(_poll(lambda: _c(f"return BG3UI.isPrepared({_lua(spell)})") == want, timeout, 0.15))


def _prepare(spell, swap, log):
    """Known but unprepared: prepare it through the spellbook UI, one confirmed toggle at a time. Returns {undo} or {error, undo}."""
    if not _c(f"return BG3UI.book({_lua(spell)}) ~= nil"):
        return {"error": "not in the spellbook (not learned)"}
    undo = []
    if _c(f"return BG3UI.isPrepared({_lua(spell)})"):
        # prepared but taken off the hotbar (the player removed it): unprepare + prepare puts it back on the class bar
        # (2026-10-29; the VM drag/assign commands run but add nothing). Always-prepared entries can't be toggled.
        if not _toggle(spell, False, 2.0):
            return _drag_to_hotbar(spell, log)
        if not _toggle(spell, True):
            return {"error": "unprepared it to re-add it, but preparing it again failed", "undo": [(spell, True)]}
        if not _poll(lambda: _c(f"return BG3UI.locate({_lua(spell)}) ~= nil"), 4.0, 0.15):
            return {"error": "re-prepared, but it still isn't on a hotbar"}
        log.append(f"re-prepared {spell} to put it back on the hotbar")
        return {"undo": []}
    out = _c(f"return BG3UI.swapFor({_lua(spell)}, {_lua(swap)})")
    if out is False:
        return {"error": "the spellbook is full and nothing can be unprepared"}
    if out:
        if not _toggle(out, False):
            return {"error": f"couldn't unprepare {out} to make room", "undo": undo}
        undo.append((out, True))
    if not _toggle(spell, True):
        return {"error": "the spellbook didn't prepare it", "undo": undo}
    undo.insert(0, (spell, False))
    if not _poll(lambda: _c(f"return BG3UI.locate({_lua(spell)}) ~= nil"), 4.0, 0.15):
        return {"error": "prepared, but it never appeared on a hotbar", "undo": undo}
    log.append(f"prepared {spell} in the spellbook" + (f" (unprepared {out}: the spellbook was full)" if out else ""))
    return {"undo": undo}


def ensure_on_hotbar(spell, swap=None):
    """Out of combat (preparing is refused in combat): prepare `spell` through the spellbook if it isn't on a hotbar.
    Returns (undo list for restore(), message or None); raises RuntimeError when it can't be done."""
    install()
    if _c(f"return BG3UI.locate({_lua(spell)}) ~= nil"):
        return [], None
    log = []
    pr = _prepare(spell, swap, log)
    if pr.get("error"):
        if pr.get("undo"):
            _undo_prepare(pr["undo"], log)
        raise RuntimeError(f"{spell} is not on any hotbar and couldn't be prepared: {pr['error']}")
    return pr.get("undo", []), "; ".join(log)


def restore(undo):
    """Toggle back what ensure_on_hotbar prepared/unprepared (out of combat)."""
    if undo:
        if not _undo_prepare(undo, []):
            raise RuntimeError(f"couldn't restore the spellbook: {undo}")


SPELLBOOK_REGION = (0.0, 0.1, 0.62, 0.8)   # where the character panel's spellbook tiles are (fractions); blobs there seed the search
HOTBAR_REGION = (0.28, 0.84, 0.42, 0.14)


def _drag_to_hotbar(spell, log):
    """A cantrip / always-prepared spell taken off the hotbar can't be re-added by re-preparing: drag it from the spellbook
    (key K) onto a visible class-bar slot like a player, then re-prepare the spell it displaced (that puts it back).
    Restore = clear the dragged slot. The VM drag/assign commands run but add nothing (2026-10-29), so this is a real drag."""
    opened = False
    if not _c('return BG3UI.widget("CharacterPanel") ~= nil'):
        gameui.send_key(0x25)
        opened = bool(_poll(lambda: _c('return BG3UI.widget("CharacterPanel") ~= nil'), 2.0, 0.1))
        time.sleep(0.6)                                # the panel animates in
    try:
        src, _n = locate_element("CharacterPanel", f"n.DataContext ~= nil and n.DataContext == BG3UI.book({_lua(spell)})",
                                 region=SPELLBOOK_REGION)
        if not src:
            return {"error": "not on any hotbar, and its spellbook tile wasn't found to drag (scrolled out of view?)"}
        # a visible class-bar slot holding a preparable spell: re-preparing it afterwards puts it back
        n_slots = _c("return #BG3UI.pcp().KeyboardHotBars[2].SlotList")
        dst = victim = None
        for i in range(1, min(n_slots, 20) + 1):
            sid = _c(f"local c = BG3UI.pcp().KeyboardHotBars[2].SlotList[{i}].Content return c and c.PrototypeID")
            vm_ok = _c(f"local v = BG3UI.book({_lua(sid)}) local ok, lv = pcall(function() return v.SpellSlotLevel end) "
                       "return v ~= nil and v.Activated and ok and lv ~= nil and lv > 0") if sid else False
            if not vm_ok:
                continue
            dst, _n = locate_element("HotBar", f"n.DataContext ~= nil and n.DataContext == BG3UI.pcp().KeyboardHotBars[2].SlotList[{i}]",
                                     region=HOTBAR_REGION, budget=25)
            if dst:
                victim = sid
                break
        if not dst:
            return {"error": "no visible class-bar slot with a preparable spell to drop it on"}
        gameui._fast("drag %d %d %d %d" % (src[0] * 1920, src[1] * 1080, dst[0] * 1920, dst[1] * 1080))
        if not _poll(lambda: _c(f"return BG3UI.locate({_lua(spell)}) ~= nil"), 2.0, 0.15):
            return {"error": "dragged it from the spellbook, but it didn't land on the hotbar"}
        back = _toggle(victim, False) and _toggle(victim, True) and _poll(
            lambda: _c(f"return BG3UI.locate({_lua(victim)}) ~= nil"), 3.0, 0.15)
        log.append(f"dragged {spell} from the spellbook onto the hotbar (over {victim}, "
                   + ("re-prepared back)" if back else "which did NOT come back)"))
        return {"undo": [("clear", spell)]}
    finally:
        if opened:
            gameui.send_key(0x25)


def _undo_prepare(undo, log):
    """undo = [(spell, prepared state to restore) | ("clear", spell)] - ours first (frees the room), then the swapped one back."""
    bad = []
    for sp, want in undo:
        if sp == "clear":     # a slot the driver dragged onto the hotbar: take it off again
            _c(f"local b, i, s = BG3UI.locate({_lua(want)}) if s then BG3UI.hb().ClearSlotCommand:Execute(s) end return true")
            if not _poll(lambda: not _c(f"return BG3UI.locate({_lua(want)}) ~= nil"), 2.0, 0.15):
                bad.append(want)
        elif not _toggle(sp, want):
            bad.append(sp)
    log.append("spellbook restored" if not bad else f"spellbook NOT fully restored: {', '.join(bad)}")
    return not bad

def cast(spell, target=None, level=None, clicks=None, reactions=None, timeout=8.0, snapshot=True, ground=None, swap=None,
         keep_prepared=False):
    """Cast `spell` through the hotbar UI with real clicks. target: an entity uuid, "self" (the host), or None (no aim
    needed / shouts). clicks: how many target clicks (multi-target spells; default: until the cast starts, max 6).
    reactions: {interrupt id (or part of it): True} to accept that prompt; other prompts get Do Not React.
    snapshot=False skips the before/after HP and slot read (the test harness takes its own).
    ground=[x, y, z]: a ground-targeted spell, clicked on that world point (the cursor's ground hit is checked first).
    A spell that is known but not prepared is prepared through the spellbook first (when full, `swap` - or the last
    prepared levelled spell - is unprepared) and the spellbook is restored afterwards unless keep_prepared."""
    t0, log = time.time(), []
    install()
    gameui.focus_game()
    before = _snapshot(target) if snapshot else None
    host = _c("return Ext.Entity.GetAllEntitiesWithComponent('ClientControl')[1].Uuid.EntityUuid") if target == "self" else None
    undo = []
    if not _c(f"return BG3UI.locate({_lua(spell)}) ~= nil"):
        pr = _prepare(spell, swap, log)
        undo = pr.get("undo", [])
        if pr.get("error"):
            if undo:
                _undo_prepare(undo, log)
            return {"ok": False, "error": f"{spell} is not on any hotbar and couldn't be prepared: {pr['error']}",
                    "seconds": round(time.time() - t0, 1)}
    try:
        return _cast(spell, target, level, clicks, reactions, timeout, snapshot, ground, log, t0, before, host)
    finally:
        if undo and not keep_prepared:
            _undo_prepare(undo, log)


def _cast(spell, target, level, clicks, reactions, timeout, snapshot, ground, log, t0, before, host):
    # a slot stays greyed out while the previous cast's animation finishes: wait for it instead of failing
    _poll(lambda: _c(f"local b, i, s = BG3UI.locate({_lua(spell)}) return not s or "
                     "(BG3UI.hb().UseSlotCommand:CanExecute(s) and not BG3UI.casting())"), 4.0, 0.15)
    sel = _c(f"return BG3UI.select({_lua(spell)}, {level if level else 'nil'})")
    if sel.get("error"):
        return {"ok": False, "error": sel["error"], "where": sel, "seconds": round(time.time() - t0, 1)}
    log.append(f"hotbar: {sel['bar']} slot {sel['index']}" + (f", variant {sel['variant']}" if sel.get("variant") else "")
               + (f", slot level {sel['level']}" if sel.get("level") else "")
               + f" @{time.time() - t0:.1f}s")
    if ground:
        pos = _hover_ground(ground, log)
        if not pos:
            _c("BG3UI.cancel() return true")
            return {"ok": False, "error": "couldn't hover the ground point (" + "; ".join(log) + ")", "seconds": round(time.time() - t0, 1)}
        gameui._fast(f"click {pos[0]:.5f} {pos[1]:.5f} L 1")
        log.append(f"clicked the ground at {[round(v, 1) for v in ground]} @{time.time() - t0:.1f}s")
        target = None
    aim = host if target == "self" else target
    n = 0
    started = False
    if aim:
        pos = _hover_target(aim, log)   # verified once: a target doesn't move while you aim at it
        if not pos:
            _c("BG3UI.cancel() return true")
            return {"ok": False, "error": "couldn't hover the target (" + "; ".join(log) + ")", "seconds": round(time.time() - t0, 1)}
        for n in range(1, (clicks or 6) + 1):
            gameui._fast(f"click {pos[0]:.5f} {pos[1]:.5f} L 1")
            if clicks and n < clicks:
                continue
            started = _poll(lambda: _c("return BG3UI.casting() or BG3UI.prompt() ~= nil"), 0.8, 0.1)
            if started:
                break
        log.append(f"{n} click(s) on the target @{time.time() - t0:.1f}s")
    else:
        started = _poll(lambda: _c("return BG3UI.casting() or BG3UI.prompt() ~= nil"), 1.5, 0.1)
    if aim and not started:
        started = _poll(lambda: _c("return BG3UI.casting() or BG3UI.prompt() ~= nil"), 1.5, 0.1)
    if not started:
        state = _c("local hb = BG3UI.hb() return {upcast = hb.IsSelectingUpcastedSpell, variants = hb.IsShowingAContainerWithVariants}")
        _c("BG3UI.cancel() return true")
        return {"ok": False, "error": "the cast didn't start after the clicks", "ui": state, "steps": log,
                "seconds": round(time.time() - t0, 1)}
    log.append(f"cast started @{time.time() - t0:.1f}s")
    prompts = answer_prompt(reactions, log, timeout=timeout)
    if prompts:
        log.append(f"prompt answered @{time.time() - t0:.1f}s")
    _poll(lambda: not _c("return BG3UI.casting()"), timeout, 0.2)
    log.append(f"cast finished @{time.time() - t0:.1f}s")
    time.sleep(0.4)                                    # damage/status functors land just after the cast ends
    after = _snapshot(target) if snapshot else None
    spent = {lv: round(before["slots"][lv] - after["slots"].get(lv, 0), 2) for lv in (before or {}).get("slots", {})
             if after and round(before["slots"][lv] - after["slots"].get(lv, 0), 2) != 0}
    return {"ok": True, "hotbar": sel, "steps": log, "prompts": prompts, "slots_spent": spent,
            "before": before, "after": after, "seconds": round(time.time() - t0, 1)}  # spent = {} without snapshot
