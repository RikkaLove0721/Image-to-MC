"""生成自包含的 HTML 体素查看器（含 Y 层滑杆）。

不依赖后端：模型数据直接内联进 HTML，双击即可打开。
3D 部分用 three.js（CDN），按方块类型建 InstancedMesh，
拖动层滑杆时只重写落在范围内的实例，因此大模型也不卡。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .palette import Block


def _rle(vox: np.ndarray) -> list[list[int]]:
    flat = vox.transpose(1, 2, 0).ravel().astype(np.int32)
    if flat.size == 0:
        return []
    change = np.nonzero(np.diff(flat))[0] + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [flat.size]))
    return [[int(flat[s]), int(e - s)] for s, e in zip(starts, ends)]


def build_payload(vox: np.ndarray, blocks: list[Block], name: str) -> dict:
    counts = np.bincount(vox[vox >= 0].ravel(), minlength=len(blocks)) if (vox >= 0).any() else np.zeros(len(blocks))
    return {
        "name": name,
        "size": [int(v) for v in vox.shape],
        "total": int((vox >= 0).sum()),
        "palette": [
            {
                "zh": b.name_zh,
                "en": b.name_en,
                "id": b.id,
                "meta": b.meta,
                "hex": b.hex_color,
                "count": int(counts[i]),
            }
            for i, b in enumerate(blocks)
        ],
        "rle": _rle(vox),
    }


_TMPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --fg:#2C2C2A; --fg2:#5F5E5A; --fg3:#8E8C86; --line:rgba(0,0,0,.10); --card:rgba(255,255,255,.88); }
  * { box-sizing:border-box; }
  html,body { margin:0; height:100%; overflow:hidden;
    font-family:system-ui,-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
    background:#F2F1ED; color:var(--fg); }
  #app { position:absolute; inset:0; }
  .card { position:absolute; background:var(--card); backdrop-filter:blur(8px);
    border:1px solid var(--line); border-radius:12px; padding:12px 14px; font-size:13px; line-height:1.6; }
  #info { top:16px; left:16px; min-width:190px; }
  #info h1 { margin:0 0 6px; font-size:14px; font-weight:500; }
  #info .row { display:flex; justify-content:space-between; gap:16px; color:var(--fg2); }
  #info .row b { font-weight:500; color:var(--fg); font-variant-numeric:tabular-nums; }
  #legend { top:16px; right:16px; width:246px; max-height:calc(100% - 150px); overflow:auto; }
  #legend h2 { margin:0 0 8px; font-size:12px; font-weight:500; color:var(--fg3); letter-spacing:.02em; }
  .li { display:flex; align-items:center; gap:8px; padding:3px 0; font-size:12px; }
  .sw { width:14px; height:14px; border-radius:3px; border:1px solid rgba(0,0,0,.18); flex:none; }
  .nm { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ct { color:var(--fg3); font-variant-numeric:tabular-nums; }
  #bar { left:50%; transform:translateX(-50%); bottom:16px; width:min(560px,calc(100% - 32px)); }
  #bar .head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
  #bar .head span { font-size:12px; color:var(--fg2); }
  #bar .head b { font-size:13px; font-weight:500; font-variant-numeric:tabular-nums; }
  .sliders { display:grid; grid-template-columns:40px 1fr; gap:6px 10px; align-items:center; }
  .sliders label { font-size:12px; color:var(--fg2); }
  input[type=range] { width:100%; accent-color:#378ADD; }
  .acts { display:flex; gap:8px; margin-top:10px; }
  button { font:inherit; font-size:12px; padding:5px 10px; border-radius:8px; cursor:pointer;
    border:1px solid var(--line); background:#fff; color:var(--fg); }
  button:hover { background:#F5F4F0; }
  #err { position:absolute; inset:0; display:none; place-items:center; text-align:center;
    font-size:13px; color:var(--fg2); padding:24px; }
  .hint { position:absolute; left:16px; bottom:16px; font-size:11px; color:var(--fg3); }
</style>
</head>
<body>
<div id="app"></div>

<div class="card" id="info">
  <h1 id="mname"></h1>
  <div class="row"><span>尺寸 X×Y×Z</span><b id="mdim">–</b></div>
  <div class="row"><span>方块总数</span><b id="mtotal">–</b></div>
  <div class="row"><span>方块种类</span><b id="mkind">–</b></div>
  <div class="row"><span>坐标轴</span><b>X东 Y上 Z南</b></div>
</div>

<div class="card" id="legend">
  <h2 id="lgtitle">方块清单</h2>
  <div id="lgbody"></div>
</div>

<div class="card" id="bar">
  <div class="head">
    <span id="rangeLabel">显示层范围</span>
    <b><span id="shown">0</span> / <span id="all">0</span> 块</b>
  </div>
  <div class="sliders">
    <label for="lo">下层</label><input type="range" id="lo" min="0" max="10" value="0">
    <label for="hi">上层</label><input type="range" id="hi" min="0" max="10" value="10">
  </div>
  <div class="acts">
    <button id="btnSingle">只看当前下层</button>
    <button id="btnAll">显示全部</button>
    <button id="btnReset">复位视角</button>
  </div>
</div>

<div class="hint">拖拽旋转 · 滚轮缩放 · 右键平移</div>
<div id="err">3D 引擎加载失败，请检查网络后刷新（模型文件本身不受影响）。</div>

<script id="model" type="application/json">__MODEL_JSON__</script>

<script type="importmap">
{ "imports": {
  "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
  "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
} }
</script>

<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const M = JSON.parse(document.getElementById('model').textContent);
const [SX, SY, SZ] = M.size;

document.getElementById('mname').textContent = M.name;
document.getElementById('mdim').textContent = SX + ' × ' + SY + ' × ' + SZ;
document.getElementById('mtotal').textContent = M.total.toLocaleString();
document.getElementById('mkind').textContent = M.palette.filter(p => p.count > 0).length;
document.getElementById('all').textContent = M.total.toLocaleString();

const lg = document.getElementById('lgbody');
const sorted = M.palette.map((p, i) => ({ ...p, i })).filter(p => p.count > 0)
  .sort((a, b) => b.count - a.count);
document.getElementById('lgtitle').textContent = '方块清单 · ' + sorted.length + ' 种';
for (const p of sorted) {
  const d = document.createElement('div');
  d.className = 'li';
  d.innerHTML = '<span class="sw" style="background:' + p.hex + '"></span>' +
    '<span class="nm" title="' + p.en + ' ' + p.id + ':' + p.meta + '">' + p.zh + '</span>' +
    '<span class="ct">' + p.count.toLocaleString() + '</span>';
  lg.appendChild(d);
}

const flat = new Int16Array(SX * SY * SZ).fill(-1);
let ptr = 0;
for (const [v, c] of M.rle) { flat.fill(v, ptr, ptr + c); ptr += c; }

const groups = new Map();
for (let y = 0; y < SY; y++) {
  for (let z = 0; z < SZ; z++) {
    const base = (y * SZ + z) * SX;
    for (let x = 0; x < SX; x++) {
      const v = flat[base + x];
      if (v < 0) continue;
      if (!groups.has(v)) groups.set(v, { x: [], y: [], z: [] });
      const g = groups.get(v);
      g.x.push(x); g.y.push(y); g.z.push(z);
    }
  }
}

const scene = new THREE.Scene();
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.domElement.style.display = 'block';
document.getElementById('app').appendChild(renderer.domElement);
scene.background = new THREE.Color(0xF2F1ED);

const maxDim = Math.max(SX, SY, SZ);
const cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, maxDim * 20);
const dir = new THREE.Vector3(0.85, 0.72, 1.05).normalize();
function resetCam() {
  cam.position.copy(dir.clone().multiplyScalar(maxDim * 2.6));
  cam.up.set(0, 1, 0);
  cam.lookAt(0, 0, 0);
}
resetCam();

const controls = new OrbitControls(cam, renderer.domElement);
controls.target.set(0, 0, 0);
controls.enableDamping = true;
controls.dampingFactor = 0.12;

scene.add(new THREE.HemisphereLight(0xffffff, 0x9a968c, 0.82));
const d1 = new THREE.DirectionalLight(0xffffff, 1.12); d1.position.set(1, 1.6, 1.2); scene.add(d1);
const d2 = new THREE.DirectionalLight(0xffffff, 0.38); d2.position.set(-1.2, 0.5, -1); scene.add(d2);

const geo = new THREE.BoxGeometry(1, 1, 1);
const meshes = [];
const dummy = new THREE.Object3D();

for (const [v, g] of groups) {
  const p = M.palette[v];
  // 玻璃（20 / 95）在游戏里是透明的，这里也做半透明，否则窗洞会糊成一块蓝板
  const isGlass = (p.id === 20 || p.id === 95);
  const mat = new THREE.MeshLambertMaterial({
    color: new THREE.Color(p.hex),
    transparent: isGlass,
    opacity: isGlass ? 0.42 : 1,
    depthWrite: !isGlass,
  });
  const mesh = new THREE.InstancedMesh(geo, mat, g.x.length);
  mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  mesh.frustumCulled = false;
  scene.add(mesh);
  meshes.push({ mesh, g, n: g.x.length });
}

function lowerBound(arr, val) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < val) lo = mid + 1; else hi = mid; }
  return lo;
}

const loEl = document.getElementById('lo');
const hiEl = document.getElementById('hi');
loEl.max = SY - 1; hiEl.max = SY - 1;
hiEl.value = SY - 1;

let shownTotal = 0;
function apply() {
  let a = +loEl.value, b = +hiEl.value;
  if (a > b) { const t = a; a = b; b = t; }
  shownTotal = 0;
  for (const it of meshes) {
    const s = lowerBound(it.g.y, a);
    const e = lowerBound(it.g.y, b + 1);
    let n = 0;
    for (let i = s; i < e; i++) {
      dummy.position.set(it.g.x[i] - SX / 2 + 0.5, it.g.y[i] - SY / 2 + 0.5, it.g.z[i] - SZ / 2 + 0.5);
      dummy.updateMatrix();
      it.mesh.setMatrixAt(n++, dummy.matrix);
    }
    it.mesh.count = n;
    it.mesh.instanceMatrix.needsUpdate = true;
    shownTotal += n;
  }
  document.getElementById('shown').textContent = shownTotal.toLocaleString();
  document.getElementById('rangeLabel').textContent =
    (a === b ? '只看第 ' + a + ' 层' : '第 ' + a + ' – ' + b + ' 层') + ' / 共 ' + SY + ' 层';
}

loEl.addEventListener('input', apply);
hiEl.addEventListener('input', apply);
document.getElementById('btnSingle').onclick = () => { hiEl.value = loEl.value; apply(); };
document.getElementById('btnAll').onclick = () => { loEl.value = 0; hiEl.value = SY - 1; apply(); };
document.getElementById('btnReset').onclick = () => { resetCam(); controls.update(); };

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h);
  const span = maxDim * 1.5;
  const asp = w / h;
  cam.left = -span * asp / 2; cam.right = span * asp / 2;
  cam.top = span / 2; cam.bottom = -span / 2;
  cam.updateProjectionMatrix();
}
addEventListener('resize', resize);

resize();
apply();
cam.position.copy(dir.clone().multiplyScalar(maxDim * 2.6));
controls.update();

(function loop() {
  requestAnimationFrame(loop);
  controls.update();
  renderer.render(scene, cam);
})();
</script>
<script>
  setTimeout(function () {
    var app = document.getElementById('app');
    if (app && app.children.length === 0) document.getElementById('err').style.display = 'grid';
  }, 4000);
</script>
</body>
</html>
"""


def write_viewer(path: str | Path, vox: np.ndarray, blocks: list[Block], name: str) -> dict:
    payload = build_payload(vox, blocks, name)
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = _TMPL.replace("__TITLE__", f"Minecraft 体素模型 · {name}").replace("__MODEL_JSON__", blob)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size, "blocks": payload["total"]}
