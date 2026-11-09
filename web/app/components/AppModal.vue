<script setup lang="ts">
withDefaults(
  defineProps<{ open: boolean; title?: string; desc?: string; width?: string }>(),
  { width: '460px' },
)
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div v-if="open" class="modal-mask" @click.self="emit('close')">
        <div class="modal" :style="{ width }">
          <div class="modal-head">
            <div class="modal-title">{{ title }}</div>
            <div v-if="desc" class="modal-desc">{{ desc }}</div>
          </div>
          <div class="modal-body">
            <slot />
          </div>
          <div class="modal-foot">
            <slot name="foot" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>
