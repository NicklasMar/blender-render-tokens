bl_info = {
    "name": "File Output Render Tokens",
    "author": "Nicklas.mar",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "Compositor > Sidebar > Render Tokens | Properties > Output",
    "description": "Render tokens for File Output nodes and the render filepath",
    "category": "Compositing",
    "doc_url": "https://github.com/NicklasMar/blender-render-tokens",
}

import bpy
import os
import re
import socket
import datetime
import urllib.request
import json
import base64
from bpy.app.handlers import persistent
from bpy.props import (StringProperty, BoolProperty, EnumProperty,
                       CollectionProperty, IntProperty)

# ─── Update config (filled in after GitHub repo is created) ──────
_GITHUB_OWNER = "NicklasMar"
_GITHUB_REPO  = "blender-render-tokens"
_GITHUB_FILE  = "file_output_tokens.py"
_GITHUB_BRANCH = "main"


DEBUG = True


def _log(msg):
    if DEBUG:
        print(f"[Render Tokens] {msg}")


# ─────────────────────────────────────────────────────────────────
# Token Resolution
# ─────────────────────────────────────────────────────────────────

def resolve_tokens(path, scene, pass_name="", frame=None):
    now = datetime.datetime.now()
    render = scene.render

    if frame is None:
        frame = scene.frame_current

    blend = bpy.data.filepath
    prj = os.path.splitext(os.path.basename(blend))[0] if blend else "untitled"
    camera_raw = scene.camera.name if scene.camera else "no_camera"
    camera = camera_raw[:-4] if camera_raw.endswith("_CAM") else camera_raw

    version = str(getattr(scene, "render_tokens_version", 1)).zfill(3)

    rx = int(render.resolution_x * render.resolution_percentage / 100)
    ry = int(render.resolution_y * render.resolution_percentage / 100)
    fps_val = render.fps / render.fps_base
    fps_str = f"{fps_val:.3f}".rstrip("0").rstrip(".")

    username = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    computer = socket.gethostname()
    author = render.stamp_note_text if render.use_stamp_note else username

    _ENGINE_NAMES = {
        "CYCLES":              "Cycles",
        "BLENDER_EEVEE":       "EEVEE",
        "BLENDER_EEVEE_NEXT":  "EEVEE",
        "BLENDER_WORKBENCH":   "Workbench",
        "BLENDER_GAME":        "BGE",
    }
    engine_display = _ENGINE_NAMES.get(render.engine, render.engine)

    try:
        viewlayer = context.view_layer.name if (context := bpy.context) else scene.view_layers[0].name
    except Exception:
        viewlayer = scene.view_layers[0].name if scene.view_layers else ""

    # Build a mapping: default_name -> active token string (custom or default).
    # _active_token_name() reads addon preferences; falls back silently.
    def tok(default):
        return _active_token_name(default)

    TOKEN_MAP = {
        # $cv* variants — fixed names, not renameable, kept for legacy
        "$cvAuthor":     author,
        "$cvUsername":   username,
        "$cvComputer":   computer,
        "$cvRenderer":   engine_display,
        "$cvHeight":     f"{ry}p",
        # Renameable tokens — keyed by whatever the user called them
        tok("$Author"):     author,
        tok("$Username"):   username,
        tok("$Computer"):   computer,
        tok("$Renderer"):   engine_display,
        tok("$Height"):     f"{ry}p",
        tok("$viewlayer"):  viewlayer,
        tok("$camera"):     camera,
        tok("$range"):      f"{scene.frame_start}-{scene.frame_end}",
        tok("$frame"):      str(frame).zfill(4),
        tok("$pass"):       pass_name,
        tok("$prj"):        prj,
        tok("$take"):       scene.name,
        tok("$res"):        f"{rx}x{ry}",
        tok("$fps"):        fps_str,
        tok("$version"):    version,
        tok("$YYYY"):       now.strftime("%Y"),
        tok("$YY"):         now.strftime("%y"),
        tok("$MM"):         now.strftime("%m"),
        tok("$DD"):         now.strftime("%d"),
        tok("$hh"):         now.strftime("%H"),
        tok("$mm"):         now.strftime("%M"),
        tok("$ss"):         now.strftime("%S"),
    }

    result = path
    for token, value in TOKEN_MAP.items():
        result = result.replace(token, value)
    return result


# ─────────────────────────────────────────────────────────────────
# Path helpers
# ─────────────────────────────────────────────────────────────────

def _blend_dir():
    fp = bpy.data.filepath
    if fp:
        return os.path.dirname(os.path.abspath(fp))
    return ""


def _to_absolute(path):
    if not path:
        return path
    path = path.strip()
    if not path:
        return path
    if path.startswith("//"):
        rel = path[2:].replace("\\", "/")
        bd = _blend_dir()
        if bd:
            return os.path.normpath(os.path.join(bd, rel))
        return os.path.normpath(os.path.abspath(rel))
    if os.path.isabs(path):
        return os.path.normpath(path)
    bd = _blend_dir()
    if bd:
        return os.path.normpath(os.path.join(bd, path))
    return os.path.normpath(os.path.abspath(path))


