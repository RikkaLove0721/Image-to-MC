<script setup lang="ts">
const props = withDefaults(defineProps<{ name: string; size?: number }>(), { size: 18 })

const ICONS: Record<string, string[]> = {
  box: ['M21 8.4 12 3 3 8.4v7.2L12 21l9-5.4V8.4Z', 'M3.3 8.5 12 13.6l8.7-5.1', 'M12 21v-7.4'],
  plus: ['M12 5v14', 'M5 12h14'],
  'arrow-left': ['M19 12H5', 'M11 6l-6 6 6 6'],
  upload: ['M12 16V4', 'M7 9l5-5 5 5', 'M5 20h14'],
  trash: ['M4 7h16', 'M9 7V5h6v2', 'M6 7l1 13h10l1-13'],
  search: ['M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14Z', 'M20 20l-4-4'],
  image: ['M4 5h16v14H4z', 'M4 16l4-4 4 4 3-3 5 5', 'M9 8.2a1.3 1.3 0 1 0 0 2.6 1.3 1.3 0 0 0 0-2.6Z'],
  layers: ['M12 3 3 8l9 5 9-5-9-5Z', 'M3 13l9 5 9-5', 'M3 17.5l9 5 9-5'],
  grid: ['M4 4h7v7H4z', 'M13 4h7v7h-7z', 'M4 13h7v7H4z', 'M13 13h7v7h-7z'],
  download: ['M12 4v11', 'M7 10l5 5 5-5', 'M5 20h14'],
  external: ['M14 4h6v6', 'M20 4l-9 9', 'M18 14v6H4V6h6'],
  check: ['M5 12.5l4.5 4.5L19 7'],
  x: ['M6 6l12 12', 'M18 6L6 18'],
  pencil: ['M4 20h4l10-10-4-4L4 16v4Z', 'M14 6l4 4'],
  folder: ['M3 6h6l2 2h10v11H3z'],
  spark: ['M12 3v4', 'M12 17v4', 'M3 12h4', 'M17 12h4', 'M6.5 6.5l2.8 2.8', 'M14.7 14.7l2.8 2.8', 'M17.5 6.5l-2.8 2.8', 'M9.3 14.7l-2.8 2.8'],
  info: ['M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16Z', 'M12 11v5', 'M12 8v.01'],
  'chevron-down': ['M6 9l6 6 6-6'],
  play: ['M8 5l11 7-11 7V5Z'],
  refresh: ['M4 12a8 8 0 1 0 3-6.2', 'M4 4v5h5'],
  camera: ['M4 8h3l1.5-2h7L17 8h3v11H4z', 'M12 10.5a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z'],
}
</script>

<template>
  <svg
    :width="props.size"
    :height="props.size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path v-for="(d, i) in ICONS[props.name] || []" :key="i" :d="d" />
  </svg>
</template>
