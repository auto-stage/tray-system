import React, { useState, useEffect, useRef, useCallback } from 'react'

// ============================================================
// TYPES
// ============================================================
type Screen =
  | 'MAIN' | 'CAMERA_CAPTURE' | 'ANALYZING' | 'REVIEW'
  | 'TRAY_MOVING' | 'PICKING' | 'VERIFICATION' | 'ITEM_COMPLETE'
  | 'FINAL_VERIFICATION' | 'TRAY_RETURN' | 'RELOCATION_COMPLETE'
  | 'WORK_COMPLETE' | 'WORK_HISTORY' | 'SETTINGS' | 'EQUIPMENT_ERROR' | 'EMERGENCY_STOP'

// Rack slot order: [top-left, top-right, mid-left, mid-right, bot-left, bot-right]
type RackSlots = [string, string, string, string, string, string]

interface TrayData {
  id: string; partNo: string; name: string; spec: string
  stock: number; maxStock: number; status: 'READY' | 'IN_USE' | 'MOVING' | 'LOW STOCK' | 'ERROR'
}
interface WorkItem {
  no: string; partNo: string; name: string; spec: string
  qty: number; unit: string; tray: string; stock: number
  status: '정상' | '확인 필요' | '재고 부족'
}
interface HistoryEntry {
  no: string; date: string; operator: string; items: number
  qty: number; result: 'COMPLETED' | 'STOPPED' | 'ERROR'; time: string
}

interface WorkHistoryRecord {
  work_id: string
  completed_at: string
  result: 'COMPLETED' | 'STOPPED' | 'ERROR' | string
  duration_seconds: number
  item_count: number
  total_quantity: number
  used_trays: string[]
  items: Array<{
    part_no: string
    name: string
    spec: string
    quantity: number
    tray: string
  }>
}

// ============================================================
// DATA
// ============================================================
const TRAYS_INITIAL: TrayData[] = [
  { id: 'TRAY 01', partNo: 'B001', name: '육각볼트', spec: 'M6X20', stock: 120, maxStock: 200, status: 'READY' },
  { id: 'TRAY 02', partNo: 'B002', name: '육각렌치볼트', spec: 'M5X15', stock: 85, maxStock: 200, status: 'READY' },
  { id: 'TRAY 03', partNo: 'S001', name: '십자머리나사', spec: 'M4X12', stock: 200, maxStock: 300, status: 'READY' },
  { id: 'TRAY 04', partNo: 'N001', name: '육각너트', spec: 'M6', stock: 150, maxStock: 200, status: 'READY' },
  { id: 'TRAY 05', partNo: 'W001', name: '평와셔', spec: 'M6', stock: 180, maxStock: 200, status: 'READY' },
  { id: 'TRAY 06', partNo: 'W002', name: '스프링와셔', spec: 'M6', stock: 28, maxStock: 200, status: 'LOW STOCK' },
]
const WORK_ITEMS_INITIAL: WorkItem[] = [
  { no: '01', partNo: 'B001', name: '육각볼트', spec: 'M6X20', qty: 10, unit: 'EA', tray: 'TRAY 01', stock: 120, status: '정상' },
  { no: '02', partNo: 'B002', name: '육각렌치볼트', spec: 'M5X15', qty: 8, unit: 'EA', tray: 'TRAY 02', stock: 85, status: '정상' },
  { no: '03', partNo: 'S001', name: '십자머리나사', spec: 'M4X12', qty: 20, unit: 'EA', tray: 'TRAY 03', stock: 200, status: '정상' },
]
const HISTORY_DATA: HistoryEntry[] = [
  { no: 'WO-20260817-001', date: '2026-08-17', operator: 'OP-001', items: 3, qty: 38, result: 'COMPLETED', time: '00:04:32' },
  { no: 'WO-20260816-003', date: '2026-08-16', operator: 'OP-002', items: 5, qty: 62, result: 'COMPLETED', time: '00:07:14' },
  { no: 'WO-20260816-002', date: '2026-08-16', operator: 'OP-001', items: 2, qty: 15, result: 'STOPPED', time: '00:02:10' },
  { no: 'WO-20260816-001', date: '2026-08-16', operator: 'OP-003', items: 4, qty: 45, result: 'COMPLETED', time: '00:05:48' },
  { no: 'WO-20260815-002', date: '2026-08-15', operator: 'OP-001', items: 3, qty: 30, result: 'ERROR', time: '00:03:22' },
]
// Physical rack: top-left, top-right, mid-left, mid-right, bot-left, bot-right
const RACK_SLOTS_INITIAL: RackSlots = ['TRAY 05','TRAY 06','TRAY 03','TRAY 04','TRAY 01','TRAY 02']
// Q-learning result: frequently-used trays (01, 02, 03) promoted to top/mid
const RACK_SLOTS_QLEARNING: RackSlots = ['TRAY 01', 'TRAY 02', 'TRAY 03', 'TRAY 04', 'TRAY 05', 'TRAY 06']

// Fixed physical positions on the rack (index 0–5, top-left to bottom-right)
const SLOT_POSITIONS = ['상단 좌측', '상단 우측', '중단 좌측', '중단 우측', '하단 좌측', '하단 우측'] as const

// Return the slot index (physical position) of a tray within a layout
function slotOf(trayId: string, slots: RackSlots): number {
  return (slots as string[]).indexOf(trayId)
}

const ANALYSIS_STEPS = [
  '문서 입력',
  '문자 / 데이터 추출',
  '품목 행 / 표 구조 분석',
  '규격 및 수량 분석',
  'Tray 매칭',
  '재고 확인'
]

// ============================================================
// HELPERS
// ============================================================
function useNow() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}
function padZ(n: number) { return String(n).padStart(2, '0') }
function formatTime(d: Date) { return `${padZ(d.getHours())}:${padZ(d.getMinutes())}:${padZ(d.getSeconds())}` }
function formatDate(d: Date) {
  return `${d.getFullYear()}-${padZ(d.getMonth()+1)}-${padZ(d.getDate())}`
}

// ============================================================
// STATUS BAR
// ============================================================
function StatusBar({
  onHistory,
  onSettings,
  onEmergency,
  screen
}: {
  onHistory: () => void
  onSettings: () => void
  onEmergency: () => void
  screen: Screen
}) {
  const now = useNow()

  const [stageStatus, setStageStatus] =
    useState<any>(null)

  useEffect(() => {
    let cancelled = false

    const pollStageStatus = async () => {
      try {
        const response = await fetch(
          'http://127.0.0.1:8000/stage/status'
        )

        if (!response.ok) {
          throw new Error(
            `Stage status 오류: ${response.status}`
          )
        }

        const status = await response.json()

        if (!cancelled) {
          setStageStatus(status)
        }

      } catch (error) {
        if (!cancelled) {
          setStageStatus(null)
        }
      }
    }

    pollStageStatus()

    const timer = setInterval(
      pollStageStatus,
      1000
    )

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  const stageIsMock =
    stageStatus?.mock === true

  const realStageConnected =
    stageStatus?.connected === true

  const stageError =
    stageStatus?.state === 'ERROR'
    ||
    stageStatus?.state === 'ESTOPPED'
    ||
    stageStatus?.estop === true
    ||
    stageStatus?.estopped === true

  const stageLabel =
    stageStatus == null
      ? 'OFFLINE'
      : stageIsMock
        ? 'MOCK'
        : !realStageConnected
          ? 'DISCONNECTED'
          : stageError
            ? 'ERROR'
            : 'NORMAL'

  const stm32Label =
    stageStatus == null
      ? 'DISCONNECTED'
      : stageIsMock
        ? 'DISCONNECTED'
        : realStageConnected
          ? 'CONNECTED'
          : 'DISCONNECTED'

  const sysItems = [
    { label: 'Camera', val: 'NORMAL', ok: true },
    {
      label: 'Stage',
      val: stageLabel,
      ok: stageStatus != null && !stageError,
    },
    { label: 'Database', val: 'NORMAL', ok: true },
    { label: 'Load Cell', val: 'NORMAL', ok: true },
    {
      label: 'STM32',
      val: stm32Label,
      ok:
        !stageIsMock
        &&
        realStageConnected,
    },
  ]
  return (
    <div style={{ background: 'var(--hmi-navy-dark)', borderBottom: '2px solid #1e3a5f', flexShrink: 0 }}>
      {/* Top row */}
      <div style={{ display: 'flex', alignItems: 'center', padding: '6px 16px', borderBottom: '1px solid #1e3a5f', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
          <span style={{ color: '#e2e8f0', fontWeight: 800, fontSize: 16, letterSpacing: '0.06em' }}>
            TRAY PROCUREMENT SYSTEM
          </span>
          <span style={{ color: '#7a9cc4', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
            Vision AI-Based Automated Tray Procurement System
          </span>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ color: '#94a3b8', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', textAlign: 'right' }}>
            <div style={{ color: '#e2e8f0', fontSize: 13, fontWeight: 700 }}>{formatTime(now)}</div>
            <div>{formatDate(now)}</div>
          </div>
          <div style={{ width: 1, height: 28, background: '#2d4a70' }} />
          <div style={{ color: '#7a9cc4', fontSize: 11 }}>
            <div style={{ color: '#cbd5e1', fontSize: 12, fontWeight: 600 }}>작업자</div>
            <div style={{ fontFamily: 'JetBrains Mono, monospace' }}>OP-001</div>
          </div>
          <div style={{ width: 1, height: 28, background: '#2d4a70' }} />
          <button className="btn-secondary" style={{ padding: '4px 12px', fontSize: 12 }} onClick={onHistory}>
            📋 작업 이력
          </button>
          <button
            className="btn-secondary"
            style={{ padding: '4px 12px', fontSize: 12 }}
            onClick={onSettings}
            disabled={[
              'TRAY_MOVING',
              'PICKING',
              'VERIFICATION',
              'ITEM_COMPLETE',
              'FINAL_VERIFICATION',
              'TRAY_RETURN'
            ].includes(screen)}
            title={
              [
                'TRAY_MOVING',
                'PICKING',
                'VERIFICATION',
                'ITEM_COMPLETE',
                'FINAL_VERIFICATION',
                'TRAY_RETURN'
              ].includes(screen)
                ? '자동 작업 중에는 Stage 설정을 변경할 수 없습니다.'
                : 'Stage 제어 및 상태 확인'
            }
          >
            ⚙ 설정
          </button>
          <button className="estop-btn" onClick={onEmergency} style={{ fontSize: 12, padding: '5px 12px' }}>
            🛑 E-STOP
          </button>
        </div>
      </div>
      {/* System status row */}
      <div style={{ display: 'flex', alignItems: 'center', padding: '4px 16px', gap: 20 }}>
        {sysItems.map(item => (
          <div key={item.label} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
            <span className="status-dot" style={{ background: item.ok ? '#22c55e' : '#dc2626' }} />
            <span style={{ color: '#94a3b8', fontSize: 11 }}>{item.label} :</span>
            <span style={{
              color: item.ok ? '#4ade80' : '#f87171',
              fontSize: 11, fontWeight: 700,
              fontFamily: 'JetBrains Mono, monospace'
            }}>{item.val}</span>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        {screen !== 'MAIN' && screen !== 'WORK_HISTORY' && screen !== 'EQUIPMENT_ERROR' && screen !== 'EMERGENCY_STOP' && (
          <div style={{ color: '#7a9cc4', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
            WO-20260817-001
          </div>
        )}
      </div>
    </div>
  )
}

// ============================================================
// TRAY CARD
// ============================================================
// Map tray ID → TrayData (for dynamic rack rendering)
function buildTrayMap(trays: TrayData[]): Record<string, TrayData> {
  const map: Record<string, TrayData> = {}
  if (Array.isArray(trays)) trays.forEach(t => { map[t.id] = t })
  return map
}

function TrayCard({ tray, highlight, slotLabel }: { tray: TrayData; highlight?: boolean; slotLabel?: string }) {
  const isLow = tray.status === 'LOW STOCK'
  const isMoving = tray.status === 'MOVING' || tray.status === 'IN_USE'
  const pct = Math.round((tray.stock / tray.maxStock) * 100)
  const barColor = isLow ? 'var(--hmi-orange-accent)' : isMoving ? 'var(--hmi-blue-light)' : 'var(--hmi-green)'

  return (
    <div style={{
      border: highlight ? '2px solid var(--hmi-blue-light)' : '1px solid var(--hmi-border)',
      background: highlight ? '#e8f0fe' : 'white',
      padding: 0,
      position: 'relative',
      boxShadow: highlight ? '0 0 0 2px rgba(59,130,246,0.3)' : 'none',
    }}>
      <div style={{
        background: highlight ? 'var(--hmi-blue)' : 'var(--hmi-navy)',
        color: 'white',
        padding: '5px 10px',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center'
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          <span style={{ fontWeight: 800, fontSize: 13, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.05em' }}>
            {tray.id}
          </span>
          {slotLabel && (
            <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.65)', fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.03em' }}>
              {slotLabel}
            </span>
          )}
        </div>
        <span style={{
          fontSize: 10, fontWeight: 700, padding: '1px 6px',
          background: isLow ? 'var(--hmi-orange-accent)' : isMoving ? 'var(--hmi-blue-light)' : 'var(--hmi-green)',
          color: 'white', fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.05em'
        }}>
          {isMoving ? 'MOVING' : tray.status}
        </span>
      </div>
      <div style={{ padding: '8px 10px' }}>
        <div style={{ fontSize: 10, color: 'var(--hmi-text-muted)', fontFamily: 'JetBrains Mono, monospace' }}>품번 {tray.partNo}</div>
        <div style={{ fontWeight: 700, fontSize: 14, margin: '2px 0' }}>{tray.name}</div>
        <div style={{ fontSize: 12, color: '#374151', fontFamily: 'JetBrains Mono, monospace', marginBottom: 6 }}>{tray.spec}</div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <span style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>현재 재고</span>
          <span style={{
            fontSize: 15, fontWeight: 800,
            color: isLow ? 'var(--hmi-orange)' : 'var(--hmi-text)',
            fontFamily: 'JetBrains Mono, monospace'
          }}>{tray.stock}</span>
        </div>
        <div className="progress-bar">
          <div style={{ height: '100%', width: `${pct}%`, background: barColor, transition: 'width 0.5s' }} />
        </div>
        <div style={{ fontSize: 10, color: 'var(--hmi-text-muted)', marginTop: 2, textAlign: 'right', fontFamily: 'JetBrains Mono, monospace' }}>
          {pct}%
        </div>
      </div>
    </div>
  )
}

// ============================================================
// PAUSE OVERLAY
// ============================================================
function PauseOverlay({
  currentItem, currentStage, onResume, onRestart, onStop
}: {
  currentItem: WorkItem; currentStage: string
  onResume: () => void; onRestart: () => void; onStop: () => void
}) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,17,31,0.85)', zIndex: 200,
      display: 'flex', alignItems: 'center', justifyContent: 'center'
    }}>
      <div style={{
        background: 'white', border: '3px solid var(--hmi-yellow-accent)',
        width: 520, padding: 0
      }}>
        <div style={{
          background: '#7c2d12', color: 'white', padding: '14px 24px',
          display: 'flex', alignItems: 'center', gap: 12
        }}>
          <span style={{ fontSize: 22 }}>⏸</span>
          <span style={{ fontWeight: 800, fontSize: 18, letterSpacing: '0.05em' }}>작업 일시 정지</span>
        </div>
        <div style={{ padding: '24px 28px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
            <div style={{ background: '#f8f9fa', border: '1px solid var(--hmi-border-light)', padding: '10px 14px' }}>
              <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 3 }}>현재 품목</div>
              <div style={{ fontWeight: 700, fontSize: 14 }}>{currentItem.name}</div>
              <div style={{ fontSize: 12, color: '#374151', fontFamily: 'JetBrains Mono, monospace' }}>{currentItem.spec}</div>
            </div>
            <div style={{ background: '#f8f9fa', border: '1px solid var(--hmi-border-light)', padding: '10px 14px' }}>
              <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 3 }}>현재 단계</div>
              <div style={{ fontWeight: 700, fontSize: 14, fontFamily: 'JetBrains Mono, monospace' }}>{currentStage}</div>
            </div>
          </div>
          <div style={{
            background: 'var(--hmi-yellow-bg)', border: '1px solid var(--hmi-yellow-accent)',
            padding: '10px 14px', marginBottom: 20, fontSize: 13, color: 'var(--hmi-yellow)'
          }}>
            현재 시스템 상태가 일시정지되었습니다.
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-green" style={{ flex: 2, padding: '10px', fontSize: 15 }} onClick={onResume}>
              ▶ 작업 재개
            </button>
            <button className="btn-warning" style={{ flex: 2, padding: '10px', fontSize: 15 }} onClick={onRestart}>
              ↺ 현재 단계 다시 시작
            </button>
            <button className="btn-danger" style={{ flex: 1, padding: '10px', fontSize: 15 }} onClick={onStop}>
              ■ 작업 중지
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// STOP CONFIRM MODAL
// ============================================================
function StopConfirmModal({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,17,31,0.85)', zIndex: 300,
      display: 'flex', alignItems: 'center', justifyContent: 'center'
    }}>
      <div style={{ background: 'white', border: '3px solid var(--hmi-red-accent)', width: 440 }}>
        <div style={{ background: 'var(--hmi-red-accent)', color: 'white', padding: '12px 20px', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 20 }}>⚠</span>
          <span style={{ fontWeight: 800, fontSize: 16 }}>작업 중지</span>
        </div>
        <div style={{ padding: '24px 24px' }}>
          <p style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>현재 작업을 중지하시겠습니까?</p>
          <p style={{ fontSize: 13, color: 'var(--hmi-text-muted)', marginBottom: 20 }}>
            현재 자동 작업은 일시적으로 정지되었습니다.
          </p>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button className="btn-secondary" style={{ minWidth: 80 }} onClick={onCancel}>취소</button>
            <button className="btn-danger" style={{ minWidth: 100 }} onClick={onConfirm}>작업 종료</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// EDIT ITEM MODAL
// ============================================================
function EditItemModal({
  item,
  onCancel,
  onSave,
}: {
  item: WorkItem
  onCancel: () => void
  onSave: (item: WorkItem) => Promise<boolean>
}) {
  const [form, setForm] = useState({ ...item })
  const [saving, setSaving] = useState(false)
  return (
    <div style={{
      position: 'fixed', inset: 0, background: 'rgba(7,17,31,0.7)', zIndex: 150,
      display: 'flex', alignItems: 'center', justifyContent: 'center'
    }}>
      <div style={{ background: 'white', border: '2px solid var(--hmi-blue)', width: 420 }}>
        <div style={{ background: 'var(--hmi-navy)', color: 'white', padding: '10px 18px', fontWeight: 700, fontSize: 14 }}>
          품목 수정 — {item.no}
        </div>
        <div style={{ padding: '20px 20px' }}>
          {(['partNo', 'name', 'spec'] as const).map(key => (
            <div key={key} style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, color: 'var(--hmi-text-muted)', display: 'block', marginBottom: 3 }}>
                {{ partNo: '품번', name: '품명', spec: '규격 / 사양' }[key]}
              </label>
              <input
                value={form[key]}
                onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                style={{
                  width: '100%', border: '1px solid var(--hmi-border)', padding: '7px 10px',
                  fontSize: 14, fontFamily: 'JetBrains Mono, monospace'
                }}
              />
            </div>
          ))}
          <div style={{ marginBottom: 16 }}>
            <label style={{ fontSize: 12, color: 'var(--hmi-text-muted)', display: 'block', marginBottom: 3 }}>수량</label>
            <input
              type="number"
              value={form.qty}
              onChange={e => setForm(f => ({ ...f, qty: parseInt(e.target.value) || 0 }))}
              style={{ width: '100%', border: '1px solid var(--hmi-border)', padding: '7px 10px', fontSize: 14, fontFamily: 'JetBrains Mono, monospace' }}
            />
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button
              className="btn-secondary"
              onClick={onCancel}
              disabled={saving}
            >
              취소
            </button>

            <button
              className="btn-primary"
              disabled={saving}
              onClick={async () => {
                setSaving(true)

                await onSave({
                  ...form,
                  status: '정상',
                })

                setSaving(false)
              }}
            >
              {saving ? '검증 중...' : '수정 완료'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: MAIN
// ============================================================
function MainScreen({
  trays, rackSlots, history, onFileUpload, onCamera, onShowHistory
}: {
  trays: TrayData[]
  rackSlots: RackSlots
  history: WorkHistoryRecord[]
  onFileUpload: (file: File) => Promise<void>
  onCamera: () => void
  onShowHistory: () => void
}) {
  const [dragOver, setDragOver] = useState(false)
  const [fileName, setFileName] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const trayMap = buildTrayMap(trays)

  const handleFile = async (file: File) => {
    setFileName(file.name)

    try {
      await onFileUpload(file)
    } catch (error) {
      console.error('파일 업로드 오류:', error)
      alert('작업지시서 처리 중 오류가 발생했습니다.')
    }
  }

  const recentHistory = history.slice(0, 4)
  const totalStock = trays.reduce((s, t) => s + t.stock, 0)
  const lowCount = trays.filter(t => t.status === 'LOW STOCK').length
  const availableCount = trays.filter(t => t.status === 'READY').length
  // Render trays in rack slot order
  const slots = Array.isArray(rackSlots) ? rackSlots : RACK_SLOTS_INITIAL
  const orderedTrays = slots
    .map((id, i) => ({ tray: trayMap[id], slotIndex: i }))
    .filter((x): x is { tray: TrayData; slotIndex: number } => x.tray != null)

  return (
    <div style={{ display: 'flex', flex: 1, overflow: 'hidden', gap: 0 }}>
      {/* LEFT: Tray Storage */}
      <div style={{ flex: '0 0 58%', display: 'flex', flexDirection: 'column', borderRight: '2px solid var(--hmi-border)', overflow: 'hidden' }}>
        <div className="section-header" style={{ fontSize: 13 }}>
          ■ TRAY STORAGE STATUS
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: 14, background: 'var(--hmi-work-bg)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {orderedTrays.map(({ tray: t, slotIndex: si }) => (
              <TrayCard key={t.id} tray={t} slotLabel={SLOT_POSITIONS[si]} />
            ))}
          </div>
        </div>
      </div>

      {/* RIGHT: Work Order Registration */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div className="section-header" style={{ fontSize: 13 }}>
          ■ 작업지시서 등록
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: 14, background: 'var(--hmi-work-bg)', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '10px 14px', fontSize: 12, color: '#374151' }}>
            작업지시서를 등록하면 필요한 품목, 수량 및 대상 Tray를 자동으로 분석합니다.
          </div>

          {/* Drop zone */}
          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }}
            style={{
              border: `2px dashed ${dragOver ? 'var(--hmi-blue)' : 'var(--hmi-border)'}`,
              background: dragOver ? '#e8f0fe' : '#fafafa',
              padding: '28px 20px',
              textAlign: 'center',
              cursor: 'pointer',
              transition: 'all 0.15s'
            }}
            onClick={() => fileRef.current?.click()}
          >
            <div style={{ fontSize: 28, marginBottom: 8, color: '#9ca3af' }}>📄</div>
            <div style={{ fontWeight: 600, fontSize: 14, color: '#374151', marginBottom: 4 }}>
              PDF / JPG / JPEG / PNG
            </div>
            <div style={{ fontSize: 12, color: '#9ca3af', marginBottom: 8 }}>파일을 여기에 드래그하거나 클릭하여 선택하세요</div>
            {fileName && (
              <div style={{
                background: '#e8f0fe', border: '1px solid var(--hmi-blue)', padding: '4px 12px',
                fontSize: 12, color: 'var(--hmi-blue)', display: 'inline-block', fontFamily: 'JetBrains Mono, monospace'
              }}>
                ✓ {fileName}
              </div>
            )}
            <input
              ref={fileRef}
              type="file"
              accept=".pdf,.jpg,.jpeg,.png"
              style={{ display: 'none' }}
              onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
            />
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-secondary" style={{ flex: 1, padding: '10px', fontSize: 14 }} onClick={onCamera}>
              📷 카메라 촬영
            </button>
            <button className="btn-primary" style={{ flex: 1, padding: '10px', fontSize: 14 }}
              onClick={() => fileRef.current?.click()}>
              📁 파일 불러오기
            </button>
          </div>

          {/* Summary stats */}
          <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)' }}>
            <div style={{ background: '#1e3a5f', color: 'white', padding: '5px 12px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em' }}>
              현황 요약
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0 }}>
              {[
                { label: '전체 Tray', value: '6', color: '#374151' },
                { label: '사용 가능', value: String(availableCount), color: 'var(--hmi-green)' },
                { label: '총 재고', value: String(totalStock), color: 'var(--hmi-blue)' },
                { label: '부족 품목', value: String(lowCount), color: lowCount > 0 ? 'var(--hmi-orange)' : '#374151' },
              ].map((s, i) => (
                <div key={i} style={{ padding: '8px 12px', borderBottom: i < 2 ? '1px solid var(--hmi-border-light)' : 'none', borderRight: i % 2 === 0 ? '1px solid var(--hmi-border-light)' : 'none' }}>
                  <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>{s.label}</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: s.color, fontFamily: 'JetBrains Mono, monospace' }}>{s.value}</div>
                </div>
              ))}
            </div>
          </div>

          {/* Recent history */}
          <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)' }}>
            <div style={{ background: '#1e3a5f', color: 'white', padding: '5px 12px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', display: 'flex', justifyContent: 'space-between' }}>
              <span>최근 작업 이력</span>
              <span style={{ cursor: 'pointer', color: '#93c5fd' }} onClick={onShowHistory}>전체 보기 ›</span>
            </div>
            {recentHistory.map((h, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'center', padding: '6px 12px',
                borderBottom: i < recentHistory.length - 1 ? '1px solid var(--hmi-border-light)' : 'none',
                gap: 8, fontSize: 12
              }}>
                <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: '#374151', minWidth: 130 }}>{h.work_id}</span>
                <span style={{ color: 'var(--hmi-text-muted)', fontSize: 11 }}>
                  {new Date(h.completed_at).toLocaleDateString()}
                </span>
                <span style={{ flex: 1 }} />
                <span className={`badge-${h.result === 'COMPLETED' ? 'green' : h.result === 'STOPPED' ? 'yellow' : 'red'}`}
                  style={{ padding: '1px 6px', fontSize: 10 }}>
                  {h.result}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: CAMERA CAPTURE
