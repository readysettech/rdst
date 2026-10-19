"""The system tray as a desktop panel sees it, on the run's private D-Bus session.

`watch ITEMS` owns org.kde.StatusNotifierWatcher, so the application registers
its tray icon here, and records the registered icons in ITEMS. `menu ITEMS
[PATH]` reads the newest live icon's menu through com.canonical.dbusmenu and,
given a "Monitoring > Pause all" path, clicks that item. Output is JSON.
"""

import json
import sys

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

WATCHER_XML = """<node><interface name="org.kde.StatusNotifierWatcher">
<method name="RegisterStatusNotifierItem"><arg type="s" direction="in"/></method>
<method name="RegisterStatusNotifierHost"><arg type="s" direction="in"/></method>
<property name="RegisteredStatusNotifierItems" type="as" access="read"/>
<property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
<property name="ProtocolVersion" type="i" access="read"/>
<signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
<signal name="StatusNotifierHostRegistered"/>
</interface></node>"""
MENU = "com.canonical.dbusmenu"


def watch(items_file):
    items = []
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)

    def call(conn, sender, path, iface, method, params, invocation):
        if method == "RegisterStatusNotifierItem":
            service = params.unpack()[0]
            # An item names its bus and path, or only a path on the caller's bus.
            item = sender + service if service.startswith("/") else service
            if "/" not in item:
                item += "/StatusNotifierItem"
            items.append(item)
            with open(items_file, "w") as f:
                json.dump(items, f)
            conn.emit_signal(
                None, "/StatusNotifierWatcher", "org.kde.StatusNotifierWatcher",
                "StatusNotifierItemRegistered", GLib.Variant("(s)", (item,)),
            )
        invocation.return_value(None)

    def get(conn, sender, path, iface, name):
        return {
            "RegisteredStatusNotifierItems": GLib.Variant("as", items),
            "IsStatusNotifierHostRegistered": GLib.Variant("b", True),
            "ProtocolVersion": GLib.Variant("i", 0),
        }[name]

    interface = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML).interfaces[0]
    bus.register_object("/StatusNotifierWatcher", interface, call, get, None)
    loop = GLib.MainLoop()

    def owned(*_):
        print("ready", flush=True)

    def lost(*_):
        print("org.kde.StatusNotifierWatcher is already owned", file=sys.stderr)
        loop.quit()

    Gio.bus_own_name_on_connection(
        bus, "org.kde.StatusNotifierWatcher", 0, owned, lost)
    Gio.bus_own_name_on_connection(bus, "org.kde.StatusNotifierHost-relentless", 0, None, None)
    loop.run()


def proxy_call(bus, name, path, iface, method, args, reply):
    return bus.call_sync(name, path, iface, method, args, GLib.VariantType(reply),
                         Gio.DBusCallFlags.NONE, 5000, None).unpack()


def live_item(bus, items_file):
    try:
        with open(items_file) as f:
            items = json.load(f)
    except FileNotFoundError:
        items = []
    for item in reversed(items):
        name, _, path = item.partition("/")
        has_owner = proxy_call(bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "NameHasOwner",
                               GLib.Variant("(s)", (name,)), "(b)")[0]
        if has_owner:
            return name, "/" + path
    return None


def layout(bus, name, path, parent):
    proxy_call(bus, name, path, MENU, "AboutToShow", GLib.Variant("(i)", (parent,)), "(b)")
    _, root = proxy_call(bus, name, path, MENU, "GetLayout",
                         GLib.Variant("(iias)", (parent, -1, [])), "(u(ia{sv}av))")
    return root


def entries(node):
    out = []
    for child_id, props, children in node[2]:
        if props.get("type") == "separator" or props.get("visible") is False:
            continue
        out.append({
            "id": child_id,
            "label": props.get("label", "").replace("_", ""),
            "enabled": props.get("enabled", True),
            "toggle": props.get("toggle-state"),
            "children": (child_id, props, children),
            "submenu": props.get("children-display") == "submenu",
        })
    return out


def tree(node):
    items = []
    for entry in entries(node):
        label = entry["label"] + ("" if entry["enabled"] else " (disabled)")
        if entry["toggle"] in (0, 1):
            label += " [on]" if entry["toggle"] == 1 else " [off]"
        items.append({label: tree(entry["children"])} if entry["submenu"] else label)
    return items


def menu(items_file, target):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    item = live_item(bus, items_file)
    if not item:
        return {"error": "the application has not registered a tray icon"}
    name, item_path = item
    menu_path = proxy_call(bus, name, item_path, "org.freedesktop.DBus.Properties", "Get",
                           GLib.Variant("(ss)", ("org.kde.StatusNotifierItem", "Menu")),
                           "(v)")[0]
    root = layout(bus, name, menu_path, 0)
    if not target:
        return tree(root)
    node = root
    found = None
    for part in [p.strip().lower() for p in target.split(">")]:
        options = entries(node)
        found = next((e for e in options if e["label"].lower() == part), None)
        if not found:
            return {"error": f'no tray item "{part}"', "available": [e["label"] for e in options]}
        node = layout(bus, name, menu_path, found["id"]) if found["submenu"] else found["children"]
    if not found["enabled"]:
        return {"error": f'tray item "{target}" is disabled'}
    proxy_call(bus, name, menu_path, MENU, "Event",
               GLib.Variant("(isvu)", (found["id"], "clicked", GLib.Variant("i", 0), 0)), "()")
    return {"clicked": target}


if __name__ == "__main__":
    command, items_file, *rest = sys.argv[1:]
    if command == "watch":
        watch(items_file)
    else:
        try:
            print(json.dumps(menu(items_file, " ".join(rest).strip())))
        except GLib.Error as error:
            print(json.dumps({"error": error.message}))
