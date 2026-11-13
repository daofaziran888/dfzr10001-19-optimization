<template>
  <section class="page" data-module="workbench">
    <header class="page-head">
      <div>
        <h2>任务工作台</h2>
        <p class="page-desc">汇总RPA流程的校验结论：阻断待办先处理，可调度集合里的流程才允许启动。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="reload">刷新工作台</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">待处理阻断待办</span>
        <strong class="stat-value">{{ pendingCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">可调度流程</span>
        <strong class="stat-value">{{ schedulable.length }}</strong>
      </article>
    </div>

    <h3 class="section-title">阻断待办</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in todoColumns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="todo in todos" :key="String(todo.id)">
          <td v-for="column in todoColumns" :key="column">{{ todo[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-if="todo.状态 === '待处理'"
              class="link"
              type="button"
              @click="closeTodo(todo)"
            >
              办结
            </button>
            <span v-else class="done-text">已办结</span>
          </td>
        </tr>
        <tr v-if="!todos.length">
          <td :colspan="todoColumns.length + 1" class="empty-state">暂无阻断待办，校验结果会同步到这里</td>
        </tr>
      </tbody>
    </table>

    <h3 class="section-title">可调度集合</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in schedulableColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="flow in schedulable" :key="flow.流程编号">
          <td v-for="column in schedulableColumns" :key="column">{{ flow[column] ?? '—' }}</td>
        </tr>
        <tr v-if="!schedulable.length">
          <td :colspan="schedulableColumns.length" class="empty-state">暂无可调度流程：未引用输出字段或校验未通过的流程不会进入该集合</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>校验结果由RPA流程配置同步生成</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Todo = Record<string, any>
type SchedulableFlow = Record<string, any>

const ENDPOINT = '/api/workbench'
const todoColumns = ["待办编号", "关联流程", "待办类型", "内容", "状态", "生成时间", "处理说明"]
const schedulableColumns = ["流程编号", "流程名称", "当前版本", "运行环境", "超时秒数", "结果字段"]

const todos = ref<Todo[]>([])
const schedulable = ref<SchedulableFlow[]>([])
const pendingCount = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')

async function closeTodo(todo: Todo) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${todo.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action: '办结' } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '待办办结未生效'
    } else {
      noticeMessage.value = payload.message
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '待办办结失败'
  }
}

async function reload() {
  try {
    const response = await request(ENDPOINT)
    if (!response.ok) {
      throw new Error('任务工作台读取失败')
    }
    const payload = await response.json()
    todos.value = payload.待办 ?? []
    schedulable.value = payload.可调度流程 ?? []
    pendingCount.value = payload.待处理数 ?? 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '任务工作台读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.section-title {
  margin: 16px 0 8px;
  font-size: 14px;
}

.done-text {
  color: var(--muted);
  font-size: 12px;
}

.notice-text {
  color: #15803d;
}
</style>