def _find_src_dir(raw_dir):
    return _to_absolute(raw_dir)


# ─────────────────────────────────────────────────────────────────
# Node access
# ─────────────────────────────────────────────────────────────────

def _get_compositor_trees(scene):
    seen = set()
    trees = []

    def _add(tree):
        if tree and id(tree) not in seen:
            seen.add(id(tree))
            trees.append(tree)

    _add(getattr(scene, "compositing_node_group", None))
    _add(getattr(scene, "node_tree", None))
    for ng in bpy.data.node_groups:
        if getattr(ng, "type", "") == "COMPOSITING":
            _add(ng)

    return trees


def _output_file_nodes(scene):
    for tree in _get_compositor_trees(scene):
        for node in tree.nodes:
            if node.type == "OUTPUT_FILE":
                yield node


def _get_directory(node):
    # base_path is the authoritative attribute in Blender 4.x+; directory is legacy
    val = getattr(node, "base_path", None) or getattr(node, "directory", "") or ""
    val = val.strip()
    if val.startswith("\\\\"):
        val = "//" + val[2:].replace("\\", "/")
    return val


def _set_directory(node, value):
    # Set both attributes so it works across Blender versions.
    # In 4.x+ base_path is the real one; directory may be read-only or a no-op alias.
    set_any = False
    for attr in ("base_path", "directory"):
        if hasattr(node, attr):
            try:
                setattr(node, attr, value)
                set_any = True
            except (AttributeError, TypeError):
                pass
    if not set_any:
        _log(f"WARNING: could not set directory on node — no base_path or directory attr")


def _get_file_name(node):
    return getattr(node, "file_name", "") or ""


def _set_file_name(node, value):
    if hasattr(node, "file_name"):
        node.file_name = value


def _get_pass_name(node):
    for inp in getattr(node, "inputs", []):
        if getattr(inp, "type", "") != "CUSTOM" and inp.name:
            return inp.name
    return ""


def _expand_hashes(template, frame):
    return re.sub(r"#+", lambda m: str(frame).zfill(len(m.group())), template)


# ─────────────────────────────────────────────────────────────────
# Backup / restore / resolve
# ─────────────────────────────────────────────────────────────────

_originals = {}


def _backup_and_resolve(scene):
    global _originals
    _originals.clear()
    frame = scene.frame_current

    # Regular render filepath
    orig_fp = scene.render.filepath
    _originals["__filepath__"] = orig_fp
    scene.render.filepath = resolve_tokens(orig_fp, scene, "", frame)
    _log(f"render_pre — filepath resolved")

    nodes = list(_output_file_nodes(scene))
    _log(f"render_pre — {len(nodes)} File Output node(s), frame {frame}")

    for node in nodes:
        nid = node.as_pointer()
        orig_dir = _get_directory(node)
        orig_fn = _get_file_name(node)

        # Backup slot paths (each input slot has its own file subpath)
        orig_slots = {i: slot.path for i, slot in enumerate(getattr(node, "file_slots", []))}

        _originals[nid] = {"directory": orig_dir, "file_name": orig_fn, "slots": orig_slots}

        pass_name = _get_pass_name(node)
        # Resolve tokens but keep the // prefix — Blender needs its own relative paths
        new_dir = resolve_tokens(orig_dir, scene, pass_name, frame)
        new_fn = resolve_tokens(orig_fn, scene, pass_name, frame)

        _set_directory(node, new_dir)
        _set_file_name(node, new_fn)

        # Also create the directory on disk
        try:
            os.makedirs(_to_absolute(new_dir), exist_ok=True)
        except OSError as e:
            _log(f"  WARNING: could not create dir '{new_dir}': {e}")
        _log(f"  '{node.name}': directory='{new_dir}'  file_name='{new_fn}'")


def _restore(scene):
    global _originals
    if "__filepath__" in _originals:
        scene.render.filepath = _originals["__filepath__"]
    for node in _output_file_nodes(scene):
        nid = node.as_pointer()
        if nid in _originals:
            _set_directory(node, _originals[nid]["directory"])
            _set_file_name(node, _originals[nid]["file_name"])
            orig_slots = _originals[nid].get("slots", {})
            for i, slot in enumerate(getattr(node, "file_slots", [])):
                if i in orig_slots:
                    slot.path = orig_slots[i]
    _originals.clear()
    _log("Paths restored.")


def _resolve_for_frame(scene, frame):
    for node in _output_file_nodes(scene):
        nid = node.as_pointer()
        if nid not in _originals:
            continue
        pass_name = _get_pass_name(node)
        new_dir = resolve_tokens(_originals[nid]["directory"], scene, pass_name, frame)
        new_fn = resolve_tokens(_originals[nid]["file_name"], scene, pass_name, frame)
        _set_directory(node, new_dir)
        _set_file_name(node, new_fn)
        try:
            os.makedirs(_to_absolute(new_dir), exist_ok=True)
        except OSError as e:
            _log(f"  WARNING: could not create dir '{new_dir}': {e}")


