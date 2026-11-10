"""生成自包含的 HTML 体素查看器（含 Y 层滑杆）。

不依赖后端：模型数据直接内联进 HTML，双击即可打开。
3D 部分用 three.js（CDN），按方块类型建 InstancedMesh，
拖动层滑杆时只重写落在范围内的实例，因此大模型也不卡。
"""

from __future__ import annotations

import json
import re
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


# 模板版本：改过查看器就 +1。服务端会在取 _view.html 时比对，
# 版本旧的就地用页面里已内联的体素数据重套一次模板（模型一格不动）。
TEMPLATE_VERSION = "3"

_TMPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="viewer-template" content="__TEMPLATE__">
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
  #info .qbtn { margin-top:10px; width:100%; display:flex; align-items:center;
    justify-content:center; gap:6px; }
  #info .qbtn.on { background:#2C2C2A; color:#fff; border-color:#2C2C2A; }
  #info .qbtn.on:hover { background:#111; }
  #info .qbtn .dot { width:7px; height:7px; border-radius:50%; background:#B9B7B0; }
  #info .qbtn.on .dot { background:#7CE07C; box-shadow:0 0 0 3px rgba(124,224,124,.25); }
  .li.hl { background:#FFF3C4; border-radius:6px; box-shadow:inset 0 0 0 1px rgba(0,0,0,.06); }
  .li.hl .nm { font-weight:600; }
  #legend { top:16px; right:16px; width:246px; max-height:calc(100% - 150px); overflow:auto; }
  #legend h2 { margin:0 0 8px; font-size:12px; font-weight:500; color:var(--fg3); letter-spacing:.02em; }
  .li { display:flex; align-items:center; gap:8px; padding:3px 0; font-size:12px; }
  .sw { width:14px; height:14px; border-radius:3px; border:1px solid rgba(0,0,0,.18); flex:none; }
  .nm { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ct { color:var(--fg3); font-variant-numeric:tabular-nums; }
  #bar { left:50%; transform:translateX(-50%); bottom:16px; width:min(620px,calc(100% - 32px)); }
  #bar .head { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:10px; }
  #bar .head span { font-size:12px; color:var(--fg2); }
  #bar .head b { font-size:13px; font-weight:500; font-variant-numeric:tabular-nums; }
  .axis { display:grid; grid-template-columns:34px 1fr 62px 54px; gap:8px; align-items:center;
    margin-bottom:5px; }
  .axis > label { font-size:12px; color:var(--fg2); }
  .axis .val { font-size:11.5px; color:var(--fg); text-align:right;
    font-variant-numeric:tabular-nums; }
  .axis .val.off { color:var(--fg3); }
  .axis .inv { display:flex; align-items:center; gap:4px; font-size:11px; color:var(--fg2);
    cursor:pointer; user-select:none; }
  .axis .inv input { margin:0; accent-color:#378ADD; cursor:pointer; }
  .axis.ax-x > label { color:#B24A3C; }
  .axis.ax-y > label { color:#3E7A3E; }
  .axis.ax-z > label { color:#3A5E9E; }
  input[type=range] { width:100%; accent-color:#378ADD; }
  input[type=range]:disabled { opacity:.35; }
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
  <div class="row"><span>原点</span><b>西南下角 (0,0,0)</b></div>
  <button class="qbtn" id="btnPick" title="开启后把鼠标移到方块上，右边清单会高亮到对应材料">
    <span class="dot"></span><span id="pickTxt">方块查询</span>
  </button>
</div>

<div class="card" id="legend">
  <h2 id="lgtitle">方块清单</h2>
  <div id="lgbody"></div>
</div>

<div class="card" id="bar">
  <div class="head">
    <span>显示范围（拖到哪就显示到哪，“倒置”看另一半）</span>
    <b><span id="shown">0</span> / <span id="all">0</span> 块</b>
  </div>
  <div class="axis ax-x">
    <label for="rx">X 东</label>
    <input type="range" id="rx" min="0" max="1" value="1" step="1">
    <span class="val" id="vx">–</span>
    <label class="inv" title="显示滑块以外的另一半"><input type="checkbox" id="ix">倒置</label>
  </div>
  <div class="axis ax-y">
    <label for="ry">Y 上</label>
    <input type="range" id="ry" min="0" max="1" value="1" step="1">
    <span class="val" id="vy">–</span>
    <label class="inv" title="显示滑块以外的另一半"><input type="checkbox" id="iy">倒置</label>
  </div>
  <div class="axis ax-z">
    <label for="rz">Z 南</label>
    <input type="range" id="rz" min="0" max="1" value="1" step="1">
    <span class="val" id="vz">–</span>
    <label class="inv" title="显示滑块以外的另一半"><input type="checkbox" id="iz">倒置</label>
  </div>
  <div class="acts">
    <button id="btnAll">全部显示</button>
    <button id="btnReset">复位视角</button>
    <button id="btnAxes">显示/隐藏坐标轴</button>
  </div>
</div>

<div class="hint">拖拽旋转 · 滚轮缩放 · 右键平移 · 左侧「方块查询」可点方块看材料</div>
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
  d.dataset.v = String(p.i);
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

// ---------------- 固定坐标系 ----------------
// 原点固定在模型的最小角（西南下角）。裁剪时模型不动、坐标系也不动，
// 这样"我截的是哪一段"始终有参照。
const axisCaps = [];
const origin = new THREE.Vector3(-SX / 2, -SY / 2, -SZ / 2);
const axesLen = Math.max(8, Math.max(SX, SY, SZ) * 0.62);

// 轴从原点上穿过建筑，默认会被挡住看不见 —— 关掉深度测试让它始终浮在最前面。
// 作为参照轴这样最实用：无论怎么转视角、怎么裁剪，三个方向都在。
const axesHelper = new THREE.AxesHelper(axesLen);
axesHelper.position.copy(origin);
axesHelper.renderOrder = 999;
axesHelper.material.depthTest = false;
axesHelper.material.transparent = true;
axesHelper.material.opacity = 0.95;
scene.add(axesHelper);

// 轴端加三个小球，方向更好认（红 X 东 / 绿 Y 上 / 蓝 Z 南）
for (const [hex, dx, dy, dz] of [[0xFF3B30, axesLen, 0, 0], [0x2FBF3F, 0, axesLen, 0], [0x2F6BFF, 0, 0, axesLen]]) {
  const cap = new THREE.Mesh(
    new THREE.SphereGeometry(Math.max(0.45, axesLen * 0.045), 14, 14),
    new THREE.MeshBasicMaterial({ color: hex, depthTest: false, transparent: true, opacity: 0.95 }),
  );
  cap.position.set(origin.x + dx, origin.y + dy, origin.z + dz);
  cap.renderOrder = 1000;
  scene.add(cap);
  axisCaps.push(cap);
}

const gridSize = Math.max(SX, SZ) * 1.35;
const grid = new THREE.GridHelper(
  gridSize,
  Math.min(32, Math.max(4, Math.round(gridSize))),
  0xC8C5BD,
  0xDFDCD4,
);
grid.position.set(0, origin.y, 0);
const gridMats = Array.isArray(grid.material) ? grid.material : [grid.material];
for (const m of gridMats) {
  m.transparent = true;
  m.opacity = 0.42;
}
scene.add(grid);

const originDot = new THREE.Mesh(
  new THREE.SphereGeometry(Math.max(0.35, axesLen * 0.035), 14, 14),
  new THREE.MeshBasicMaterial({ color: 0x2C2C2A }),
);
originDot.position.copy(origin);
scene.add(originDot);

let axesVisible = true;
document.getElementById('btnAxes').onclick = () => {
  axesVisible = !axesVisible;
  axesHelper.visible = axesVisible;
  grid.visible = axesVisible;
  originDot.visible = axesVisible;
  for (const c of axisCaps) c.visible = axesVisible;
};

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
  meshes.push({ mesh, g, n: g.x.length, key: v });
}

function lowerBound(arr, val) {
  let lo = 0, hi = arr.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < val) lo = mid + 1; else hi = mid; }
  return lo;
}

// ---------------- 三轴裁剪 ----------------
// 原点固定在模型的西南下角（世界坐标 -SX/2, -SY/2, -SZ/2），不随裁剪移动。
// 每个轴一个滑条：value = n 表示"显示到第 n 格"。
//   正常 → [0, n-1]；倒置 → [n, size-1]。两者正好互补，合起来是全部。
const AX = [
  { key: 'x', size: SX, el: rxEl('rx'), inv: invEl('ix'), val: valEl('vx') },
  { key: 'y', size: SY, el: rxEl('ry'), inv: invEl('iy'), val: valEl('vy') },
  { key: 'z', size: SZ, el: rxEl('rz'), inv: invEl('iz'), val: valEl('vz') },
];
function rxEl(id) { return document.getElementById(id); }
function invEl(id) { return document.getElementById(id); }
function valEl(id) { return document.getElementById(id); }

for (const a of AX) {
  a.el.min = 0;
  a.el.max = a.size;
  a.el.step = 1;
  a.el.value = a.size;   // 输出时默认拉满
  a.inv.checked = false; // 默认关闭倒置
}

function spanOf(a) {
  const n = Math.max(0, Math.min(a.size, Number(a.el.value) || 0));
  return a.inv.checked ? [n, a.size - 1] : [0, n - 1];
}

let shownTotal = 0;

function apply() {
  const [x0, x1] = spanOf(AX[0]);
  const [y0, y1] = spanOf(AX[1]);
  const [z0, z1] = spanOf(AX[2]);
  shownTotal = 0;
  for (const it of meshes) {
    const gx = it.g.x, gy = it.g.y, gz = it.g.z;
    let n = 0;
    for (let i = 0; i < it.n; i++) {
      const x = gx[i], y = gy[i], z = gz[i];
      if (x < x0 || x > x1 || y < y0 || y > y1 || z < z0 || z > z1) continue;
      dummy.position.set(x - SX / 2 + 0.5, y - SY / 2 + 0.5, z - SZ / 2 + 0.5);
      dummy.updateMatrix();
      it.mesh.setMatrixAt(n++, dummy.matrix);
    }
    it.mesh.count = n;
    it.mesh.instanceMatrix.needsUpdate = true;
    shownTotal += n;
  }
  document.getElementById('shown').textContent = shownTotal.toLocaleString();

  for (const a of AX) {
    const n = Math.max(0, Math.min(a.size, Number(a.el.value) || 0));
    const empty = a.inv.checked ? n >= a.size : n <= 0;
    a.val.textContent = empty
      ? '—'
      : a.inv.checked
        ? (n + 1) + '–' + a.size
        : '1–' + n;
    a.val.classList.toggle('off', empty);
  }
}

// 拖动时合并到一帧，避免 1 万多个方块每像素重排
let pending = false;
function schedule() {
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => {
    pending = false;
    apply();
    // 裁剪会重排实例，旧的描边位置不再对应那一格
    outline.visible = false;
    markRow(-1);
  });
}

for (const a of AX) {
  a.el.addEventListener('input', schedule);
  a.inv.addEventListener('change', schedule);
}

document.getElementById('btnAll').onclick = () => {
  for (const a of AX) {
    a.el.value = a.size;
    a.inv.checked = false;
  }
  apply();
};
document.getElementById('btnReset').onclick = () => { resetCam(); controls.update(); };

// ---------------- 方块查询 ----------------
// 按一下按钮进入拾取模式：鼠标指着哪个方块，右边清单里对应的材料就高亮并滚到可见，
// 同时在方块外画一圈橙框。再按一下退出。
// 视角操作保持可用（拾取只读 pointermove，不吃事件），拖动时高亮会跟着最新的指针位置。
const raycaster = new THREE.Raycaster();
const ndc = new THREE.Vector2();
let picking = false;
let hoverV = -1;
let pickPending = false;

const outline = new THREE.LineSegments(
  new THREE.EdgesGeometry(new THREE.BoxGeometry(1.02, 1.02, 1.02)),
  new THREE.LineBasicMaterial({ color: 0xFF7A00, depthTest: false, transparent: true, opacity: 0.95 }),
);
outline.visible = false;
outline.renderOrder = 1002;
scene.add(outline);

const rowOf = new Map();
for (const el of lg.children) rowOf.set(Number(el.dataset.v), el);

function markRow(v) {
  if (v === hoverV) return;
  hoverV = v;
  for (const el of lg.children) el.classList.remove('hl');
  const el = rowOf.get(v);
  if (el) {
    el.classList.add('hl');
    el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  }
}

function pickAt() {
  pickPending = false;
  if (!picking) return;
  raycaster.setFromCamera(ndc, cam);
  const hits = raycaster.intersectObjects(meshes.map((m) => m.mesh), false);
  if (!hits.length) {
    outline.visible = false;
    markRow(-1);
    return;
  }
  const hit = hits[0];
  const it = meshes.find((m) => m.mesh === hit.object);
  if (!it) return;
  const mtx = new THREE.Matrix4();
  hit.object.getMatrixAt(hit.instanceId, mtx);
  outline.position.setFromMatrixPosition(mtx);
  outline.visible = true;
  markRow(it.key);
}

renderer.domElement.addEventListener('pointermove', (e) => {
  if (!picking) return;
  const r = renderer.domElement.getBoundingClientRect();
  ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
  ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
  if (pickPending) return;
  pickPending = true;
  requestAnimationFrame(pickAt);
});

renderer.domElement.addEventListener('pointerleave', () => {
  if (!picking) return;
  outline.visible = false;
  markRow(-1);
});

const btnPick = document.getElementById('btnPick');
const pickTxt = document.getElementById('pickTxt');
function setPicking(on) {
  picking = on;
  btnPick.classList.toggle('on', on);
  pickTxt.textContent = on ? '方块查询中（再按退出）' : '方块查询';
  if (!on) {
    outline.visible = false;
    markRow(-1);
  } else {
    renderer.domElement.style.cursor = 'crosshair';
  }
  if (!on) renderer.domElement.style.cursor = '';
}
btnPick.onclick = () => setPicking(!picking);

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


_MODEL_RE_HTML = re.compile(
    r'<script id="model" type="application/json">(.*?)</script>', re.S
)


def upgrade_view_file(path) -> str:
    """把已有的 _view.html 就地换成最新模板，模型数据一个字节不动。

    返回 ok（已最新）/ updated（换过）/ skip:…（没有内联数据或解析失败）。
    查看器加了新功能时，老模型的页面不用重建模型就能拿到 —— 这是
    "方块查询按钮在旧模型里找不到"那次的修法。
    """
    path = Path(path)
    try:
        html = path.read_text(encoding="utf-8")
    except OSError:
        return "skip:读不到文件"
    if f'name="viewer-template" content="{TEMPLATE_VERSION}"' in html:
        return "ok"
    m = _MODEL_RE_HTML.search(html)
    if not m:
        return "skip:没有内联模型数据"
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return "skip:模型数据解析失败"
    # 文件名才是这个模型现在的名字（导入/改名后 payload 里可能还是旧的）
    payload["name"] = path.name[: -len("_view.html")] if path.name.endswith("_view.html") else payload.get("name")
    path.write_text(render_html(payload), encoding="utf-8")
    return "updated"


def render_html(payload: dict) -> str:
    """把 payload 套进模板。和 write_viewer 分开，是为了"只换壳不换模型"——
    老模型的 _view.html 里已经内联了体素数据，升级查看器时要原样复用。"""
    name = str(payload.get("name") or "model")
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        _TMPL.replace("__TITLE__", f"Minecraft 体素模型 · {name}")
        .replace("__TEMPLATE__", TEMPLATE_VERSION)
        .replace("__MODEL_JSON__", blob)
    )


def write_viewer(path: str | Path, vox: np.ndarray, blocks: list[Block], name: str) -> dict:
    payload = build_payload(vox, blocks, name)
    html = render_html(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size, "blocks": payload["total"]}
