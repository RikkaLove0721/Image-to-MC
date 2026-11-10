<script setup lang="ts">
const version = useRuntimeConfig().public.version as string
const desktop = useDesktopMode()
</script>

<template>
  <footer class="app-footer">
    <span>{{ APP_NAME }} · v{{ version }} · Java 1.12.2</span>
    <span class="app-footer-dot" />
    <span v-if="desktop" class="app-footer-mode">桌面版</span>
    <span v-if="desktop" class="app-footer-dot" />
    <span>
      作者
      <a :href="AUTHOR.url" target="_blank" rel="noreferrer">{{ AUTHOR.name }}</a>
    </span>
    <span class="app-footer-dot" />
    <span>照片与模型保存在本机 projects 目录</span>
    <span class="app-footer-dot" />
    <span
      class="app-footer-note"
      title="方块库以 MC Java 1.12.2 为基准，只收录常用的一百多种方块。需要别的版本或材质，改 mc12/palette.py 即可，软件其余部分不用动。"
    >
      方块库为 1.12.2 常用子集，可自行扩充
    </span>
  </footer>
</template>
