<template>
  <section class="page" data-module="rpa">
    <header class="page-head">
      <div>
        <h2>RPA流程配置管理</h2>
        <p class="page-desc">维护RPA自动化流程的动作编排、等待条件与执行结果，实现无人值守数据采集。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记RPA流程</button>
        <button class="btn" type="button" @click="exportRows">导出RPA流程配置清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr>
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
              <button class="link" type="button" @click="toggleEditor(row)">
                {{ editingId === row.id ? '收起依赖' : '等待依赖' }}
              </button>
            </td>
          </tr>
          <tr v-if="editingId === row.id">
            <td :colspan="columns.length + 1" class="dep-editor">
              <div class="dep-grid">
                <div>
                  <h4>动作序列（步骤列表）</h4>
                  <ul class="step-list">
                    <li v-for="step in row.步骤列表 ?? []" :key="String(step.步骤号)">
                      第{{ step.步骤号 }}步 · {{ step.动作 }} → 输出「{{ step.输出字段 }}」（{{ step.运行环境 }}）
                    </li>
                    <li v-if="!(row.步骤列表 ?? []).length">尚未配置步骤</li>
                  </ul>
                  <p class="dep-hint">
                    校验结论：{{ row.校验结论 }}<template v-if="validationOf(row)">
                      ，检查顺序：{{ validationOf(row) }}
                    </template>
                  </p>
                  <p v-if="problemsOf(row).length" class="error-text">
                    <span v-for="problem in problemsOf(row)" :key="problem" class="problem-item">· {{ problem }}</span>
                  </p>
                </div>
                <div>
                  <h4>等待条件（依赖列表，JSON 编辑）</h4>
                  <textarea
                    v-model="depDraft"
                    class="dep-textarea"
                    rows="8"
                    spellcheck="false"
                    placeholder='[{"所在步骤": 4, "引用步骤": 2, "引用输出字段": "列表数据", "超时秒数": 30}]'
                  ></textarea>
                  <div class="dep-actions">
                    <button class="btn primary" type="button" @click="saveDependencies(row)">保存依赖</button>
                    <button class="btn ghost" type="button" @click="toggleEditor(row)">取消</button>
                  </div>
                  <p class="dep-hint">同一依赖（引用流程/所在步骤/引用步骤/输出字段相同）反复保存只保留一版；保存后立即重跑校验并同步任务工作台。</p>
                </div>
              </div>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无RPA流程配置数据，可先登记RPA流程</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条RPA流程配置记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/rpa'
const columns = ["流程编号", "流程名称", "输入来源", "动作序列", "等待条件", "超时秒数", "结果字段", "运行环境", "校验结论", "当前版本", "可调度"]
const actions = ["提交校验", "启用配置", "停用配置"]
const statuses = ["草稿", "待校验", "已启用", "已停用"]
const stats = [{"label": "RPA流程总数", "value": 0}, {"label": "待校验RPA流程", "value": 0}, {"label": "已启用RPA流程", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const editingId = ref<number | null>(null)
const depDraft = ref('')

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = 'RPA流程登记入口尚未接入审批流'
}

function validationOf(row: Row): string {
  const result = row.校验结果
  if (!result) return ''
  return (result.检查顺序 ?? []).join(' → ')
}

function problemsOf(row: Row): string[] {
  const result = row.校验结果
  return result?.问题 ?? []
}

function toggleEditor(row: Row) {
  if (editingId.value === row.id) {
    editingId.value = null
    depDraft.value = ''
    return
  }
  editingId.value = Number(row.id)
  depDraft.value = JSON.stringify(row.依赖列表 ?? [], null, 2)
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || 'RPA流程配置动作未生效'
    } else {
      noticeMessage.value = payload.message
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'RPA流程配置操作失败'
  }
}

async function saveDependencies(row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  let deps: unknown
  try {
    deps = JSON.parse(depDraft.value || '[]')
  } catch {
    errorMessage.value = '依赖列表不是合法 JSON，请检查后再保存'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${row.id}/dependencies`, {
      method: 'POST',
      body: JSON.stringify({ values: { 依赖列表: deps } }),
    })
    const payload = await response.json()
    if (!payload.ok) {
      errorMessage.value = payload.message || '依赖保存未生效'
    } else {
      noticeMessage.value = payload.message
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '依赖保存失败'
  }
}

async function reload() {
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('RPA流程列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    if (editingId.value !== null) {
      const current = rows.value.find((row) => Number(row.id) === editingId.value)
      if (current) {
        depDraft.value = JSON.stringify(current.依赖列表 ?? [], null, 2)
      } else {
        editingId.value = null
      }
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'RPA流程配置列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.dep-editor {
  background: #f8fafc;
  padding: 12px 16px;
}

.dep-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.dep-grid h4 {
  margin: 0 0 8px;
  font-size: 13px;
}

.step-list {
  margin: 0 0 8px;
  padding-left: 18px;
  line-height: 1.8;
}

.dep-textarea {
  width: 100%;
  box-sizing: border-box;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  border-radius: 6px;
  border: 1px solid var(--border);
  background: #fff;
  color: inherit;
  padding: 8px;
}

.dep-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}

.dep-hint {
  font-size: 12px;
  color: var(--muted);
  margin: 8px 0 0;
}

.problem-item {
  display: block;
}

.notice-text {
  color: #15803d;
}
</style>