def _rename_frame(scene, frame):
    for node in _output_file_nodes(scene):
        raw_dir = _get_directory(node)
        raw_fn = _get_file_name(node)

        if "$" not in raw_dir and "$" not in raw_fn:
            continue

        pass_name = _get_pass_name(node)
        src_dir = _find_src_dir(raw_dir)
        resolved_fn_template = resolve_tokens(raw_fn, scene, pass_name, frame)
        dst_dir = _to_absolute(resolve_tokens(raw_dir, scene, pass_name, frame))

        literal = _expand_hashes(raw_fn, frame) if "#" in raw_fn else raw_fn + str(frame).zfill(4)
        resolved_fn = (_expand_hashes(resolved_fn_template, frame)
                       if "#" in resolved_fn_template
                       else resolve_tokens(literal, scene, pass_name, frame))

        if not os.path.isdir(src_dir):
            _log(f"  Dir not found: {src_dir}")
            continue

        for fname in os.listdir(src_dir):
            name_no_ext, ext = os.path.splitext(fname)
            if name_no_ext != literal:
                continue
            old_path = os.path.join(src_dir, fname)
            new_path = os.path.join(dst_dir, resolved_fn + ext)
            if old_path == new_path:
                continue
            try:
                os.makedirs(dst_dir, exist_ok=True)
                os.rename(old_path, new_path)
                _log(f"  Moved: {old_path} → {new_path}")
            except OSError as e:
                _log(f"  ERROR: {e}")


# ─────────────────────────────────────────────────────────────────
# Render Handlers
# ─────────────────────────────────────────────────────────────────

def _scene(args):
    for a in args:
        if isinstance(a, bpy.types.Scene):
            return a
    return bpy.context.scene if bpy.context else None


@persistent
def _on_render_init(*args):
    """Fires before animation render starts — before Blender caches compositor paths."""
    s = _scene(args)
    if s:
        _backup_and_resolve(s)


@persistent
def _on_render_pre(*args):
    """Single-frame fallback: only resolves if render_init didn't already run."""
    s = _scene(args)
    if s and not _originals:
        _backup_and_resolve(s)


@persistent
def _on_render_write(*args):
    pass


@persistent
def _on_render_post(*args):
    pass


@persistent
def _on_render_complete(*args):
    s = _scene(args)
    if s:
        _restore(s)


@persistent
def _on_render_cancel(*args):
    s = _scene(args)
    if s:
        _restore(s)


_DEFAULT_RENDER_OUTPUT = "//Export/$prj/$version/$camera/PNG/$camera_$version_PNG_####"

# Blender's built-in default render filepaths (platform-dependent)
_BLENDER_DEFAULT_OUTPUTS = {"//", "/tmp\\", "/tmp/"}


@persistent
def _on_load_post(*args):
    try:
        scene = bpy.context.scene
        if scene:
            _ensure_presets_initialized(scene)
            if scene.render.filepath in _BLENDER_DEFAULT_OUTPUTS:
                scene.render.filepath = _DEFAULT_RENDER_OUTPUT
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────
# Default presets
# ─────────────────────────────────────────────────────────────────

_DEFAULT_PRESETS = [
    {
        "name":      "Beauty",
        "directory": "//Export/$prj/$version/$camera/Beauty/",
        "file_name": "$camera_$version_Beauty_",
    },
    {
        "name":      "Cryptomatte",
        "directory": "//Export/$prj/$version/$camera/Cryptomatte/",
        "file_name": "$camera_$version_Cryptomatte_",
    },
    {
        "name":      "AOV",
        "directory": "//Export/$prj/$version/$camera/AOV/",
        "file_name": "$camera_$version_$pass_",
    },
]


def _ensure_presets_initialized(scene):
    """Add default presets if the scene has none yet."""
    if scene is None or len(scene.render_tokens_presets) > 0:
        return
    for data in _DEFAULT_PRESETS:
        p = scene.render_tokens_presets.add()
        p.name      = data["name"]
        p.directory = data["directory"]
        p.file_name = data["file_name"]
    _log(f"Default presets added to scene '{scene.name}'")


# ─────────────────────────────────────────────────────────────────
# Token data
# ─────────────────────────────────────────────────────────────────

TOKEN_GROUPS = [
    ("Project", [
        "$prj", "$camera", "$viewlayer", "$take", "$pass",
        "$frame", "$res", "$range", "$fps", "$version",
    ]),
    ("Date / Time", [
        "$YYYY", "$YY", "$MM", "$DD", "$hh", "$mm", "$ss",
    ]),
    ("System", [
        "$Author", "$Username", "$Computer", "$Renderer", "$Height",
    ]),
]

