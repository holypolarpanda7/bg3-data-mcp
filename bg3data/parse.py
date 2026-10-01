"""Parsers for BG3 data files: stats .txt, localization .xml, and .lsx resources."""
import re
import xml.etree.ElementTree as ET

_ENTRY = re.compile(r'^new entry "([^"]+)"')
_TYPE = re.compile(r'^type "([^"]+)"')
_USING = re.compile(r'^using "([^"]+)"')
_DATA = re.compile(r'^data "([^"]+)" "(.*)"\s*$')
_KEY = re.compile(r'^key "([^"]+)",\s*"(.*)"\s*$')


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8-sig", errors="replace").replace("\r\n", "\n")


def parse_stats(text):
    """Yield (name, type, using, {field: value}) for every `new entry` block."""
    name = typ = using = None
    data = {}
    for line in text.split("\n"):
        line = line.strip()
        m = _ENTRY.match(line)
        if m:
            if name is not None:
                yield name, typ, using, data
            name, typ, using, data = m.group(1), None, None, {}
            continue
        if name is None:
            continue
        if (m := _TYPE.match(line)):
            typ = m.group(1)
        elif (m := _USING.match(line)):
            using = m.group(1)
        elif (m := _DATA.match(line)) or (m := _KEY.match(line)):
            data[m.group(1)] = m.group(2)
    if name is not None:
        yield name, typ, using, data


def parse_loca(path):
    """Yield (handle, version, text) from a localization .xml."""
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag == "content":
            yield el.get("contentuid"), int(el.get("version") or 1), el.text or ""
            el.clear()


def _attrs(node):
    """Direct <attribute> children of an lsx <node> as {id: value-or-handle}."""
    out = {}
    for a in node.findall("attribute"):
        v = a.get("value")
        if v is None and a.get("handle") is not None:
            v = f'{a.get("handle")};{a.get("version") or 1}'
        out[a.get("id")] = v
    return out


def _children(node, node_id):
    ch = node.find("children")
    return [] if ch is None else [n for n in ch.findall("node") if n.get("id") == node_id]


def parse_templates(path):
    """Yield dicts for every GameObjects template in a RootTemplates .lsx."""
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag != "node" or el.get("id") != "GameObjects":
            continue
        attrs = _attrs(el)
        if not attrs.get("MapKey"):
            continue
        skills, statuses = [], []
        for sl in _children(el, "SkillList"):
            for s in _children(sl, "Skill"):
                skills.append(_attrs(s).get("Skill"))
        for sl in _children(el, "StatusList"):
            for s in _children(sl, "Status"):
                statuses.append(_attrs(s).get("Object"))
        if skills:
            attrs["_SkillList"] = ";".join(filter(None, skills))
        if statuses:
            attrs["_StatusList"] = ";".join(filter(None, statuses))
        yield attrs
        el.clear()


def parse_nodes(path, node_id):
    """Yield attribute dicts for every node with the given id (Progression, SpellList, ...)."""
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag == "node" and el.get("id") == node_id:
            a = _attrs(el)
            sub = []
            for s in _children(el, "SubClasses"):
                for c in _children(s, "SubClass"):
                    sub.append(_attrs(c).get("Object"))
            if sub:
                a["_SubClasses"] = ";".join(filter(None, sub))
            # other child lists of single-Object nodes (ClassDescription Tags, ...) -> "_<ChildId>"
            ch = el.find("children")
            for c in ([] if ch is None else ch.findall("node")):
                cid = c.get("id")
                if cid == "SubClasses":
                    continue
                ca = _attrs(c)
                if set(ca) == {"Object"}:
                    key = "_" + cid
                    a[key] = (a[key] + ";" if key in a else "") + (ca["Object"] or "")
            yield a


def list_node_ids(path):
    """Node ids directly under the region's root children (e.g. SpellList, PassiveList)."""
    ids = set()
    for _, el in ET.iterparse(path, events=("start",)):
        if el.tag == "node":
            ids.add(el.get("id"))
    return ids


def parse_multieffect(path):
    """A MultiEffectInfos .lsx -> (uuid, name, [ {resource, start, target_bones, source_bones} ])."""
    root = ET.parse(path).getroot()
    for node in root.iter("node"):
        if node.get("id") != "MultiEffectInfos":
            continue
        a = _attrs(node)
        effects = []
        for info in node.iter("node"):
            if info.get("id") != "EffectInfo":
                continue
            ia = _attrs(info)
            bones = {"TargetBone": [], "SourceBone": []}
            for b in info.iter("node"):
                if b.get("id") in bones:
                    v = _attrs(b).get("Value")
                    if v:
                        bones[b.get("id")].append(v)
            effects.append({"resource": ia.get("EffectResourceGuid"), "start": ia.get("StartTextKey") or "",
                            "target_bones": bones["TargetBone"], "source_bones": bones["SourceBone"]})
        return a.get("UUID"), a.get("Name"), effects
    return None


def parse_effect_bank(path):
    """Effect resources in a Content/Assets/Effects bank: (id, name, duration, looping, source_file)."""
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag != "node" or el.get("id") != "Resource":
            continue
        a = _attrs(el)
        src = a.get("SourceFile") or ""
        if a.get("ID") and src.lower().endswith(".lsfx"):
            yield a["ID"], a.get("EffectName") or a.get("Name"), a.get("Duration"), a.get("Looping"), src
        el.clear()
