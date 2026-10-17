"""Copy 86Box.exe plus every MinGW DLL it needs (found via objdump) and the Qt plugins."""
import os
import shutil
import subprocess
import sys

exe, out = sys.argv[1], sys.argv[2]
sysroot = "/usr/x86_64-w64-mingw32/sys-root/mingw"
search = [f"{sysroot}/bin", f"{sysroot}/lib"]


def deps(path):
    text = subprocess.run(["x86_64-w64-mingw32-objdump", "-p", path], capture_output=True, text=True).stdout
    return [line.split()[-1] for line in text.splitlines() if "DLL Name:" in line]


def find(name):
    for d in search:
        for f in os.listdir(d):
            if f.lower() == name.lower():
                return os.path.join(d, f)
    return None  # a Windows system DLL


shutil.rmtree(out, ignore_errors=True)
os.makedirs(out)
shutil.copy(exe, out)
qt_plugins = f"{sysroot}/lib/qt6/plugins"
plugins = [("platforms", "qwindows.dll"), ("styles", "qmodernwindowsstyle.dll"), ("imageformats", "qico.dll")]
todo = [exe]
for sub, name in plugins:
    src = os.path.join(qt_plugins, sub, name)
    if os.path.exists(src):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
        shutil.copy(src, os.path.join(out, sub))
        todo.append(src)
seen = set()
while todo:
    for dll in deps(todo.pop()):
        if dll.lower() in seen:
            continue
        seen.add(dll.lower())
        path = find(dll)
        if path:
            shutil.copy(path, out)
            todo.append(path)
print(f"packaged {out}: {len(os.listdir(out))} entries")