TOKEN_DESCRIPTIONS = {
    "$prj":        "Project filename (no extension)",
    "$camera":     "Active camera name",
    "$viewlayer":  "Active view layer name",
    "$take":       "Scene name",
    "$pass":       "First render pass input name",
    "$frame":      "Current frame, zero-padded (0001)",
    "$res":        "Resolution  e.g. 1920x1080",
    "$range":      "Frame range  e.g. 1-250",
    "$fps":        "Frame rate",
    "$YYYY":       "Year (4-digit)",
    "$YY":         "Year (2-digit)",
    "$MM":         "Month (01–12)",
    "$DD":         "Day (01–31)",
    "$hh":         "Hour (00–23)",
    "$mm":         "Minute (00–59)",
    "$ss":         "Second (00–59)",
    "$version":    "Version number, zero-padded (001)",
    "$cvAuthor":   "Author (stamp note or OS user)",
    "$cvUsername": "OS username",
    "$cvComputer": "Computer hostname",
    "$cvRenderer": "Render engine",
    "$cvHeight":   "Render height  e.g. 1080p",
    "$Author":     "Author (stamp note or OS user)",
    "$Username":   "OS username",
    "$Computer":   "Computer hostname",
    "$Renderer":   "Render engine",
    "$Height":     "Render height  e.g. 1080p",
}

# Tokens that can receive a custom alias (shown in preferences)
_ALIASABLE_TOKENS = [
    ("$prj",       "Project filename (no extension)"),
    ("$camera",    "Active camera name"),
    ("$viewlayer", "Active view layer name"),
    ("$take",      "Scene name"),
    ("$pass",   "Render pass input name"),
    ("$frame",  "Current frame, zero-padded (0001)"),
    ("$res",    "Resolution e.g. 1920x1080"),
    ("$range",    "Frame range e.g. 1-250"),
    ("$fps",      "Frame rate"),
    ("$version",  "Version number (001)"),
    ("$YYYY",     "Year (4-digit)"),
    ("$YY",       "Year (2-digit)"),
    ("$MM",       "Month (01-12)"),
    ("$DD",       "Day (01-31)"),
    ("$hh",       "Hour (00-23)"),
    ("$mm",       "Minute (00-59)"),
    ("$ss",       "Second (00-59)"),
    ("$Author",   "Author (stamp note or OS user)"),
    ("$Username", "OS username"),
    ("$Computer", "Computer hostname"),
    ("$Renderer", "Render engine"),
    ("$Height",   "Render height e.g. 1080p"),
]


# ─────────────────────────────────────────────────────────────────
# Property Groups
# ─────────────────────────────────────────────────────────────────

def _on_alias_rename(self, context):
    old = self._prev_name.strip()
    new = self.custom_name.strip()
    if not old or old == new:
        self._prev_name = new
        return
    for scene in bpy.data.scenes:
        # render filepath
        scene.render.filepath = scene.render.filepath.replace(old, new)
        # scene-level templates
        scene.render_tokens_dir_template  = scene.render_tokens_dir_template.replace(old, new)
        scene.render_tokens_file_template = scene.render_tokens_file_template.replace(old, new)
        # File Output nodes
        for node in _output_file_nodes(scene):
            _set_directory(node, _get_directory(node).replace(old, new))
            _set_file_name(node, _get_file_name(node).replace(old, new))
            for slot in getattr(node, "file_slots", []):
                slot.path = slot.path.replace(old, new)
    self._prev_name = new


class TokenAlias(bpy.types.PropertyGroup):
    """One row in the token rename table: default_name → custom_name."""
    default_name: StringProperty(name="Default Token")
    custom_name:  StringProperty(name="Custom Name", default="",
                                  description="Leave empty to use the default token name",
                                  update=_on_alias_rename)
    _prev_name:   StringProperty(options={"HIDDEN"})
    description:  StringProperty(name="Description")


class TokenPreset(bpy.types.PropertyGroup):
    name:      StringProperty(name="Name",      default="New Preset")
    directory: StringProperty(name="Directory", default="//Export/$prj/$camera/")
    file_name: StringProperty(name="File Name", default="$camera_$res_####")


# ─────────────────────────────────────────────────────────────────
# Addon Preferences
# ─────────────────────────────────────────────────────────────────

