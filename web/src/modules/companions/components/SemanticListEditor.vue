<script setup lang="ts">
import { Delete, Plus } from '@element-plus/icons-vue'

const props = withDefaults(defineProps<{
  modelValue: string[]
  placeholder?: string
  addLabel?: string
}>(), {
  placeholder: '',
  addLabel: '添加一条',
})

const emit = defineEmits<{
  'update:modelValue': [value: string[]]
}>()

function addItem() {
  emit('update:modelValue', [...props.modelValue, ''])
}

function updateItem(index: number, value: string) {
  const next = [...props.modelValue]
  next[index] = value
  emit('update:modelValue', next)
}

function removeItem(index: number) {
  emit('update:modelValue', props.modelValue.filter((_, itemIndex) => itemIndex !== index))
}
</script>

<template>
  <div class="semantic-list">
    <div v-for="(item, index) in modelValue" :key="index" class="semantic-row">
      <el-input
        :model-value="item"
        :placeholder="placeholder"
        @update:model-value="updateItem(index, String($event))"
      />
      <el-tooltip content="删除" placement="top">
        <el-button circle :icon="Delete" @click="removeItem(index)" />
      </el-tooltip>
    </div>
    <el-button class="add-button" text :icon="Plus" @click="addItem">
      {{ addLabel }}
    </el-button>
  </div>
</template>

<style scoped>
.semantic-list {
  display: grid;
  gap: 8px;
  width: 100%;
}
.semantic-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 32px;
  gap: 8px;
  align-items: center;
  min-width: 0;
}
.semantic-row :deep(.el-button) {
  width: 32px;
  height: 32px;
}
.add-button {
  justify-self: start;
}
</style>
