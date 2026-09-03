import React, { useEffect, useMemo, useState } from 'react'
import YoloAnnotationCanvas, { type YoloBox } from './YoloAnnotationCanvas'

const API = 'http://127.0.0.1:8000'
type Tab = 'capture' | 'label' | 'dataset' | 'training' | 'models' | 'live'

export default function YoloInspectionPanel({ connected, inspectionEnabled }: { connected: boolean; inspectionEnabled: boolean }) {
  const [tab, setTab] = useState<Tab>('capture')
  const [status, setStatus] = useState<any>(null)
  const [images, setImages] = useState<any[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [boxes, setBoxes] = useState<YoloBox[]>([])
  const [selectedClass, setSelectedClass] = useState('')
  const [captureGroup, setCaptureGroup] = useState('')
  const [autoOnCapture, setAutoOnCapture] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [trainRatio, setTrainRatio] = useState(80)
  const [seed, setSeed] = useState(42)
  const [baseModel, setBaseModel] = useState('')
  const [epochs, setEpochs] = useState(50)
  const [imageSize, setImageSize] = useState(640)
  const [batch, setBatch] = useState(4)
  const [live, setLive] = useState<any>(null)
  const [liveKey, setLiveKey] = useState(0)
  const [threshold, setThreshold] = useState(0.25)

  const classes = Array.isArray(status?.classes) ? status.classes : []
  const classLabels = useMemo(() => Object.fromEntries(classes.map((item: any) => [item.class_key, item.display_name])), [classes])
  const selectedImage = images.find(item => item.image_id === selectedId)
  const selectedIndex = images.findIndex(item => item.image_id === selectedId)

  const readJson = async (path: string, options?: RequestInit) => {
    const response = await fetch(`${API}${path}`, { cache: 'no-store', ...options })
    const data = await response.json().catch(() => ({}))
    if (!response.ok) throw new Error(data?.detail ?? `HTTP ${response.status}`)
    return data
  }

  const refresh = async () => {
    try {
      const [nextStatus, nextImages] = await Promise.all([
        readJson('/inspection/yolo/status'),
        readJson('/inspection/yolo/images'),
      ])
      const nextImageList = nextImages.images ?? []
      const nextClassList = nextStatus.classes ?? []
      const nextBaseModels = nextStatus.base_models ?? []

      setStatus(nextStatus)
      setImages(nextImageList)

      // refresh() is called from a 2-second polling timer created only once.
      // Use functional updates so that the timer never restores stale initial
      // selections over the operator's current class/image/model choice.
      setSelectedClass(current => {
        if (current && nextClassList.some((item: any) => item.class_key === current)) return current
        return nextClassList[0]?.class_key ?? ''
      })
      setBaseModel(current => {
        if (current && nextBaseModels.some((item: any) => item.id === current)) return current
        return nextBaseModels[0]?.id ?? ''
      })
      setSelectedId(current => {
        if (current && nextImageList.some((item: any) => item.image_id === current)) return current
        return nextImageList[0]?.image_id ?? ''
      })

      if (Number.isFinite(Number(nextStatus.inference?.confidence_threshold))) setThreshold(Number(nextStatus.inference.confidence_threshold))
    } catch (error) {
      setMessage(error instanceof Error ? `ERROR: ${error.message}` : 'ERROR')
    }
  }

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 2000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!selectedImage) { setBoxes([]); return }
    setBoxes((selectedImage.boxes ?? []).map((box: any) => ({
      class_key: box.class_key, x: Number(box.x), y: Number(box.y),
      width: Number(box.width), height: Number(box.height), confidence: box.confidence,
    })))
  }, [selectedImage?.image_id, selectedImage?.updated_at, selectedImage?.captured_at])

  useEffect(() => {
    // Apply the capture-time class hint only when another image is opened.
    // Annotation save/refresh must not roll back a class the operator selected.
    if (selectedImage?.suggested_class_key) setSelectedClass(selectedImage.suggested_class_key)
  }, [selectedImage?.image_id])

  useEffect(() => {
    if (tab !== 'live' || !status?.inference?.model_ready) return
    let stopped = false
    const poll = async () => {
      try {
        const value = await readJson('/inspection/yolo/live')
        if (!stopped) { setLive(value); if (value.success) setLiveKey(key => key + 1) }
      } catch { /* status panel reports errors */ }
    }
    poll(); const timer = window.setInterval(poll, 700)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [tab, status?.inference?.model_ready])

  const post = async (path: string, value: any = {}) => readJson(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) })
  const action = async (work: () => Promise<any>, success: string) => {
    if (busy) return
    setBusy(true)
    try { const value = await work(); setMessage(value?.message ?? success); await refresh(); return value }
    catch (error) { setMessage(error instanceof Error ? `ERROR: ${error.message}` : 'ERROR') }
    finally { setBusy(false) }
  }

  const capture = () => action(async () => {
    const value = await post('/inspection/yolo/capture', { suggested_class_key: selectedClass, capture_group: captureGroup || null, auto_label: autoOnCapture })
    setSelectedId(value.image.image_id); setTab('label'); return value
  }, '실제 C920 frame을 저장했습니다.')

  const save = (state: 'MANUAL' | 'REVIEWED' | 'BACKGROUND') => action(async () => {
    const value = await readJson(`/inspection/yolo/images/${selectedId}/annotation`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ boxes: state === 'BACKGROUND' ? [] : boxes, state }) })
    return value
  }, `${state} annotation을 저장했습니다.`)

  const removeImage = () => {
    if (!selectedImage || !window.confirm(`${selectedImage.file_name}을 삭제할까요?`)) return
    action(async () => {
      const value = await readJson(`/inspection/yolo/images/${selectedId}`, { method: 'DELETE' })
      setSelectedId(''); return value
    }, '이미지를 삭제했습니다.')
  }

  const autoLabel = () => action(() => post(`/inspection/yolo/images/${selectedId}/auto-label`), 'Auto Label 후보를 생성했습니다. 반드시 검토하세요.')
  const validation = status?.dataset ?? {}
  const split = status?.split ?? {}
  const training = status?.training ?? {}
  const inference = status?.inference ?? {}

  const importModel = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setBusy(true)
    try {
      const form = new FormData(); form.append('file', file)
      const response = await fetch(`${API}/inspection/yolo/models/import`, { method: 'POST', body: form })
      const value = await response.json().catch(() => ({}))
      if (!response.ok) throw new Error(value?.detail ?? `HTTP ${response.status}`)
      setMessage(`모델 Import 완료: ${value.name}`); await refresh()
    } catch (error) { setMessage(error instanceof Error ? `ERROR: ${error.message}` : 'ERROR') }
    finally { setBusy(false); event.target.value = '' }
  }

  const steps = [
    ['capture', '1. 이미지 촬영'], ['label', '2. Bounding Box 라벨링'], ['dataset', '3. 검증 / Train·Val'],
    ['training', '4. 학습 / Export'], ['models', '5. 모델 Import·활성화'], ['live', '6. Live Detection'],
  ] as Array<[Tab, string]>

  return (
    <div style={{ marginTop: 14, border: '2px solid #1d4ed8', background: 'white', padding: 12 }}>
      <div className="section-header">YOLO OBJECT DETECTION · C920 SHARED CAMERA</div>
      <div style={{ display: 'flex', gap: 5, marginTop: 9, flexWrap: 'wrap' }}>
        {steps.map(([key, label]) => <button key={key} className={tab === key ? 'btn-primary' : 'btn-secondary'} onClick={() => setTab(key)}>{label}</button>)}
      </div>
      <div style={{ marginTop: 8, padding: 8, background: '#eff6ff', border: '1px solid #93c5fd', fontSize: 11 }}>
        Workflow: 촬영 → Box 라벨링 → 검증 → Train/Val → 학습 또는 Colab Export → best.pt Import/활성화 → Live Detection
        <br />첫 모델 이후: 촬영 → Auto Label(Prediction) → 사람 검토/수정 → REVIEWED → 재학습
      </div>
      <div style={{ display: 'flex', gap: 12, marginTop: 8, fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }}>
        <strong>MODE {String(status?.part_inspection_mode ?? '--').toUpperCase()}</strong>
        <span>C920 {connected ? 'CONNECTED' : 'NOT CONNECTED'}</span>
        <span>DEVICE CPU · CUDA UNAVAILABLE</span>
        <span>MODEL {inference.model_ready ? 'READY' : 'YOLO MODEL NOT READY'}</span>
      </div>

      {tab === 'capture' && <div style={{ display: 'grid', gridTemplateColumns: '1.4fr .8fr', gap: 12, marginTop: 12 }}>
        <div style={{ background: '#07111f', aspectRatio: '16/9', position: 'relative' }}>
          {connected ? <img src={`${API}/work-order-camera/stream`} alt="C920 dataset capture" style={{ width: '100%', height: '100%', objectFit: 'contain' }} /> : <div style={{ color: '#fca5a5', display: 'grid', placeItems: 'center', height: '100%' }}>CAMERA NOT CONNECTED</div>}
        </div>
        <div>
          <label>현재 촬영 Class<select value={selectedClass} onChange={event => setSelectedClass(event.target.value)} style={{ width: '100%', padding: 7, marginTop: 4 }}>{classes.map((item: any) => <option key={item.class_key} value={item.class_key}>{item.display_name} · {item.class_key}</option>)}</select></label>
          <label style={{ display: 'block', marginTop: 8 }}>Capture group<input value={captureGroup} onChange={event => setCaptureGroup(event.target.value)} placeholder="비우면 분 단위 자동 group" style={{ width: '100%', padding: 7, marginTop: 4 }} /></label>
          <label style={{ display: 'block', marginTop: 8 }}><input type="checkbox" checked={autoOnCapture} disabled={!inference.model_ready} onChange={event => setAutoOnCapture(event.target.checked)} /> 촬영 즉시 Auto Label (검토 전 Prediction)</label>
          <button className="btn-primary" disabled={!connected || !inspectionEnabled || busy || !selectedClass} onClick={capture} style={{ width: '100%', marginTop: 10 }}>현재 실제 Frame 저장</button>
          <div style={{ marginTop: 9, padding: 8, background: '#f8fafc', fontSize: 10 }}>저장 이미지: <strong>{images.length}</strong><br />부품을 손으로 자연스럽게 회전하고 위치·거리·조명을 정상 범위에서 조금씩 바꿔 촬영하세요. Class 선택은 후보일 뿐 Box를 자동 확정하지 않습니다.</div>
        </div>
      </div>}

      {tab === 'label' && <div style={{ display: 'grid', gridTemplateColumns: '230px minmax(500px, 1fr)', gap: 12, marginTop: 12 }}>
        <div style={{ border: '1px solid #dbe3ec', maxHeight: 610, overflow: 'auto' }}>
          <div style={{ padding: 7, background: '#1e3a5f', color: 'white', fontWeight: 800 }}>저장 이미지 {images.length}</div>
          {images.map((item, index) => <button key={item.image_id} onClick={() => setSelectedId(item.image_id)} style={{ width: '100%', padding: 7, textAlign: 'left', border: 0, borderBottom: '1px solid #e5e7eb', background: selectedId === item.image_id ? '#dbeafe' : 'white' }}>
            <strong>{index + 1}. {item.suggested_class_key ?? 'no class hint'}</strong><br /><span style={{ fontSize: 9 }}>{item.annotation_state} · {item.boxes?.length ?? 0} objects</span>
          </button>)}
        </div>
        <div>
          {selectedImage ? <>
            <div style={{ display: 'flex', gap: 6, marginBottom: 7 }}>
              <button className="btn-secondary" disabled={selectedIndex <= 0} onClick={() => setSelectedId(images[selectedIndex - 1].image_id)}>← 이전 이미지</button>
              <strong style={{ flex: 1, textAlign: 'center', padding: 6 }}>{selectedIndex + 1} / {images.length} · {selectedImage.annotation_state}</strong>
              <button className="btn-secondary" disabled={selectedIndex < 0 || selectedIndex >= images.length - 1} onClick={() => setSelectedId(images[selectedIndex + 1].image_id)}>다음 이미지 →</button>
            </div>
            <div style={{ display: 'flex', gap: 7, alignItems: 'center', marginBottom: 7, padding: 7, background: '#f8fafc', border: '1px solid #dbe3ec' }}>
              <strong style={{ fontSize: 10 }}>새 Box Class</strong>
              <select value={selectedClass} onChange={event => setSelectedClass(event.target.value)} style={{ minWidth: 190, padding: 6 }}>
                {classes.map((item: any) => <option key={item.class_key} value={item.class_key}>{item.display_name} · {item.class_key}</option>)}
              </select>
              <span style={{ fontSize: 9, color: '#64748b' }}>선택한 Class는 다음에 새로 그리는 Box에 적용됩니다.</span>
            </div>
            <YoloAnnotationCanvas imageUrl={`${API}/inspection/yolo/images/${selectedId}?v=${selectedImage.updated_at ?? selectedImage.captured_at}`} imageWidth={Number(selectedImage.width)} imageHeight={Number(selectedImage.height)} boxes={boxes} selectedClass={selectedClass || classes[0]?.class_key} classLabels={classLabels} onChange={setBoxes} />
            <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
              <button className="btn-primary" disabled={busy || boxes.length === 0} onClick={() => save('MANUAL')}>Manual 저장</button>
              <button className="btn-green" disabled={busy || boxes.length === 0} onClick={() => save('REVIEWED')}>검토 완료 · REVIEWED</button>
              <button className="btn-secondary" disabled={busy || !inference.model_ready} onClick={autoLabel}>Auto Label</button>
              <button className="btn-secondary" disabled={busy} onClick={() => save('BACKGROUND')}>Intentional Background</button>
              <button className="btn-warning" disabled={busy} onClick={removeImage}>이미지 삭제</button>
            </div>
            {selectedImage.annotation_state === 'AUTO_UNREVIEWED' && <div style={{ marginTop: 7, padding: 7, background: '#fff7ed', color: '#9a3412', fontWeight: 800 }}>AUTO_UNREVIEWED — Prediction일 뿐 Ground Truth가 아닙니다. Box/Class를 확인하고 REVIEWED로 저장하세요.</div>}
          </> : <div style={{ padding: 30 }}>촬영 이미지를 선택하세요.</div>}
        </div>
      </div>}

      {tab === 'dataset' && <div style={{ marginTop: 12 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 6 }}>{[
          ['전체', validation.image_count], ['라벨 완료', validation.labeled_image_count], ['미라벨', validation.unlabeled_image_count], ['Background', validation.background_image_count], ['Auto 미검토', validation.auto_unreviewed_count], ['잘못된 항목', validation.invalid_annotation_count],
        ].map(([label, value]) => <div key={label} style={{ border: '1px solid #dbe3ec', padding: 8 }}><small>{label}</small><div style={{ fontSize: 18, fontWeight: 900 }}>{value ?? 0}</div></div>)}</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 5, marginTop: 7 }}>{classes.map((item: any) => <div key={item.class_key} style={{ padding: 7, background: '#f8fafc', border: '1px solid #dbe3ec', fontSize: 10 }}>{item.display_name}<br /><strong>{validation.class_object_counts?.[item.class_key] ?? 0} objects</strong></div>)}</div>
        <button className="btn-primary" disabled={busy} onClick={() => action(() => post('/inspection/yolo/validate'), 'Dataset 검증 완료')} style={{ marginTop: 9 }}>Dataset 검증</button>
        <div style={{ marginTop: 7, color: validation.valid ? '#15803d' : '#b91c1c', fontWeight: 800 }}>{validation.valid ? 'VALID · Split/Export 가능' : 'INVALID · 아래 항목을 수정하세요.'}</div>
        {(validation.errors ?? []).slice(0, 20).map((error: any, index: number) => <div key={index} style={{ fontSize: 10, color: '#b91c1c' }}>{error.image_id}: {error.message}</div>)}
        <div style={{ display: 'flex', gap: 7, alignItems: 'end', marginTop: 10 }}>
          <label>Train %<input type="number" value={trainRatio} onChange={event => setTrainRatio(Number(event.target.value))} style={{ width: 75, padding: 6, display: 'block' }} /></label>
          <label>Seed<input type="number" value={seed} onChange={event => setSeed(Number(event.target.value))} style={{ width: 85, padding: 6, display: 'block' }} /></label>
          <button className="btn-green" disabled={!validation.valid || busy} onClick={() => action(() => post('/inspection/yolo/split', { train_ratio: trainRatio / 100, seed }), 'Train/Validation과 dataset.yaml을 생성했습니다.')}>Train / Validation 생성</button>
          <span style={{ fontSize: 10 }}>Split: {split.ready ? `READY · train ${split.train_image_ids?.length} / val ${split.val_image_ids?.length}` : 'NOT READY'}</span>
        </div>
      </div>}

      {tab === 'training' && <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 12 }}>
        <div style={{ border: '1px solid #dbe3ec', padding: 10 }}><strong>EXTERNAL TRAINING · 권장</strong><p style={{ fontSize: 11 }}>현재 PC는 CUDA unavailable입니다. Dataset ZIP을 Google Colab에 올려 GPU 학습 후 best.pt를 Import하세요.</p><button className="btn-primary" disabled={!split.ready || busy} onClick={() => action(async () => { const value = await post('/inspection/yolo/export'); window.location.href = `${API}${value.download_url}`; return value }, 'Dataset ZIP 생성 완료')}>YOLO Dataset Export</button><ol style={{ fontSize: 10, lineHeight: 1.6 }}><li>ZIP을 Colab에 업로드·압축 해제</li><li>Ultralytics 설치 후 dataset.yaml로 detect train</li><li>생성된 best.pt 다운로드</li><li>Models 탭에서 Import</li></ol></div>
        <div style={{ border: '1px solid #dbe3ec', padding: 10 }}><strong>LOCAL TRAINING · CPU (느릴 수 있음)</strong><select value={baseModel} onChange={event => setBaseModel(event.target.value)} style={{ width: '100%', padding: 6, marginTop: 7 }}><option value="">Local base weight 없음</option>{(status?.base_models ?? []).map((item: any) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><div style={{ display: 'flex', gap: 5, marginTop: 6 }}>{[['Epochs', epochs, setEpochs], ['Image', imageSize, setImageSize], ['Batch', batch, setBatch]].map(([label, value, setter]: any) => <label key={label} style={{ fontSize: 9 }}>{label}<input type="number" value={value} onChange={event => setter(Number(event.target.value))} style={{ width: '100%', padding: 5, display: 'block' }} /></label>)}</div><button className="btn-green" disabled={!split.ready || !baseModel || busy || training.running} onClick={() => action(() => post('/inspection/yolo/training/start', { base_model_id: baseModel, epochs, image_size: imageSize, batch }), '학습 시작')} style={{ width: '100%', marginTop: 7 }}>CPU 학습 시작</button><div style={{ fontSize: 10, marginTop: 7 }}>STATUS {training.status ?? 'IDLE'} · epoch {training.epoch ?? 0}/{training.epochs ?? '--'} · elapsed {training.elapsed_seconds ?? '--'}s</div>{training.error && <div style={{ color: '#b91c1c', fontSize: 10 }}>{training.error}</div>}</div>
      </div>}

      {tab === 'models' && <div style={{ marginTop: 12 }}>
        <label className="btn-primary" style={{ display: 'inline-block', cursor: 'pointer' }}>YOLO Model Import (.pt)<input type="file" accept=".pt" onChange={importModel} style={{ display: 'none' }} /></label>
        {(status?.models ?? []).map((model: any) => <div key={model.id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: 8, borderBottom: '1px solid #e5e7eb' }}><strong style={{ flex: 1 }}>{model.name}</strong><span>{model.active ? 'ACTIVE' : 'READY'}</span><button className="btn-green" disabled={model.active || busy} onClick={() => action(() => post('/inspection/yolo/models/activate', { model_id: model.id }), '검수 모델을 활성화했습니다.')}>검수 모델로 사용</button></div>)}
        {(status?.models ?? []).length === 0 && <div style={{ padding: 15, color: '#b45309' }}>등록 모델 없음 — Colab best.pt를 Import하세요.</div>}
      </div>}

      {tab === 'live' && <div style={{ display: 'grid', gridTemplateColumns: '1.5fr .7fr', gap: 12, marginTop: 12 }}>
        <div style={{ background: '#07111f', aspectRatio: '16/9', display: 'grid', placeItems: 'center' }}>{inference.model_ready ? <img key={liveKey} src={`${API}/inspection/yolo/live/snapshot?v=${liveKey}`} alt="Live YOLO detection" style={{ width: '100%', height: '100%', objectFit: 'contain' }} /> : <strong style={{ color: '#fca5a5' }}>YOLO MODEL NOT READY</strong>}</div>
        <div><label>Confidence threshold {threshold.toFixed(2)}<input type="range" min="0.05" max="0.95" step="0.05" value={threshold} onChange={event => setThreshold(Number(event.target.value))} onMouseUp={() => action(() => post('/inspection/yolo/confidence', { confidence_threshold: threshold }), 'Confidence threshold 적용')} style={{ width: '100%' }} /></label><div style={{ marginTop: 8, fontSize: 10 }}>Confidence는 정확도가 아니라 모델 score입니다.<br />Inference {live?.inference_ms ?? '--'} ms</div>{classes.map((item: any) => <div key={item.class_key} style={{ display: 'flex', padding: 6, borderBottom: '1px solid #e5e7eb' }}><span style={{ flex: 1 }}>{item.display_name}</span><strong>{live?.counts?.[item.class_key] ?? 0}</strong></div>)}<div style={{ marginTop: 10, padding: 8, border: '1px solid #dbe3ec' }}><strong>단품 Ground Truth Test</strong><select value={selectedClass} onChange={event => setSelectedClass(event.target.value)} style={{ width: '100%', padding: 6, marginTop: 5 }}>{classes.map((item: any) => <option key={item.class_key} value={item.class_key}>{item.display_name}</option>)}</select><button className="btn-green" disabled={!inference.model_ready || busy} onClick={() => action(() => post('/inspection/yolo/classification-test', { ground_truth_class_key: selectedClass }), '실제 YOLO 시험 결과를 기록했습니다.')} style={{ width: '100%', marginTop: 6 }}>현재 Prediction과 비교 기록</button>{(() => { const value = inference.validation?.by_class?.[selectedClass] ?? {}; return <div style={{ fontSize: 10, marginTop: 5 }}>tests {value.tests ?? 0} · correct {value.correct ?? 0} · misclassified {value.misclassified ?? 0} · no detection {value.no_detection ?? 0}</div> })()}</div></div>
      </div>}

      <div style={{ minHeight: 20, marginTop: 9, fontSize: 11, color: message.startsWith('ERROR') ? '#b91c1c' : '#334155' }}>{message || 'READY'}</div>
    </div>
  )
}