class TOKENS_OT_update(bpy.types.Operator):
    bl_idname = "render_tokens.update"
    bl_label = "Update Addon"
    bl_description = "Download and install the latest version from GitHub"

    def execute(self, context):
        api_url = (f"https://api.github.com/repos/{_GITHUB_OWNER}/{_GITHUB_REPO}"
                   f"/contents/{_GITHUB_FILE}?ref={_GITHUB_BRANCH}")
        req = urllib.request.Request(api_url, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Blender-Addon-Updater",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            content = base64.b64decode(data["content"]).decode("utf-8")
        except Exception as e:
            self.report({"ERROR"}, f"Fetch failed: {e}")
            return {"CANCELLED"}

        # Parse remote version
        m = re.search(r'"version":\s*\((\d+),\s*(\d+),\s*(\d+)\)', content)
        if not m:
            self.report({"ERROR"}, "Could not read remote version")
            return {"CANCELLED"}
        remote_ver = tuple(int(x) for x in m.groups())
        current_ver = bl_info["version"]

        if remote_ver <= current_ver:
            self.report({"INFO"}, f"Already up to date (v{'.'.join(map(str, current_ver))})")
            return {"FINISHED"}

        # Write new file
        addon_path = os.path.abspath(__file__)
        try:
            with open(addon_path, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            self.report({"ERROR"}, f"Could not write file: {e}")
            return {"CANCELLED"}

        # Defer reload via timer so operator finishes cleanly first
        import sys
        mod_name = __name__

        def _reload():
            bpy.ops.preferences.addon_disable(module=mod_name)
            for key in [k for k in sys.modules if k == mod_name or k.startswith(mod_name + ".")]:
                del sys.modules[key]
            bpy.ops.preferences.addon_enable(module=mod_name)
            return None  # run once

        bpy.app.timers.register(_reload, first_interval=0.1)
        self.report({"INFO"}, f"Updated to v{'.'.join(map(str, remote_ver))} — reloading...")
        return {"FINISHED"}


def _ensure_aliases_initialized(prefs):
    """Populate token_aliases with defaults if the list is still empty."""
    if len(prefs.token_aliases) == 0:
        for token, desc in _ALIASABLE_TOKENS:
            a = prefs.token_aliases.add()
            a.default_name = token
            a.description  = desc
            a.custom_name  = token
            a._prev_name   = token
        _log("Token aliases initialized with defaults")


def _active_token_name(default_name):
    """Return the current (possibly renamed) token string for a given default token."""
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        for a in prefs.token_aliases:
            if a.default_name == default_name:
                return a.custom_name.strip() or default_name
    except Exception:
        pass
    return default_name


class TOKENS_OT_reset_aliases(bpy.types.Operator):
    bl_idname  = "render_tokens.reset_aliases"
    bl_label   = "Reset All Token Names"
    bl_description = "Revert every token name back to the Blender default"

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        for a in prefs.token_aliases:
            a.custom_name = a.default_name
        self.report({"INFO"}, "All token names reset to defaults")
        return {"FINISHED"}


class TOKENS_OT_reset_single_alias(bpy.types.Operator):
    bl_idname  = "render_tokens.reset_single_alias"
    bl_label   = "Reset Token Name"
    bl_description = "Revert this token name to its default"

    default_name: bpy.props.StringProperty()

    def execute(self, context):
        prefs = context.preferences.addons[__name__].preferences
        for a in prefs.token_aliases:
            if a.default_name == self.default_name:
                a.custom_name = a.default_name
                break
        return {"FINISHED"}


class TOKENS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    show_aliases: BoolProperty(
        name="Show & Rename Tokens",
        default=False,
        description="Show all tokens and assign custom names (company/pipeline presets)",
    )
    token_aliases: CollectionProperty(type=TokenAlias)

    def draw(self, context):
        layout = self.layout

        layout.operator("render_tokens.update", icon="FILE_REFRESH")

        layout.separator(factor=0.5)

        # ── Rename Tokens ─────────────────────────────────────────────────────
        layout.prop(self, "show_aliases", toggle=True, icon="INFO")
        if self.show_aliases:
            _ensure_aliases_initialized(self)
            abox = layout.box()
            acol = abox.column(align=True)
            header = acol.row()
            header.label(text="Token Name")
            header.label(text="Resolves to")
            acol.separator(factor=0.3)
            for alias in self.token_aliases:
                changed = alias.custom_name.strip() != alias.default_name
                row = acol.row(align=True)
                row.scale_y = 0.85
                sub = row.row(align=True)
                sub.scale_x = 0.9
                sub.prop(alias, "custom_name", text="")
                if changed:
                    op = row.operator("render_tokens.reset_single_alias",
                                      text="", icon="LOOP_BACK", emboss=False)
                    op.default_name = alias.default_name
                else:
                    row.label(text="", icon="BLANK1")
                row.label(text=alias.description)
            acol.separator(factor=0.5)
            acol.operator("render_tokens.reset_aliases", icon="LOOP_BACK")
            layout.separator(factor=0.3)



# ─────────────────────────────────────────────────────────────────
# UIList
# ─────────────────────────────────────────────────────────────────

class TOKENS_UL_presets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=False)
            row.prop(item, "name", text="", emboss=False)
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="DOT")


# ─────────────────────────────────────────────────────────────────
# Operators
# ─────────────────────────────────────────────────────────────────

class TOKENS_OT_quick_apply(bpy.types.Operator):
    bl_idname = "render_tokens.quick_apply"
    bl_label = "Quick Apply"
    bl_description = "Apply this preset to all File Output nodes"

    index: IntProperty(default=0)

    def execute(self, context):
        node = context.active_node
        if node is None or node.type != "OUTPUT_FILE":
            self.report({"WARNING"}, "No File Output node selected")
            return {"CANCELLED"}
        presets = context.scene.render_tokens_presets
        if self.index < 0 or self.index >= len(presets):
            return {"CANCELLED"}
        preset = presets[self.index]
        _set_directory(node, preset.directory)
        _set_file_name(node, preset.file_name)
        node.name = "File Output"
        node.label = f"View Layer {preset.name}"
        return {"FINISHED"}


class TOKENS_OT_select_preset(bpy.types.Operator):
    bl_idname = "render_tokens.select_preset"
    bl_label = "Select Preset"
    index: IntProperty(default=0)

    def execute(self, context):
        context.scene.render_tokens_preset_index = self.index
        return {"FINISHED"}


