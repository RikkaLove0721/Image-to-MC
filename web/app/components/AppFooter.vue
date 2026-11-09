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
  </footer>
</template>