// ============================================================
function CameraCaptureScreen({ onUse, onCancel }: { onUse: () => void; onCancel: () => void }) {
  const [captured, setCaptured] = useState(false)
  const [flashVisible, setFlashVisible] = useState(false)

  const handleCapture = () => {
    setFlashVisible(true)
    setTimeout(() => { setFlashVisible(false); setCaptured(true) }, 300)
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div className="section-header" style={{ fontSize: 13 }}>
        📷 작업지시서 촬영
      </div>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--hmi-work-bg)', padding: 24 }}>
        <div style={{ width: 700, display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Status bar */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="status-dot" style={{ background: '#22c55e' }} />
            <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, fontWeight: 700, color: 'var(--hmi-green)' }}>
              CAMERA CONNECTED
            </span>
          </div>

          {/* Camera view */}
          <div style={{ position: 'relative', background: '#0a0f1a', border: '2px solid #2d4a70', overflow: 'hidden', aspectRatio: '4/3' }}>
            {flashVisible && (
              <div style={{ position: 'absolute', inset: 0, background: 'white', zIndex: 10, opacity: 0.9 }} />
            )}
            {!captured ? (
              <>
                {/* Simulated live camera */}
                <div className="camera-live" style={{ position: 'absolute', inset: 0 }}>
                  {/* Mock document lines */}
                  <div style={{ position: 'absolute', inset: '15%', border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.04)' }} />
                  <div style={{ position: 'absolute', top: '20%', left: '18%', right: '18%', borderTop: '1px solid rgba(255,255,255,0.1)' }} />
                  <div style={{ position: 'absolute', top: '28%', left: '18%', right: '18%', borderTop: '1px solid rgba(255,255,255,0.07)' }} />
                  <div style={{ position: 'absolute', top: '36%', left: '18%', right: '18%', borderTop: '1px solid rgba(255,255,255,0.07)' }} />
                  <div style={{ position: 'absolute', top: '50%', left: '18%', right: '18%', borderTop: '1px solid rgba(255,255,255,0.07)' }} />
                </div>
                {/* Guide frame */}
                <div style={{
                  position: 'absolute', inset: '8%',
                  border: '2px dashed rgba(34,197,94,0.7)',
                  pointerEvents: 'none'
                }}>
                  {/* Corner markers */}
                  {[{top:0,left:0},{top:0,right:0},{bottom:0,left:0},{bottom:0,right:0}].map((s,i) => (
                    <div key={i} style={{
                      position: 'absolute', ...s,
                      width: 20, height: 20,
                      borderTop: (s.top === 0) ? '3px solid #22c55e' : 'none',
                      borderBottom: (s as any).bottom === 0 ? '3px solid #22c55e' : 'none',
                      borderLeft: (s.left === 0) ? '3px solid #22c55e' : 'none',
                      borderRight: (s as any).right === 0 ? '3px solid #22c55e' : 'none',
                    }} />
                  ))}
                  <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', color: 'rgba(255,255,255,0.5)', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', whiteSpace: 'nowrap' }}>
                    작업지시서를 안에 배치하세요
                  </div>
                </div>
                {/* LIVE indicator */}
                <div style={{ position: 'absolute', top: 10, left: 10, display: 'flex', alignItems: 'center', gap: 5 }}>
                  <span className="status-dot blink" style={{ background: '#ef4444' }} />
                  <span style={{ color: 'white', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>LIVE</span>
                </div>
                <div style={{ position: 'absolute', top: 10, right: 10, color: 'rgba(255,255,255,0.6)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>CAM-01</div>
              </>
            ) : (
              <>
                {/* Captured image mockup */}
                <div style={{ position: 'absolute', inset: 0, background: '#f0f0ee', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <div style={{ width: '70%', background: 'white', padding: 20, border: '1px solid #ccc', boxShadow: '0 4px 20px rgba(0,0,0,0.3)' }}>
                    <div style={{ borderBottom: '2px solid #1e3a5f', paddingBottom: 8, marginBottom: 10, fontWeight: 800, fontSize: 13, color: '#1e3a5f' }}>
                      작업지시서 / WORK ORDER
                    </div>
                    <div style={{ fontSize: 11, color: '#374151', fontFamily: 'JetBrains Mono, monospace', lineHeight: 1.6 }}>
                      <div>작업번호: WO-20260817-001</div>
                      <div>발행일자: 2026-08-17</div>
                      <div style={{ marginTop: 6, marginBottom: 4, fontWeight: 700 }}>품목 목록:</div>
                      <div>B001 | 육각볼트 M6X20 | 10 EA</div>
                      <div>B002 | 육각렌치볼트 M5X15 | 8 EA</div>
                      <div>S001 | 십자머리나사 M4X12 | 20 EA</div>
                    </div>
                  </div>
                </div>
                <div style={{ position: 'absolute', top: 10, left: 10, background: '#16a34a', color: 'white', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', padding: '2px 8px', fontWeight: 700 }}>
                  CAPTURED
                </div>
              </>
            )}
          </div>

          {/* Buttons */}
          <div style={{ display: 'flex', gap: 10 }}>
            {!captured ? (
              <>
                <button className="btn-secondary" style={{ flex: 1, padding: '12px' }} onClick={onCancel}>✕ 취소</button>
                <button className="btn-primary" style={{ flex: 2, padding: '12px', fontSize: 16 }} onClick={handleCapture}>
                  📷 촬영
                </button>
              </>
            ) : (
              <>
                <button className="btn-warning" style={{ flex: 1, padding: '12px' }} onClick={() => setCaptured(false)}>
                  ↺ 다시 촬영
                </button>
                <button className="btn-green" style={{ flex: 2, padding: '12px', fontSize: 15 }} onClick={onUse}>
                  ✓ 이 사진 사용
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: ANALYZING
// ============================================================
function AnalyzingScreen({
  onComplete,
  previewUrl,
  previewType,
  fileName,
}: {
  onComplete: () => void
  previewUrl: string | null
  previewType: string
  fileName: string
}) {
  const [step, setStep] = useState(0)
  const [progressMessage, setProgressMessage] = useState('분석 시작 대기')

  // 부모에서 전달하는 실제 Python 진행률을 받기 위해
  // window 이벤트를 사용한다.
  useEffect(() => {
    const handler = (event: Event) => {
      const customEvent =
        event as CustomEvent<{
          step: number
          message: string
        }>

      const nextStep =
        Number(customEvent.detail?.step ?? 0)

      setStep(
        Math.max(
          0,
          Math.min(
            nextStep,
            ANALYSIS_STEPS.length
          )
        )
      )

      setProgressMessage(
        customEvent.detail?.message ||
        '처리 중...'
      )
    }

    window.addEventListener(
      'analysis-progress',
      handler
    )

    return () => {
      window.removeEventListener(
        'analysis-progress',
        handler
      )
    }
  }, [])

  const progress =
    Math.round(
      (step / ANALYSIS_STEPS.length) * 100
    )

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
      {/* Left: document preview */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', borderRight: '2px solid var(--hmi-border)' }}>
        <div className="section-header">■ 문서 미리보기</div>
        <div style={{
          flex: 1,
          background: '#dfe3ea',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0
        }}>
          <div style={{
            height: 34,
            background: '#f8fafc',
            borderBottom: '1px solid var(--hmi-border-light)',
            display: 'flex',
            alignItems: 'center',
            padding: '0 12px',
            fontSize: 11,
            color: '#475569',
            flexShrink: 0
          }}>
            선택 문서: <strong style={{ marginLeft: 6 }}>{fileName || '파일 없음'}</strong>
          </div>

          <div style={{
            flex: 1,
            minHeight: 0,
            padding: 14,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            {!previewUrl ? (
              <div style={{ color: '#64748b', fontSize: 14 }}>
                문서 미리보기를 준비 중입니다.
              </div>
            ) : previewType === 'application/pdf' ? (
              <iframe
                title="작업지시서 PDF 미리보기"
                src={previewUrl}
                style={{
                  width: '100%',
                  height: '100%',
                  border: '1px solid #94a3b8',
                  background: 'white'
                }}
              />
            ) : previewType.startsWith('image/') ? (
              <img
                src={previewUrl}
                alt="선택한 작업지시서"
                style={{
                  maxWidth: '100%',
                  maxHeight: '100%',
                  objectFit: 'contain',
                  border: '1px solid #94a3b8',
                  background: 'white',
                  boxShadow: '0 4px 20px rgba(0,0,0,0.12)'
                }}
              />
            ) : (
              <div style={{
                background: 'white',
                border: '1px solid #cbd5e1',
                padding: 24,
                textAlign: 'center',
                color: '#475569'
              }}>
                이 파일 형식은 브라우저 미리보기를 지원하지 않습니다.<br />
                <strong>{fileName}</strong>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Right: analysis progress */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div className="section-header">■ 작업지시서 분석 중</div>
        <div style={{ flex: 1, background: 'var(--hmi-work-bg)', padding: 24, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '20px 24px' }}>
            <div style={{ marginBottom: 20 }}>
              {ANALYSIS_STEPS.map((s, i) => {
                const done = i < step
                const active =
                  step < ANALYSIS_STEPS.length &&
                  i === step
                const pending = i >= step
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '9px 0', borderBottom: i < ANALYSIS_STEPS.length - 1 ? '1px solid #f3f4f6' : 'none' }}>
                    <div style={{
                      width: 24, height: 24, flexShrink: 0,
                      background: done ? 'var(--hmi-green)' : active ? 'var(--hmi-blue)' : '#d1d5db',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 12, fontWeight: 700, color: 'white'
                    }}>
                      {done ? '✓' : active ? <span className="spin" style={{ display: 'inline-block', width: 12, height: 12, border: '2px solid white', borderTopColor: 'transparent' }} /> : i + 1}
                    </div>
                    <span style={{
                      fontSize: 14, fontWeight: done ? 500 : active ? 700 : 400,
                      color: done ? 'var(--hmi-green-dark)' : active ? 'var(--hmi-blue)' : '#9ca3af'
                    }}>
                      {i + 1}. {s}
                    </span>
                    {done && <span style={{ marginLeft: 'auto', color: 'var(--hmi-green)', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>DONE</span>}
                    {active && <span className="blink" style={{ marginLeft: 'auto', color: 'var(--hmi-blue)', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>처리 중...</span>}
                  </div>
                )
              })}
            </div>
            <div style={{ marginTop: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6, fontSize: 13 }}>
                <span style={{ fontWeight: 600 }}>전체 진행률</span>
                <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, color: 'var(--hmi-blue)', fontSize: 15 }}>{progress}%</span>
              </div>
              <div className="progress-bar" style={{ height: 12 }}>
                <div className="progress-fill-blue" style={{ width: `${progress}%` }} />
              </div>
              <div style={{
                marginTop: 8,
                fontSize: 11,
                color: 'var(--hmi-text-muted)',
                fontFamily: 'JetBrains Mono, monospace'
              }}>
                {progressMessage}
              </div>
            </div>
          </div>
          {step >= ANALYSIS_STEPS.length && (
            <div style={{
              background: 'var(--hmi-green-bg)', border: '2px solid var(--hmi-green)',
              padding: '14px 20px', display: 'flex', alignItems: 'center', gap: 10
            }}>
              <span style={{ fontSize: 20 }}>✓</span>
              <span style={{ fontWeight: 700, color: 'var(--hmi-green)', fontSize: 15 }}>분석 완료 — 결과 화면으로 이동합니다...</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: REVIEW
// ============================================================
function ReviewScreen({
  items, isPaused, showStopConfirm,
  onStart, onCancelAuto, onEditSave
}: {
  items: WorkItem[]
  isPaused: boolean
  showStopConfirm: boolean
  onStart: () => void
  onCancelAuto: () => void
  onEditSave: (idx: number, item: WorkItem) => Promise<boolean>
}) {
  const [countdown, setCountdown] = useState(5)
  const [autoActive, setAutoActive] = useState(true)
  const [editIdx, setEditIdx] = useState<number | null>(null)
  const [hasEdited, setHasEdited] = useState(false)

  const allOk = items.every(i => i.status === '정상')
  const hasIssue = items.some(i => i.status === '확인 필요' || i.status === '재고 부족')

  useEffect(() => {
    if (!autoActive || !allOk || hasIssue || isPaused || showStopConfirm) return
    if (countdown <= 0) { onStart(); return }
    const t = setTimeout(() => setCountdown(c => c - 1), 1000)
    return () => clearTimeout(t)
  }, [countdown, autoActive, allOk, hasIssue, isPaused, showStopConfirm, onStart])

  const handleEdit = (idx: number) => {
    setEditIdx(idx)
    setAutoActive(false)
    setHasEdited(true)
    onCancelAuto()
  }

  const handleSave = async (
    item: WorkItem
  ): Promise<boolean> => {
    if (editIdx === null) {
      return false
    }

    const success = await onEditSave(
      editIdx,
      item
    )

    if (success) {
      setEditIdx(null)
    }

    return success
  }

  const statusColor = (s: WorkItem['status']) =>
    s === '정상' ? 'var(--hmi-green)' : s === '확인 필요' ? 'var(--hmi-yellow)' : 'var(--hmi-red)'
  const statusBg = (s: WorkItem['status']) =>
    s === '정상' ? 'var(--hmi-green-bg)' : s === '확인 필요' ? 'var(--hmi-yellow-bg)' : 'var(--hmi-red-bg)'

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div className="section-header" style={{ fontSize: 13 }}>
        ■ 작업지시서 분석 완료 — 결과 검토
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 16, background: 'var(--hmi-work-bg)', display: 'flex', flexDirection: 'column', gap: 14 }}>

        {/* Table */}
        <div style={{ background: 'white', border: '1px solid var(--hmi-border)' }}>
          <table className="hmi-table">
            <thead>
              <tr>
                {['No.', '품번', '품명', '규격/사양', '요청수량', '단위', '대상 Tray', '현재 재고', '인식 상태', '수정'].map(h => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item, i) => (
                <tr key={i}>
                  <td style={{ textAlign: 'center', fontWeight: 700 }}>{item.no}</td>
                  <td>{item.partNo}</td>
                  <td style={{ fontFamily: 'inherit', minWidth: 110 }}>{item.name}</td>
                  <td>{item.spec}</td>
                  <td style={{ textAlign: 'right', fontWeight: 700, fontSize: 15 }}>{item.qty}</td>
                  <td>{item.unit}</td>
                  <td style={{ color: 'var(--hmi-blue)', fontWeight: 600 }}>{item.tray}</td>
                  <td style={{ textAlign: 'right', fontWeight: 600 }}>{item.stock}</td>
                  <td>
                    <span style={{
                      background: statusBg(item.status), color: statusColor(item.status),
                      border: `1px solid ${statusColor(item.status)}`, padding: '2px 8px',
                      fontSize: 11, fontWeight: 700
                    }}>
                      {item.status}
                    </span>
                  </td>
                  <td>
                    <button className="btn-warning" style={{ padding: '3px 10px', fontSize: 11 }} onClick={() => handleEdit(i)}>
                      수정
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Status / action area */}
        {hasIssue ? (
          <div style={{
            background: 'var(--hmi-yellow-bg)', border: '2px solid var(--hmi-yellow-accent)',
            padding: '14px 20px', display: 'flex', alignItems: 'center', gap: 12
          }}>
            <span style={{ fontSize: 20 }}>⚠</span>
            <span style={{ fontWeight: 700, color: 'var(--hmi-yellow)', fontSize: 14 }}>
              확인 필요 항목을 수정해주세요. 수정 완료 전까지 작업을 시작할 수 없습니다.
            </span>
          </div>
        ) : allOk && autoActive && !hasEdited ? (
          <div style={{ background: 'var(--hmi-green-bg)', border: '2px solid var(--hmi-green)', padding: '16px 20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
              <span style={{ fontSize: 20, color: 'var(--hmi-green)' }}>✓</span>
              <span style={{ fontWeight: 700, color: 'var(--hmi-green)', fontSize: 15 }}>모든 항목 확인 완료</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
              <span style={{ fontSize: 13, color: 'var(--hmi-green-dark)' }}>
                {isPaused || showStopConfirm ? '일시정지됨 — 자동 시작 대기' : `${countdown}초 후 자동으로 작업을 시작합니다.`}
              </span>
              {!isPaused && !showStopConfirm && (
                <div style={{
                  width: 50, height: 50, border: '3px solid var(--hmi-green)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'JetBrains Mono, monospace', fontWeight: 900, fontSize: 22,
                  color: 'var(--hmi-green)'
                }}>
                  {countdown}
                </div>
              )}
              <button className="btn-warning" onClick={() => { setAutoActive(false); onCancelAuto() }}>
                자동 시작 취소
              </button>
            </div>
          </div>
        ) : allOk && (!autoActive || hasEdited) ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ flex: 1, background: 'var(--hmi-blue-bg)', border: '1px solid var(--hmi-blue)', padding: '10px 16px', fontSize: 13, color: 'var(--hmi-blue)' }}>
              {hasEdited ? '수정 완료. 작업자가 직접 작업을 시작해주세요.' : '자동 시작이 취소되었습니다. 작업자가 직접 시작해주세요.'}
            </div>
            <button className="btn-green" style={{ padding: '12px 24px', fontSize: 15, whiteSpace: 'nowrap' }} onClick={onStart}>
              ✓ 수정 완료 및 작업 시작
            </button>
          </div>
        ) : null}
      </div>

      {editIdx !== null && (
        <EditItemModal item={items[editIdx]} onCancel={() => setEditIdx(null)} onSave={handleSave} />
      )}
    </div>
  )
}

// ============================================================
// SCREEN: TRAY MOVING
// ============================================================
function TrayMovingScreen({
  item, itemIndex, totalItems, trays, isPaused, showStop,
  onArrived, onPause, onStop
}: {
  item: WorkItem; itemIndex: number; totalItems: number; trays: TrayData[]
  isPaused: boolean; showStop: boolean
  onArrived: () => void; onPause: () => void; onStop: () => void
}) {
  const stages = [
    '대기',
    'Tray 탐색',
    'ArUco 확인',
    'Tray 접근',
    'Tray 이동',
    '작업 위치 이동',
    '도착'
  ]

  const [stageIdx, setStageIdx] =
    useState(0)

  const [xPos, setXPos] =
    useState(0)

  const [zPos, setZPos] =
    useState(0)

  const [arucoMatch, setArucoMatch] =
    useState(false)

  const [stageStatus, setStageStatus] =
    useState<any>(null)

  const startPosRef =
    useRef<{
      x: number
      z: number
    } | null>(null)

  const targetTrayId =
    Number(
      item.tray.match(/\d+/)?.[0]
      ?? -1
    )

  const targetX =
    Number(
      stageStatus?.current_target?.x_mm
      ?? 0
    )

  const targetZ =
    Number(
      stageStatus?.current_target?.z_mm
      ?? 0
    )


  // ------------------------------------------------------------
  // UI 단계 표시는 기존 흐름 유지
  // 도착 판정만 실제 Stage 상태로 처리
  // ------------------------------------------------------------
  useEffect(() => {
    if (
      isPaused ||
      showStop ||
      stageIdx >= 5
    ) {
      return
    }

    const delays = [
      800,
      1200,
      1400,
      1000,
      1400,
    ]

    const timer =
      setTimeout(() => {
        const next =
          stageIdx + 1

        setStageIdx(next)

        if (next >= 2) {
          setArucoMatch(true)
        }
      }, delays[stageIdx] ?? 1000)

    return () =>
      clearTimeout(timer)

  }, [
    isPaused,
    showStop,
    stageIdx
  ])


  // ------------------------------------------------------------
  // 실제 Backend Stage 상태 조회
  // Mock/STM32 어느 쪽이든 동일 API 사용
  // ------------------------------------------------------------
  useEffect(() => {
    let cancelled = false

    const pollStageStatus =
      async () => {
        try {
          const response =
            await fetch(
              'http://127.0.0.1:8000/stage/status'
            )

          if (!response.ok) {
            throw new Error(
              `Stage status 오류: ${response.status}`
            )
          }

          const status =
            await response.json()

          if (cancelled) {
            return
          }

          setStageStatus(
            status
          )

          const nextX =
            Number(
              status?.position?.x
              ?? 0
            )

          const nextZ =
            Number(
              status?.position?.z
              ?? 0
            )

          setXPos(
            Math.round(
              nextX * 1000
            ) / 1000
          )

          setZPos(
            Math.round(
              nextZ * 1000
            ) / 1000
          )

          // 이동 목표가 처음 잡힌 순간의
          // 시작 위치를 진행률 계산 기준으로 저장
          if (
            !startPosRef.current &&
            status?.current_target
          ) {
            startPosRef.current = {
              x: nextX,
              z: nextZ,
            }
          }

        } catch (error) {
          console.error(
            '[STAGE] status 조회 실패:',
            error
          )
        }
      }

    pollStageStatus()

    const timer =
      setInterval(
        pollStageStatus,
        250
      )

    return () => {
      cancelled = true
      clearInterval(timer)
    }

  }, [])


  // ------------------------------------------------------------
  // 실제 도착 판정
  //
  // STM32StageAdapter:
  //   이동 완료 후 current_tray 설정 + READY
  //
  // MockStageAdapter:
  //   이동 완료 후 current_tray 설정 + moving=false
  // ------------------------------------------------------------
  useEffect(() => {
    if (
      !stageStatus ||
      isPaused ||
      showStop
    ) {
      return
    }

    const currentTray =
      Number(
        stageStatus.current_tray
      )

    const ready =
      stageStatus.state === 'READY'
      ||
      (
        stageStatus.mock === true
        &&
        stageStatus.moving === false
      )

    const arrived =
      Boolean(
        stageStatus.current_target
      )
      &&
      currentTray === targetTrayId
      &&
      ready
      &&
      !stageStatus.estop
      &&
      !stageStatus.estopped

    if (
      arrived &&
      stageIdx !== 6
    ) {
      setStageIdx(6)
    }

  }, [
    stageStatus,
    targetTrayId,
    isPaused,
    showStop,
    stageIdx
  ])


  // ------------------------------------------------------------
  // 실제 도착 확인 후 Workflow 다음 단계
  // ------------------------------------------------------------
  useEffect(() => {
    if (
      isPaused ||
      showStop ||
      stageIdx !== 6
    ) {
      return
    }

    const timer =
      setTimeout(
        () => {
          onArrived()
        },
        600
      )

    return () =>
      clearTimeout(timer)

  }, [
    isPaused,
    showStop,
    stageIdx,
    onArrived
  ])


  const hasStageTarget =
    Boolean(
      stageStatus?.current_target
    )

  const axisIsHomed = (
    axis: 'x' | 'z'
  ) => {
    if (!stageStatus) {
      return false
    }

    if (stageStatus.mock === true) {
      return stageStatus.homed === true
    }

    return (
      stageStatus?.homed?.[axis]
      === true
    )
  }

  const axisDisplayState = (
    axis: 'x' | 'z',
    value: number,
    target: number
  ) => {
    if (!stageStatus) {
      return 'WAIT'
    }

    if (
      stageStatus.mock !== true
      &&
      stageStatus.connected !== true
    ) {
      return 'DISCONNECTED'
    }

    if (
      stageStatus.estop === true
      ||
      stageStatus.estopped === true
      ||
      stageStatus.state === 'ERROR'
      ||
      stageStatus.state === 'ESTOPPED'
    ) {
      return 'ERROR'
    }

    if (!axisIsHomed(axis)) {
      return 'NOT HOMED'
    }

    if (!hasStageTarget) {
      return 'NO TARGET'
    }

    if (
      Math.abs(
        value - target
      ) <= 0.2
    ) {
      return 'READY'
    }

    return 'MOVING'
  }


  const axisProgress = (
    value: number,
    target: number,
    startValue: number
  ) => {
    const total =
      Math.abs(
        target - startValue
      )

    if (total < 0.001) {
      return 100
    }

    const remaining =
      Math.abs(
        target - value
      )

    return Math.max(
      0,
      Math.min(
        100,
        (
          1 -
          remaining / total
        ) * 100
      )
    )
  }


  const trayStatuses = trays.map(t => ({
    ...t,
    highlight: t.id === item.tray
  }))

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Title bar */}
      <div style={{
        background: 'var(--hmi-navy)', color: 'white', padding: '8px 16px',
        display: 'flex', alignItems: 'center', gap: 12, borderBottom: '2px solid var(--hmi-blue-light)', flexShrink: 0
      }}>
        <span style={{ fontWeight: 900, fontSize: 16, letterSpacing: '0.1em', fontFamily: 'JetBrains Mono, monospace' }}>TRAY MOVING</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: '#93c5fd', fontFamily: 'JetBrains Mono, monospace' }}>품목 {itemIndex + 1} / {totalItems}</span>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Left: tray list */}
        <div style={{ flex: '0 0 26%', borderRight: '1px solid var(--hmi-border)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ background: '#1e3a5f', color: 'white', padding: '6px 12px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em' }}>
            TRAY STORAGE
          </div>
          <div style={{ flex: 1, overflow: 'auto', background: 'var(--hmi-work-bg)', padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {trayStatuses.map(t => (
              <div key={t.id} style={{
                border: t.highlight ? '2px solid var(--hmi-blue-light)' : '1px solid var(--hmi-border-light)',
                background: t.highlight ? '#e8f0fe' : 'white',
                padding: '6px 10px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: 700, fontSize: 12, fontFamily: 'JetBrains Mono, monospace' }}>{t.id}</span>
                  {t.highlight && <span className={stageIdx >= 6 ? 'badge-green' : 'badge-blue'} style={{ fontSize: 9, padding: '1px 5px' }}>
                    {stageIdx >= 6 ? 'ARRIVED' : 'MOVING'}
                  </span>}
                </div>
                <div style={{ fontSize: 11, color: '#374151', marginTop: 2 }}>{t.name} <span style={{ fontFamily: 'JetBrains Mono, monospace', color: '#6b7280' }}>{t.spec}</span></div>
              </div>
            ))}
          </div>
        </div>

        {/* Center: status */}
        <div style={{ flex: '0 0 38%', borderRight: '1px solid var(--hmi-border)', display: 'flex', flexDirection: 'column', background: 'var(--hmi-work-bg)' }}>
          <div style={{ background: '#1e3a5f', color: 'white', padding: '6px 12px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em' }}>
            현재 대상 품목
          </div>
          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 10, flex: 1, overflow: 'auto' }}>
            {/* Item info */}
            <div style={{ background: 'white', border: '2px solid var(--hmi-blue)', padding: '12px 16px' }}>
              <div style={{ fontSize: 20, fontWeight: 800, color: '#111827' }}>{item.name}</div>
              <div style={{ fontSize: 14, color: '#374151', fontFamily: 'JetBrains Mono, monospace' }}>{item.spec}</div>
              <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
                <div>
                  <span style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>요청 수량</span>
                  <div style={{ fontSize: 22, fontWeight: 900, color: 'var(--hmi-blue)', fontFamily: 'JetBrains Mono, monospace' }}>{item.qty}</div>
                </div>
                <div>
                  <span style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>대상 Tray</span>
                  <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--hmi-navy)', fontFamily: 'JetBrains Mono, monospace' }}>{item.tray}</div>
                </div>
              </div>
            </div>

            {/* Stage position */}
            <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '10px 14px' }}>
              <div style={{ fontSize: 11, fontWeight: 700, color: '#374151', marginBottom: 8, letterSpacing: '0.05em' }}>STAGE POSITION</div>
              {[
                {
                  axis: 'x' as const,
                  label: 'X Axis',
                  val: xPos,
                  target: targetX,
                  start: startPosRef.current?.x ?? xPos,
                },
                {
                  axis: 'z' as const,
                  label: 'Z Axis',
                  val: zPos,
                  target: targetZ,
                  start: startPosRef.current?.z ?? zPos,
                },
              ].map(ax => (
                <div key={ax.label} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                  <span style={{ fontSize: 12, color: 'var(--hmi-text-muted)', width: 60 }}>{ax.label}</span>
                  <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 15, width: 70, color: 'var(--hmi-blue)' }}>
                    {ax.val} <span style={{ fontSize: 11, fontWeight: 400 }}>mm</span>
                  </span>
                  <div style={{ flex: 1, height: 6, background: '#e5e7eb' }}>
                    <div style={{ height: '100%', width: `${
                        hasStageTarget
                          ? axisProgress(
                              ax.val,
                              ax.target,
                              ax.start
                            )
                          : 0
                      }%`, background: 'var(--hmi-blue)', transition: 'width 0.1s' }} />
                  </div>
                  <span className={`badge-${
                    axisDisplayState(
                      ax.axis,
                      ax.val,
                      ax.target
                    ) === 'READY'
                      ? 'green'
                      : 'blue'
                  }`} style={{ fontSize: 9, padding: '1px 5px' }}>
                    {axisDisplayState(
                      ax.axis,
                      ax.val,
                      ax.target
                    )}
                  </span>
                </div>
              ))}
            </div>

            {/* Stage steps */}
            <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '10px 14px' }}>
              {stages.map((s, i) => (
                <div key={i} style={{
                  display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0',
                  borderBottom: i < stages.length - 1 ? '1px solid #f3f4f6' : 'none'
                }}>
                  <span style={{ width: 16, height: 16, background: i < stageIdx ? 'var(--hmi-green)' : i === stageIdx ? 'var(--hmi-blue)' : '#d1d5db', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 10, color: 'white', fontWeight: 700 }}>
                    {i < stageIdx ? '✓' : ''}
                  </span>
                  <span style={{ fontSize: 12, color: i < stageIdx ? 'var(--hmi-green-dark)' : i === stageIdx ? 'var(--hmi-blue)' : '#9ca3af', fontWeight: i === stageIdx ? 700 : 400 }}>
                    {s}
                  </span>
                  {i === stageIdx && <span className="blink" style={{ fontSize: 10, color: 'var(--hmi-blue)', fontFamily: 'JetBrains Mono, monospace', marginLeft: 'auto' }}>●</span>}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Right: camera */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: 'var(--hmi-work-bg)' }}>
          <div style={{ background: '#1e3a5f', color: 'white', padding: '6px 12px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em' }}>
            그리퍼 카메라 LIVE
          </div>
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: 12, gap: 10, overflow: 'auto' }}>
            {/* Camera view */}
            <div className="camera-live" style={{ flex: 1, position: 'relative', border: '2px solid #2d4a70', minHeight: 200 }}>
              <div style={{ position: 'absolute', top: 8, left: 8, color: 'white', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>
                CAM-01 LIVE
              </div>
              <span className="status-dot blink" style={{ background: '#ef4444', position: 'absolute', top: 10, right: 8 }} />
              {/* ArUco marker simulation */}
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {arucoMatch ? (
                  <div style={{ position: 'relative' }}>
                    <div style={{ width: 80, height: 80, background: 'white', border: '3px solid #22c55e', display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 3, padding: 6 }}>
                      {[1,0,1,0,1,0,1,0,1].map((v,i) => <div key={i} style={{ background: v ? '#111' : 'white' }} />)}
                    </div>
                    <div style={{ position: 'absolute', inset: -8, border: '2px solid #22c55e', opacity: 0.7 }} />
                  </div>
                ) : (
                  <div style={{ width: 80, height: 80, border: '2px dashed rgba(255,255,255,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <span className="blink" style={{ color: 'rgba(255,255,255,0.5)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>SEARCHING</span>
                  </div>
                )}
              </div>
            </div>

            {/* ArUco info */}
            <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '10px 12px', fontSize: 12 }}>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                {[
                  { label: 'Target Tray', val: item.tray },
                  { label: 'Target ArUco ID', val: item.tray.split(' ')[1] },
                  { label: 'Detected ID', val: arucoMatch ? item.tray.split(' ')[1] : '--' },
                  { label: 'Status', val: arucoMatch ? 'MATCH' : 'SEARCHING' },
                ].map(r => (
                  <div key={r.label}>
                    <div style={{ fontSize: 10, color: 'var(--hmi-text-muted)' }}>{r.label}</div>
                    <div style={{
                      fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 13,
                      color: r.label === 'Status' ? (arucoMatch ? 'var(--hmi-green)' : 'var(--hmi-blue)') : '#111827'
                    }}>{r.val}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Buttons */}
            <div style={{ display: 'flex', gap: 6 }}>
              <button className="btn-warning" style={{ flex: 1, padding: '8px', fontSize: 12 }} onClick={onPause}>
                ⏸ 일시 정지
              </button>
              <button className="btn-danger" style={{ flex: 1, padding: '8px', fontSize: 12 }} onClick={onStop}>
                ■ 작업 중지
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: PICKING
// ============================================================

// Generates stable random bounding box positions for detected parts
function makeBoxes(count: number, seed: number) {
  const boxes: { x: number; y: number; w: number; h: number }[] = []
  for (let i = 0; i < count; i++) {
    const pseudo = Math.sin(seed * 9301 + i * 49297 + 233720) * 0.5 + 0.5
    const pseudo2 = Math.sin(seed * 7919 + i * 31337 + 104729) * 0.5 + 0.5
    boxes.push({
      x: 8 + (pseudo * 72),
      y: 8 + (pseudo2 * 68),
      w: 10 + (Math.sin(i * 17) * 0.5 + 0.5) * 8,
      h: 10 + (Math.cos(i * 13) * 0.5 + 0.5) * 8,
    })
  }
  return boxes
}

function PickingScreen({
  item, itemIndex, totalItems, isPaused, showStop,
  onAutoVerify, onManualVerify, onPause, onStop
}: {
  item: WorkItem; itemIndex: number; totalItems: number
  isPaused: boolean; showStop: boolean
  onAutoVerify: () => void; onManualVerify: () => void
  onPause: () => void; onStop: () => void
}) {
  // count goes up slowly — simulates camera detecting as worker drops parts in tray
  // interval is longer to feel realistic, not a fake timer
  const [count, setCount] = useState(0)
  const [stable, setStable] = useState(false)
  const [stableCountdown, setStableCountdown] = useState(3)
  const pausedRef = useRef(isPaused)
  const stopRef = useRef(showStop)
  const countRef = useRef(0)

  useEffect(() => { pausedRef.current = isPaused }, [isPaused])
  useEffect(() => { stopRef.current = showStop }, [showStop])

  // Simulate YOLO detection: a new part is "detected" every ~1.8s (realistic picking pace)
  useEffect(() => {
    if (count >= item.qty) return
    const t = setTimeout(() => {
      if (pausedRef.current || stopRef.current) return
      setCount(c => {
        const next = c + 1
        countRef.current = next
        return next
      })
    }, 1600 + Math.random() * 800)
    return () => clearTimeout(t)
  }, [count, item.qty])

  useEffect(() => {
    if (count >= item.qty) setStable(true)
  }, [count, item.qty])

  useEffect(() => {
    if (!stable || isPaused || showStop) return
    if (stableCountdown <= 0) { onAutoVerify(); return }
    const t = setTimeout(() => setStableCountdown(c => c - 1), 1000)
    return () => clearTimeout(t)
  }, [stable, stableCountdown, isPaused, showStop, onAutoVerify])

  const pct = Math.min(Math.round((count / item.qty) * 100), 100)
  const reached = count >= item.qty
  const boxes = makeBoxes(count, itemIndex * 100 + count)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* Title */}
      <div style={{
        background: 'var(--hmi-navy)', color: 'white', padding: '8px 16px',
        display: 'flex', alignItems: 'center', gap: 12, borderBottom: '2px solid var(--hmi-blue-light)', flexShrink: 0
      }}>
        <span style={{ fontWeight: 900, fontSize: 16, letterSpacing: '0.1em' }}>작업자 피킹</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: '#93c5fd', fontFamily: 'JetBrains Mono, monospace' }}>품목 {itemIndex + 1} / {totalItems}</span>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* LEFT: instruction panel */}
        <div style={{ flex: '0 0 260px', borderRight: '2px solid var(--hmi-border)', background: 'white', display: 'flex', flexDirection: 'column' }}>
          <div style={{ background: 'var(--hmi-navy)', color: 'white', padding: '10px 14px' }}>
            <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 800, fontSize: 16, letterSpacing: '0.1em' }}>{item.tray}</div>
            <div style={{ fontSize: 10, color: '#93c5fd', marginTop: 2 }}>작업 위치 도착</div>
          </div>
          <div style={{ padding: '16px 14px', flex: 1, display: 'flex', flexDirection: 'column', gap: 14 }}>
            {/* Part info */}
            <div>
              <div style={{ fontSize: 22, fontWeight: 800, color: '#111827', lineHeight: 1.2 }}>{item.name}</div>
              <div style={{ fontSize: 14, fontFamily: 'JetBrains Mono, monospace', color: '#374151', marginTop: 2 }}>{item.spec}</div>
            </div>

            {/* Big target qty */}
            <div style={{ textAlign: 'center', padding: '12px 0', borderTop: '1px solid var(--hmi-border-light)', borderBottom: '1px solid var(--hmi-border-light)' }}>
              <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', letterSpacing: '0.06em', marginBottom: 4 }}>피킹 목표 수량</div>
              <div style={{ fontSize: 80, fontWeight: 900, color: 'var(--hmi-navy)', fontFamily: 'JetBrains Mono, monospace', lineHeight: 1 }}>
                {item.qty}
              </div>
              <div style={{ fontSize: 12, color: 'var(--hmi-text-muted)' }}>EA</div>
            </div>

            {/* Instruction */}
            <div style={{ background: '#eff6ff', border: '1px solid #93c5fd', borderLeft: '4px solid var(--hmi-blue)', padding: '10px 12px', fontSize: 13, color: '#1e3a8a', lineHeight: 1.5 }}>
              {item.tray}에서<br />
              <strong>{item.name} {item.spec}</strong>을<br />
              <span style={{ fontWeight: 800, fontSize: 15 }}>{item.qty}개</span> 피킹하세요.
            </div>

            <div style={{ flex: 1 }} />

            {/* Exception buttons */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ fontSize: 10, color: 'var(--hmi-text-muted)', letterSpacing: '0.05em', marginBottom: 2 }}>예외 조작</div>
              <button className="btn-warning" style={{ width: '100%', padding: '9px', fontSize: 13 }} onClick={onPause}>⏸ 일시 정지</button>
              <button className="btn-secondary" style={{ width: '100%', padding: '9px', fontSize: 12 }} onClick={onManualVerify}>수동 확인</button>
              <button className="btn-danger" style={{ width: '100%', padding: '9px', fontSize: 13 }} onClick={onStop}>■ 작업 중지</button>
            </div>
          </div>
        </div>

        {/* CENTER: YOLO camera — main focus */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#0a0f1a' }}>
          <div style={{ background: '#0f1929', borderBottom: '1px solid #1e3a5f', padding: '6px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
            <span className="status-dot blink" style={{ background: '#ef4444' }} />
            <span style={{ color: '#94a3b8', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>CAM-02  YOLO DETECTION  LIVE</span>
            <div style={{ flex: 1 }} />
            <span style={{ color: '#4ade80', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>MODEL: yolov8n-parts  FPS: 28</span>
          </div>

          {/* Camera area */}
          <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
            {/* Tray background simulation */}
            <div style={{ position: 'absolute', inset: 0, background: '#111827' }}>
              {/* Tray grid lines */}
              <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.12 }}>
                <defs>
                  <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
                    <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#60a5fa" strokeWidth="0.5"/>
                  </pattern>
                </defs>
                <rect width="100%" height="100%" fill="url(#grid)" />
              </svg>

              {/* Tray outline */}
              <div style={{
                position: 'absolute', inset: '12%',
                border: '2px solid rgba(96,165,250,0.3)',
                background: 'rgba(15,23,42,0.6)'
              }} />

              {/* YOLO bounding boxes for detected parts */}
              {boxes.map((b, i) => (
                <div key={i} style={{
                  position: 'absolute',
                  left: `${12 + b.x * 0.76}%`,
                  top: `${12 + b.y * 0.76}%`,
                  width: `${b.w}%`,
                  height: `${b.h}%`,
                  border: '2px solid #4ade80',
                  boxShadow: '0 0 6px rgba(74,222,128,0.5)',
                }}>
                  <div style={{
                    position: 'absolute', top: -16, left: 0,
                    background: '#16a34a', color: 'white',
                    fontSize: 9, padding: '1px 4px',
                    fontFamily: 'JetBrains Mono, monospace', whiteSpace: 'nowrap'
                  }}>
                    {item.partNo} {(0.92 + i * 0.007).toFixed(2)}
                  </div>
                </div>
              ))}

              {/* Scan line */}
              <div style={{
                position: 'absolute', left: 0, right: 0, height: 2,
                background: 'rgba(74,222,128,0.4)',
                animation: 'scan-line 2.5s linear infinite'
              }} />

              {/* Corner markers */}
              {[
                { top: '12%', left: '12%' },
                { top: '12%', right: '12%' },
                { bottom: '12%', left: '12%' },
                { bottom: '12%', right: '12%' },
              ].map((pos, i) => (
                <div key={i} style={{
                  position: 'absolute', ...pos,
                  width: 16, height: 16,
                  borderTop: (pos as any).top ? '2px solid #60a5fa' : 'none',
                  borderBottom: (pos as any).bottom !== undefined ? '2px solid #60a5fa' : 'none',
                  borderLeft: (pos as any).left ? '2px solid #60a5fa' : 'none',
                  borderRight: (pos as any).right !== undefined ? '2px solid #60a5fa' : 'none',
                }} />
              ))}
            </div>

            {/* Count overlay — bottom bar */}
            <div style={{
              position: 'absolute', bottom: 0, left: 0, right: 0,
              background: 'rgba(7,17,31,0.92)', borderTop: '1px solid #1e3a5f',
              padding: '10px 18px', display: 'flex', alignItems: 'center', gap: 20
            }}>
              {/* Live count */}
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
                <span style={{ color: '#64748b', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>DETECTED</span>
                <span style={{
                  fontFamily: 'JetBrains Mono, monospace', fontWeight: 900,
                  fontSize: 42, lineHeight: 1,
                  color: reached ? '#4ade80' : '#60a5fa',
                  transition: 'color 0.3s'
                }}>{count}</span>
                <span style={{ color: '#475569', fontSize: 22, fontFamily: 'JetBrains Mono, monospace' }}>/</span>
                <span style={{ color: '#94a3b8', fontSize: 26, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>{item.qty}</span>
              </div>

              {/* Progress bar */}
              <div style={{ flex: 1 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontSize: 10, color: '#64748b', fontFamily: 'JetBrains Mono, monospace' }}>진행률</span>
                  <span style={{ fontSize: 10, color: '#94a3b8', fontFamily: 'JetBrains Mono, monospace' }}>{pct}%</span>
                </div>
                <div style={{ height: 8, background: '#1e293b' }}>
                  <div style={{ height: '100%', width: `${pct}%`, background: reached ? '#16a34a' : '#2563eb', transition: 'width 0.4s, background 0.3s' }} />
                </div>
              </div>

              {/* Stability / status */}
              <div style={{ minWidth: 180 }}>
                {!reached ? (
                  <div style={{ color: '#60a5fa', fontSize: 12, fontFamily: 'JetBrains Mono, monospace', display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span className="blink">●</span> 피킹 대기 중...
                  </div>
                ) : stable && stableCountdown > 0 ? (
                  <div>
                    <div style={{ color: '#fcd34d', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', marginBottom: 4 }}>
                      ✓ 목표 수량 도달 — 안정성 확인 중
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <div style={{ flex: 1, height: 4, background: '#1e293b' }}>
                        <div style={{ height: '100%', width: `${((3 - stableCountdown) / 3) * 100}%`, background: '#f59e0b', transition: 'width 1s linear' }} />
                      </div>
                      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 900, fontSize: 20, color: '#fcd34d' }}>{stableCountdown}</span>
                    </div>
                  </div>
                ) : stable && stableCountdown <= 0 ? (
                  <div style={{ color: '#4ade80', fontSize: 12, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>
                    ✓ 검증 화면으로 이동 중...
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </div>

      </div>
    </div>
  )
}

// ============================================================
// SCREEN: VERIFICATION (카메라 수량 확인 — 품목별, 빠른 자동 전환)
// ============================================================
function VerificationScreen({
  item, itemIndex, totalItems, isPaused, showStop, mode,
  onNext, onPause, onStop, onVisionCheck
}: {
  item: WorkItem; itemIndex: number; totalItems: number
  isPaused: boolean; showStop: boolean
  mode: 'AUTO' | 'MANUAL'
  onNext: () => void
  onPause: () => void
  onStop: () => void
  onVisionCheck: (item: WorkItem) => Promise<any>
}) {
  const [confirmed, setConfirmed] = useState(false)
  const [countdown, setCountdown] = useState(2)
  const [detectedQuantity, setDetectedQuantity] =
    useState<number | null>(null)
  const [visionError, setVisionError] = useState('')
  const visionPromiseRef = useRef<{
    key: string
    promise: Promise<any>
  } | null>(null)
  const pausedRef = useRef(isPaused)
  const stopRef = useRef(showStop)
  useEffect(() => { pausedRef.current = isPaused }, [isPaused])
  useEffect(() => { stopRef.current = showStop }, [showStop])

  // AUTO: Python Vision API가 실제 검출 결과를 반환해야 MATCH.
  // MANUAL: 사람이 직접 확인 완료 버튼을 누를 때까지 대기.
  useEffect(() => {
    let cancelled = false

    const runVisionCheck = async () => {
      setCountdown(2)
      setVisionError('')

      if (mode === 'MANUAL') {
        setConfirmed(false)
        setDetectedQuantity(null)
        visionPromiseRef.current = null
        return
      }

      const requestKey =
        `${itemIndex}-${item.partNo}-${item.qty}-${mode}`

      setConfirmed(false)
      setDetectedQuantity(null)

      // React 개발모드 StrictMode에서는 effect가
      // setup -> cleanup -> setup 순서로 한 번 더 실행될 수 있다.
      //
      // 이전 코드는 첫 요청을 시작한 뒤 cleanup이 되면
      // 두 번째 effect가 "이미 요청함"이라고 그냥 return해서
      // 결과를 아무도 반영하지 못하고 SCANNING에서 멈출 수 있었다.
      //
      // 이제는 같은 요청이면 "return"하지 않고
      // 이미 진행 중인 동일 Promise를 재사용한다.
      let visionPromise: Promise<any>

      if (
        visionPromiseRef.current?.key ===
        requestKey
      ) {
        visionPromise =
          visionPromiseRef.current.promise
      } else {
        visionPromise =
          onVisionCheck(item)

        visionPromiseRef.current = {
          key: requestKey,
          promise: visionPromise,
        }
      }

      const result =
        await visionPromise

      if (cancelled) {
        return
      }

      const detected =
        Number(
          result.detected_quantity ?? 0
        )

      setDetectedQuantity(detected)

      if (
        result.success &&
        result.matched
      ) {
        setConfirmed(true)
      } else {
        setConfirmed(false)
        setVisionError(
          `수량 불일치 — 검출 ${detected} / 요청 ${item.qty}`
        )
      }
    }

    runVisionCheck()

    return () => {
      cancelled = true
    }
  }, [
    mode,
    item.partNo,
    item.qty
  ]) // eslint-disable-line

  useEffect(() => {
    if (mode === 'MANUAL') return
    if (!confirmed || isPaused || showStop) return

    if (countdown <= 0) {
      onNext()
      return
    }

    const t = setTimeout(
      () => setCountdown(c => c - 1),
      1000
    )

    return () => clearTimeout(t)
  }, [mode, confirmed, countdown, isPaused, showStop, onNext])

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        background: 'var(--hmi-navy)', color: 'white', padding: '8px 16px',
        display: 'flex', alignItems: 'center', gap: 12, borderBottom: '2px solid var(--hmi-blue-light)', flexShrink: 0
      }}>
        <span style={{ fontWeight: 900, fontSize: 16, letterSpacing: '0.1em' }}>카메라 수량 확인</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: '#93c5fd', fontFamily: 'JetBrains Mono, monospace' }}>품목 {itemIndex + 1} / {totalItems}</span>
      </div>

      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--hmi-work-bg)', padding: 24 }}>
        <div style={{ display: 'flex', gap: 20, width: '100%', maxWidth: 900 }}>

          {/* Camera view */}
          <div style={{ flex: '0 0 380px' }}>
            <div style={{ background: '#1e3a5f', color: 'white', padding: '6px 14px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em', display: 'flex', alignItems: 'center', gap: 8 }}>
              CAM-02  CAMERA INSPECTION
              <span className="status-dot blink" style={{ background: '#ef4444', marginLeft: 4 }} />
              <span style={{ fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>LIVE</span>
            </div>
            <div className="camera-live" style={{ height: 260, position: 'relative', border: '1px solid #2d4a70' }}>
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, justifyContent: 'center', maxWidth: 200 }}>
                  {Array.from({ length: item.qty }).map((_, i) => (
                    <div key={i} style={{
                      width: 16, height: 16,
                      background: (mode === 'AUTO' && confirmed) ? 'rgba(74,222,128,0.85)' : 'rgba(96,165,250,0.5)',
                      border: `1px solid ${(mode === 'AUTO' && confirmed) ? '#22c55e' : '#60a5fa'}`,
                      transform: `rotate(${i * 17}deg)`,
                      transition: 'background 0.3s, border 0.3s'
                    }} />
                  ))}
                </div>
              </div>
              {mode === 'AUTO' && confirmed && (
                <div style={{ position: 'absolute', top: 8, left: 8, background: '#16a34a', color: 'white', fontSize: 10, padding: '2px 8px', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>
                  ✓ COUNT CONFIRMED
                </div>
              )}
              {mode === 'AUTO' && !confirmed && (
                <div className="blink" style={{ position: 'absolute', top: 8, left: 8, color: '#60a5fa', fontSize: 10, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>
                  SCANNING...
                </div>
              )}
            </div>
          </div>

          {/* Result panel */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ background: 'white', border: '1px solid var(--hmi-border)', padding: '14px 18px' }}>
              <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 12, letterSpacing: '0.05em', fontWeight: 700 }}>YOLO 검출 결과</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                {[
                  { label: '품목', val: item.name },
                  { label: '규격', val: item.spec },
                  { label: '요청 수량', val: `${item.qty} EA` },
                  { label: '검출 수량', val: `${detectedQuantity ?? '-'} EA` },
                ].map(r => (
                  <div key={r.label} style={{ background: '#f8f9fa', border: '1px solid var(--hmi-border-light)', padding: '8px 10px' }}>
                    <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>{r.label}</div>
                    <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 14 }}>{r.val}</div>
                  </div>
                ))}
              </div>
            </div>

            <div style={{
              background: confirmed ? 'var(--hmi-green-bg)' : '#f0f9ff',
              border: `2px solid ${confirmed ? 'var(--hmi-green)' : 'var(--hmi-blue)'}`,
              padding: '14px 18px'
            }}>
              {visionError && mode === 'AUTO' && (
                <div style={{
                  marginBottom: 10,
                  padding: '8px 10px',
                  background: '#fff7ed',
                  border: '1px solid #fdba74',
                  color: '#9a3412',
                  fontSize: 12,
                  fontWeight: 700
                }}>
                  {visionError}
                </div>
              )}

              {mode === 'MANUAL' ? (
                <div>
                  <div style={{ fontWeight: 800, fontSize: 15, color: '#92400e', marginBottom: 6 }}>
                    수동 확인 모드
                  </div>
                  <div style={{ fontSize: 12, color: 'var(--hmi-text-muted)', marginBottom: 12 }}>
                    작업자가 품목과 수량을 직접 확인한 뒤 아래 확인 완료 버튼을 누르세요.
                    시간 제한 없이 이 화면에서 대기합니다.
                  </div>
                  <button
                    className="btn-success"
                    style={{ width: '100%', padding: '11px', fontSize: 14, fontWeight: 800 }}
                    onClick={onNext}
                    disabled={isPaused || showStop}
                  >
                    ✓ 수동 확인 완료
                  </button>
                </div>
              ) : !confirmed ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--hmi-blue)' }}>
                  <div className="spin" style={{ width: 18, height: 18, border: '2px solid var(--hmi-blue)', borderTopColor: 'transparent', flexShrink: 0 }} />
                  <span style={{ fontWeight: 700 }}>카메라 수량 확인 중...</span>
                </div>
              ) : (
                <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
                    <span style={{ fontSize: 20, color: 'var(--hmi-green)' }}>✓</span>
                    <div>
                      <div style={{ fontWeight: 700, fontSize: 15, color: 'var(--hmi-green)' }}>수량 확인 완료 — MATCH</div>
                      <div style={{ fontSize: 12, color: 'var(--hmi-text-muted)' }}>
                        {itemIndex + 1 < totalItems
                          ? `다음 품목 (${itemIndex + 2}/${totalItems})으로 이동합니다...`
                          : '모든 품목 피킹 완료 — 최종 무게 검증으로 이동합니다...'}
                      </div>
                    </div>
                    {!isPaused && !showStop && (
                      <span style={{ marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace', fontWeight: 900, fontSize: 24, color: 'var(--hmi-green)' }}>
                        {countdown > 0 ? countdown : ''}
                      </span>
                    )}
                  </div>
                </>
              )}
            </div>

            <div style={{ display: 'flex', gap: 8, marginTop: 'auto' }}>
              <button className="btn-warning" style={{ flex: 1, padding: '9px' }} onClick={onPause}>⏸ 일시 정지</button>
              <button className="btn-danger" style={{ flex: 1, padding: '9px' }} onClick={onStop}>■ 작업 중지</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: FINAL VERIFICATION (모든 품목 합산 Load Cell 검증)
// ============================================================
function FinalVerificationScreen({
  items, isPaused, showStop,
  onPass, onPause, onStop
}: {
  items: WorkItem[]; isPaused: boolean; showStop: boolean
  onPass: () => void; onPause: () => void; onStop: () => void
}) {
  const [weightResult, setWeightResult] = useState<'checking' | 'pass' | 'fail'>('checking')
  const [remeasuring, setRemeasuring] = useState(false)
  const [autoCountdown, setAutoCountdown] = useState<number | null>(null)

  // Per-item weight constants
  const UNIT_WEIGHTS: Record<string, number> = { 'B001': 0.05, 'B002': 0.04, 'S001': 0.02 }
  const collectionTrayEmpty = 0.50
  const itemWeights = items.map(i => ({ ...i, unitW: UNIT_WEIGHTS[i.partNo] ?? 0.03, totalW: (UNIT_WEIGHTS[i.partNo] ?? 0.03) * i.qty }))
  const expectedNet = parseFloat(itemWeights.reduce((s, i) => s + i.totalW, 0).toFixed(3))
  const measuredTotal = parseFloat((collectionTrayEmpty + expectedNet).toFixed(3))
  const actualNet = parseFloat((measuredTotal - collectionTrayEmpty).toFixed(3))
  const tolerance = parseFloat((expectedNet * 0.02).toFixed(3))
  const lo = parseFloat((expectedNet - tolerance).toFixed(3))
  const hi = parseFloat((expectedNet + tolerance).toFixed(3))

  useEffect(() => {
    const t = setTimeout(() => setWeightResult('pass'), 1500)
    return () => clearTimeout(t)
  }, [])

  useEffect(() => {
    if (remeasuring) {
      setWeightResult('checking')
      const t = setTimeout(() => { setWeightResult('pass'); setRemeasuring(false) }, 2200)
      return () => clearTimeout(t)
    }
  }, [remeasuring])

  useEffect(() => {
    if (weightResult !== 'pass' || isPaused || showStop) return
    setAutoCountdown(3)
  }, [weightResult, isPaused, showStop])

  useEffect(() => {
    if (autoCountdown === null || isPaused || showStop) return
    if (autoCountdown <= 0) { onPass(); return }
    const t = setTimeout(() => setAutoCountdown(c => (c ?? 1) - 1), 1000)
    return () => clearTimeout(t)
  }, [autoCountdown, isPaused, showStop, onPass])

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        background: 'var(--hmi-navy)', color: 'white', padding: '8px 16px',
        display: 'flex', alignItems: 'center', gap: 12, borderBottom: '2px solid var(--hmi-blue-light)', flexShrink: 0
      }}>
        <span style={{ fontWeight: 900, fontSize: 16, letterSpacing: '0.1em' }}>FINAL WEIGHT VERIFICATION</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: '#93c5fd', fontFamily: 'JetBrains Mono, monospace' }}>전체 {items.length}개 품목 합산 검증</span>
      </div>

      <div style={{ flex: 1, overflow: 'auto', background: 'var(--hmi-work-bg)', padding: 16, display: 'flex', gap: 14 }}>

        {/* Left: per-item breakdown */}
        <div style={{ flex: '0 0 300px', display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ background: 'white', border: '1px solid var(--hmi-border)' }}>
            <div className="section-header" style={{ fontSize: 11 }}>피킹 완료 품목</div>
            {itemWeights.map((item, i) => (
              <div key={i} style={{ padding: '10px 14px', borderBottom: i < items.length - 1 ? '1px solid var(--hmi-border-light)' : 'none' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ fontWeight: 700, fontSize: 13 }}>{item.name}</span>
                  <span className="badge-green" style={{ fontSize: 9, padding: '1px 6px' }}>✓ PICKED</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11 }}>
                  <span style={{ fontFamily: 'JetBrains Mono, monospace', color: '#374151' }}>{item.spec}</span>
                  <span style={{ fontFamily: 'JetBrains Mono, monospace', color: 'var(--hmi-text-muted)' }}>{item.qty} EA × {item.unitW.toFixed(2)}kg = <strong style={{ color: '#111827' }}>{item.totalW.toFixed(2)}kg</strong></span>
                </div>
              </div>
            ))}
          </div>
          <div style={{ background: '#eff6ff', border: '2px solid var(--hmi-blue)', padding: '10px 14px' }}>
            <div style={{ fontSize: 11, color: 'var(--hmi-blue)', marginBottom: 4 }}>예상 총 부품 순중량</div>
            <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 900, fontSize: 24, color: 'var(--hmi-blue)' }}>{expectedNet.toFixed(3)} kg</div>
          </div>
        </div>

        {/* Right: load cell verification */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ background: 'white', border: '1px solid var(--hmi-border)' }}>
            <div className="section-header" style={{ fontSize: 11 }}>LOAD CELL — 최종 무게 검증</div>
            <div style={{ padding: '14px 18px' }}>
              {remeasuring ? (
                <div style={{ textAlign: 'center', padding: '28px', color: 'var(--hmi-blue)' }}>
                  <div className="spin" style={{ display: 'inline-block', width: 28, height: 28, border: '3px solid var(--hmi-blue)', borderTopColor: 'transparent', marginBottom: 10 }} />
                  <div style={{ fontWeight: 700 }}>무게 재측정 중...</div>
                </div>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 8 }}>측정값</div>
                    {[
                      { label: '수집 Tray 전체 무게', val: `${measuredTotal.toFixed(3)} kg` },
                      { label: '빈 수집 Tray 기준 무게', val: `${collectionTrayEmpty.toFixed(2)} kg` },
                    ].map(r => (
                      <div key={r.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 10px', background: '#f8f9fa', border: '1px solid var(--hmi-border-light)', marginBottom: 6 }}>
                        <span style={{ fontSize: 12, color: '#374151' }}>{r.label}</span>
                        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>{r.val}</span>
                      </div>
                    ))}
                    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 10px', background: '#eff6ff', border: '2px solid var(--hmi-blue)' }}>
                      <span style={{ fontWeight: 700, color: 'var(--hmi-blue)' }}>실제 부품 순중량</span>
                      <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 900, fontSize: 17, color: 'var(--hmi-blue)' }}>{actualNet.toFixed(3)} kg</span>
                    </div>
                  </div>
                  <div>
                    <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 8 }}>기준값</div>
                    {[
                      { label: '예상 부품 순중량', val: `${expectedNet.toFixed(3)} kg` },
                      { label: '허용 범위', val: `${lo.toFixed(3)} ~ ${hi.toFixed(3)} kg` },
                    ].map(r => (
                      <div key={r.label} style={{ display: 'flex', justifyContent: 'space-between', padding: '7px 10px', background: '#f8f9fa', border: '1px solid var(--hmi-border-light)', marginBottom: 6 }}>
                        <span style={{ fontSize: 12, color: '#374151' }}>{r.label}</span>
                        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>{r.val}</span>
                      </div>
                    ))}
                    {weightResult !== 'checking' && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '9px 10px', background: weightResult === 'pass' ? 'var(--hmi-green-bg)' : 'var(--hmi-red-bg)', border: `2px solid ${weightResult === 'pass' ? 'var(--hmi-green)' : 'var(--hmi-red-accent)'}` }}>
                        <span style={{ fontWeight: 700, color: weightResult === 'pass' ? 'var(--hmi-green)' : 'var(--hmi-red)' }}>무게 판정</span>
                        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 900, fontSize: 16, color: weightResult === 'pass' ? 'var(--hmi-green)' : 'var(--hmi-red)' }}>{weightResult === 'pass' ? 'PASS' : 'FAIL'}</span>
                      </div>
                    )}
                    {weightResult === 'checking' && (
                      <div style={{ padding: '9px 10px', background: '#f0f9ff', border: '1px solid var(--hmi-blue)', textAlign: 'center', color: 'var(--hmi-blue)', fontSize: 12 }}>
                        <span className="blink">무게 측정 중...</span>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Final result */}
          {weightResult !== 'checking' && !remeasuring && (
            <div style={{
              background: weightResult === 'pass' ? 'var(--hmi-green-bg)' : 'var(--hmi-red-bg)',
              border: `2px solid ${weightResult === 'pass' ? 'var(--hmi-green)' : 'var(--hmi-red-accent)'}`,
              padding: '16px 20px'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <span style={{ fontSize: 28, color: weightResult === 'pass' ? 'var(--hmi-green)' : 'var(--hmi-red)' }}>
                  {weightResult === 'pass' ? '✓' : '✗'}
                </span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 900, fontSize: 18, color: weightResult === 'pass' ? 'var(--hmi-green)' : 'var(--hmi-red)' }}>
                    {weightResult === 'pass' ? 'FINAL WEIGHT VERIFIED — PASS' : 'WEIGHT VERIFICATION FAILED'}
                  </div>
                  {weightResult === 'pass' && !isPaused && !showStop && autoCountdown !== null && (
                    <div style={{ fontSize: 13, color: 'var(--hmi-green-dark)', marginTop: 4 }}>
                      {autoCountdown > 0 ? `${autoCountdown}초 후 Tray 복귀 단계로 이동합니다...` : 'Tray 복귀 단계로 이동합니다...'}
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-warning" style={{ flex: 1, padding: '10px' }} onClick={() => { setRemeasuring(true); setAutoCountdown(null) }}>↺ 재측정</button>
            <button className="btn-secondary" style={{ flex: 1, padding: '10px' }} onClick={onPause}>⏸ 일시 정지</button>
            <button className="btn-danger" style={{ flex: 1, padding: '10px' }} onClick={onStop}>■ 작업 중지</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: TRAY RETURN & RELOCATION (Q-learning 기반 재배치)
// ============================================================
type ReturnPhase = 'waiting' | 'detecting' | 'recognized' | 'relocating' | 'placed'

interface TrayReturnEntry {
  tray: WorkItem          // which tray was used
  arucoId: string         // ArUco marker ID
  fromSlot: number        // current rack slot index
  toSlot: number          // Q-learning target slot index
  phase: ReturnPhase
}

function TrayReturnScreen({
  usedItems, currentSlots, targetSlots, isPaused, showStop,
  onComplete, onPause, onStop, onArucoCheck
}: {
  usedItems: WorkItem[]
  currentSlots: RackSlots
  targetSlots: RackSlots
  isPaused: boolean
  showStop: boolean
  onComplete: (finalSlots: RackSlots) => void
  onPause: () => void
  onStop: () => void
  onArucoCheck: () => Promise<any>
}) {
  // Derive from/to positions from actual rack layout data
  const uniqueUsedItems = usedItems.filter(
    (item, index, all) =>
      all.findIndex(
        other => other.tray === item.tray
      ) === index
  )

  const initEntries: TrayReturnEntry[] = uniqueUsedItems.map((item, i) => ({
    tray: item,
    arucoId: String(i + 1).padStart(2, '0'),
    fromSlot: slotOf(item.tray, currentSlots),
    toSlot: slotOf(item.tray, targetSlots),
    phase: 'waiting',
  }))

  const [entries, setEntries] = useState<TrayReturnEntry[]>(initEntries)
  const [currentIdx, setCurrentIdx] = useState(0)
  const pausedRef = useRef(isPaused)
  const stopRef = useRef(showStop)
  useEffect(() => { pausedRef.current = isPaused }, [isPaused])
  useEffect(() => { stopRef.current = showStop }, [showStop])

  const setPhase = (idx: number, phase: ReturnPhase) => {
    setEntries(prev => prev.map((e, i) => i === idx ? { ...e, phase } : e))
  }

  // Tray 복귀:
  // waiting은 작업자가 Tray를 올려놓는 시간을 표현하는 Mock 대기.
  // ArUco 확인 자체는 실제 /vision/aruco API 결과로 판정한다.
  // 실제 장비에서는 waiting도 센서/버튼 신호로 교체 가능하다.
  useEffect(() => {
    if (isPaused || showStop) return

    const idx = currentIdx

    if (idx >= entries.length) {
      return
    }

    let cancelled = false
    let timer:
      ReturnType<typeof setTimeout> | undefined

    const execute = async () => {
      setPhase(idx, 'waiting')

      // 현재는 Mock 데모상 Tray를 올려놓을 시간을 짧게 둔다.
      await new Promise<void>(
        resolve => {
          timer = setTimeout(
            resolve,
            1200
          )
        }
      )

      if (
        cancelled ||
        pausedRef.current ||
        stopRef.current
      ) {
        return
      }

      setPhase(idx, 'detecting')

      const visionResult =
        await onArucoCheck()

      if (
        cancelled ||
        pausedRef.current ||
        stopRef.current
      ) {
        return
      }

      const expectedTrayId =
        Number(
          entries[idx].tray.tray
            .match(/\d+/)?.[0]
        )

      const detectedTrayId =
        Number(
          visionResult.tray_id ??
          visionResult.aruco_id
        )

      // MockVisionAdapter는 현재 고정 ID를 돌려주므로
      // mock=true일 때는 연결 통로 테스트로 간주해 현재 대상 Tray를 승인.
      // 실제 VisionAdapter(mock=false)에서는 반드시 ID가 일치해야 통과.
      const matched =
        visionResult.success &&
        visionResult.detected &&
        (
          visionResult.mock === true ||
          detectedTrayId === expectedTrayId
        )

      if (!matched) {
        setPhase(idx, 'waiting')

        alert(
          `ArUco 확인 실패\n\n` +
          `기대 Tray: ${entries[idx].tray.tray}\n` +
          `인식 ID: ${
            Number.isFinite(detectedTrayId)
              ? detectedTrayId
              : '인식 실패'
          }`
        )

        return
      }

      setPhase(idx, 'recognized')

      await new Promise<void>(
        resolve => {
          timer = setTimeout(
            resolve,
            500
          )
        }
      )

      if (
        cancelled ||
        pausedRef.current ||
        stopRef.current
      ) {
        return
      }

      // 재배치 이동 자체는 아직 Stage/Q-learning 실제 명령 전이므로
      // UI Mock 진행을 유지한다.
      setPhase(idx, 'relocating')

      await new Promise<void>(
        resolve => {
          timer = setTimeout(
            resolve,
            1200
          )
        }
      )

      if (
        cancelled ||
        pausedRef.current ||
        stopRef.current
      ) {
        return
      }

      setPhase(idx, 'placed')

      await new Promise<void>(
        resolve => {
          timer = setTimeout(
            resolve,
            400
          )
        }
      )

      if (cancelled) {
        return
      }

      if (idx + 1 < entries.length) {
        setCurrentIdx(idx + 1)
      } else {
        onComplete(targetSlots)
      }
    }

    execute()

    return () => {
      cancelled = true

      if (timer) {
        clearTimeout(timer)
      }
    }
  }, [
    currentIdx,
    isPaused,
    showStop
  ]) // eslint-disable-line

  const cur = entries[currentIdx]

  const phaseLabel: Record<ReturnPhase, string> = {
    waiting: '다음 Tray를 그리퍼에 올려주세요',
    detecting: 'ArUco Marker 감지 중...',
    recognized: `TRAY 인식 완료 — ${cur?.tray.tray}`,
    relocating: `${cur?.tray.tray} 재배치 중`,
    placed: `${cur?.tray.tray} 배치 완료`,
  }
  const phaseColor: Record<ReturnPhase, string> = {
    waiting: 'var(--hmi-yellow)',
    detecting: 'var(--hmi-blue)',
    recognized: 'var(--hmi-blue)',
    relocating: 'var(--hmi-blue)',
    placed: 'var(--hmi-green)',
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        background: 'var(--hmi-navy)', color: 'white', padding: '8px 16px',
        display: 'flex', alignItems: 'center', gap: 12, borderBottom: '2px solid var(--hmi-blue-light)', flexShrink: 0
      }}>
        <span style={{ fontWeight: 900, fontSize: 16, letterSpacing: '0.1em' }}>TRAY RETURN &amp; RELOCATION</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: '#93c5fd', fontFamily: 'JetBrains Mono, monospace' }}>
          {entries.filter(e => e.phase === 'placed').length} / {entries.length} 완료
        </span>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* Left: return list */}
        <div style={{ flex: '0 0 220px', borderRight: '2px solid var(--hmi-border)', background: 'white', display: 'flex', flexDirection: 'column' }}>
          <div style={{ background: '#1e3a5f', color: 'white', padding: '6px 12px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em' }}>
            재배치 목록
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {entries.map((e, i) => {
              const isActive = i === currentIdx
              const isDone = e.phase === 'placed'
              const isPending = i > currentIdx
              return (
                <div key={i} style={{
                  border: isActive ? '2px solid var(--hmi-blue)' : isDone ? '1px solid var(--hmi-green)' : '1px solid var(--hmi-border-light)',
                  background: isActive ? '#e8f0fe' : isDone ? '#f0fdf4' : '#f9fafb',
                  padding: '8px 10px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 800, fontSize: 13, color: isActive ? 'var(--hmi-blue)' : isDone ? 'var(--hmi-green)' : '#9ca3af' }}>
                      {e.tray.tray}
                    </span>
                    {isDone && <span style={{ fontSize: 12, color: 'var(--hmi-green)' }}>✓</span>}
                    {isActive && !isDone && <span className="blink" style={{ fontSize: 10, color: 'var(--hmi-blue)', fontFamily: 'JetBrains Mono, monospace' }}>●</span>}
                  </div>
                  <div style={{ fontSize: 11, color: '#6b7280' }}>{e.tray.name}</div>
                  <div style={{ fontSize: 10, color: '#9ca3af', fontFamily: 'JetBrains Mono, monospace', marginTop: 3 }}>
                    ArUco #{e.arucoId}
                  </div>
                  {!isPending && (
                    <div style={{ fontSize: 10, marginTop: 4, fontFamily: 'JetBrains Mono, monospace', color: isDone ? 'var(--hmi-green)' : 'var(--hmi-blue)' }}>
                      {SLOT_POSITIONS[e.fromSlot]} → {SLOT_POSITIONS[e.toSlot]}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
          {/* Progress */}
          <div style={{ padding: '10px 12px', borderTop: '1px solid var(--hmi-border-light)' }}>
            <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 4 }}>전체 재배치 진행률</div>
            <div className="progress-bar" style={{ height: 8, marginBottom: 4 }}>
              <div className="progress-fill" style={{ width: `${(entries.filter(e => e.phase === 'placed').length / entries.length) * 100}%` }} />
            </div>
            <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, fontWeight: 700, textAlign: 'right', color: 'var(--hmi-green)' }}>
              {entries.filter(e => e.phase === 'placed').length} / {entries.length}
            </div>
          </div>
        </div>

        {/* Center: current tray detail */}
        <div style={{ flex: '0 0 360px', borderRight: '2px solid var(--hmi-border)', background: 'var(--hmi-work-bg)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ background: '#1e3a5f', color: 'white', padding: '6px 12px', fontSize: 11, fontWeight: 700, letterSpacing: '0.05em' }}>
            현재 재배치 정보
          </div>
          <div style={{ flex: 1, overflow: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {cur && (
              <>
                {/* Status banner */}
                <div style={{
                  background: cur.phase === 'placed' ? 'var(--hmi-green-bg)' : cur.phase === 'waiting' ? 'var(--hmi-yellow-bg)' : 'var(--hmi-blue-bg)',
                  border: `2px solid ${phaseColor[cur.phase]}`,
                  padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10
                }}>
                  {cur.phase === 'detecting' && (
                    <div className="spin" style={{ width: 16, height: 16, border: `2px solid var(--hmi-blue)`, borderTopColor: 'transparent', flexShrink: 0 }} />
                  )}
                  {cur.phase !== 'detecting' && (
                    <span style={{ fontSize: 16, color: phaseColor[cur.phase] }}>
                      {cur.phase === 'placed' ? '✓' : cur.phase === 'waiting' ? '⏳' : '●'}
                    </span>
                  )}
                  <span style={{ fontWeight: 700, fontSize: 14, color: phaseColor[cur.phase] }}>
                    {phaseLabel[cur.phase]}
                  </span>
                </div>

                {/* Tray identity */}
                <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '12px 14px' }}>
                  <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 8, letterSpacing: '0.05em' }}>인식된 Tray 정보</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                    {[
                      { label: 'ArUco ID', val: `#${cur.arucoId}`, highlight: cur.phase !== 'waiting' },
                      { label: '인식된 Tray', val: cur.phase !== 'waiting' ? cur.tray.tray : '--', highlight: cur.phase !== 'waiting' },
                      { label: '부품', val: cur.phase !== 'waiting' ? cur.tray.name : '--', highlight: false },
                      { label: '규격', val: cur.phase !== 'waiting' ? cur.tray.spec : '--', highlight: false },
                    ].map(r => (
                      <div key={r.label} style={{ background: r.highlight && cur.phase !== 'waiting' ? '#eff6ff' : '#f8f9fa', border: `1px solid ${r.highlight && cur.phase !== 'waiting' ? 'var(--hmi-blue)' : 'var(--hmi-border-light)'}`, padding: '7px 10px' }}>
                        <div style={{ fontSize: 10, color: 'var(--hmi-text-muted)' }}>{r.label}</div>
                        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 13, color: r.highlight && cur.phase !== 'waiting' ? 'var(--hmi-blue)' : '#111827' }}>{r.val}</div>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Position mapping */}
                {cur.phase !== 'waiting' && cur.phase !== 'detecting' && (
                  <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '12px 14px' }}>
                    <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 10, letterSpacing: '0.05em' }}>Q-LEARNING 재배치 계획</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                      <div style={{ flex: 1, background: '#fef3c7', border: '1px solid var(--hmi-yellow-accent)', padding: '8px 10px' }}>
                        <div style={{ fontSize: 10, color: 'var(--hmi-yellow)' }}>기존 위치</div>
                        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12, color: 'var(--hmi-yellow)' }}>{SLOT_POSITIONS[cur.fromSlot]}</div>
                      </div>
                      <div style={{ fontSize: 18, color: cur.phase === 'placed' ? 'var(--hmi-green)' : 'var(--hmi-blue)', fontWeight: 700 }}>→</div>
                      <div style={{ flex: 1, background: cur.phase === 'placed' ? 'var(--hmi-green-bg)' : '#eff6ff', border: `1px solid ${cur.phase === 'placed' ? 'var(--hmi-green)' : 'var(--hmi-blue)'}`, padding: '8px 10px' }}>
                        <div style={{ fontSize: 10, color: cur.phase === 'placed' ? 'var(--hmi-green)' : 'var(--hmi-blue)' }}>목표 위치</div>
                        <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 12, color: cur.phase === 'placed' ? 'var(--hmi-green)' : 'var(--hmi-blue)' }}>{SLOT_POSITIONS[cur.toSlot]}</div>
                      </div>
                    </div>
                    {cur.fromSlot === cur.toSlot && (
                      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--hmi-text-muted)', fontStyle: 'italic' }}>
                        Q-learning 결과: 현재 위치가 최적 위치입니다.
                      </div>
                    )}
                  </div>
                )}

                {/* Relocation status */}
                {cur.phase !== 'waiting' && (
                  <div style={{ background: 'white', border: '1px solid var(--hmi-border-light)', padding: '10px 14px' }}>
                    <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)', marginBottom: 6 }}>재배치 상태</div>
                    {(['detecting', 'recognized', 'relocating', 'placed'] as ReturnPhase[]).map((p, i) => {
                      const phases = ['detecting', 'recognized', 'relocating', 'placed'] as ReturnPhase[]
                      const curIdx = phases.indexOf(cur.phase)
                      const done = i < curIdx
                      const active = i === curIdx
                      const labels = ['ArUco 감지 중', 'Tray 인식 완료', '목표 위치로 이동 중', '배치 완료']
                      return (
                        <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 0', borderBottom: i < 3 ? '1px solid #f3f4f6' : 'none' }}>
                          <div style={{ width: 16, height: 16, background: done ? 'var(--hmi-green)' : active ? 'var(--hmi-blue)' : '#d1d5db', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 9, color: 'white', fontWeight: 700 }}>
                            {done ? '✓' : ''}
                          </div>
                          <span style={{ fontSize: 12, color: done ? 'var(--hmi-green-dark)' : active ? 'var(--hmi-blue)' : '#9ca3af', fontWeight: active ? 700 : 400 }}>
                            {labels[i]}
                          </span>
                          {active && <span className="blink" style={{ fontSize: 10, color: 'var(--hmi-blue)', marginLeft: 'auto', fontFamily: 'JetBrains Mono, monospace' }}>●</span>}
                        </div>
                      )
                    })}
                  </div>
                )}
              </>
            )}
          </div>

          <div style={{ padding: '10px 14px', borderTop: '1px solid var(--hmi-border-light)', display: 'flex', gap: 6 }}>
            <button className="btn-warning" style={{ flex: 1, padding: '8px', fontSize: 12 }} onClick={onPause}>⏸ 일시 정지</button>
            <button className="btn-danger" style={{ flex: 1, padding: '8px', fontSize: 12 }} onClick={onStop}>■ 작업 중지</button>
          </div>
        </div>

        {/* Right: camera */}
        <div style={{ flex: 1, background: '#0a0f1a', display: 'flex', flexDirection: 'column' }}>
          <div style={{ background: '#0f1929', borderBottom: '1px solid #1e3a5f', padding: '6px 14px', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span className="status-dot blink" style={{ background: '#ef4444' }} />
            <span style={{ color: '#94a3b8', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>CAM-01  ARUCO DETECTION  LIVE</span>
          </div>
          <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
            {/* Camera background */}
            <div style={{ position: 'absolute', inset: 0, background: '#111827' }}>
              <svg style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.08 }}>
                <defs><pattern id="grid2" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="#60a5fa" strokeWidth="0.5"/></pattern></defs>
                <rect width="100%" height="100%" fill="url(#grid2)" />
              </svg>
            </div>
            {/* Center detection area */}
            <div style={{ position: 'absolute', inset: '15%', border: '2px dashed rgba(96,165,250,0.25)' }} />

            {cur && cur.phase !== 'waiting' && (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {cur.phase === 'detecting' ? (
                  <div style={{ width: 90, height: 90, border: '2px dashed rgba(250,204,21,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', position: 'relative' }}>
                    <span className="blink" style={{ color: 'rgba(250,204,21,0.8)', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>SEARCHING</span>
                    <div style={{ position: 'absolute', inset: -14, border: '2px dashed rgba(250,204,21,0.3)' }} />
                  </div>
                ) : (
                  <div style={{ position: 'relative' }}>
                    <div style={{ width: 90, height: 90, background: 'white', border: `3px solid ${cur.phase === 'placed' ? '#22c55e' : '#3b82f6'}`, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: 2, padding: 6, boxShadow: `0 0 16px ${cur.phase === 'placed' ? 'rgba(34,197,94,0.5)' : 'rgba(59,130,246,0.5)'}` }}>
                      {[1,0,1,0,1,0,0,1,0,0,1,0,1,0,0,1].map((v, k) => (
                        <div key={k} style={{ background: v ? '#0f172a' : 'white' }} />
                      ))}
                    </div>
                    <div style={{ position: 'absolute', inset: -10, border: `2px solid ${cur.phase === 'placed' ? '#22c55e' : '#3b82f6'}`, opacity: 0.6 }} />
                    <div style={{ position: 'absolute', top: -24, left: '50%', transform: 'translateX(-50%)', background: cur.phase === 'placed' ? '#16a34a' : '#1d4ed8', color: 'white', fontSize: 10, padding: '2px 8px', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, whiteSpace: 'nowrap' }}>
                      ArUco #{cur.arucoId} → {cur.tray.tray}
                    </div>
                  </div>
                )}
              </div>
            )}

            {cur && cur.phase === 'waiting' && (
              <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
                <div style={{ width: 80, height: 80, border: '2px dashed rgba(255,255,255,0.2)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <span style={{ color: 'rgba(255,255,255,0.2)', fontSize: 28 }}>?</span>
                </div>
                <div style={{ color: 'rgba(255,255,255,0.5)', fontSize: 12, fontFamily: 'JetBrains Mono, monospace', textAlign: 'center' }}>
                  Tray를 그리퍼에 올리면<br />ArUco Marker를 자동 인식합니다
                </div>
              </div>
            )}

            {/* Scan line */}
            <div style={{ position: 'absolute', left: 0, right: 0, height: 2, background: 'rgba(74,222,128,0.3)', animation: 'scan-line 3s linear infinite' }} />
          </div>

          {/* Camera info overlay */}
          {cur && cur.phase !== 'waiting' && (
            <div style={{ background: 'rgba(7,17,31,0.95)', borderTop: '1px solid #1e3a5f', padding: '8px 14px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
              {[
                { label: 'ArUco ID', val: cur.phase === 'detecting' ? '감지 중...' : `#${cur.arucoId}` },
                { label: '인식 Tray', val: cur.phase === 'detecting' ? '--' : cur.tray.tray },
                { label: '기존 위치', val: SLOT_POSITIONS[cur.fromSlot] },
                { label: '목표 위치', val: SLOT_POSITIONS[cur.toSlot] },
              ].map(r => (
                <div key={r.label}>
                  <div style={{ color: '#64748b', fontSize: 9, fontFamily: 'JetBrains Mono, monospace' }}>{r.label}</div>
                  <div style={{ color: '#e2e8f0', fontSize: 11, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>{r.val}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: RELOCATION COMPLETE
// ============================================================
function RelocationCompleteScreen({
  initialSlots, finalSlots, onNext
}: {
  initialSlots: RackSlots; finalSlots: RackSlots; onNext: () => void
}) {
  useEffect(() => {
    const t = setTimeout(onNext, 4000)
    return () => clearTimeout(t)
  }, [onNext])

  // Build per-tray movement summary (all unique tray IDs across both layouts)
  const allTrayIds = Array.from(new Set([...initialSlots, ...finalSlots]))
  const movements = allTrayIds.map(id => {
    const fromIdx = slotOf(id, initialSlots)
    const toIdx   = slotOf(id, finalSlots)
    return { id, fromIdx, toIdx, moved: fromIdx !== toIdx }
  }).sort((a, b) => a.id.localeCompare(b.id))

  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--hmi-work-bg)' }}>
      <div style={{ background: 'white', border: '2px solid var(--hmi-green)', width: 640, padding: 0 }}>
        <div style={{ background: 'var(--hmi-green)', color: 'white', padding: '14px 24px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 24 }}>✓</span>
          <span style={{ fontWeight: 800, fontSize: 18 }}>TRAY 재배치 완료</span>
        </div>
        <div style={{ padding: '24px 28px' }}>
          <p style={{ fontSize: 14, color: '#374151', marginBottom: 20 }}>
            모든 Tray의 복귀 및 Q-learning 기반 재배치가 완료되었습니다.<br />
            최종 Tray 배치 현황이 메인 화면에 반영됩니다.
          </p>

          {/* Tray-centric movement table */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--hmi-navy)', marginBottom: 8, letterSpacing: '0.05em', background: '#f3f4f6', padding: '6px 10px', borderLeft: '3px solid var(--hmi-blue)' }}>
              TRAY 위치 변경 내역 — Tray ID 기준
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  {['Tray ID', '이전 위치', '', '변경 위치', '상태'].map((h, i) => (
                    <th key={i} style={{ background: '#1e3a5f', color: 'white', padding: '6px 10px', textAlign: 'left', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, border: '1px solid #2d4a70' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {movements.map(({ id, fromIdx, toIdx, moved }) => (
                  <tr key={id}>
                    <td style={{ padding: '6px 10px', border: '1px solid #e5e7eb', fontFamily: 'JetBrains Mono, monospace', fontWeight: 700 }}>{id}</td>
                    <td style={{ padding: '6px 10px', border: '1px solid #e5e7eb', fontFamily: 'JetBrains Mono, monospace', color: moved ? 'var(--hmi-yellow)' : 'var(--hmi-text-muted)' }}>
                      {fromIdx >= 0 ? SLOT_POSITIONS[fromIdx] : '—'}
                    </td>
                    <td style={{ padding: '6px 10px', border: '1px solid #e5e7eb', textAlign: 'center', color: moved ? 'var(--hmi-blue)' : '#d1d5db', fontWeight: 700 }}>→</td>
                    <td style={{ padding: '6px 10px', border: '1px solid #e5e7eb', fontFamily: 'JetBrains Mono, monospace', fontWeight: moved ? 700 : 400, color: moved ? 'var(--hmi-green)' : 'var(--hmi-text-muted)' }}>
                      {toIdx >= 0 ? SLOT_POSITIONS[toIdx] : '—'}
                    </td>
                    <td style={{ padding: '6px 10px', border: '1px solid #e5e7eb' }}>
                      {moved
                        ? <span style={{ background: 'var(--hmi-green-bg)', color: 'var(--hmi-green)', border: '1px solid var(--hmi-green)', fontWeight: 700, fontSize: 10, padding: '1px 6px', fontFamily: 'JetBrains Mono, monospace' }}>RELOCATED</span>
                        : <span style={{ background: '#f3f4f6', color: '#6b7280', border: '1px solid #d1d5db', fontWeight: 600, fontSize: 10, padding: '1px 6px', fontFamily: 'JetBrains Mono, monospace' }}>NO CHANGE</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div style={{ textAlign: 'center', color: 'var(--hmi-text-muted)', fontSize: 12 }}>작업 완료 화면으로 이동합니다...</div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: ITEM COMPLETE
// ============================================================
function ItemCompleteScreen({
  item, itemIndex, totalItems, isPaused, showStop, onNext
}: {
  item: WorkItem; itemIndex: number; totalItems: number
  isPaused: boolean; showStop: boolean; onNext: () => void
}) {
  const isLast = itemIndex === totalItems - 1
  useEffect(() => {
    if (isPaused || showStop) return
    const t = setTimeout(onNext, 2200)
    return () => clearTimeout(t)
  }, [isPaused, showStop, onNext])

  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--hmi-work-bg)' }}>
      <div style={{ background: 'white', border: '2px solid var(--hmi-green)', width: 480, padding: 0 }}>
        <div style={{ background: 'var(--hmi-green)', color: 'white', padding: '14px 24px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 24 }}>✓</span>
          <span style={{ fontWeight: 800, fontSize: 18 }}>피킹 완료</span>
        </div>
        <div style={{ padding: '24px 28px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 20 }}>
            {[
              { label: '품목', val: item.name },
              { label: '규격', val: item.spec },
              { label: '피킹 수량', val: `${item.qty} EA` },
              { label: '완료 위치', val: item.tray },
              { label: '품번', val: item.partNo },
              { label: '상태', val: 'VERIFIED' },
            ].map(r => (
              <div key={r.label} style={{ background: '#f8f9fa', border: '1px solid var(--hmi-border-light)', padding: '8px 12px' }}>
                <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>{r.label}</div>
                <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 14, color: r.label === '상태' ? 'var(--hmi-green)' : '#111827' }}>
                  {r.val}
                </div>
              </div>
            ))}
          </div>

          <div style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
              <span style={{ fontWeight: 600, fontSize: 13 }}>전체 진행률</span>
              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, color: 'var(--hmi-blue)', fontSize: 13 }}>
                {itemIndex + 1} / {totalItems} 완료
              </span>
            </div>
            <div className="progress-bar" style={{ height: 10 }}>
              <div className="progress-fill" style={{ width: `${((itemIndex + 1) / totalItems) * 100}%` }} />
            </div>
          </div>

          {isLast ? (
            <div style={{ background: 'var(--hmi-green-bg)', border: '1px solid var(--hmi-green)', padding: '10px 14px', fontSize: 13, color: 'var(--hmi-green-dark)', fontWeight: 600 }}>
              마지막 품목 완료 — 최종 무게 검증으로 이동합니다.
            </div>
          ) : (
            <div style={{ color: 'var(--hmi-text-muted)', fontSize: 12, textAlign: 'center' }}>
              다음 품목으로 자동 이동합니다...
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: WORK COMPLETE
// ============================================================
function WorkCompleteScreen({ items, onNewWork, onHistory }: { items: WorkItem[]; onNewWork: () => void; onHistory: () => void }) {
  const totalQty = items.reduce((s, i) => s + i.qty, 0)
  const stockChanges = items.map(i => ({ name: i.name, spec: i.spec, before: i.stock, after: i.stock - i.qty, delta: -i.qty }))

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--hmi-work-bg)', padding: 20 }}>
      {/* Header */}
      <div style={{ background: 'var(--hmi-green)', color: 'white', padding: '16px 24px', display: 'flex', alignItems: 'center', gap: 14, marginBottom: 16 }}>
        <span style={{ fontSize: 28 }}>✓</span>
        <div>
          <div style={{ fontWeight: 900, fontSize: 22, letterSpacing: '0.05em' }}>WORK COMPLETED</div>
          <div style={{ fontSize: 12, opacity: 0.85, fontFamily: 'JetBrains Mono, monospace' }}>WO-20260817-001</div>
        </div>
        <div style={{ flex: 1 }} />
        <div style={{ textAlign: 'right', fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>
          <div>완료 시간: 12:04:52</div>
          <div>총 작업 시간: 00:04:32</div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
        {/* Summary */}
        <div style={{ background: 'white', border: '1px solid var(--hmi-border)' }}>
          <div className="section-header" style={{ fontSize: 11 }}>작업 요약</div>
          <div style={{ padding: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {[
              { label: '완료 품목', val: `${items.length} / ${items.length}`, color: 'var(--hmi-green)' },
              { label: '총 피킹 수량', val: String(totalQty), color: 'var(--hmi-blue)' },
              { label: '검증 결과', val: '전체 정상', color: 'var(--hmi-green)' },
              { label: '작업자', val: 'OP-001', color: '#111827' },
            ].map(s => (
              <div key={s.label} style={{ background: '#f8f9fa', border: '1px solid var(--hmi-border-light)', padding: '10px 14px' }}>
                <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>{s.label}</div>
                <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 800, fontSize: 18, color: s.color }}>{s.val}</div>
              </div>
            ))}
          </div>
        </div>

        {/* DB Update */}
        <div style={{ background: 'white', border: '1px solid var(--hmi-border)' }}>
          <div className="section-header" style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 6 }}>
            DATABASE UPDATED
            <span className="badge-green" style={{ fontSize: 9, padding: '1px 6px', marginLeft: 4 }}>SYNC</span>
          </div>
          <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 6 }}>
            {stockChanges.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', background: '#f8f9fa', border: '1px solid var(--hmi-border-light)' }}>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{s.name}</div>
                  <div style={{ fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: '#374151' }}>{s.spec}</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }}>
                  <span style={{ color: '#374151' }}>{s.before}</span>
                  <span style={{ color: '#9ca3af' }}>→</span>
                  <span style={{ fontWeight: 700, color: '#111827' }}>{s.after}</span>
                  <span style={{ color: 'var(--hmi-red)', fontWeight: 700, fontSize: 11 }}>({s.delta})</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Completed items table */}
      <div style={{ background: 'white', border: '1px solid var(--hmi-border)', marginBottom: 16 }}>
        <div className="section-header" style={{ fontSize: 11 }}>완료 품목 내역</div>
        <table className="hmi-table">
          <thead>
            <tr>
              {['No.', '품명', '규격/사양', '피킹 수량', 'Tray', '결과'].map(h => <th key={h}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {items.map((item, i) => (
              <tr key={i}>
                <td style={{ textAlign: 'center' }}>{item.no}</td>
                <td style={{ fontFamily: 'inherit' }}>{item.name}</td>
                <td>{item.spec}</td>
                <td style={{ textAlign: 'right', fontWeight: 700 }}>{item.qty} EA</td>
                <td>{item.tray}</td>
                <td><span className="badge-green" style={{ padding: '2px 10px', fontSize: 11 }}>PASS</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
        <button className="btn-secondary" style={{ padding: '12px 24px', fontSize: 14 }} onClick={onHistory}>
          📋 작업 이력 확인
        </button>
        <button className="btn-green" style={{ padding: '12px 32px', fontSize: 15 }} onClick={onNewWork}>
          ＋ 새 작업 시작
        </button>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: WORK HISTORY
// ============================================================
function WorkHistoryScreen({
  onBack,
  history,
}: {
  onBack: () => void
  history: WorkHistoryRecord[]
}) {
  const [expandedRow, setExpandedRow] =
    useState<number | null>(null)

  const resultBadge = (result: string) => {
    if (result === 'COMPLETED') {
      return 'badge-green'
    }

    if (result === 'STOPPED') {
      return 'badge-yellow'
    }

    return 'badge-red'
  }

  const formatDuration = (
    totalSeconds: number
  ) => {
    const seconds = Math.max(
      0,
      Number(totalSeconds || 0)
    )

    const h = Math.floor(
      seconds / 3600
    )

    const m = Math.floor(
      (seconds % 3600) / 60
    )

    const s = seconds % 60

    return [
      h,
      m,
      s,
    ]
      .map(value =>
        String(value).padStart(2, '0')
      )
      .join(':')
  }

  return (
    <div style={{
      flex: 1,
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden'
    }}>
      <div
        className="section-header"
        style={{
          fontSize: 13,
          display: 'flex',
          alignItems: 'center',
          gap: 12
        }}
      >
        ■ 작업 이력

        <button
          className="btn-secondary"
          style={{
            padding: '3px 12px',
            fontSize: 11,
            marginLeft: 'auto'
          }}
          onClick={onBack}
        >
          ← 뒤로
        </button>
      </div>

      <div style={{
        flex: 1,
        overflow: 'auto',
        background: 'var(--hmi-work-bg)',
        padding: 16
      }}>
        <div style={{
          background: 'white',
          border: '1px solid var(--hmi-border)'
        }}>
          {history.length === 0 ? (
            <div style={{
              padding: 40,
              textAlign: 'center',
              color: 'var(--hmi-text-muted)'
            }}>
              아직 저장된 작업 이력이 없습니다.
            </div>
          ) : (
            <table
              className="hmi-table"
              style={{ tableLayout: 'fixed' }}
            >
              <thead>
                <tr>
                  {[
                    '작업번호',
                    '완료일시',
                    '품목 수',
                    '총 피킹',
                    '사용 Tray',
                    '결과',
                    '작업 시간'
                  ].map(header => (
                    <th key={header}>
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>

              <tbody>
                {history.map((record, i) => (
                  <React.Fragment
                    key={record.work_id}
                  >
                    <tr
                      style={{ cursor: 'pointer' }}
                      onClick={() =>
                        setExpandedRow(
                          expandedRow === i
                            ? null
                            : i
                        )
                      }
                    >
                      <td style={{
                        color: 'var(--hmi-blue)',
                        fontWeight: 600,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis'
                      }}>
                        {record.work_id}
                      </td>

                      <td>
                        {new Date(
                          record.completed_at
                        ).toLocaleString()}
                      </td>

                      <td style={{
                        textAlign: 'center'
                      }}>
                        {record.item_count}
                      </td>

                      <td style={{
                        textAlign: 'right'
                      }}>
                        {record.total_quantity}
                      </td>

                      <td style={{
                        fontFamily:
                          'JetBrains Mono, monospace'
                      }}>
                        {record.used_trays.join(', ')}
                      </td>

                      <td>
                        <span
                          className={
                            resultBadge(
                              record.result
                            )
                          }
                          style={{
                            padding: '2px 8px',
                            fontSize: 11
                          }}
                        >
                          {record.result}
                        </span>
                      </td>

                      <td>
                        {formatDuration(
                          record.duration_seconds
                        )}
                      </td>
                    </tr>

                    {expandedRow === i && (
                      <tr>
                        <td
                          colSpan={7}
                          style={{
                            background: '#f0f4ff',
                            padding: '12px 20px'
                          }}
                        >
                          <div style={{
                            fontSize: 12,
                            fontWeight: 700,
                            marginBottom: 8,
                            color: 'var(--hmi-navy)'
                          }}>
                            작업 상세 내역 — {record.work_id}
                          </div>

                          <table style={{
                            width: '100%',
                            borderCollapse: 'collapse',
                            fontSize: 12
                          }}>
                            <thead>
                              <tr>
                                {[
                                  '품번',
                                  '품명',
                                  '규격',
                                  '수량',
                                  'Tray',
                                  '결과'
                                ].map(header => (
                                  <th
                                    key={header}
                                    style={{
                                      background: '#1e3a5f',
                                      color: 'white',
                                      padding: '5px 10px',
                                      textAlign: 'left',
                                      border: '1px solid #2d4a70'
                                    }}
                                  >
                                    {header}
                                  </th>
                                ))}
                              </tr>
                            </thead>

                            <tbody>
                              {record.items.map(
                                (item, j) => (
                                  <tr key={j}>
                                    <td style={{
                                      padding: '5px 10px',
                                      border: '1px solid #d1d5db',
                                      fontFamily:
                                        'JetBrains Mono, monospace'
                                    }}>
                                      {item.part_no}
                                    </td>

                                    <td style={{
                                      padding: '5px 10px',
                                      border: '1px solid #d1d5db'
                                    }}>
                                      {item.name}
                                    </td>

                                    <td style={{
                                      padding: '5px 10px',
                                      border: '1px solid #d1d5db',
                                      fontFamily:
                                        'JetBrains Mono, monospace'
                                    }}>
                                      {item.spec}
                                    </td>

                                    <td style={{
                                      padding: '5px 10px',
                                      border: '1px solid #d1d5db',
                                      fontFamily:
                                        'JetBrains Mono, monospace'
                                    }}>
                                      {item.quantity} EA
                                    </td>

                                    <td style={{
                                      padding: '5px 10px',
                                      border: '1px solid #d1d5db',
                                      fontFamily:
                                        'JetBrains Mono, monospace'
                                    }}>
                                      {item.tray}
                                    </td>

                                    <td style={{
                                      padding: '5px 10px',
                                      border: '1px solid #d1d5db'
                                    }}>
                                      <span
                                        className="badge-green"
                                        style={{
                                          padding: '1px 8px',
                                          fontSize: 10
                                        }}
                                      >
                                        PASS
                                      </span>
                                    </td>
                                  </tr>
                                )
                              )}
                            </tbody>
                          </table>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: EQUIPMENT ERROR
// ============================================================
function EquipmentErrorScreen({ onRetry, onHome, onStop }: { onRetry: () => void; onHome: () => void; onStop: () => void }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ background: '#7f1d1d', color: 'white', padding: '8px 16px', borderBottom: '2px solid var(--hmi-red-accent)', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontSize: 20 }}>⚠</span>
        <span style={{ fontWeight: 900, fontSize: 16, letterSpacing: '0.1em', fontFamily: 'JetBrains Mono, monospace' }}>STAGE ERROR</span>
      </div>
      <div style={{ flex: 1, overflow: 'auto', background: '#fef2f2', padding: 24, display: 'flex', gap: 20 }}>
        {/* Error info */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ background: 'white', border: '2px solid var(--hmi-red-accent)', padding: '18px 22px' }}>
            <div style={{ fontWeight: 700, fontSize: 18, color: 'var(--hmi-red)', marginBottom: 4 }}>X축 이동 시간 초과</div>
            <div style={{ fontSize: 13, color: '#374151', marginBottom: 16 }}>지정 시간 내에 목표 위치에 도달하지 못했습니다.</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
              {[
                { label: '목표 위치', val: '358 mm' },
                { label: '현재 위치', val: '241 mm' },
                { label: '오류 코드', val: 'ERR-X01' },
                { label: '발생 시각', val: '12:02:14' },
              ].map(r => (
                <div key={r.label} style={{ background: '#fef2f2', border: '1px solid #fecaca', padding: '8px 12px' }}>
                  <div style={{ fontSize: 11, color: 'var(--hmi-text-muted)' }}>{r.label}</div>
                  <div style={{ fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, fontSize: 14, color: 'var(--hmi-red)' }}>{r.val}</div>
                </div>
              ))}
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn-warning" style={{ flex: 1, padding: '12px', fontSize: 14 }} onClick={onRetry}>
              ↺ 재시도
            </button>
            <button className="btn-primary" style={{ flex: 1, padding: '12px', fontSize: 14 }} onClick={onHome}>
              ⌂ 원점 복귀
            </button>
            <button className="btn-danger" style={{ flex: 1, padding: '12px', fontSize: 14 }} onClick={onStop}>
              ■ 작업 종료
            </button>
          </div>
        </div>

        {/* System status */}
        <div style={{ flex: '0 0 260px', background: 'white', border: '1px solid var(--hmi-border)' }}>
          <div className="section-header" style={{ fontSize: 11 }}>장비 상태</div>
          <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {[
              { label: 'STM32', val: 'CONNECTED', ok: true },
              { label: 'X Axis', val: 'ERROR', ok: false },
              { label: 'Z Axis', val: 'READY', ok: true },
              { label: 'Camera', val: 'NORMAL', ok: true },
              { label: 'Load Cell', val: 'NORMAL', ok: true },
              { label: 'Database', val: 'NORMAL', ok: true },
            ].map(s => (
              <div key={s.label} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px',
                background: !s.ok ? '#fef2f2' : '#f8f9fa',
                border: `1px solid ${!s.ok ? '#fecaca' : 'var(--hmi-border-light)'}`,
              }}>
                <span className="status-dot" style={{ background: s.ok ? '#22c55e' : '#dc2626' }} />
                <span style={{ flex: 1, fontSize: 13, fontFamily: 'JetBrains Mono, monospace' }}>{s.label}</span>
                <span style={{
                  fontFamily: 'JetBrains Mono, monospace', fontSize: 12, fontWeight: 700,
                  color: s.ok ? 'var(--hmi-green)' : 'var(--hmi-red)'
                }}>{s.val}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

// ============================================================
// SCREEN: EMERGENCY STOP
// ============================================================
function EmergencyStopScreen({
  onDismiss,
  onHome,
}: {
  onDismiss: () => void
  onHome: () => Promise<boolean>
}) {
  const steps = [
    '비상정지 원인 확인',
    '비상정지 해제',
    '장비 상태 확인',
    '원점 복귀 HOMING',
    'READY 확인',
  ]
  const [step, setStep] = useState(-1)
  const [homing, setHoming] = useState(false)

  return (
    <div style={{ width: '100%', height: '100%', background: '#0a0000', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 32 }}>
      {/* E-STOP header */}
      <div style={{
        width: '100%', maxWidth: 800, background: '#dc2626', border: '4px solid #7f1d1d',
        padding: '20px 32px', textAlign: 'center', marginBottom: 28
      }}>
        <div style={{ fontSize: 48, fontWeight: 900, color: 'white', letterSpacing: '0.1em', fontFamily: 'JetBrains Mono, monospace', lineHeight: 1 }}>
          🛑 EMERGENCY STOP
        </div>
        <div style={{ fontSize: 18, color: '#fecaca', marginTop: 8, fontWeight: 600 }}>
          장비 구동이 중지되었습니다.
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20, width: '100%', maxWidth: 800 }}>
        {/* Status */}
        <div style={{ background: '#1a0000', border: '2px solid #dc2626', padding: '18px 22px' }}>
          <div style={{ color: '#f87171', fontWeight: 700, fontSize: 14, marginBottom: 12, letterSpacing: '0.05em' }}>시스템 상태</div>
          <div style={{ color: '#fca5a5', fontSize: 14, marginBottom: 6 }}>• 자동 작업이 모두 중단되었습니다.</div>
          <div style={{ color: '#fca5a5', fontSize: 14, marginBottom: 6 }}>• 비상정지 스위치를 확인하십시오.</div>
          <div style={{ color: '#fca5a5', fontSize: 14 }}>• 원인을 확인하기 전에 재가동하지 마십시오.</div>
        </div>

        {/* Recovery procedure */}
        <div style={{ background: '#1a0000', border: '2px solid #7f1d1d', padding: '18px 22px' }}>
          <div style={{ color: '#f87171', fontWeight: 700, fontSize: 14, marginBottom: 12, letterSpacing: '0.05em' }}>복구 절차</div>
          {steps.map((s, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
              <div style={{
                width: 22, height: 22, background: step > i ? '#16a34a' : step === i ? '#f59e0b' : '#374151',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                color: 'white', fontSize: 11, fontWeight: 700, flexShrink: 0
              }}>
                {step > i ? '✓' : i + 1}
              </div>
              <span style={{ color: step > i ? '#4ade80' : step === i ? '#fcd34d' : '#9ca3af', fontSize: 13, fontWeight: step === i ? 700 : 400 }}>
                {s}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 12, marginTop: 24 }}>
        <button
          className="btn-warning"
          style={{ padding: '12px 24px', fontSize: 15 }}
          onClick={() => { if (step < 4) setStep(s => s + 1) }}
        >
          {step < 0 ? '복구 시작' : step < 4 ? '다음 단계' : '완료 확인'}
        </button>
        {step >= 3 && (
          <button
            className="btn-primary"
            style={{ padding: '12px 24px', fontSize: 15 }}
            disabled={homing}
            onClick={async () => {
              setHoming(true)

              const success = await onHome()

              setHoming(false)

              if (success) {
                setStep(4)
              } else {
                alert('Stage 원점 복귀 요청에 실패했습니다.')
              }
            }}
          >
            {homing ? <span className="blink">원점 복귀 요청 중...</span> : '⌂ 원점 복귀 실행'}
          </button>
        )}
        {step >= 4 && (
          <button className="btn-green" style={{ padding: '12px 24px', fontSize: 15 }} onClick={onDismiss}>
            ✓ READY — 메인으로
          </button>
        )}
      </div>
    </div>
  )
}



// ============================================================
// SETTINGS PANEL: CAMERA / VISION
// ============================================================
function CameraControlScreen({
  onBack,
  onStage,
}: {
  onBack: () => void
  onStage: () => void
}) {
  const [status, setStatus] = useState<any>(null)
  const [detection, setDetection] = useState<any>(null)
  const [profiles, setProfiles] = useState<any[]>([])
  const [calibration, setCalibration] = useState<any>(null)
  const [selectedProfile, setSelectedProfile] = useState('')
  const [cameraIndex, setCameraIndex] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [autoDetect, setAutoDetect] = useState(true)
  const [streamKey, setStreamKey] = useState(0)

  const api = 'http://127.0.0.1:8000'

  const loadStatus = async () => {
    try {
      const response = await fetch(`${api}/vision/status`)
      if (!response.ok) throw new Error(`Camera status 오류: ${response.status}`)
      const data = await response.json()
      setStatus(data)

      if (Number.isFinite(Number(data?.camera_index))) {
        setCameraIndex(Number(data.camera_index))
      }
    } catch (error) {
      console.error('[CAMERA SETTINGS] status 오류:', error)
      setStatus(null)
    }
  }

  const loadProfiles = async () => {
    try {
      const response = await fetch(`${api}/vision/camera/profiles`)
      if (!response.ok) throw new Error(`Camera profile 오류: ${response.status}`)
      const data = await response.json()
      setProfiles(Array.isArray(data?.profiles) ? data.profiles : [])

      const selected =
        data?.profiles?.find((profile: any) => profile.selected)
        ?? data?.profiles?.[0]

      if (selected?.file) {
        setSelectedProfile(String(selected.file))
      }

      if (Number.isFinite(Number(data?.camera_index))) {
        setCameraIndex(Number(data.camera_index))
      }
    } catch (error) {
      console.error('[CAMERA SETTINGS] profile 오류:', error)
      setProfiles([])
    }
  }

  const loadCalibration = async () => {
    try {
      const response = await fetch(`${api}/vision/calibration/status`)
      if (!response.ok) throw new Error(`Calibration status 오류: ${response.status}`)
      setCalibration(await response.json())
    } catch (error) {
      console.error('[CAMERA SETTINGS] calibration status 오류:', error)
      setCalibration(null)
    }
  }

  const loadDetection = async () => {
    try {
      const response = await fetch(`${api}/vision/aruco`)
      if (!response.ok) throw new Error(`ArUco 오류: ${response.status}`)
      setDetection(await response.json())
    } catch (error) {
      console.error('[CAMERA SETTINGS] ArUco 오류:', error)
      setDetection(null)
    }
  }

  const refreshAll = async () => {
    await Promise.all([
      loadStatus(),
      loadProfiles(),
      loadCalibration(),
      loadDetection(),
    ])
  }

  useEffect(() => {
    refreshAll()

    const statusTimer = setInterval(() => {
      loadStatus()
      loadCalibration()
    }, 1000)

    return () => clearInterval(statusTimer)
  }, [])

  useEffect(() => {
    if (!autoDetect) return

    loadDetection()
    const timer = setInterval(loadDetection, 700)
    return () => clearInterval(timer)
  }, [autoDetect])

  const postJson = async (
    endpoint: string,
    body?: Record<string, unknown>,
  ) => {
    const response = await fetch(
      `${api}${endpoint}`,
      {
        method: 'POST',
        headers: body
          ? { 'Content-Type': 'application/json' }
          : undefined,
        body: body
          ? JSON.stringify(body)
          : undefined,
      }
    )

    const data = await response.json().catch(() => ({}))

    if (!response.ok) {
      throw new Error(
        data?.detail
        ?? data?.message
        ?? `서버 오류: ${response.status}`
      )
    }

    return data
  }

  const applyCamera = async () => {
    if (!selectedProfile || busy) return

    setBusy(true)
    setMessage('카메라 설정 적용 중...')

    try {
      const result = await postJson(
        '/vision/camera/select',
        {
          profile_name: selectedProfile,
          camera_index: cameraIndex,
        }
      )

      setMessage(
        result?.message
        ?? '카메라 설정 적용 완료'
      )
      setStreamKey(key => key + 1)
      await refreshAll()
    } catch (error) {
      const msg = error instanceof Error ? error.message : '카메라 설정 실패'
      setMessage(`ERROR: ${msg}`)
    } finally {
      setBusy(false)
    }
  }

  const addSample = async () => {
    if (busy) return

    setBusy(true)
    setMessage('체커보드 검출 중...')

    try {
      const result = await postJson('/vision/calibration/sample')
      setMessage(result?.message ?? '샘플 처리 완료')
      await loadCalibration()
    } catch (error) {
      const msg = error instanceof Error ? error.message : '샘플 추가 실패'
      setMessage(`ERROR: ${msg}`)
    } finally {
      setBusy(false)
    }
  }

  const clearSamples = async () => {
    if (busy) return
    if (!window.confirm('수집한 캘리브레이션 샘플을 모두 지우시겠습니까?')) return

    setBusy(true)

    try {
      const result = await postJson('/vision/calibration/clear')
      setMessage(result?.message ?? '샘플 초기화 완료')
      await loadCalibration()
    } catch (error) {
      const msg = error instanceof Error ? error.message : '초기화 실패'
      setMessage(`ERROR: ${msg}`)
    } finally {
      setBusy(false)
    }
  }

  const runCalibration = async () => {
    if (busy) return

    const sampleCount = Number(calibration?.sample_count ?? 0)
    if (sampleCount < 10) {
      setMessage('ERROR: 유효한 체커보드 샘플이 최소 10장 필요합니다.')
      return
    }

    if (!window.confirm(
      `현재 선택된 ${selectedProfile || '카메라 프로파일'}에 새 Intrinsic을 저장합니다.\n\n계속하시겠습니까?`
    )) {
      return
    }

    setBusy(true)
    setMessage('Intrinsic 캘리브레이션 계산 중...')

    try {
      const result = await postJson('/vision/calibration/run')
      const rms = Number(result?.rms_reprojection_error)

      setMessage(
        Number.isFinite(rms)
          ? `캘리브레이션 완료 - RMS ${rms.toFixed(4)}`
          : (result?.message ?? '캘리브레이션 완료')
      )

      setStreamKey(key => key + 1)
      await refreshAll()
    } catch (error) {
      const msg = error instanceof Error ? error.message : '캘리브레이션 실패'
      setMessage(`ERROR: ${msg}`)
    } finally {
      setBusy(false)
    }
  }

  const connected = status?.connected === true
  const calibrated = status?.camera_calibrated === true
  const markerDetected = detection?.detected === true
  const pose = detection?.pose_rpy_deg
  const grip = detection?.grip_target_camera_mm
  const sampleCount = Number(calibration?.sample_count ?? 0)
  const minimumSamples = Number(calibration?.minimum_samples ?? 10)

  const formatValue = (value: unknown, digits = 2) => {
    const number = Number(value)
    return Number.isFinite(number)
      ? number.toFixed(digits)
      : '--'
  }

  return (
    <div style={{
      flex: 1,
      background: '#e5eaf0',
      padding: 18,
      overflow: 'auto'
    }}>
      <div style={{
        background: 'var(--hmi-navy)',
        color: 'white',
        padding: '10px 16px',
        fontWeight: 900,
        fontSize: 16,
        letterSpacing: '0.08em'
      }}>
        SETTINGS / CAMERA · VISION
      </div>

      <div style={{
        display: 'flex',
        gap: 8,
        marginTop: 12
      }}>
        <button className="btn-secondary" onClick={onStage}>
          STAGE / STM32
        </button>
        <button className="btn-primary" disabled>
          CAMERA / VISION
        </button>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(520px, 1.45fr) minmax(360px, 1fr)',
        gap: 14,
        marginTop: 14
      }}>
        <div style={{
          background: 'white',
          border: '1px solid var(--hmi-border)',
          padding: 14
        }}>
          <div className="section-header">
            Live Camera / ArUco Overlay
          </div>

          <div style={{
            background: '#07111f',
            border: '1px solid #334155',
            minHeight: 360,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            overflow: 'hidden',
            marginTop: 10
          }}>
            {connected ? (
              <img
                key={streamKey}
                src={`${api}/vision/stream?annotate=true&v=${streamKey}`}
                alt="Camera live stream"
                style={{
                  width: '100%',
                  maxHeight: 520,
                  objectFit: 'contain',
                  display: 'block'
                }}
              />
            ) : (
              <div style={{
                color: '#94a3b8',
                fontFamily: 'JetBrains Mono, monospace',
                textAlign: 'center'
              }}>
                CAMERA DISCONNECTED
              </div>
            )}
          </div>

          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 8,
            marginTop: 10
          }}>
            {[
              ['Camera', connected ? 'CONNECTED' : 'DISCONNECTED', connected],
              ['Intrinsic', calibrated ? 'CALIBRATED' : 'NOT CALIBRATED', calibrated],
              ['ArUco', markerDetected ? 'DETECTED' : 'NOT DETECTED', markerDetected],
            ].map(([label, value, ok]) => (
              <div
                key={String(label)}
                style={{
                  border: '1px solid var(--hmi-border-light)',
                  padding: '9px 10px',
                  background: '#f8fafc'
                }}
              >
                <div style={{ fontSize: 10, color: '#64748b' }}>
                  {String(label)}
                </div>
                <div style={{
                  marginTop: 2,
                  fontSize: 12,
                  fontWeight: 800,
                  fontFamily: 'JetBrains Mono, monospace',
                  color: ok ? 'var(--hmi-green)' : 'var(--hmi-red)'
                }}>
                  {String(value)}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 14
        }}>
          <div style={{
            background: 'white',
            border: '1px solid var(--hmi-border)',
            padding: 14
          }}>
            <div className="section-header">
              Camera Source
            </div>

            <label style={{
              display: 'block',
              fontSize: 11,
              color: '#64748b',
              marginTop: 10,
              marginBottom: 4
            }}>
              Camera Profile
            </label>

            <select
              value={selectedProfile}
              onChange={event => {
                const file = event.target.value
                setSelectedProfile(file)

                const profile = profiles.find(
                  item => item.file === file
                )

                if (profile && Number.isFinite(Number(profile.camera_index_hint))) {
                  setCameraIndex(Number(profile.camera_index_hint))
                }
              }}
              style={{
                width: '100%',
                padding: '7px 8px',
                border: '1px solid var(--hmi-border)',
                background: 'white'
              }}
            >
              {profiles.map(profile => (
                <option key={profile.file} value={profile.file}>
                  {profile.file}
                  {profile.calibrated ? ' [CAL]' : ' [UNCAL]'}
                </option>
              ))}
            </select>

            <label style={{
              display: 'block',
              fontSize: 11,
              color: '#64748b',
              marginTop: 10,
              marginBottom: 4
            }}>
              Camera Index
            </label>

            <input
              type="number"
              min={0}
              value={cameraIndex}
              onChange={event => setCameraIndex(Number(event.target.value))}
              style={{
                width: '100%',
                padding: '7px 8px',
                border: '1px solid var(--hmi-border)'
              }}
            />

            <button
              className="btn-primary"
              disabled={busy || !selectedProfile}
              onClick={applyCamera}
              style={{
                width: '100%',
                marginTop: 10
              }}
            >
              카메라 설정 적용
            </button>

            <div style={{
              marginTop: 10,
              fontSize: 11,
              color: '#64748b',
              lineHeight: 1.6,
              fontFamily: 'JetBrains Mono, monospace'
            }}>
              <div>MODE : {status?.mode ?? 'OFFLINE'}</div>
              <div>INDEX : {status?.camera_index ?? '--'}</div>
              <div>PROFILE : {status?.camera_profile_name ?? '--'}</div>
              <div>SIZE : {status?.image_width ?? '--'} × {status?.image_height ?? '--'}</div>
              <div>RMS : {formatValue(status?.rms_reprojection_error, 4)}</div>
              <div>
                EXTRINSIC :
                {' '}
                {status?.camera_to_stage_calibrated ? 'CALIBRATED' : 'NOT CALIBRATED'}
              </div>
            </div>
          </div>

          <div style={{
            background: 'white',
            border: '1px solid var(--hmi-border)',
            padding: 14
          }}>
            <div className="section-header">
              ArUco / Pose Monitor
            </div>

            <div style={{
              display: 'flex',
              gap: 8,
              marginTop: 10
            }}>
              <button
                className="btn-secondary"
                onClick={loadDetection}
              >
                1회 검출
              </button>
              <button
                className={autoDetect ? 'btn-primary' : 'btn-secondary'}
                onClick={() => setAutoDetect(value => !value)}
              >
                AUTO {autoDetect ? 'ON' : 'OFF'}
              </button>
            </div>

            <div style={{
              marginTop: 10,
              display: 'grid',
              gridTemplateColumns: '1fr 1fr',
              gap: 6,
              fontSize: 12
            }}>
              {[
                ['ArUco ID', detection?.aruco_id ?? '--'],
                ['Tray', detection?.tray_label ?? detection?.tray_code ?? '--'],
                ['Pose', detection?.pose_valid ? 'VALID' : 'INVALID'],
                ['Pose Limit', detection?.pose_ok ? 'OK' : 'NG'],
                ['Roll', `${formatValue(pose?.roll)} °`],
                ['Pitch', `${formatValue(pose?.pitch)} °`],
                ['Yaw', `${formatValue(pose?.yaw)} °`],
                ['Image Yaw', `${formatValue(detection?.image_yaw_deg)} °`],
                ['Grip X', `${formatValue(grip?.x)} mm`],
                ['Grip Y', `${formatValue(grip?.y)} mm`],
                ['Grip Z', `${formatValue(grip?.z)} mm`],
                [
                  'Stage Correction',
                  detection?.ready_for_stage_correction ? 'READY' : 'BLOCKED'
                ],
              ].map(([label, value]) => (
                <div
                  key={String(label)}
                  style={{
                    display: 'flex',
                    gap: 8,
                    justifyContent: 'space-between',
                    borderBottom: '1px solid #e5e7eb',
                    padding: '6px 2px'
                  }}
                >
                  <span style={{ color: '#64748b' }}>{String(label)}</span>
                  <strong style={{
                    fontFamily: 'JetBrains Mono, monospace',
                    textAlign: 'right'
                  }}>
                    {String(value)}
                  </strong>
                </div>
              ))}
            </div>

            <div style={{
              marginTop: 8,
              fontSize: 11,
              color: detection?.pose_ok === false
                ? 'var(--hmi-red)'
                : '#64748b'
            }}>
              {detection?.message ?? 'ArUco 마커를 카메라에 보여주세요.'}
            </div>
          </div>
        </div>
      </div>

      <div style={{
        background: 'white',
        border: '1px solid var(--hmi-border)',
        marginTop: 14,
        padding: 16
      }}>
        <div className="section-header">
          Camera Intrinsic Calibration
        </div>

        <div style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 16,
          marginTop: 10
        }}>
          <div>
            <div style={{
              fontSize: 12,
              lineHeight: 1.8,
              color: '#374151'
            }}>
              <div>
                체커보드 :
                {' '}
                <strong>
                  {calibration?.pattern?.inner_cols ?? 9}
                  ×
                  {calibration?.pattern?.inner_rows ?? 6}
                </strong>
                {' '}
                inner corners
              </div>
              <div>
                Square :
                {' '}
                <strong>
                  {formatValue(calibration?.pattern?.square_mm ?? 25, 1)} mm
                </strong>
              </div>
              <div>
                Sample :
                {' '}
                <strong style={{
                  color:
                    sampleCount >= minimumSamples
                      ? 'var(--hmi-green)'
                      : 'var(--hmi-orange)'
                }}>
                  {sampleCount} / {minimumSamples}+
                </strong>
              </div>
              <div>
                Current Intrinsic :
                {' '}
                <strong>
                  {calibration?.calibrated ? 'CALIBRATED' : 'NOT CALIBRATED'}
                </strong>
              </div>
              <div>
                Current RMS :
                {' '}
                <strong>
                  {formatValue(calibration?.rms_reprojection_error, 4)}
                </strong>
              </div>
            </div>

            <div style={{
              marginTop: 8,
              fontSize: 11,
              color: '#64748b',
              lineHeight: 1.6
            }}>
              체커보드를 화면 전체의 서로 다른 위치·거리·각도로 이동시키면서
              샘플을 추가하세요. 기존 calibration.py의 동일 알고리즘을 사용합니다.
            </div>
          </div>

          <div style={{
            display: 'flex',
            flexDirection: 'column',
            gap: 8,
            justifyContent: 'center'
          }}>
            <button
              className="btn-primary"
              disabled={busy || !connected}
              onClick={addSample}
            >
              + 현재 프레임 샘플 추가
            </button>

            <button
              className="btn-secondary"
              disabled={busy}
              onClick={clearSamples}
            >
              샘플 초기화
            </button>

            <button
              className="btn-green"
              disabled={busy || sampleCount < minimumSamples}
              onClick={runCalibration}
            >
              Intrinsic 계산 및 YAML 저장
            </button>
          </div>
        </div>
      </div>

      <div style={{
        marginTop: 12,
        minHeight: 20,
        fontSize: 12,
        fontFamily: 'JetBrains Mono, monospace',
        color:
          message.startsWith('ERROR')
            ? 'var(--hmi-red)'
            : '#374151'
      }}>
        {message || 'READY'}
      </div>

      <div style={{ marginTop: 12 }}>
        <button
          className="btn-secondary"
          onClick={onBack}
        >
          ← 메인으로
        </button>
      </div>
    </div>
  )
}


// ============================================================
// SCREEN: SETTINGS / STAGE CONTROL
// ============================================================
function StageControlScreen({
  onBack,
}: {
  onBack: () => void
}) {
  const [settingsTab, setSettingsTab] =
    useState<'STAGE' | 'CAMERA'>('STAGE')

  const [status, setStatus] =
    useState<any>(null)

  const [busy, setBusy] =
    useState(false)

  const [message, setMessage] =
    useState('')


  const loadStatus = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/stage/status'
      )

      if (!response.ok) {
        throw new Error(
          `Stage status 오류: ${response.status}`
        )
      }

      const data =
        await response.json()

      setStatus(data)

    } catch (error) {
      console.error(
        '[STAGE CONTROL] status 오류:',
        error
      )

      setStatus(null)
    }
  }


  useEffect(() => {
    loadStatus()

    const timer =
      setInterval(
        loadStatus,
        500
      )

    return () =>
      clearInterval(timer)

  }, [])


  const runCommand = async (
    endpoint: string,
    successText: string
  ) => {
    if (busy) {
      return
    }

    setBusy(true)
    setMessage('처리 중...')

    try {
      const response = await fetch(
        `http://127.0.0.1:8000${endpoint}`,
        {
          method: 'POST',
        }
      )

      if (!response.ok) {
        throw new Error(
          `서버 오류: ${response.status}`
        )
      }

      const result =
        await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          'Stage 명령 실패'
        )
      }

      setMessage(successText)

      await loadStatus()

    } catch (error) {
      const msg =
        error instanceof Error
          ? error.message
          : 'Stage 명령 실패'

      setMessage(
        `ERROR: ${msg}`
      )
    } finally {
      setBusy(false)
    }
  }


  const isMock =
    status?.mock === true

  const connected =
    status?.connected === true

  const xHomed =
    isMock
      ? status?.homed === true
      : status?.homed?.x === true

  const zHomed =
    isMock
      ? status?.homed === true
      : status?.homed?.z === true

  const xPos =
    Number(
      status?.position?.x ?? 0
    )

  const zPos =
    Number(
      status?.position?.z ?? 0
    )

  const xMode =
    isMock
      ? (
          status?.moving
            ? 'MOVING'
            : xHomed
              ? 'READY'
              : 'NOT HOMED'
        )
      : (
          status?.mode?.x
          ?? 'UNKNOWN'
        )

  const zMode =
    isMock
      ? (
          status?.moving
            ? 'MOVING'
            : zHomed
              ? 'READY'
              : 'NOT HOMED'
        )
      : (
          status?.mode?.z
          ?? 'UNKNOWN'
        )


  if (settingsTab === 'CAMERA') {
    return (
      <CameraControlScreen
        onBack={onBack}
        onStage={() => setSettingsTab('STAGE')}
      />
    )
  }


  return (
    <div style={{
      flex: 1,
      background: '#e5eaf0',
      padding: 18,
      overflow: 'auto'
    }}>
      <div style={{
        background: 'var(--hmi-navy)',
        color: 'white',
        padding: '10px 16px',
        fontWeight: 900,
        fontSize: 16,
        letterSpacing: '0.08em'
      }}>
        SETTINGS / STAGE CONTROL
      </div>

      <div style={{
        display: 'flex',
        gap: 8,
        marginTop: 12
      }}>
        <button className="btn-primary" disabled>
          STAGE / STM32
        </button>
        <button
          className="btn-secondary"
          onClick={() => setSettingsTab('CAMERA')}
        >
          CAMERA / VISION
        </button>
      </div>

      <div style={{
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gap: 14,
        marginTop: 14
      }}>

        <div style={{
          background: 'white',
          border: '1px solid var(--hmi-border)',
          padding: 16
        }}>
          <div className="section-header">
            연결 상태
          </div>

          {[
            {
              label: 'Mode',
              value:
                status == null
                  ? 'OFFLINE'
                  : isMock
                    ? 'MOCK'
                    : 'STM32'
            },
            {
              label: 'STM32',
              value:
                isMock
                  ? 'DISCONNECTED'
                  : connected
                    ? 'CONNECTED'
                    : 'DISCONNECTED'
            },
            {
              label: 'Stage State',
              value:
                status?.state
                ?? (
                  isMock
                    ? 'MOCK'
                    : 'UNKNOWN'
                )
            }
          ].map(row => (
            <div
              key={row.label}
              style={{
                display: 'flex',
                padding: '9px 4px',
                borderBottom:
                  '1px solid #e5e7eb'
              }}
            >
              <span style={{ flex: 1 }}>
                {row.label}
              </span>

              <strong style={{
                fontFamily:
                  'JetBrains Mono, monospace'
              }}>
                {row.value}
              </strong>
            </div>
          ))}
        </div>


        <div style={{
          background: 'white',
          border: '1px solid var(--hmi-border)',
          padding: 16
        }}>
          <div className="section-header">
            Limit Switch
          </div>

          {[
            ['X MIN', status?.limits?.X_MIN],
            ['X MAX', status?.limits?.X_MAX],
            ['Z MIN', status?.limits?.Z_MIN],
            ['Z MAX', status?.limits?.Z_MAX],
          ].map(([label, value]) => (
            <div
              key={String(label)}
              style={{
                display: 'flex',
                padding: '9px 4px',
                borderBottom:
                  '1px solid #e5e7eb'
              }}
            >
              <span style={{ flex: 1 }}>
                {String(label)}
              </span>

              <strong>
                {value ? 'ON' : 'OFF'}
              </strong>
            </div>
          ))}
        </div>
      </div>


      <div style={{
        background: 'white',
        border: '1px solid var(--hmi-border)',
        marginTop: 14,
        padding: 16
      }}>
        <div className="section-header">
          Axis Status
        </div>

        {[
          {
            axis: 'X',
            pos: xPos,
            homed: xHomed,
            mode: xMode,
          },
          {
            axis: 'Z',
            pos: zPos,
            homed: zHomed,
            mode: zMode,
          },
        ].map(axis => (
          <div
            key={axis.axis}
            style={{
              display: 'grid',
              gridTemplateColumns:
                '90px 1fr 160px 160px',
              alignItems: 'center',
              gap: 12,
              padding: '12px 6px',
              borderBottom:
                '1px solid #e5e7eb'
            }}
          >
            <strong>
              {axis.axis} Axis
            </strong>

            <span style={{
              fontFamily:
                'JetBrains Mono, monospace',
              fontSize: 18,
              fontWeight: 800
            }}>
              {axis.pos.toFixed(3)} mm
            </span>

            <span>
              HOME :
              {' '}
              <strong style={{
                color:
                  axis.homed
                    ? 'var(--hmi-green)'
                    : 'var(--hmi-red)'
              }}>
                {axis.homed
                  ? 'DONE'
                  : 'REQUIRED'}
              </strong>
            </span>

            <span>
              MODE :
              {' '}
              <strong>
                {axis.mode}
              </strong>
            </span>
          </div>
        ))}
      </div>


      <div style={{
        background: 'white',
        border: '1px solid var(--hmi-border)',
        marginTop: 14,
        padding: 16
      }}>
        <div className="section-header">
          Manual Control
        </div>

        <div style={{
          display: 'flex',
          gap: 10,
          marginTop: 12
        }}>
          <button
            className="btn-primary"
            disabled={busy}
            onClick={() =>
              runCommand(
                '/stage/home',
                'X/Z HOME 완료'
              )
            }
          >
            ⌂ X/Z HOME
          </button>

          <button
            className="btn-danger"
            disabled={busy}
            onClick={() =>
              runCommand(
                '/stage/stop',
                'HARD STOP 완료'
              )
            }
          >
            ■ HARD STOP
          </button>

          <button
            className="btn-warning"
            disabled={busy}
            onClick={() => {
              const ok = window.confirm(
                'Stage RESET 후 HOME이 다시 필요합니다.\n\nRESET하시겠습니까?'
              )

              if (ok) {
                runCommand(
                  '/stage/reset',
                  'RESET 완료 - HOME 필요'
                )
              }
            }}
          >
            ↺ RESET
          </button>

          <button
            className="btn-secondary"
            disabled={busy}
            onClick={loadStatus}
          >
            ⟳ STATUS 갱신
          </button>
        </div>

        <div style={{
          marginTop: 12,
          fontFamily:
            'JetBrains Mono, monospace',
          fontSize: 12,
          color:
            message.startsWith('ERROR')
              ? 'var(--hmi-red)'
              : '#374151'
        }}>
          {message || 'READY'}
        </div>

        <div style={{
          marginTop: 10,
          fontSize: 11,
          color: '#6b7280'
        }}>
          RESET은 E-STOP/오류 상태를 해제하지만
          HOME 기준도 해제됩니다.
          RESET 후 반드시 X/Z HOME을 다시 수행하세요.
        </div>
      </div>


      <div style={{
        marginTop: 16
      }}>
        <button
          className="btn-secondary"
          onClick={onBack}
        >
          ← 메인으로
        </button>
      </div>
    </div>
  )
}


// ============================================================
// MAIN APP
// ============================================================
export default function App() {
  const [screen, setScreen] = useState<Screen>('MAIN')
  const [prevScreen, setPrevScreen] = useState<Screen>('MAIN')
  const [isPaused, setIsPaused] = useState(false)
  const [showStopConfirm, setShowStopConfirm] = useState(false)
  const [currentItemIndex, setCurrentItemIndex] = useState(0)
  const [trays, setTrays] = useState(TRAYS_INITIAL)
  const [workItems, setWorkItems] = useState(WORK_ITEMS_INITIAL)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [previewType, setPreviewType] = useState('')
  const [previewFileName, setPreviewFileName] = useState('')
  const [workSessionId, setWorkSessionId] = useState('')
  const [analysisId, setAnalysisId] = useState('')
  const [restartKey, setRestartKey] = useState(0)
  const [verificationMode, setVerificationMode] = useState<'AUTO' | 'MANUAL'>('AUTO')
  const [workHistory, setWorkHistory] = useState<WorkHistoryRecord[]>([])



  // ============================================================
  // Python inventory.json → React UI 재고 동기화
  // ============================================================
  useEffect(() => {
    const loadInventory = async () => {
      try {
        const response = await fetch(
          'http://127.0.0.1:8000/inventory'
        )

        if (!response.ok) {
          throw new Error(
            `재고 서버 오류: ${response.status}`
          )
        }

        const result = await response.json()

        if (!result.success || !result.data) {
          throw new Error(
            result.message || '재고 데이터를 가져오지 못했습니다.'
          )
        }

        const inventory = result.data

        setTrays(currentTrays =>
          currentTrays.map(tray => {
            const matchedEntry = Object.values(inventory).find(
              (item: any) =>
                Number(item.tray) ===
                Number(tray.id.match(/\d+/)?.[0])
            ) as any

            if (!matchedEntry) {
              return tray
            }

            const stock = Number(matchedEntry.stock ?? 0)
          const maxStock = 500

          return {
            ...tray,
            stock,
            maxStock,
            status: stock <= 100 ? 'LOW STOCK' : 'READY',
          }
          })
        )

        console.log(
          '[INVENTORY] Python 재고 동기화 완료:',
          inventory
        )

      } catch (error) {
        console.error(
          '[INVENTORY] 재고 불러오기 실패:',
          error
        )
      }
    }

    loadInventory()
  }, [])


  useEffect(() => {
    loadWorkHistory()
  }, [])


  useEffect(() => {
    loadRackLayout()
  }, [])

  // Rack slot layout — updated to Q-learning result after relocation
  const [rackSlots, setRackSlots] = useState<RackSlots>(RACK_SLOTS_INITIAL)
  const [finalSlots, setFinalSlots] = useState<RackSlots>(RACK_SLOTS_QLEARNING)

  const nav = useCallback((s: Screen) => {
    // nav는 화면 전환만 담당한다.
    // 실제 Stage 이동 명령은 goToTrayMoving() 한 곳에서만 보낸다.
    setPrevScreen(screen)
    setScreen(s)
  }, [screen])

  useEffect(() => {
    return () => {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl)
      }
    }
  }, [previewUrl])

  // 실제 작업지시서 파일 → FastAPI → work_order_ocr.py
  const handleFileUpload = async (file: File) => {
    const nextPreviewUrl = URL.createObjectURL(file)

    // 같은 작업에서 재고가 두 번 차감되지 않도록
    // 파일을 새로 선택할 때 작업 세션 ID를 한 번 생성한다.
    const newWorkSessionId =
      `${Date.now()}-${file.name}`

    setWorkSessionId(newWorkSessionId)

    const newAnalysisId =
      `${Date.now()}-${Math.random().toString(36).slice(2)}`

    setAnalysisId(newAnalysisId)

    setPreviewUrl(nextPreviewUrl)
    setPreviewType(file.type)
    setPreviewFileName(file.name)

    nav('ANALYZING')

    const formData = new FormData()
    formData.append('file', file)
    formData.append(
      'analysis_id',
      newAnalysisId
    )

    let progressPolling = true

    const pollProgress = async () => {
      while (progressPolling) {
        try {
          const progressResponse = await fetch(
            `http://127.0.0.1:8000/analysis-progress/${newAnalysisId}`
          )

          if (progressResponse.ok) {
            const progressData =
              await progressResponse.json()

            window.dispatchEvent(
              new CustomEvent(
                'analysis-progress',
                {
                  detail: {
                    step:
                      Number(
                        progressData.step ?? 0
                      ),
                    message:
                      String(
                        progressData.message ??
                        '처리 중...'
                      ),
                  },
                }
              )
            )

            if (progressData.done) {
              progressPolling = false
              break
            }
          }
        } catch (pollError) {
          console.error(
            '분석 진행률 조회 오류:',
            pollError
          )
        }

        await new Promise(
          resolve =>
            setTimeout(resolve, 150)
        )
      }
    }

    const pollingPromise = pollProgress()

    try {
      const response = await fetch(
        'http://127.0.0.1:8000/analyze-work-order',
        {
          method: 'POST',
          body: formData,
        }
      )

      if (!response.ok) {
        throw new Error(`서버 오류: ${response.status}`)
      }

      const result = await response.json()

      console.log('Python 분석 결과:', result)

      if (!result.success) {
        throw new Error(result.message || '작업지시서 분석 실패')
      }

      const sourceItems = Array.isArray(result?.data?.items)
        ? result.data.items
        : []

      if (sourceItems.length === 0) {
        throw new Error('분석 결과에 품목이 없습니다.')
      }

      const convertedItems: WorkItem[] = sourceItems.map(
        (item: any, index: number) => {
          const trayNumber = Number(item.tray)

          const trayId = Number.isNaN(trayNumber)
            ? String(item.tray ?? '-')
            : `TRAY ${String(trayNumber).padStart(2, '0')}`

          const trayInfo = TRAYS_INITIAL.find(
            tray => tray.id === trayId
          )

          const quantity = Number(item.quantity ?? 0)

          let status: WorkItem['status'] =
            item.status === 'OK' ? '정상' : '확인 필요'

          if (
            trayInfo &&
            Number.isFinite(quantity) &&
            quantity > trayInfo.stock
          ) {
            status = '재고 부족'
          }

          return {
            no: String(item.row ?? index + 1).padStart(2, '0'),
            partNo: String(item.part_no ?? '-'),
            name: String(item.name ?? '-'),
            spec: String(item.spec ?? '-'),
            qty: Number.isFinite(quantity) ? quantity : 0,
            unit: 'EA',
            tray: trayId,
            stock: trayInfo?.stock ?? 0,
            status,
          }
        }
      )

      setWorkItems(convertedItems)
      setCurrentItemIndex(0)

      progressPolling = false
      await pollingPromise

      window.dispatchEvent(
        new CustomEvent(
          'analysis-progress',
          {
            detail: {
              step: 6,
              message: '재고 확인 완료',
            },
          }
        )
      )

      // 실제 Python 6단계 완료를 잠깐 보여준 뒤 REVIEW 이동
      await new Promise(
        resolve => setTimeout(resolve, 250)
      )

      nav('REVIEW')

    } catch (error) {
      progressPolling = false
      console.error('작업지시서 분석 오류:', error)

      const message =
        error instanceof Error
          ? error.message
          : '알 수 없는 오류'

      alert(`작업지시서 분석에 실패했습니다.\n\n${message}`)
      nav('MAIN')
    }
  }

  const handlePause = async () => {
    setShowStopConfirm(false)
    setIsPaused(true)

    // 현재는 MockStageAdapter가 응답.
    // 실제 STM32 연결 후에도 UI 코드는 그대로 사용한다.
    await requestStagePause()
  }

  const handleResume = async () => {
    const resumed = await requestStageResume()

    if (resumed) {
      setIsPaused(false)
    } else {
      alert('Stage 작업 재개 요청에 실패했습니다.')
    }
  }

  const handleRestartCurrentStage = async () => {
    // 현재 화면 컴포넌트를 새로 생성해서
    // 화면 내부 진행 상태를 처음(대기)부터 다시 시작한다.
    setIsPaused(false)
    setRestartKey(key => key + 1)

    // Tray 이동 화면을 다시 시작하는 경우에는
    // 동일한 Tray 이동 요청도 Python Stage 계층에 다시 보낸다.
    if (screen === 'TRAY_MOVING' && currentItem?.tray) {
      await requestStageMove(currentItem.tray)
    }
  }

  const handleStopRequest = () => {
    setIsPaused(false)
    setShowStopConfirm(true)
  }

  const handleStopConfirm = async () => {
    // 사용자가 '작업 중지'를 최종 확인한 시점에 Stage 정지 요청
    await requestStageStop()

    setShowStopConfirm(false)
    setIsPaused(false)
    setCurrentItemIndex(0)
    nav('MAIN')
  }

  const handleStopCancel = () => setShowStopConfirm(false)

  const handleEmergency = async () => {
    // 화면은 즉시 E-STOP 상태로 전환하고,
    // 동시에 Python backend에 실제 비상정지 요청을 보낸다.
    nav('EMERGENCY_STOP')
    await requestEmergencyStop()
  }


  // ============================================================
  // Stage API 연결
  // 현재는 FastAPI의 MockStageAdapter가 응답하고,
  // 나중에는 server.py 뒤의 실제 STM32StageAdapter로 교체한다.
  // ============================================================
  const requestStageMove = async (
    trayLabel: string
  ): Promise<{
    success: boolean
    message: string
  }> => {
    const match =
      trayLabel.match(/\d+/)

    if (!match) {
      return {
        success: false,
        message:
          `Tray 번호를 찾을 수 없습니다: ${trayLabel}`,
      }
    }

    const trayId =
      Number(match[0])

    try {
      const response = await fetch(
        `http://127.0.0.1:8000/stage/move-to-tray/${trayId}`,
        {
          method: 'POST',
        }
      )

      if (!response.ok) {
        throw new Error(
          `Stage 서버 오류: ${response.status}`
        )
      }

      const result =
        await response.json()

      if (!result.success) {
        return {
          success: false,
          message:
            result.message ||
            result.error ||
            'Stage 이동에 실패했습니다.',
        }
      }

      console.log(
        '[STAGE] 이동 완료:',
        result
      )

      return {
        success: true,
        message:
          result.message ||
          'Stage 이동 완료',
      }

    } catch (error) {
      console.error(
        '[STAGE] 이동 요청 실패:',
        error
      )

      return {
        success: false,
        message:
          error instanceof Error
            ? error.message
            : 'Stage 이동 요청 실패',
      }
    }
  }

  const requestStagePause = async (): Promise<boolean> => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/stage/pause',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Stage PAUSE 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      console.log(
        '[STAGE] PAUSE 요청 성공:',
        result
      )

      return true

    } catch (error) {
      console.error(
        '[STAGE] PAUSE 요청 실패:',
        error
      )

      return false
    }
  }


  const requestStageResume = async (): Promise<boolean> => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/stage/resume',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Stage RESUME 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      console.log(
        '[STAGE] RESUME 요청 성공:',
        result
      )

      return true

    } catch (error) {
      console.error(
        '[STAGE] RESUME 요청 실패:',
        error
      )

      return false
    }
  }


  const requestStageStop = async (): Promise<boolean> => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/stage/stop',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(`Stage STOP 서버 오류: ${response.status}`)
      }

      const result = await response.json()
      console.log('[STAGE] STOP 요청 성공:', result)
      return true

    } catch (error) {
      console.error('[STAGE] STOP 요청 실패:', error)
      return false
    }
  }

  const requestEmergencyStop = async (): Promise<boolean> => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/stage/emergency-stop',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(`Stage E-STOP 서버 오류: ${response.status}`)
      }

      const result = await response.json()
      console.log('[STAGE] E-STOP 요청 성공:', result)
      return true

    } catch (error) {
      console.error('[STAGE] E-STOP 요청 실패:', error)
      return false
    }
  }

  const requestStageHome = async (): Promise<boolean> => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/stage/home',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(`Stage HOME 서버 오류: ${response.status}`)
      }

      const result = await response.json()

      if (!result.success) {
        console.error(
          '[STAGE] HOME 실패:',
          result
        )
        return false
      }

      console.log(
        '[STAGE] HOME 요청 성공:',
        result
      )

      return true

    } catch (error) {
      console.error('[STAGE] HOME 요청 실패:', error)
      return false
    }
  }

  const applyInventoryToTrays = (
    inventory: Record<string, any>
  ) => {
    setTrays(currentTrays =>
      currentTrays.map(tray => {
        const trayNumber =
          Number(
            tray.id.match(/\d+/)?.[0]
          )

        const matchedEntry =
          Object.values(inventory).find(
            (item: any) =>
              Number(item.tray) === trayNumber
          ) as any

        if (!matchedEntry) {
          return tray
        }

        const stock =
          Number(matchedEntry.stock ?? 0)

        return {
          ...tray,
          stock,
          maxStock: 500,
          status:
            stock <= 100
              ? 'LOW STOCK'
              : 'READY',
        }
      })
    )
  }


  const consumeCompletedWorkInventory =
    async (): Promise<boolean> => {

      if (!workSessionId) {
        console.error(
          '[INVENTORY] 작업 세션 ID가 없습니다.'
        )
        return false
      }

      try {
        const response = await fetch(
          'http://127.0.0.1:8000/inventory/consume',
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify({
              work_id: workSessionId,
              items: workItems.map(item => ({
                part_no: item.partNo,
                quantity: item.qty,
              })),
            }),
          }
        )

        if (!response.ok) {
          throw new Error(
            `재고 차감 서버 오류: ${response.status}`
          )
        }

        const result = await response.json()

        if (!result.success) {
          throw new Error(
            result.message || '재고 차감 실패'
          )
        }

        if (result.data) {
          applyInventoryToTrays(
            result.data
          )
        }

        console.log(
          '[INVENTORY] 작업 완료 재고 차감:',
          result
        )

        return true

      } catch (error) {
        console.error(
          '[INVENTORY] 작업 완료 재고 차감 실패:',
          error
        )

        alert(
          '작업은 완료되었지만 재고 저장에 실패했습니다.\n' +
          '재고 상태를 확인해주세요.'
        )

        return false
      }
    }


  // ============================================================
  // 재배치 계획 요청
  //
  // 규칙:
  // - 이번 작업에서 사용한 Tray만 재배치 가능
  // - 사용하지 않은 Tray 위치는 절대 변경하지 않음
  // - 현재는 Q-learning 대신 테스트용으로 사용 Tray 순서를 뒤집음
  //   예: [TRAY 01, TRAY 02] -> [TRAY 02, TRAY 01]
  // - 나중에는 targetOrder 만드는 부분만 Q-learning 결과로 교체
  // ============================================================
  const requestRelocationPlan = async () => {
    const usedTrays = Array.from(
      new Set(
        workItems
          .map(item => item.tray)
          .filter(Boolean)
      )
    )

    // 사용 Tray가 하나뿐이면 자리 교환이 불가능하므로 그대로 유지
    if (usedTrays.length <= 1) {
      return {
        success: true,
        data: {
          used_trays: usedTrays,
          moves: [],
          final_slots: rackSlots,
        },
      }
    }

    // 현재는 통로 테스트용 임시 규칙
    const targetOrder = [...usedTrays].reverse()

    try {
      const response = await fetch(
        'http://127.0.0.1:8000/relocation/plan',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            current_slots: rackSlots,
            used_trays: usedTrays,
            target_order: targetOrder,
          }),
        }
      )

      if (!response.ok) {
        throw new Error(
          `재배치 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message || '재배치 계획 생성 실패'
        )
      }

      console.log(
        '[RELOCATION] 재배치 계획:',
        result.data
      )

      return result

    } catch (error) {
      console.error(
        '[RELOCATION] 재배치 계획 요청 실패:',
        error
      )

      return {
        success: false,
        message:
          error instanceof Error
            ? error.message
            : '재배치 계획 요청 실패',
      }
    }
  }


  const loadWorkHistory = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/history'
      )

      if (!response.ok) {
        throw new Error(
          `작업 이력 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          '작업 이력을 불러오지 못했습니다.'
        )
      }

      setWorkHistory(
        Array.isArray(result.data)
          ? result.data
          : []
      )

      return true

    } catch (error) {
      console.error(
        '[HISTORY] 불러오기 실패:',
        error
      )

      return false
    }
  }


  const saveCompletedWorkHistory =
    async (): Promise<boolean> => {

      if (!workSessionId) {
        console.error(
          '[HISTORY] 작업 세션 ID가 없습니다.'
        )

        return false
      }

      const usedTrays = Array.from(
        new Set(
          workItems
            .map(item => item.tray)
            .filter(Boolean)
        )
      )

      const startedAt = Number(
        workSessionId.split('-')[0]
      )

      const durationSeconds =
        Number.isFinite(startedAt)
          ? Math.max(
              0,
              Math.round(
                (Date.now() - startedAt) / 1000
              )
            )
          : 0

      try {
        const response = await fetch(
          'http://127.0.0.1:8000/history',
          {
            method: 'POST',
            headers: {
              'Content-Type':
                'application/json',
            },
            body: JSON.stringify({
              work_id: workSessionId,
              result: 'COMPLETED',
              duration_seconds:
                durationSeconds,
              used_trays: usedTrays,
              items: workItems.map(
                item => ({
                  part_no: item.partNo,
                  name: item.name,
                  spec: item.spec,
                  quantity: item.qty,
                  tray: item.tray,
                })
              ),
            }),
          }
        )

        if (!response.ok) {
          throw new Error(
            `작업 이력 저장 오류: ${response.status}`
          )
        }

        const result = await response.json()

        if (!result.success) {
          throw new Error(
            result.message ||
            '작업 이력 저장 실패'
          )
        }

        console.log(
          '[HISTORY] 저장 완료:',
          result
        )

        await loadWorkHistory()

        return true

      } catch (error) {
        console.error(
          '[HISTORY] 저장 실패:',
          error
        )

        alert(
          '작업은 완료되었지만 작업 이력 저장에 실패했습니다.'
        )

        return false
      }
    }


  // ============================================================
  // Vision API 연결
  //
  // 지금: MockVisionAdapter
  // 나중: 실제 YOLO / ArUco 구현체
  // ============================================================

  const requestVisionCount = async (
    item: WorkItem
  ) => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/vision/count',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            part_no: item.partNo,
            expected_quantity: item.qty,
          }),
        }
      )

      if (!response.ok) {
        throw new Error(
          `Vision count 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      console.log(
        '[VISION] 수량 확인:',
        result
      )

      return result

    } catch (error) {
      console.error(
        '[VISION] 수량 확인 실패:',
        error
      )

      return {
        success: false,
        matched: false,
        detected_quantity: 0,
      }
    }
  }


  const requestVisionAruco = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/vision/aruco'
      )

      if (!response.ok) {
        throw new Error(
          `Vision ArUco 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      console.log(
        '[VISION] ArUco 확인:',
        result
      )

      return result

    } catch (error) {
      console.error(
        '[VISION] ArUco 확인 실패:',
        error
      )

      return {
        success: false,
        detected: false,
        mock: false,
        tray_id: null,
        aruco_id: null,
      }
    }
  }


  // ============================================================
  // Workflow API
  //
  // 품목 순서를 React가 직접 계산하지 않고
  // Python WorkflowController가 관리한다.
  // ============================================================

  const requestWorkflowStart = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/start',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            items: workItems.map(item => ({
              part_no: item.partNo,
              name: item.name,
              spec: item.spec,
              tray: item.tray,
              quantity: item.qty,
            })),
          }),
        }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow 시작 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message || 'Workflow 시작 실패'
        )
      }

      console.log(
        '[WORKFLOW] 작업 시작:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] 작업 시작 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowNextItem = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/next-item',
        {
          method: 'POST',
        }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow 다음 품목 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message || '다음 품목 결정 실패'
        )
      }

      console.log(
        '[WORKFLOW] 다음 품목:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] 다음 품목 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowTrayArrived = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/tray-arrived',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow tray-arrived 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message || 'tray-arrived 처리 실패'
        )
      }

      console.log(
        '[WORKFLOW] TRAY 도착:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] TRAY 도착 처리 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowStartVision = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/start-vision',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow start-vision 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message || 'Vision 단계 진입 실패'
        )
      }

      console.log(
        '[WORKFLOW] Vision 시작:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] Vision 단계 진입 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowVisionPassed = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/vision-passed',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow vision-passed 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message || 'Vision 완료 처리 실패'
        )
      }

      console.log(
        '[WORKFLOW] Vision 통과:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] Vision 완료 처리 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowFinalVerificationPassed = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/final-verification-passed',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow final-verification 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          '최종 검증 완료 상태 반영 실패'
        )
      }

      console.log(
        '[WORKFLOW] 최종 검증 통과:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] 최종 검증 처리 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowTrayReturnComplete = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/tray-return-complete',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow tray-return-complete 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          'Tray 복귀 완료 상태 반영 실패'
        )
      }

      console.log(
        '[WORKFLOW] Tray 복귀 완료:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] Tray 복귀 완료 처리 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowRelocationComplete = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/relocation-complete',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow relocation-complete 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          '재배치 완료 상태 반영 실패'
        )
      }

      console.log(
        '[WORKFLOW] 재배치 완료:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] 재배치 완료 처리 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowInventoryComplete = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/inventory-complete',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow inventory-complete 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          '재고 완료 상태 반영 실패'
        )
      }

      console.log(
        '[WORKFLOW] 재고 반영 완료:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] 재고 완료 처리 실패:',
        error
      )

      return null
    }
  }


  const requestWorkflowHistoryComplete = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/workflow/history-complete',
        { method: 'POST' }
      )

      if (!response.ok) {
        throw new Error(
          `Workflow history-complete 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          '작업 이력 완료 상태 반영 실패'
        )
      }

      console.log(
        '[WORKFLOW] 작업 이력 저장 완료:',
        result.data
      )

      return result.data

    } catch (error) {
      console.error(
        '[WORKFLOW] 작업 이력 완료 처리 실패:',
        error
      )

      return null
    }
  }


  const goToTrayMoving = async (
    itemIndex: number
  ) => {
    const targetItem =
      workItems[itemIndex]

    if (!targetItem) {
      console.error(
        '[STAGE] 이동 대상 품목이 없습니다:',
        itemIndex
      )
      return
    }

    if (!targetItem.tray) {
      alert(
        '이동 대상 Tray 정보가 없습니다.'
      )
      return
    }

    setCurrentItemIndex(
      itemIndex
    )

    // 실제 Stage가 이동하는 동안
    // TRAY MOVING 화면을 먼저 보여준다.
    nav('TRAY_MOVING')

    const moveResult =
      await requestStageMove(
        targetItem.tray
      )

    if (!moveResult.success) {
      alert(
        `${targetItem.tray} 이동에 실패했습니다.\n\n`
        + moveResult.message
      )

      // 현재는 REVIEW로 복귀.
      // 이후 장비 오류 화면을 실제 오류정보 기반으로 개선 예정.
      nav('REVIEW')
    }
  }

  // ============================================================
  // Rack Layout 저장 / 복원
  // ============================================================

  const loadRackLayout = async () => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/rack-layout'
      )

      if (!response.ok) {
        throw new Error(
          `Rack layout 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      const slots =
        result?.data?.slots

      if (
        !result.success ||
        !Array.isArray(slots) ||
        slots.length !== 6
      ) {
        throw new Error(
          '저장된 Rack 배치 형식이 올바르지 않습니다.'
        )
      }

      const restoredSlots =
        slots as RackSlots

      setRackSlots(restoredSlots)
      setFinalSlots(restoredSlots)

      console.log(
        '[RACK] 마지막 배치 복원:',
        restoredSlots
      )

      return true

    } catch (error) {
      console.error(
        '[RACK] 마지막 배치 불러오기 실패:',
        error
      )

      // 불러오기에 실패하면 App.tsx의 기존 초기값을 유지한다.
      return false
    }
  }


  const saveRackLayout = async (
    slots: RackSlots
  ) => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/rack-layout',
        {
          method: 'POST',
          headers: {
            'Content-Type':
              'application/json',
          },
          body: JSON.stringify({
            slots,
          }),
        }
      )

      if (!response.ok) {
        throw new Error(
          `Rack layout 저장 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.success) {
        throw new Error(
          result.message ||
          'Rack layout 저장 실패'
        )
      }

      console.log(
        '[RACK] 현재 배치 저장:',
        result.data
      )

      return true

    } catch (error) {
      console.error(
        '[RACK] 배치 저장 실패:',
        error
      )

      alert(
        '작업은 완료되었지만 Rack 배치 저장에 실패했습니다.'
      )

      return false
    }
  }


  const currentItem = workItems[currentItemIndex] ?? workItems[0]
  const pauseStageLabel =
    screen === 'TRAY_MOVING' ? `${currentItem.tray} 작업 위치 이동` :
    screen === 'PICKING' ? '작업자 피킹' :
    screen === 'VERIFICATION' ? '카메라 수량 확인' :
    screen === 'FINAL_VERIFICATION' ? '최종 무게 검증' :
    screen === 'TRAY_RETURN' ? 'Tray 복귀 및 재배치' : screen

  const handleWorkStart = async () => {
    try {
      const stageResponse = await fetch(
        'http://127.0.0.1:8000/stage/status'
      )

      if (!stageResponse.ok) {
        throw new Error(
          `Stage 상태 조회 실패: ${stageResponse.status}`
        )
      }

      const stageStatus =
        await stageResponse.json()

      const isMock =
        stageStatus?.mock === true

      if (
        !isMock &&
        stageStatus?.connected !== true
      ) {
        alert(
          'STM32가 연결되어 있지 않습니다.\n\n'
          + 'Stage 연결 상태를 확인해주세요.'
        )
        return
      }

      const xHomed =
        isMock
          ? stageStatus?.homed === true
          : stageStatus?.homed?.x === true

      const zHomed =
        isMock
          ? stageStatus?.homed === true
          : stageStatus?.homed?.z === true

      if (!xHomed || !zHomed) {
        const runHome =
          window.confirm(
            'Stage HOME이 필요합니다.\n\n'
            + `X : ${xHomed ? 'HOME 완료' : 'NOT HOMED'}\n`
            + `Z : ${zHomed ? 'HOME 완료' : 'NOT HOMED'}\n\n`
            + '지금 X/Z HOME을 실행하시겠습니까?'
          )

        if (!runHome) {
          return
        }

        const homeSuccess =
          await requestStageHome()

        if (!homeSuccess) {
          alert(
            'Stage HOME에 실패했습니다.\n'
            + '설정 → Stage Control에서 상태를 확인해주세요.'
          )
          return
        }
      }

    } catch (error) {
      console.error(
        '[STAGE] 작업 시작 전 상태 검사 실패:',
        error
      )

      alert(
        'Stage 상태를 확인할 수 없습니다.'
      )
      return
    }

    const workflowState =
      await requestWorkflowStart()

    if (!workflowState) {
      alert(
        'Workflow 시작에 실패했습니다.'
      )
      return
    }

    const firstIndex =
      Number(
        workflowState.current_item_index
      )

    if (
      !Number.isInteger(firstIndex) ||
      firstIndex < 0 ||
      firstIndex >= workItems.length
    ) {
      alert(
        'Workflow가 올바른 첫 품목을 반환하지 않았습니다.'
      )
      return
    }

    await goToTrayMoving(
      firstIndex
    )
  }
  const handleTrayArrived = async () => {
    const workflowState =
      await requestWorkflowTrayArrived()

    if (!workflowState) {
      alert(
        'TRAY 도착 상태를 Workflow에 반영하지 못했습니다.'
      )
      return
    }

    if (
      workflowState.state !== 'PICKING'
    ) {
      alert(
        `Workflow 상태 오류: ${workflowState.state}`
      )
      return
    }

    nav('PICKING')
  }
  const handlePickingAutoVerify = async () => {
    const workflowState =
      await requestWorkflowStartVision()

    if (!workflowState) {
      alert(
        'Vision 단계 시작에 실패했습니다.'
      )
      return
    }

    if (
      workflowState.state !==
      'VISION_CHECK'
    ) {
      alert(
        `Workflow 상태 오류: ${workflowState.state}`
      )
      return
    }

    setVerificationMode('AUTO')
    nav('VERIFICATION')
  }

  const handlePickingManualVerify = async () => {
    const workflowState =
      await requestWorkflowStartVision()

    if (!workflowState) {
      alert(
        '수동 Vision 확인 단계 시작에 실패했습니다.'
      )
      return
    }

    if (
      workflowState.state !==
      'VISION_CHECK'
    ) {
      alert(
        `Workflow 상태 오류: ${workflowState.state}`
      )
      return
    }

    setVerificationMode('MANUAL')
    nav('VERIFICATION')
  }
  const handleVerificationNext = async () => {
    const workflowState =
      await requestWorkflowVisionPassed()

    if (!workflowState) {
      alert(
        'Vision 완료 상태를 Workflow에 반영하지 못했습니다.'
      )
      return
    }

    if (
      workflowState.state !==
      'ITEM_COMPLETE'
    ) {
      alert(
        `Workflow 상태 오류: ${workflowState.state}`
      )
      return
    }

    // 마지막 품목이더라도 ITEM_COMPLETE를 먼저 보여준다.
    // 그 다음 버튼에서 /workflow/next-item을 호출하면
    // Python이 FINAL_VERIFICATION 또는 다음 TRAY_MOVING을 결정한다.
    nav('ITEM_COMPLETE')
  }

  const handleItemNext = async () => {
    const workflowState =
      await requestWorkflowNextItem()

    if (!workflowState) {
      alert(
        '다음 품목 결정에 실패했습니다.'
      )
      return
    }

    // Python Workflow가 모든 품목이 끝났다고 판단
    if (
      workflowState.state ===
      'FINAL_VERIFICATION'
    ) {
      nav('FINAL_VERIFICATION')
      return
    }

    const nextIndex =
      Number(
        workflowState.current_item_index
      )

    if (
      !Number.isInteger(nextIndex) ||
      nextIndex < 0 ||
      nextIndex >= workItems.length
    ) {
      alert(
        'Workflow가 올바르지 않은 품목 index를 반환했습니다.'
      )
      return
    }

    await goToTrayMoving(
      nextIndex
    )
  }
  const handleFinalWeightPass = async () => {
    const workflowState =
      await requestWorkflowFinalVerificationPassed()

    if (!workflowState) {
      alert(
        '최종 검증 완료 상태를 Workflow에 반영하지 못했습니다.'
      )
      return
    }

    if (
      workflowState.state !==
      'TRAY_RETURN'
    ) {
      alert(
        `Workflow 상태 오류: ${workflowState.state}`
      )
      return
    }

    // Tray 복귀 전에 재배치 목표를 계산해 둔다.
    // 현재는 테스트용 relocation 로직이고,
    // 나중 실제 Q-learning 결과로 교체한다.
    const relocationResult =
      await requestRelocationPlan()

    if (
      relocationResult.success &&
      relocationResult.data?.final_slots
    ) {
      setFinalSlots(
        relocationResult.data.final_slots as RackSlots
      )
    } else {
      setFinalSlots(rackSlots)
    }

    nav('TRAY_RETURN')
  }
  const handleRelocationComplete = async (
    slots: RackSlots
  ) => {
    // 현재 TrayReturnScreen이
    // Tray 복귀와 재배치 애니메이션을 한 화면에서 모두 수행한다.
    // 그래서 Workflow에는 두 완료 이벤트를 순서대로 알려준다.

    const trayReturnState =
      await requestWorkflowTrayReturnComplete()

    if (!trayReturnState) {
      alert(
        'Tray 복귀 완료 상태를 Workflow에 반영하지 못했습니다.'
      )
      return
    }

    if (
      trayReturnState.state !==
      'RELOCATION'
    ) {
      alert(
        `Workflow 상태 오류: ${trayReturnState.state}`
      )
      return
    }

    const relocationState =
      await requestWorkflowRelocationComplete()

    if (!relocationState) {
      alert(
        '재배치 완료 상태를 Workflow에 반영하지 못했습니다.'
      )
      return
    }

    if (
      relocationState.state !==
      'INVENTORY_UPDATE'
    ) {
      alert(
        `Workflow 상태 오류: ${relocationState.state}`
      )
      return
    }

    setFinalSlots(slots)
    nav('RELOCATION_COMPLETE')
  }
  const handleRelocationDone = async () => {
    // Workflow 현재 상태:
    // INVENTORY_UPDATE

    // 1) 실제 inventory.json 차감
    const inventorySaved =
      await consumeCompletedWorkInventory()

    if (!inventorySaved) {
      return
    }

    // 2) Python Workflow에 재고 반영 완료 알림
    const inventoryState =
      await requestWorkflowInventoryComplete()

    if (!inventoryState) {
      alert(
        '재고 완료 상태를 Workflow에 반영하지 못했습니다.'
      )
      return
    }

    if (
      inventoryState.state !==
      'HISTORY_SAVE'
    ) {
      alert(
        `Workflow 상태 오류: ${inventoryState.state}`
      )
      return
    }

    // 3) 실제 작업 이력 저장
    const historySaved =
      await saveCompletedWorkHistory()

    if (!historySaved) {
      return
    }

    // 4) Python Workflow에 이력 저장 완료 알림
    const historyState =
      await requestWorkflowHistoryComplete()

    if (!historyState) {
      alert(
        '작업 이력 완료 상태를 Workflow에 반영하지 못했습니다.'
      )
      return
    }

    if (
      historyState.state !==
      'WORK_COMPLETE'
    ) {
      alert(
        `Workflow 상태 오류: ${historyState.state}`
      )
      return
    }

    // 5) 실제 Rack 배치를 영구 저장
    // 프로그램을 껐다 켜도 이 배치가 다시 복원된다.
    const rackSaved =
      await saveRackLayout(finalSlots)

    if (!rackSaved) {
      return
    }

    // 6) Workflow가 WORK_COMPLETE를 반환했고
    // Rack 배치 저장도 성공했을 때만 완료 화면으로 이동
    setRackSlots(finalSlots)
    setCurrentItemIndex(0)
    nav('WORK_COMPLETE')
  }

  const handleEditSave = async (
    idx: number,
    item: WorkItem
  ): Promise<boolean> => {
    try {
      const response = await fetch(
        'http://127.0.0.1:8000/validate-item',
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            part_no: item.partNo,
            name: item.name,
            spec: item.spec,
            quantity: item.qty,
          }),
        }
      )

      if (!response.ok) {
        throw new Error(
          `검증 서버 오류: ${response.status}`
        )
      }

      const result = await response.json()

      if (!result.valid) {
        alert(
          `수정 불가\n\n${result.message}`
        )

        return false
      }

      // DB에 등록된 정식 Tray 매칭을 다시 적용
      const trayId =
        `TRAY ${String(result.part.tray).padStart(2, '0')}`

      const trayInfo = trays.find(
        tray => tray.id === trayId
      )

      const stock = trayInfo?.stock ?? 0

      const validatedItem: WorkItem = {
        ...item,
        partNo: result.part.part_no,
        name: result.part.name,
        spec: result.part.spec,
        tray: trayId,
        stock,
        status:
          item.qty > stock
            ? '재고 부족'
            : '정상',
      }

      setWorkItems(items =>
        items.map(
          (it, i) =>
            i === idx
              ? validatedItem
              : it
        )
      )

      return true

    } catch (error) {
      console.error(
        '품목 수정 검증 오류:',
        error
      )

      alert(
        '품목 정보를 검증하는 중 오류가 발생했습니다.'
      )

      return false
    }
  }

  const handleShowHistory = async () => {
    setPrevScreen(screen)
    await loadWorkHistory()
    nav('WORK_HISTORY')
  }
  const handleHistoryBack = () => nav(prevScreen === 'WORK_HISTORY' ? 'MAIN' : prevScreen)

  const canShowPause = ['TRAY_MOVING', 'PICKING', 'VERIFICATION', 'FINAL_VERIFICATION', 'TRAY_RETURN'].includes(screen)
  const showStatusBar = screen !== 'EMERGENCY_STOP'

  return (
    <div style={{ width: '100%', height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 1024 }}>
      {showStatusBar && (
        <StatusBar
          onHistory={handleShowHistory}
          onSettings={() => nav('SETTINGS')}
          onEmergency={handleEmergency}
          screen={screen}
        />
      )}

      {/* Main content area */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', position: 'relative' }}>
        {screen === 'MAIN' && (
          <MainScreen
            trays={trays}
            rackSlots={rackSlots}
            history={workHistory}
            onFileUpload={handleFileUpload}
            onCamera={() => nav('CAMERA_CAPTURE')}
            onShowHistory={handleShowHistory}
          />
        )}
        {screen === 'SETTINGS' && (
          <StageControlScreen
            onBack={() => nav('MAIN')}
          />
        )}

        {screen === 'CAMERA_CAPTURE' && (
          <CameraCaptureScreen
            onUse={() => nav('ANALYZING')}
            onCancel={() => nav('MAIN')}
          />
        )}
        {screen === 'ANALYZING' && (
          <AnalyzingScreen
            onComplete={() => {}}
            previewUrl={previewUrl}
            previewType={previewType}
            fileName={previewFileName}
          />
        )}
        {screen === 'REVIEW' && (
          <ReviewScreen
            items={workItems}
            isPaused={isPaused}
            showStopConfirm={showStopConfirm}
            onStart={handleWorkStart}
            onCancelAuto={() => {}}
            onEditSave={handleEditSave}
          />
        )}
        {screen === 'TRAY_MOVING' && (
          <TrayMovingScreen
            key={`move-${currentItemIndex}-${restartKey}`}
            item={currentItem}
            itemIndex={currentItemIndex}
            totalItems={workItems.length}
            trays={trays.map(t => t.id === currentItem.tray ? { ...t, status: 'MOVING' as const } : t)}
            isPaused={isPaused}
            showStop={showStopConfirm}
            onArrived={handleTrayArrived}
            onPause={handlePause}
            onStop={handleStopRequest}
          />
        )}
        {screen === 'PICKING' && (
          <PickingScreen
            key={`pick-${currentItemIndex}-${restartKey}`}
            item={currentItem}
            itemIndex={currentItemIndex}
            totalItems={workItems.length}
            isPaused={isPaused}
            showStop={showStopConfirm}
            onAutoVerify={handlePickingAutoVerify}
            onManualVerify={handlePickingManualVerify}
            onPause={handlePause}
            onStop={handleStopRequest}
          />
        )}
        {screen === 'VERIFICATION' && (
          <VerificationScreen
            key={`verify-${currentItemIndex}-${restartKey}`}
            item={currentItem}
            itemIndex={currentItemIndex}
            totalItems={workItems.length}
            isPaused={isPaused}
            showStop={showStopConfirm}
            mode={verificationMode}
            onVisionCheck={requestVisionCount}
            onNext={handleVerificationNext}
            onPause={handlePause}
            onStop={handleStopRequest}
          />
        )}
        {screen === 'ITEM_COMPLETE' && (
          <ItemCompleteScreen
            key={`complete-${currentItemIndex}-${restartKey}`}
            item={currentItem}
            itemIndex={currentItemIndex}
            totalItems={workItems.length}
            isPaused={isPaused}
            showStop={showStopConfirm}
            onNext={handleItemNext}
          />
        )}
        {screen === 'FINAL_VERIFICATION' && (
          <FinalVerificationScreen
            items={workItems}
            isPaused={isPaused}
            showStop={showStopConfirm}
            onPass={handleFinalWeightPass}
            onPause={handlePause}
            onStop={handleStopRequest}
          />
        )}
        {screen === 'TRAY_RETURN' && (
          <TrayReturnScreen
            usedItems={workItems}
            currentSlots={rackSlots}
            targetSlots={finalSlots}
            isPaused={isPaused}
            showStop={showStopConfirm}
            onComplete={handleRelocationComplete}
            onPause={handlePause}
            onStop={handleStopRequest}
            onArucoCheck={requestVisionAruco}
          />
        )}
        {screen === 'RELOCATION_COMPLETE' && (
          <RelocationCompleteScreen
            initialSlots={rackSlots}
            finalSlots={finalSlots}
            onNext={handleRelocationDone}
          />
        )}
        {screen === 'WORK_COMPLETE' && (
          <WorkCompleteScreen
            items={workItems}
            onNewWork={() => { setCurrentItemIndex(0); nav('MAIN') }}
            onHistory={handleShowHistory}
          />
        )}
        {screen === 'WORK_HISTORY' && (
          <WorkHistoryScreen
            onBack={handleHistoryBack}
            history={workHistory}
          />
        )}
        {screen === 'EQUIPMENT_ERROR' && (
          <EquipmentErrorScreen
            onRetry={() => {
              goToTrayMoving(currentItemIndex)
            }}
            onHome={() => nav('MAIN')}
            onStop={() => { setCurrentItemIndex(0); nav('MAIN') }}
          />
        )}
        {screen === 'EMERGENCY_STOP' && (
          <EmergencyStopScreen
            onHome={requestStageHome}
            onDismiss={() => {
              setCurrentItemIndex(0)
              nav('MAIN')
            }}
          />
        )}

        {/* PAUSE overlay */}
        {isPaused && canShowPause && (
          <PauseOverlay
            currentItem={currentItem}
            currentStage={pauseStageLabel}
            onResume={handleResume}
            onRestart={handleRestartCurrentStage}
            onStop={handleStopRequest}
          />
        )}

        {/* Stop confirm modal */}
        {showStopConfirm && (
          <StopConfirmModal onCancel={handleStopCancel} onConfirm={handleStopConfirm} />
        )}
      </div>

      {/* Bottom status bar for active work */}
      {['TRAY_MOVING', 'PICKING', 'VERIFICATION', 'ITEM_COMPLETE', 'FINAL_VERIFICATION', 'TRAY_RETURN'].includes(screen) && (
        <div style={{
          background: 'var(--hmi-navy-mid)', borderTop: '1px solid #2d4a70',
          padding: '4px 16px', display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            {[0, 1, 2].map(i => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <div style={{
                  width: 20, height: 20,
                  background: i < currentItemIndex ? 'var(--hmi-green)' : i === currentItemIndex ? 'var(--hmi-blue)' : '#374151',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 10, color: 'white', fontWeight: 700
                }}>
                  {i < currentItemIndex ? '✓' : i + 1}
                </div>
                <span style={{ fontSize: 10, color: i === currentItemIndex ? '#93c5fd' : '#6b7280', fontFamily: 'JetBrains Mono, monospace' }}>
                  {workItems[i]?.name.slice(0, 5)}
                </span>
                {i < 2 && <span style={{ color: '#374151', fontSize: 10 }}>›</span>}
              </div>
            ))}
          </div>
          <div style={{ flex: 1 }} />
          <span style={{ color: '#94a3b8', fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>
            WO-20260817-001 · OP-001 · 작업 진행 중
          </span>
          <span className="status-dot blink" style={{ background: '#22c55e' }} />
        </div>
      )}
    </div>
  )
}