class TOKENS_OT_add_preset(bpy.types.Operator):
    bl_idname = "render_tokens.add_preset"
    bl_label = "Add Empty Preset"
    bl_description = "Add a blank preset"

    def execute(self, context):
        presets = context.scene.render_tokens_presets
        p = presets.add()
        p.name = "New Preset"
        p.directory = "//Export/$prj/$camera/"
        p.file_name = "$camera_$res_####"
        context.scene.render_tokens_preset_index = len(presets) - 1
        return {"FINISHED"}


class TOKENS_OT_preset_from_node(bpy.types.Operator):
    bl_idname = "render_tokens.preset_from_node"
    bl_label = "Preset from Node"
    bl_description = "Create a preset from the active File Output node's current paths"

    def execute(self, context):
        node = context.active_node
        if node is None or node.type != "OUTPUT_FILE":
            self.report({"WARNING"}, "No File Output node selected")
            return {"CANCELLED"}
        presets = context.scene.render_tokens_presets
        p = presets.add()
        p.name = node.name
        p.directory = _get_directory(node)
        p.file_name = _get_file_name(node)
        context.scene.render_tokens_preset_index = len(presets) - 1
        return {"FINISHED"}


class TOKENS_OT_open_folder(bpy.types.Operator):
    bl_idname = "render_tokens.open_folder"
    bl_label = "Open Render Folder"
    bl_description = "Open the resolved render folder in the file manager"

    path: bpy.props.StringProperty()

    def execute(self, context):
        import os
        path = self.path
        if not os.path.exists(path):
            self.report({"WARNING"}, f"Folder does not exist: {path}")
            return {"CANCELLED"}
        bpy.ops.wm.path_open(filepath=path)
        return {"FINISHED"}


