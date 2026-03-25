bl_info = {
    "name": "File Output Render Tokens",
    "author": "Custom",
    "version": (3, 0, 0),
    "blender": (3, 0, 0),
    "location": "Compositor > Sidebar > Render Tokens",
    "description": "Cinema 4D-style render tokens for File Output nodes (Blender 5.0 compatible)",
    "category": "Compositing",
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
    camera = scene.camera.name if scene.camera else "no_camera"

    rx = int(render.resolution_x * render.resolution_percentage / 100)
    ry = int(render.resolution_y * render.resolution_percentage / 100)
    fps_val = render.fps / render.fps_base
    fps_str = f"{fps_val:.3f}".rstrip("0").rstrip(".")

    username = os.environ.get("USERNAME") or os.environ.get("USER") or "user"
    computer = socket.gethostname()
    author = render.stamp_note_text if render.use_stamp_note else username

    TOKEN_MAP = {
        "$cvAuthor":   author,
        "$cvUsername": username,
        "$cvComputer": computer,
        "$cvRenderer": render.engine,
        "$cvHeight":   f"{ry}p",
        "$userpass":   pass_name,
        "$camera":     camera,
        "$range":      f"{scene.frame_start}-{scene.frame_end}",
        "$frame":      str(frame).zfill(4),
        "$pass":       pass_name,
        "$prj":        prj,
        "$take":       scene.name,
        "$res":        f"{rx}x{ry}",
        "$fps":        fps_str,
        "$rs":         scene.name,
        "$YYYY":       now.strftime("%Y"),
        "$YY":         now.strftime("%y"),
        "$MM":         now.strftime("%m"),
        "$DD":         now.strftime("%d"),
        "$hh":         now.strftime("%H"),
        "$mm":         now.strftime("%M"),
        "$ss":         now.strftime("%S"),
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
    val = getattr(node, "directory", None) or getattr(node, "base_path", "") or ""
    val = val.strip()
    if val.startswith("\\\\"):
        val = "//" + val[2:].replace("\\", "/")
    return val


def _set_directory(node, value):
    if hasattr(node, "directory"):
        node.directory = value
    elif hasattr(node, "base_path"):
        node.base_path = value


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
    nodes = list(_output_file_nodes(scene))
    _log(f"render_pre — {len(nodes)} File Output node(s), frame {frame}")

    for node in nodes:
        nid = node.as_pointer()
        orig_dir = _get_directory(node)
        orig_fn = _get_file_name(node)
        _originals[nid] = {"directory": orig_dir, "file_name": orig_fn}

        pass_name = _get_pass_name(node)
        new_dir = _to_absolute(resolve_tokens(orig_dir, scene, pass_name, frame))
        new_fn = resolve_tokens(orig_fn, scene, pass_name, frame)

        _set_directory(node, new_dir)
        _set_file_name(node, new_fn)
        try:
            os.makedirs(new_dir, exist_ok=True)
        except OSError as e:
            _log(f"  WARNING: could not create dir '{new_dir}': {e}")
        _log(f"  '{node.name}': directory='{new_dir}'  file_name='{new_fn}'")


def _restore(scene):
    global _originals
    for node in _output_file_nodes(scene):
        nid = node.as_pointer()
        if nid in _originals:
            _set_directory(node, _originals[nid]["directory"])
            _set_file_name(node, _originals[nid]["file_name"])
    _originals.clear()
    _log("Paths restored.")


def _resolve_for_frame(scene, frame):
    for node in _output_file_nodes(scene):
        nid = node.as_pointer()
        if nid not in _originals:
            continue
        pass_name = _get_pass_name(node)
        new_dir = _to_absolute(resolve_tokens(_originals[nid]["directory"], scene, pass_name, frame))
        new_fn = resolve_tokens(_originals[nid]["file_name"], scene, pass_name, frame)
        _set_directory(node, new_dir)
        _set_file_name(node, new_fn)
        try:
            os.makedirs(new_dir, exist_ok=True)
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
def _on_render_pre(*args):
    s = _scene(args)
    if s:
        _backup_and_resolve(s)


@persistent
def _on_render_write(*args):
    s = _scene(args)
    if not s:
        return
    frame = s.frame_current
    _log(f"render_write — frame {frame}")
    _rename_frame(s, frame)
    next_f = frame + s.frame_step
    if next_f <= s.frame_end:
        _resolve_for_frame(s, next_f)


@persistent
def _on_render_post(*args):
    s = _scene(args)
    if s:
        _restore(s)


@persistent
def _on_render_cancel(*args):
    s = _scene(args)
    if s:
        _restore(s)


# ─────────────────────────────────────────────────────────────────
# Token data
# ─────────────────────────────────────────────────────────────────

TOKEN_GROUPS = [
    ("Project", [
        "$prj", "$camera", "$take", "$pass", "$userpass",
        "$frame", "$rs", "$res", "$range", "$fps",
    ]),
    ("Date / Time", [
        "$YYYY", "$YY", "$MM", "$DD", "$hh", "$mm", "$ss",
    ]),
    ("CV", [
        "$cvAuthor", "$cvUsername", "$cvComputer", "$cvRenderer", "$cvHeight",
    ]),
]

TOKEN_DESCRIPTIONS = {
    "$prj":        "Project filename (no extension)",
    "$camera":     "Active camera name",
    "$take":       "Scene name",
    "$pass":       "First render pass input name",
    "$userpass":   "Same as $pass",
    "$frame":      "Current frame, zero-padded (0001)",
    "$rs":         "Scene name",
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
    "$cvAuthor":   "Author (stamp note or OS user)",
    "$cvUsername": "OS username",
    "$cvComputer": "Computer hostname",
    "$cvRenderer": "Render engine",
    "$cvHeight":   "Render height  e.g. 1080p",
}


# ─────────────────────────────────────────────────────────────────
# Property Group
# ─────────────────────────────────────────────────────────────────

class TokenPreset(bpy.types.PropertyGroup):
    name:      StringProperty(name="Name",      default="New Preset")
    directory: StringProperty(name="Directory", default="//render/$prj/$camera/")
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

        # Full reload: disable → flush module cache → re-enable
        import sys
        bpy.ops.preferences.addon_disable(module=__name__)
        for key in [k for k in sys.modules if k == __name__ or k.startswith(__name__ + ".")]:
            del sys.modules[key]
        bpy.ops.preferences.addon_enable(module=__name__)
        self.report({"INFO"}, f"Updated to v{'.'.join(map(str, remote_ver))}")
        return {"FINISHED"}


class TOKENS_Preferences(bpy.types.AddonPreferences):
    bl_idname = __name__

    show_reference: BoolProperty(
        name="Show Token Reference",
        default=True,
    )

    def draw(self, context):
        layout = self.layout

        # Update section
        box = layout.box()
        row = box.row(align=True)
        row.label(text=f"Version: {'.'.join(map(str, bl_info['version']))}", icon="INFO")
        row.operator("render_tokens.update", icon="FILE_REFRESH")

        layout.separator(factor=0.5)

        # Token reference
        layout.prop(self, "show_reference", toggle=True, icon="QUESTION")
        if not self.show_reference:
            return

        for group_name, tokens in TOKEN_GROUPS:
            col = layout.column(align=True)
            col.label(text=group_name)
            for token in tokens:
                row = col.row(align=True)
                row.scale_y = 0.8
                split = row.split(factor=0.28)
                split.label(text=token)
                split.label(text=TOKEN_DESCRIPTIONS.get(token, ""))
            layout.separator(factor=0.3)


# ─────────────────────────────────────────────────────────────────
# UIList
# ─────────────────────────────────────────────────────────────────

class TOKENS_UL_presets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            layout.prop(item, "name", text="", emboss=False, icon="DOT")
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text="", icon="DOT")


# ─────────────────────────────────────────────────────────────────
# Operators
# ─────────────────────────────────────────────────────────────────

class TOKENS_OT_add_preset(bpy.types.Operator):
    bl_idname = "render_tokens.add_preset"
    bl_label = "Add Empty Preset"
    bl_description = "Add a blank preset"

    def execute(self, context):
        presets = context.scene.render_tokens_presets
        p = presets.add()
        p.name = "New Preset"
        p.directory = "//render/$prj/$camera/"
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
        return {"FINISHED"}


# ─────────────────────────────────────────────────────────────────
# Panels (sub-panels = native Blender collapse, no custom arrows)
# ─────────────────────────────────────────────────────────────────

class TOKENS_PT_panel(bpy.types.Panel):
    bl_label = "Render Tokens"
    bl_idname = "TOKENS_PT_panel"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Render Tokens"

    @classmethod
    def poll(cls, context):
        sdata = context.space_data
        return sdata is not None and sdata.tree_type == "CompositorNodeTree"

    def draw(self, context):
        self.layout.label(text="$tokens resolve automatically on render.", icon="INFO")


class TOKENS_PT_reference(bpy.types.Panel):
    bl_label = "Token Reference"
    bl_idname = "TOKENS_PT_reference"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Render Tokens"
    bl_parent_id = "TOKENS_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        for group_name, tokens in TOKEN_GROUPS:
            layout.label(text=group_name)
            col = layout.column(align=True)
            for token in tokens:
                row = col.row(align=True)
                row.scale_y = 0.85
                split = row.split(factor=0.38)
                split.label(text=token)
                split.label(text=TOKEN_DESCRIPTIONS.get(token, ""))
            layout.separator(factor=0.3)


class TOKENS_PT_presets(bpy.types.Panel):
    bl_label = "Presets"
    bl_idname = "TOKENS_PT_presets"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Render Tokens"
    bl_parent_id = "TOKENS_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

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

        # "From Node" button
        if has_node:
            layout.operator("render_tokens.preset_from_node", icon="IMPORT")

        # Selected preset details
        if 0 <= idx < len(presets):
            preset = presets[idx]
            layout.separator(factor=0.5)
            layout.prop(preset, "name", text="Name")
            layout.prop(preset, "directory", text="Dir")
            layout.prop(preset, "file_name", text="File")
            if has_node:
                layout.operator("render_tokens.apply_preset", icon="CHECKMARK")
            else:
                layout.label(text="Select a File Output node to apply", icon="INFO")


class TOKENS_PT_preview(bpy.types.Panel):
    bl_label = "Live Preview"
    bl_idname = "TOKENS_PT_preview"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Render Tokens"
    bl_parent_id = "TOKENS_PT_panel"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        node = context.active_node
        return node is not None and node.type == "OUTPUT_FILE"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        node = context.active_node

        raw_dir = _get_directory(node)
        raw_fn = _get_file_name(node)
        pass_name = _get_pass_name(node)

        layout.label(text="Directory:")
        box = layout.box()
        box.scale_y = 0.75
        resolved_dir = resolve_tokens(raw_dir, scene, pass_name)
        for chunk in [resolved_dir[i:i+52] for i in range(0, max(len(resolved_dir), 1), 52)]:
            box.label(text=chunk)

        layout.label(text="File name:")
        layout.label(text=f"  {resolve_tokens(raw_fn, scene, pass_name)}", icon="DOT")


# ─────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────

CLASSES = [
    TokenPreset,
    TOKENS_OT_update,
    TOKENS_Preferences,
    TOKENS_UL_presets,
    TOKENS_OT_add_preset,
    TOKENS_OT_preset_from_node,
    TOKENS_OT_remove_preset,
    TOKENS_OT_apply_preset,
    TOKENS_PT_panel,
    TOKENS_PT_reference,
    TOKENS_PT_presets,
    TOKENS_PT_preview,
]

_SCENE_PROPS = [
    ("render_tokens_presets",      CollectionProperty(type=TokenPreset)),
    ("render_tokens_preset_index", IntProperty(default=0)),
]


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for name, prop in _SCENE_PROPS:
        setattr(bpy.types.Scene, name, prop)
    bpy.app.handlers.render_pre.append(_on_render_pre)
    bpy.app.handlers.render_write.append(_on_render_write)
    bpy.app.handlers.render_post.append(_on_render_post)
    bpy.app.handlers.render_cancel.append(_on_render_cancel)
    _log("v1.0.0 registered")


def unregister():
    for h, lst in [
        (_on_render_pre,    bpy.app.handlers.render_pre),
        (_on_render_write,  bpy.app.handlers.render_write),
        (_on_render_post,   bpy.app.handlers.render_post),
        (_on_render_cancel, bpy.app.handlers.render_cancel),
    ]:
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
