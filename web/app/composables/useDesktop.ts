/** 是否跑在桌面外壳 (MC工程图.exe) 里. pywebview 的注入是异步的, 等 ready 事件再确认一次. */
export function useDesktopMode() {
  const enabled = ref(false)

  const sync = () => {
    enabled.value = isDesktopApp()
  }

  onMounted(() => {
    sync()
    window.addEventListener('pywebviewready', sync)
  })

  onUnmounted(() => {
    window.removeEventListener('pywebviewready', sync)
  })

  return enabled
}
