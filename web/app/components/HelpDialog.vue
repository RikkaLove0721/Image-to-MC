<script setup lang="ts">
/**
 * 使用说明弹窗。
 *
 * 放在首页而不是塞进 README：桌面版用户看不到仓库里的 markdown，
 * 而"怎么开始、图片怎么拍、那几个滑条是什么意思"是第一次打开就要回答的问题。
 * 内容保持短句 + 分节，一屏能扫完；细节原理留给 README。
 */
defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()

const version = useRuntimeConfig().public.version as string

const SECTIONS = [
  {
    id: 'start',
    title: '三分钟上手',
    items: [
      '首页「新建项目」→ 起个名字',
      '进入项目，在「输入工作图片」里传一张**正视图**（手机拍的正面照就行）',
      '选好尺寸比例与细节等级，点「生成 AI 方案」；看一眼蓝图，点「按方案生成」',
      '跑完自动跳到「查看结果」，可以看 3D、看方块清单、下载 .schematic',
    ],
  },
  {
    id: 'photo',
    title: '照片怎么拍',
    items: [
      '正面、平视、尽量正对建筑；斜拍会让比例失真（程序会尝试矫正，但不如重拍）',
      '整栋楼尽量在画面里；被裁掉的一侧程序会按对称补全，但补出来的部分只能是推测',
      '白天顺光最好；强光阴影会让墙面颜色偏黄、偏暗',
      '画面里少一点车、行人、树——它们会干扰立面判读',
    ],
  },
  {
    id: 'settings',
    title: '生成设置怎么选',
    items: [
      '**尺寸**：1 米 = 多少格。默认 2 格/米适合住宅；想把细节做足就调大，想做小摆件就调小',
      '**细节等级**：基础 / 标准 / 精细 / 观赏性。等级越高，勒脚、腰线、角柱、设备箱越多，方块数也越多',
      '**游戏建筑技巧性**：低=尽量照抄照片；中=还原为主、套用社区做法；高=优先社区美学，允许改配色和开间节奏',
      '层数不单独设置：需要强制层数就写在「补充要求」里，例如"三层带车库"',
    ],
  },
  {
    id: 'ai',
    title: 'AI 方案与「修改」',
    items: [
      'AI 只写蓝图（体块 / 窗阵 / 屋顶 / 细节），不读像素也不吐方块；模型是本地展开的',
      '生成后会本地体检一轮（比例、层高、开间、虚实），把偏差喂回去最多改 3 轮',
      '仓库页每个结果都能「修改」：带着原分析和蓝图改，比例、层数、种子不动，产出 m07_r2、m07_r3…',
      '仓库信息里能看到这一次花了多少 token',
    ],
  },
  {
    id: 'viewer',
    title: '3D 查看器',
    items: [
      '拖拽旋转、滚轮缩放、右键平移；底部三个滑条按 X/Y/Z 剖切，勾「倒置」看另一半',
      '**方块查询**：点信息卡下面的按钮，把鼠标移到方块上，右侧清单会高亮对应材料并自动滚动；再点一次退出',
      '坐标轴 X 东、Y 上、Z 南，原点在模型西南下角',
    ],
  },
  {
    id: 'export',
    title: '导进 Minecraft',
    items: [
      '下载 `.schematic`，用 MCEdit 或 WorldEdit 导入（`//schematic load` + `//paste`）',
      '只支持 **Java 1.12.2**：1.13+ 的方块体系变了，需要先用工具转换',
      '方块清单 `.txt` 可以照着准备材料；逐层平面图适合照着摆',
    ],
  },
  {
    id: 'blocks',
    title: '方块库范围',
    items: [
      '内置 177 种方块，是 1.12.2 的**常用子集**，不是全集',
      '1.13+ 的方块（深板岩、方解石、凝灰岩、铜块、樱花木等）没有，写了会兜底成石头',
      '要加方块或换版本：改 `mc12/palette.py` 里的 `_rows()`，再跑 `tools/export_library.py`，其余代码不用动',
    ],
  },
  {
    id: 'faq',
    title: '常见问题',
    items: [
      '**双击没反应**：可能被杀毒软件拦了，把整个文件夹加白名单',
      '**提示缺少 WebView2**：装一次微软的 WebView2 Runtime（Win11 自带）',
      '**想挪位置**：整个文件夹一起挪，别只拷 exe',
      '**模型太长/太扁**：多半是照片斜拍导致比例估错，改用正面照，或在补充要求里写明朝向与层数',
    ],
  },
]
</script>

<template>
  <AppModal :open="open" title="使用说明" :desc="`MC 工程图 v${version} · 照片转 Minecraft 建筑`" width="640px" @close="emit('close')">
    <div class="help-body">
      <section v-for="s in SECTIONS" :key="s.id" class="help-sec">
        <h3>{{ s.title }}</h3>
        <ul>
          <li v-for="(t, i) in s.items" :key="i" v-html="t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')" />
        </ul>
      </section>
      <p class="help-foot">
        更详细的设计取舍与命令见仓库 README；软件只读取本机
        <code>projects</code> 目录，拷走这个文件夹就是备份。
      </p>
    </div>
    <template #foot>
      <button class="btn sm primary" @click="emit('close')">知道了</button>
    </template>
  </AppModal>
</template>

<style scoped>
.help-body {
  max-height: min(62vh, 560px);
  overflow: auto;
  padding-right: 4px;
}
.help-sec {
  margin-bottom: 16px;
}
.help-sec h3 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--fg);
}
.help-sec ul {
  margin: 0;
  padding-left: 18px;
}
.help-sec li {
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--muted);
  margin-bottom: 2px;
}
.help-sec li :deep(b) {
  color: var(--fg);
  font-weight: 600;
}
.help-foot {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: var(--faint);
  line-height: 1.7;
}
.help-foot code {
  background: var(--line);
  padding: 1px 4px;
  border-radius: 4px;
}
</style>
