<template>
  <section class="page" data-module="calibration">
    <header class="page-head">
      <div>
        <h2>校准记录管理</h2>
        <p class="page-desc">维护校准记录，围绕校准编号、关联设备、校准方式、标准物质做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记校准记录</button>
        <button class="btn" type="button" @click="exportRows">导出校准记录清单</button>
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
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">
            <template v-if="column === '标准物质状态'">
              <span v-if="row['标准物质']" :class="['tag', row[column] === '已使用' ? 'tag-pass' : 'tag-pending']">{{ row[column] ?? '未使用' }}</span>
              <template v-else>—</template>
            </template>
            <template v-else>{{ row[column] ?? '—' }}</template>
          </td>
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
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无校准记录数据，可先登记校准记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条校准记录记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type StatItem = { label: string; value: number | string }

const ENDPOINT = '/api/calibration'
const columns = ["校准编号", "关联设备", "校准方式", "标准物质", "标准物质状态", "校准结果", "校准日期", "下次校准日", "校准状态"]
const actions = ["开始校准", "判定合格", "判定不合格"]
const statuses = ["待校准", "校准中", "已合格", "不合格"]
const statLabels = ["待校准记录", "校准合格率", "不合格设备"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const stats = ref<StatItem[]>([
  { label: "待校准记录", value: 0 },
  { label: "校准合格率", value: '0.0%' },
  { label: "不合格设备", value: 0 },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '校准记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '校准记录动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校准记录操作失败'
  }
}

async function reloadStats() {
  try {
    const response = await request(`${ENDPOINT}/stats`)
    if (!response.ok) {
      throw new Error('校准统计读取失败')
    }
    const payload = await response.json()
    stats.value = statLabels.map((label) => {
      if (label === '校准合格率') {
        return { label, value: `${Number(payload[label] ?? 0).toFixed(1)}%` }
      }
      return { label, value: Number(payload[label] ?? 0) }
    })
  } catch {
    // 统计卡片不阻断列表，沿用默认 0 值。
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('校准记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    void reloadStats()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '校准记录列表读取失败'
  }
}

onMounted(reload)
</script>