class TOKENS_OT_pick_dir(bpy.types.Operator):
    bl_idname = "render_tokens.pick_dir"
    bl_label = "Choose Directory"
    bl_description = "Browse for a base directory"

    directory: bpy.props.StringProperty(subtype="DIR_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        context.scene.render_tokens_dir_template = self.directory
        return {"FINISHED"}


class TOKENS_OT_copy_token(bpy.types.Operator):
    bl_idname = "render_tokens.copy_token"
    bl_label = "Copy Token"
    bl_description = "Copy token to clipboard"

    token: bpy.props.StringProperty()

    def execute(self, context):
        context.window_manager.clipboard = self.token
        self.report({"INFO"}, f"Copied: {self.token}")
        return {"FINISHED"}


class TOKENS_OT_version_inc(bpy.types.Operator):
    bl_idname = "render_tokens.version_inc"
    bl_label = "Version +"
    bl_description = "Increment $version"

    def execute(self, context):
        context.scene.render_tokens_version += 1
        return {"FINISHED"}


class TOKENS_OT_version_dec(bpy.types.Operator):
    bl_idname = "render_tokens.version_dec"
    bl_label = "Version -"
    bl_description = "Decrement $version"

    def execute(self, context):
        v = context.scene.render_tokens_version
        if v > 1:
            context.scene.render_tokens_version = v - 1
        return {"FINISHED"}


class TOKENS_OT_remove_preset(bpy.types.Operator):
    bl_idname = "render_tokens.remove_preset"
    bl_label = "Remove Preset"
    bl_description = "Remove the selected preset"

    def execute(self, context):
        presets = context.scene.render_tokens_presets
        idx = context.scene.render_tokens_preset_index
        if 0 <= idx < len(presets):
            presets.remove(idx)
            context.scene.render_tokens_preset_index = max(0, idx - 1)
        return {"FINISHED"}


class TOKENS_OT_apply_preset(bpy.types.Operator):
    bl_idname = "render_tokens.apply_preset"
    bl_label = "Apply to Node"
    bl_description = "Set directory and file name of the active File Output node to this preset"

    def execute(self, context):
        node = context.active_node
        if node is None or node.type != "OUTPUT_FILE":
            self.report({"WARNING"}, "No File Output node selected")
            return {"CANCELLED"}
        presets = context.scene.render_tokens_presets
        idx = context.scene.render_tokens_preset_index
        if idx < 0 or idx >= len(presets):
            return {"CANCELLED"}
        preset = presets[idx]
        _set_directory(node, preset.directory)
        _set_file_name(node, preset.file_name)
        node.name = "File Output"
        node.label = f"View Layer {preset.name}"
        return {"FINISHED"}


class TOKENS_OT_move_preset(bpy.types.Operator):
    bl_idname  = "render_tokens.move_preset"
    bl_label   = "Move Preset"
    bl_description = "Move the selected preset up or down"

    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        presets = context.scene.render_tokens_presets
        idx = context.scene.render_tokens_preset_index
        if self.direction == "UP" and idx > 0:
            presets.move(idx, idx - 1)
            context.scene.render_tokens_preset_index = idx - 1
        elif self.direction == "DOWN" and idx < len(presets) - 1:
            presets.move(idx, idx + 1)
            context.scene.render_tokens_preset_index = idx + 1
        return {"FINISHED"}


# ─────────────────────────────────────────────────────────────────
# Panels
# ─────────────────────────────────────────────────────────────────

class TOKENS_PT_panel(bpy.types.Panel):
    bl_label = "Render Tokens"
    bl_idname = "TOKENS_PT_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Render Tokens"
    bl_order = 0

    @classmethod
    def poll(cls, context):
        sdata = context.space_data
        return sdata is not None and sdata.tree_type == "CompositorNodeTree"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # Read paths from the active node
        node = context.active_node
        if node is not None and node.type == "OUTPUT_FILE":
            pass_name = _get_pass_name(node)
            resolved_dir = resolve_tokens(_get_directory(node), scene, pass_name)
            resolved_fn  = resolve_tokens(_get_file_name(node), scene, pass_name)
            raw_dir = _get_directory(node)
            raw_fn  = _get_file_name(node)
        else:
            resolved_dir = resolved_fn = raw_dir = raw_fn = ""

        # Path Preview label
        layout.label(text="Path Preview")

        # Directory
        box = layout.box()
        box.scale_y = 0.7
        row = box.row(align=True)
        row.label(text=resolved_dir if resolved_dir else "No File Output node selected")
        row.operator("render_tokens.open_folder", text="", icon="FILE_FOLDER", emboss=False).path = resolved_dir

        # File name
        box2 = layout.box()
        box2.scale_y = 0.7
        box2.label(text=resolved_fn)

        # Version control
        vbox = layout.box()
        row = vbox.row(align=True)
        split = row.split(factor=0.75, align=True)
        split.label(text=f"Version: {str(scene.render_tokens_version).zfill(3)}")
        sub = split.row(align=True)
        sub.operator("render_tokens.version_dec", text="-")
        sub.operator("render_tokens.version_inc", text="+")


class TOKENS_PT_reference(bpy.types.Panel):
    bl_label = "Tokens"
    bl_idname = "TOKENS_PT_reference"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Render Tokens"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        for group_name, tokens in TOKEN_GROUPS:
            layout.label(text=group_name)
            box = layout.box()
            col = box.column(align=False)
            for token in tokens:
                active = _active_token_name(token)
                row = col.row(align=True)
                row.scale_y = 1.0
                split = row.split(factor=0.3, align=True)
                split.label(text=active)
                desc_row = split.row(align=True)
                desc_row.label(text=TOKEN_DESCRIPTIONS.get(token, ""))
                sub = desc_row.row()
                sub.alignment = "RIGHT"
                sub.scale_x = 1.8
                op = sub.operator("render_tokens.copy_token", text="", icon="COPYDOWN")
                op.token = active   # copy the current (possibly renamed) name
            layout.separator(factor=0.3)


class TOKENS_PT_presets(bpy.types.Panel):
    bl_label = "Token Preset"
    bl_idname = "TOKENS_PT_presets"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Render Tokens"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        node = context.active_node
        has_node = node is not None and node.type == "OUTPUT_FILE"
        presets = scene.render_tokens_presets
        idx = scene.render_tokens_preset_index

        # List + buttons
        row = layout.row()
        row.template_list("TOKENS_UL_presets", "", scene, "render_tokens_presets",
                          scene, "render_tokens_preset_index", rows=3)
        col = row.column(align=True)
        col.operator("render_tokens.add_preset", icon="ADD", text="")
        col.operator("render_tokens.remove_preset", icon="REMOVE", text="")
        col.separator()
        col.operator("render_tokens.move_preset", icon="TRIA_UP",   text="").direction = "UP"
        col.operator("render_tokens.move_preset", icon="TRIA_DOWN", text="").direction = "DOWN"

        # Preset from Node
        if has_node:
            layout.operator("render_tokens.preset_from_node", icon="IMPORT")

        # Selected preset details
        if 0 <= idx < len(presets):
            preset = presets[idx]
            layout.separator(factor=0.3)
            layout.prop(preset, "name", text="Name")
            layout.prop(preset, "directory", text="Dir")
            layout.prop(preset, "file_name", text="File")
            if has_node:
                layout.operator("render_tokens.apply_preset", icon="CHECKMARK")
            else:
                layout.label(text="Select a File Output node to apply", icon="INFO")





# ─────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────

class TOKENS_PT_output_tokens(bpy.types.Panel):
    bl_label       = "Tokens"
    bl_idname      = "TOKENS_PT_output_tokens"
    bl_space_type  = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context     = "output"
    bl_parent_id   = "TOKENS_PT_output_props"
    bl_options     = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        for group_name, tokens in TOKEN_GROUPS:
            layout.label(text=group_name)
            box = layout.box()
            col = box.column(align=True)
            for token in tokens:
                active = _active_token_name(token)
                row    = col.row(align=True)
                row.scale_y = 0.85
                split  = row.split(factor=0.3, align=True)
                split.label(text=active)
                desc_row = split.row(align=True)
                desc_row.label(text=TOKEN_DESCRIPTIONS.get(token, ""))
                sub = desc_row.row()
                sub.alignment = "RIGHT"
                sub.scale_x = 1.8
                op = sub.operator("render_tokens.copy_token", text="", icon="COPYDOWN")
                op.token = active
            layout.separator(factor=0.3)


class TOKENS_PT_output_props(bpy.types.Panel):
    bl_label       = "Render Tokens"
    bl_idname      = "TOKENS_PT_output_props"
    bl_space_type  = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context     = "output"

    def draw(self, context):
        layout = self.layout
        scene  = context.scene
        resolved = resolve_tokens(scene.render.filepath, scene)

        # ── Path Preview ──────────────────────────────────────────────────────
        layout.label(text="Path Preview")
        box = layout.box()
        box.scale_y = 0.7
        row = box.row(align=True)
        row.label(text=resolved if resolved else "—")
        row.operator("render_tokens.open_folder",
                     text="", icon="FILE_FOLDER", emboss=False).path = resolved

        # ── Version control ───────────────────────────────────────────────────
        vbox = layout.box()
        row  = vbox.row(align=True)
        split = row.split(factor=0.65, align=True)
        split.label(text=f"Version: {str(scene.render_tokens_version).zfill(3)}")
        sub = split.row(align=True)
        sub.operator("render_tokens.version_dec", text="-")
        sub.operator("render_tokens.version_inc", text="+")



CLASSES = [
    TokenAlias,
    TokenPreset,
    TOKENS_OT_update,
    TOKENS_OT_reset_aliases,
    TOKENS_OT_reset_single_alias,
    TOKENS_Preferences,
    TOKENS_UL_presets,
    TOKENS_OT_open_folder,
    TOKENS_OT_pick_dir,
    TOKENS_OT_copy_token,
    TOKENS_OT_quick_apply,
    TOKENS_OT_select_preset,
    TOKENS_OT_add_preset,
    TOKENS_OT_preset_from_node,
    TOKENS_OT_version_inc,
    TOKENS_OT_version_dec,
    TOKENS_OT_remove_preset,
    TOKENS_OT_apply_preset,
    TOKENS_OT_move_preset,
    TOKENS_PT_panel,
    TOKENS_PT_reference,
    TOKENS_PT_presets,
    TOKENS_PT_output_props,
    TOKENS_PT_output_tokens,
]

def _tag_redraw_all(self, context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


_SCENE_PROPS = [
    ("render_tokens_presets",      CollectionProperty(type=TokenPreset)),
    ("render_tokens_preset_index", IntProperty(default=0)),
    ("render_tokens_version",      IntProperty(name="$version", default=1, min=1, soft_max=999, update=_tag_redraw_all)),
    ("render_tokens_dir_template",        bpy.props.StringProperty(name="Directory", default="//Export/$prj/$version/$camera/")),
    ("render_tokens_file_template",       bpy.props.StringProperty(name="File Name", default="$camera_$pass_####")),
]


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for name, prop in _SCENE_PROPS:
        setattr(bpy.types.Scene, name, prop)
    if hasattr(bpy.app.handlers, "render_init"):
        bpy.app.handlers.render_init.append(_on_render_init)
    bpy.app.handlers.render_pre.append(_on_render_pre)
    bpy.app.handlers.render_write.append(_on_render_write)
    bpy.app.handlers.render_post.append(_on_render_post)
    bpy.app.handlers.render_cancel.append(_on_render_cancel)
    if hasattr(bpy.app.handlers, "render_complete"):
        bpy.app.handlers.render_complete.append(_on_render_complete)
    bpy.app.handlers.load_post.append(_on_load_post)
    # Initialize aliases immediately (preferences are always available)
    try:
        prefs = bpy.context.preferences.addons[__name__].preferences
        _ensure_aliases_initialized(prefs)
    except Exception:
        pass
    # Initialize presets after a short delay so bpy.context.scene is ready
    def _delayed_preset_init():
        try:
            scene = bpy.context.scene if bpy.context else None
            _ensure_presets_initialized(scene)
            if scene and scene.render.filepath in _BLENDER_DEFAULT_OUTPUTS:
                scene.render.filepath = _DEFAULT_RENDER_OUTPUT
        except Exception:
            pass
        return None  # do not repeat
    bpy.app.timers.register(_delayed_preset_init, first_interval=0.1)
    _log("v1.0.0 registered")


def unregister():
    handlers = [
        (_on_render_pre,    bpy.app.handlers.render_pre),
        (_on_render_write,  bpy.app.handlers.render_write),
        (_on_render_post,   bpy.app.handlers.render_post),
        (_on_render_cancel, bpy.app.handlers.render_cancel),
        (_on_load_post,     bpy.app.handlers.load_post),
    ]
    if hasattr(bpy.app.handlers, "render_init"):
        handlers.append((_on_render_init,     bpy.app.handlers.render_init))
    if hasattr(bpy.app.handlers, "render_complete"):
        handlers.append((_on_render_complete, bpy.app.handlers.render_complete))
    for h, lst in handlers:
        if h in lst:
            lst.remove(h)
    for name, _ in reversed(_SCENE_PROPS):
        if hasattr(bpy.types.Scene, name):
            delattr(bpy.types.Scene, name)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    _log("v1.0.0 unregistered")


if __name__ == "__main__":
    register()
