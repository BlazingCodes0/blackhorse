"""ImageMagick plugin — let Spice route images by copying/managing them with IM."""
META = {
  "name": "imagemagick",
  "description": "Run ImageMagick commands on files: list sizes, rotate copies, copy from src to dst",
  "trust": "trusted",
  "params": {
    "src": "source file or folder",
    "dst": "destination folder or file",
    "action": "'list' | 'copy-rotate'",
    "rotation": "0/90/180/270"
  }
}

def run(context):
    import os, shutil, subprocess, json, pathlib
    src = context.get('src')
    dst = context.get('dst')
    action = context.get('action', 'list')
    rotation = int(context.get('rotation', 0) or 0)
    if not src:
        return {"error": "missing src"}
    out = []
    if action == 'list':
        if os.path.isdir(src):
            for f in sorted(os.listdir(src)):
                if f.lower().endswith(('.jpg','.jpeg','.png','.gif','.webp')):
                    p = os.path.join(src, f)
                    try:
                        r = subprocess.run(['identify', '-format', '%f %wx%h', p], capture_output=True, text=True, timeout=10)
                        out.append({"file": f, "info": r.stdout.strip()})
                    except Exception as e:
                        out.append({"file": f, "error": str(e)})
            return {"count": len(out), "items": out}
        elif os.path.isfile(src):
            r = subprocess.run(['identify', '-format', '%f %wx%h', src], capture_output=True, text=True, timeout=10)
            return {"file": src, "info": r.stdout.strip()}
        else:
            return {"error": "src not found"}
    if action == 'copy-rotate':
        if not dst:
            return {"error": "missing dst"}
        if os.path.isdir(src):
            os.makedirs(dst, exist_ok=True)
            for f in sorted(os.listdir(src)):
                if not f.lower().endswith(('.jpg','.jpeg','.png','.gif','.webp')):
                    continue
                s = os.path.join(src, f); d = os.path.join(dst, f)
                rot = f"{rotation}" if rotation else "0"
                cmd = ['convert', s, '-rotate', rot, d]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                out.append({"file": f, "dst": d, "rc": r.returncode, "stderr": r.stderr.strip()[:120]})
            return {"copied": len(out), "items": out}
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
            rot = f"{rotation}" if rotation else "0"
            r = subprocess.run(['convert', src, '-rotate', rot, dst], capture_output=True, text=True, timeout=60)
            return {"file": src, "dst": dst, "rc": r.returncode, "stderr": r.stderr.strip()[:120]}
        else:
            return {"error": "src not found"}
    return {"error": f"unknown action {action}"}
